# PyInstaller 스펙. 윈도우에서 실행한다.
#
#   pyinstaller packaging/lyricsync.spec --noconfirm
#
# onedir로 묶는다. onefile은 실행할 때마다 2GB를 임시 폴더에 전부 풀기 때문에
# 시작에 몇 분이 걸린다 — 이 크기에서는 쓸 수 없다.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

ROOT = Path(SPECPATH).parent
NAME = "lyricsync"

# --- torch / demucs / whisper 가 숨겨 들고 오는 것들 --------------------
# 이 라이브러리들은 런타임에 문자열로 모듈을 임포트하거나 데이터 파일을
# 읽기 때문에, PyInstaller의 정적 분석만으로는 전부 못 잡는다.

hidden = []
datas = []
binaries = []

for pkg in ("demucs", "torch", "torchaudio", "faster_whisper", "whisper", "whisperx"):
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        pass  # 설치되지 않은 백엔드는 조용히 건너뛴다

for pkg in (
    "demucs",          # 모델 정의(remote/*.yaml)와 가중치 URL 목록
    "torchaudio",
    "faster_whisper",  # VAD용 onnx 모델(silero)
    "whisper",         # mel_filters.npz, 토크나이저
    "whisperx",
    "lightning_fabric",
    "transformers",
):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

for pkg in ("torch", "torchaudio", "ctranslate2", "onnxruntime"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

# dora/omegaconf는 demucs가 설정을 읽을 때 쓰는데 이름으로만 임포트한다
hidden += [
    "dora", "dora.log", "omegaconf", "julius", "einops", "openunmix",
    "soundfile", "soxr", "av", "ctranslate2", "onnxruntime",
    "tokenizers", "sentencepiece", "regex", "tqdm",
    "scipy.special", "sklearn.utils._typedefs",
    "lyricsync.gui", "lyricsync.cli", "lyricsync.pipeline",
]
hidden = sorted(set(hidden))

# --- ffmpeg 번들 --------------------------------------------------------
# 받는 사람 PC에 ffmpeg가 있다고 가정할 수 없으므로 같이 넣는다.
# CI가 packaging/ffmpeg/ 에 미리 내려받아 둔다.
ffmpeg_dir = ROOT / "packaging" / "ffmpeg"
ffmpeg_files = []
if ffmpeg_dir.is_dir():
    for exe in ("ffmpeg.exe", "ffprobe.exe", "ffmpeg", "ffprobe"):
        path = ffmpeg_dir / exe
        if path.is_file():
            ffmpeg_files.append((str(path), "ffmpeg"))
if not ffmpeg_files:
    print("*** 경고: packaging/ffmpeg/ 에 ffmpeg 실행 파일이 없습니다.")
    print("***       빌드는 되지만 받는 사람이 음원을 열 수 없습니다.")
datas += ffmpeg_files

# --- 빼도 되는 무거운 것들 ---------------------------------------------
# 묶지 않아도 동작하는데 용량만 잡아먹는 것들. 300~500MB 정도 줄어든다.
excludes = [
    "tkinter.test", "test", "unittest", "pydoc_data",
    "matplotlib", "pandas", "IPython", "notebook", "jupyter",
    "pytest", "setuptools", "pip", "wheel",
    "torch.distributions", "torch.testing", "torch.utils.tensorboard",
    "torch.onnx", "tensorboard", "sympy.plotting",
]


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
