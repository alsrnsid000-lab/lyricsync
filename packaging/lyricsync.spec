# PyInstaller 스펙. 윈도우에서 실행한다.
#
#   pyinstaller packaging/lyricsync.spec --noconfirm
#
# 설계 원칙 — 두 번의 실패에서 배운 것:
#
# 1. 용량보다 동작이 우선이다.
#    excludes로 setuptools를 뺐더니 pkg_resources가 jaraco를 못 찾아
#    앱이 아예 안 떴다. 아껴지는 수십 MB가 못 뜨는 앱을 정당화하지 못한다.
#
# 2. 과하게 긁어모으지 않는다.
#    collect_submodules("torch")로 1800개를 끌어왔더니 numpy가 중복 적재되어
#    "cannot load module more than once per process"로 torch가 죽었다.
#    PyInstaller는 torch/numpy/faster_whisper 전용 훅을 이미 갖고 있다.
#    hiddenimports에 최상위 이름만 적어서 훅이 돌게 하는 편이 안전하다.
#
# 그래서 여기서는 '지연 임포트라 정적 분석이 못 보는 것'만 이름으로 알려주고,
# 실제 수집은 PyInstaller 훅에 맡긴다.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
NAME = "lyricsync"

datas = []
problems = []


def add_data(pkg):
    """패키지의 데이터 파일을 모은다. 실패는 삼키지 않고 기록한다.

    전에는 except: pass 였고, 그래서 수집이 실패해도 빌드가 조용히
    '성공'했다. 실패는 빌드 로그에 남겨야 다음에 알아챌 수 있다.
    """
    try:
        items = collect_data_files(pkg)
        datas.extend(items)
        print(f"[spec] data {pkg:18} {len(items)}개")
    except Exception as exc:
        problems.append(f"{pkg}: {type(exc).__name__}: {exc}")
        print(f"[spec] data {pkg:18} 실패 — {exc}")


# 코드가 아니라 '파일'이 필요한 것들.
#   demucs         remote/*.yaml — 모델 정의와 가중치 URL 목록
#   faster_whisper assets/ — 음성 구간 검출(VAD)용 onnx
for pkg in ("demucs", "faster_whisper"):
    add_data(pkg)

# --- 지연 임포트라 정적 분석이 못 보는 것들 -----------------------------
# ASR과 보컬 분리는 함수 안에서 import 한다(앱 시작을 빠르게 하려고).
# 그래서 이름을 여기 적어야 PyInstaller가 알고, 각 패키지의 공식 훅이 돈다.
hidden = [
    # 핵심 — 이게 빠지면 아무것도 안 된다
    "numpy",
    "torch",
    "demucs", "demucs.separate", "demucs.apply", "demucs.pretrained",
    "faster_whisper", "ctranslate2",
    # demucs가 설정을 읽을 때 이름으로 임포트한다
    "dora", "dora.log", "omegaconf", "julius", "einops", "openunmix",
    # 오디오 입출력
    "torchaudio", "soundfile", "soxr",
    # 음성 구간 검출
    "onnxruntime", "tokenizers",
    # pkg_resources가 런타임에 끌어오는 vendored 의존성.
    # 이게 없으면 앱이 아예 못 뜬다 (ModuleNotFoundError: jaraco).
    "pkg_resources", "setuptools",
    "jaraco", "jaraco.text", "jaraco.functools", "jaraco.context",
    "jaraco.collections",
    "more_itertools", "platformdirs", "backports", "backports.tarfile",
    # 우리 모듈 (cli가 상황에 따라 늦게 불러온다)
    "lyricsync.gui", "lyricsync.pipeline", "lyricsync.selftest",
]
hidden = sorted(set(hidden))

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
# 확실히 안 쓰는 것만. setuptools와 torch 하위 모듈은 다른 라이브러리가
# 런타임에 몰래 임포트하는 사례가 있어 건드리지 않는다.
excludes = [
    "matplotlib", "pandas", "IPython", "notebook", "jupyter",
    "pytest", "tensorboard", "tkinter.test", "pydoc_data",
]

print(f"[spec] hiddenimports {len(hidden)}개 · datas {len(datas)}개")
print("[spec] binaries는 PyInstaller 훅에 맡깁니다 (직접 모으면 numpy가 중복됩니다)")
if problems:
    print("[spec] *** 수집 실패 항목:")
    for line in problems:
        print(f"[spec] ***   {line}")


a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],          # torch/onnxruntime DLL은 각자의 공식 훅이 가져온다
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
