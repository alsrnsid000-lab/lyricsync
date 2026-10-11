"""오디오 전처리: 길이 측정, 16kHz 모노 변환, 보컬 분리.

ffmpeg를 외부 프로세스로 쓴다. 파이썬 오디오 라이브러리를 더 끼우는 것보다
설치가 간단하고, 어떤 포맷이 들어와도 같은 코드로 처리된다.

exe로 묶였을 때는 번들에 넣은 ffmpeg를 쓴다(runtime.find_binary).
Demucs도 번들에서는 `python -m demucs`를 띄울 수 없으므로 같은 프로세스
안에서 직접 호출한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .runtime import find_binary, is_frozen, model_cache_dir

# 보컬 분리에 쓸 Demucs 모델. htdemucs가 기본이고 품질 대비 속도가 가장 낫다.
DEMUCS_MODEL = "htdemucs"

# 윈도우에서 subprocess가 콘솔 창을 띄우지 않게 한다. GUI에서 중요.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class AudioError(RuntimeError):
    """오디오 처리 실패."""


def separation_available() -> bool:
    """보컬 분리를 쓸 수 있는지.

    Demucs는 PyTorch를 끌고 오는데, 윈도우 exe 번들에서는 torch가
    numpy·OpenMP와 충돌해 앱이 뜨지 않았다. 그래서 배포 exe에는 넣지 않는다.
    소스로 돌릴 때 `pip install demucs` 되어 있으면 그대로 쓴다.

    실제 import까지 하지는 않는다 — torch 로딩은 수 초가 걸려서 창이 뜨는
    속도를 체감할 만큼 늦춘다.
    """
    from importlib.util import find_spec

    try:
        return find_spec("demucs") is not None and find_spec("torch") is not None
    except (ImportError, ValueError):
        return False


SEPARATION_HINT = (
    "보컬 분리는 이 버전에 포함되지 않았습니다. "
    "반주가 깔린 채로 인식하므로 받아쓰기 정확도가 낮아질 수 있습니다. "
    "가사 파일을 함께 넣으면 글자는 그대로 유지되므로 영향이 작습니다."
)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )


def _require(tool: str) -> str:
    path = find_binary(tool)
    if not path:
        raise AudioError(
            f"'{tool}'을 찾을 수 없습니다.\n"
            "  윈도우: 이 프로그램을 압축 푼 폴더 그대로 실행하세요 "
            "(ffmpeg 폴더가 같이 있어야 합니다)\n"
            "  직접 설치: https://ffmpeg.org/download.html"
        )
    return path


def probe_duration(path: Path) -> float:
    """음원 길이(초). 실패하면 AudioError."""
    ffprobe = _require("ffprobe")
    proc = _run([
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ])
    if proc.returncode != 0:
        raise AudioError(f"음원을 읽을 수 없습니다: {path.name}\n{(proc.stderr or '').strip()}")
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise AudioError(f"길이를 확인할 수 없습니다: {path.name}") from exc


def to_wav16k(src: Path, dst: Path) -> Path:
    """ASR이 먹기 좋은 형태(16kHz 모노 PCM)로 변환."""
    ffmpeg = _require("ffmpeg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = _run([
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(src),
        "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(dst),
    ])
    if proc.returncode != 0:
        raise AudioError(f"변환 실패: {src.name}\n{(proc.stderr or '').strip()}")
    return dst


def _find_vocals(workdir: Path, model: str) -> Path | None:
    for pattern in (f"{model}/*/vocals.wav", "*/*/vocals.wav", "**/vocals.wav"):
        found = sorted(workdir.glob(pattern))
        if found:
            return found[0]
    return None


def _separate_in_process(src: Path, workdir: Path, model: str) -> Path:
    """같은 프로세스에서 Demucs를 호출한다 (exe 번들용).

    번들에는 python.exe가 없어서 `python -m demucs`를 띄울 수 없다.
    """
    try:
        from demucs.separate import main as demucs_main
    except ImportError as exc:
        raise AudioError(f"Demucs를 불러올 수 없습니다: {exc}") from exc

    args = [
        "-n", model,
        "--two-stems", "vocals",
        "-o", str(workdir),
        str(src),
    ]
    try:
        demucs_main(args)
    except SystemExit as exc:  # demucs가 인자 오류 등에서 exit을 호출한다
        if exc.code not in (0, None):
            raise AudioError(f"Demucs가 코드 {exc.code}로 종료했습니다.") from exc
    except Exception as exc:
        raise AudioError(f"Demucs 보컬 분리 실패: {exc}") from exc

    vocals = _find_vocals(workdir, model)
    if not vocals:
        raise AudioError("Demucs가 끝났지만 vocals.wav를 찾지 못했습니다.")
    return vocals


def _separate_subprocess(src: Path, workdir: Path, model: str) -> Path:
    """별도 프로세스로 Demucs 실행 (소스 실행용).

    메모리를 확실히 회수하고, 모델이 터져도 본 프로세스가 같이 죽지 않는다.
    """
    proc = _run([
        sys.executable, "-m", "demucs",
        "-n", model,
        "--two-stems", "vocals",
        "-o", str(workdir),
        str(src),
    ])
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        hint = tail[-1] if tail else "원인 불명"
        raise AudioError(f"Demucs 보컬 분리 실패: {hint}")

    vocals = _find_vocals(workdir, model)
    if not vocals:
        raise AudioError("Demucs가 끝났지만 vocals.wav를 찾지 못했습니다.")
    return vocals


def separate_vocals(src: Path, workdir: Path, model: str = DEMUCS_MODEL) -> Path:
    """Demucs로 보컬만 분리해서 그 경로를 돌려준다.

    반주가 깔린 채로 ASR을 돌리면 인식률이 크게 떨어지므로 가능하면 항상 거친다.
    실패하면 AudioError를 던지고, 호출부가 원본으로 폴백한다.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    model_cache_dir()  # 가중치 받을 폴더 미리 확보
    if is_frozen():
        return _separate_in_process(src, workdir, model)
    return _separate_subprocess(src, workdir, model)


def prepare(
    src: Path,
    workdir: Path,
    separate: bool = True,
    on_log=None,
) -> tuple[Path, float, bool]:
    """ASR에 넣을 오디오를 준비한다.

    반환: (오디오 경로, 전체 길이(초), 보컬 분리 성공 여부)
    """
    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    src = Path(src)
    if not src.exists():
        raise AudioError(f"파일이 없습니다: {src}")

    duration = probe_duration(src)
    separated = False
    source = src

    if separate and not separation_available():
        log(f"[주의] {SEPARATION_HINT}")
        separate = False

    if separate:
        try:
            source = separate_vocals(src, workdir / "demucs")
            separated = True
        except AudioError as exc:
            # 분리는 품질 향상용이지 필수는 아니다. 원본으로 계속 간다.
            log(f"[주의] 보컬 분리를 건너뜁니다 — {exc}")
            log("       반주 때문에 인식률이 떨어질 수 있습니다.")
            source = src

    wav = to_wav16k(source, workdir / "asr_input.wav")
    return wav, duration, separated
