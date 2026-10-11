# PyInstaller 스펙 (경량 빌드). 윈도우에서 실행한다.
#
#   pyinstaller packaging/lyricsync.spec --noconfirm
#
# === 이 빌드에 PyTorch와 Demucs가 없는 이유 ===
#
# 처음에는 보컬 분리(Demucs)를 포함한 빌드를 만들려 했고, 네 번 실패했다.
# 기록을 남겨둔다. 다시 시도할 사람이 같은 길을 걷지 않도록.
#
#   1차  excludes로 setuptools를 뺐다 → pkg_resources가 jaraco를 못 찾아
#        앱이 아예 뜨지 않음.
#   2차  반대로 pkg_resources/setuptools를 hiddenimports에 강제로 넣었다
#        → PyInstaller가 pkg_resources 런타임 훅을 같이 붙였고, 그 훅이
#        우리 코드보다 먼저 돌면서 numpy의 C 확장을 건드려
#        "CPU dispatcher tracer already initlized"로 죽음.
#   3~4차 numpy가 번들에 중복으로 들어간 줄 알고 수집 방식을 두 번 바꿨다.
#        자가진단 결과 numpy는 한 벌뿐이었다. 가정이 틀렸다.
#        실제로 중복이던 것은 OpenMP DLL(ctranslate2와 torch가 각각 들고 옴).
#
# torch와 ctranslate2를 한 프로세스에 묶는 것 자체가 불안정했다.
# 보컬 분리는 품질 향상 기능이지 필수가 아니고, 가사 파일을 함께 쓰는
# 주 사용 경로에서는 ASR에서 시간만 빌려오므로 손실이 작다.
# 그래서 배포 exe에서는 빼고, 소스로 돌릴 때만 쓰도록 선택 기능으로 남겼다
# (lyricsync/audio.py의 separation_available 참고).
#
# 설계 원칙:
#   - 용량보다 동작이 우선. 아껴지는 수십 MB가 못 뜨는 앱을 정당화하지 못한다.
#   - 과하게 긁어모으지 않는다. PyInstaller의 공식 훅이 대개 더 정확하다.
#     여기서는 '지연 임포트라 정적 분석이 못 보는 것'만 이름으로 알려준다.

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


# 코드가 아니라 '파일'이 필요한 것: faster_whisper의 VAD용 onnx 자산
add_data("faster_whisper")

# --- 지연 임포트라 정적 분석이 못 보는 것들 -----------------------------
# ASR은 함수 안에서 import 한다(앱 시작을 빠르게 하려고).
# 그래서 이름을 여기 적어야 PyInstaller가 알고, 각 패키지의 공식 훅이 돈다.
hidden = [
    "numpy",
    "faster_whisper", "ctranslate2", "tokenizers",
    "onnxruntime",          # 음성 구간 검출(VAD)
    "av",                   # 음원 디코딩
    "soundfile", "soxr",
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
excludes = [
    # 이 둘이 들어오면 PyInstaller가 pkg_resources 런타임 훅을 붙이고,
    # 그 훅이 numpy보다 먼저 돌아 C 확장 초기화를 깨뜨린다. (2차 실패)
    "pkg_resources", "setuptools",
    # 보컬 분리 계열. 위의 설명 참고 — 의도적으로 뺀다.
    # 빌드 환경에 설치돼 있어도 번들에는 들어가지 않는다.
    "torch", "torchaudio", "demucs", "dora", "openunmix", "julius",
    "lameenc", "treetable",
    # 확실히 안 쓰는 것
    "matplotlib", "pandas", "IPython", "notebook", "jupyter",
    "pytest", "tensorboard", "tkinter.test", "pydoc_data",
]

print(f"[spec] hiddenimports {len(hidden)}개 · datas {len(datas)}개")
print("[spec] torch/demucs는 의도적으로 제외 — 파일 상단 주석 참고")
if problems:
    print("[spec] *** 수집 실패 항목:")
    for line in problems:
        print(f"[spec] ***   {line}")


a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],          # DLL은 각 패키지의 공식 훅이 가져온다
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    # 다른 어떤 훅보다 먼저 numpy를 적재한다. 자세한 이유는 그 파일 안에.
    runtime_hooks=[str(ROOT / "packaging" / "rthook_numpy.py")],
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
    upx=False,          # 네이티브 DLL을 UPX로 압축하면 깨지는 사례가 많다
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
