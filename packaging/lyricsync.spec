# PyInstaller 스펙. 윈도우에서 실행한다.
#
#   pyinstaller packaging/lyricsync.spec --noconfirm
#
# onedir로 묶는다. onefile은 실행할 때마다 전부 임시 폴더에 풀기 때문에
# 이 크기에서는 시작이 너무 느리다.
#
# 설계 원칙: 용량보다 동작이 우선이다. 한 번 excludes로 setuptools를 뺐다가
# pkg_resources가 jaraco를 못 찾아 앱이 아예 안 떴다. 빼서 줄어드는 수십 MB는
# 못 뜨는 앱을 정당화하지 못한다.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

ROOT = Path(SPECPATH).parent
NAME = "lyricsync"

hidden = []
datas = []
binaries = []
problems = []


def safe(label, fn, pkg, sink):
    """수집 실패를 삼키지 않고 기록한다.

    전에는 except: pass 였는데, 그래서 torch 수집이 실패해도 빌드가 조용히
    '성공'했다. 실패는 빌드 로그에 남겨야 다음에 알아챌 수 있다.
    """
    try:
        items = fn(pkg)
        if items:
            sink.extend(items)
        print(f"[spec] {label:18} {pkg:20} {len(items)}개")
        return len(items)
    except Exception as exc:
        problems.append(f"{label}({pkg}): {type(exc).__name__}: {exc}")
        print(f"[spec] {label:18} {pkg:20} 실패 — {exc}")
        return 0


# --- 핵심 패키지 --------------------------------------------------------
# 이 셋은 지연 임포트(함수 안에서 import)라서 PyInstaller의 정적 분석이
# 절대 못 찾는다. 반드시 명시해야 한다.
CORE = ["torch", "demucs", "faster_whisper"]
OPTIONAL = ["torchaudio", "whisper", "whisperx"]

for pkg in CORE + OPTIONAL:
    safe("collect_submodules", collect_submodules, pkg, hidden)

for pkg in CORE + OPTIONAL + ["lightning_fabric", "transformers", "pkg_resources"]:
    safe("collect_data_files", collect_data_files, pkg, datas)

for pkg in ["torch", "torchaudio", "ctranslate2", "onnxruntime"]:
    safe("collect_dynamic_libs", collect_dynamic_libs, pkg, binaries)

# --- 이름으로만 임포트되는 것들 -----------------------------------------
hidden += [
    # demucs가 설정을 읽을 때 쓴다
    "dora", "dora.log", "omegaconf", "julius", "einops", "openunmix",
    "demucs.separate", "demucs.apply", "demucs.pretrained",
    # 오디오 입출력
    "soundfile", "soxr", "av",
    # faster-whisper 백엔드
    "ctranslate2", "onnxruntime", "tokenizers", "sentencepiece",
    # pkg_resources가 런타임에 끌어오는 vendored 의존성.
    # 이게 없으면 앱이 아예 못 뜬다 (ModuleNotFoundError: jaraco).
    "pkg_resources", "setuptools",
    "jaraco", "jaraco.text", "jaraco.functools", "jaraco.context",
    "jaraco.collections", "jaraco.structures",
    "more_itertools", "platformdirs", "backports", "backports.tarfile",
    # 기타
    "regex", "tqdm", "scipy.special", "sklearn.utils._typedefs",
    # 우리 모듈 (지연 임포트)
    "lyricsync.gui", "lyricsync.cli", "lyricsync.pipeline", "lyricsync.selftest",
]
hidden = sorted(set(h for h in hidden if h))

# --- ffmpeg 번들 --------------------------------------------------------
# 받는 사람 PC에 ffmpeg가 있다고 가정할 수 없다. CI가 미리 내려받아 둔다.
ffmpeg_dir = ROOT / "packaging" / "ffmpeg"
ffmpeg_files = []
if ffmpeg_dir.is_dir():
    for exe in ("ffmpeg.exe", "ffprobe.exe", "ffmpeg", "ffprobe"):
        path = ffmpeg_dir / exe
        if path.is_file():
            ffmpeg_files.append((str(path), "ffmpeg"))
if not ffmpeg_files:
    print("[spec] *** 경고: packaging/ffmpeg/ 에 실행 파일이 없습니다.")
    print("[spec] ***       빌드는 되지만 받는 사람이 음원을 열 수 없습니다.")
datas += ffmpeg_files

# --- 제외 ---------------------------------------------------------------
# 확실히 안 쓰는 것만 뺀다. setuptools, unittest, torch 하위 모듈은
# 다른 라이브러리가 런타임에 몰래 임포트하는 사례가 있어 건드리지 않는다.
excludes = [
    "matplotlib", "pandas", "IPython", "notebook", "jupyter",
    "pytest", "tensorboard", "tkinter.test", "pydoc_data",
]

print(f"[spec] hiddenimports {len(hidden)}개 · datas {len(datas)}개 "
      f"· binaries {len(binaries)}개")
if problems:
    print("[spec] *** 수집 실패 항목:")
    for line in problems:
        print(f"[spec] ***   {line}")


a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # torch DLL을 UPX로 압축하면 깨지는 사례가 많다
    console=False,      # 창만 띄운다 (콘솔 깜빡임 없음)
    icon=str(ROOT / "packaging" / "icon.ico")
         if (ROOT / "packaging" / "icon.ico").is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=NAME,
)
