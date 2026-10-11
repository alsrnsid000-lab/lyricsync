"""numpy를 가장 먼저 적재하는 런타임 훅.

PyInstaller는 번들이 시작될 때 런타임 훅들을 우리 코드보다 먼저 실행한다.
그 훅 중 하나(pkg_resources용)가 numpy의 C 확장을 건드린 뒤 모듈 캐시에서
놓쳐버리면, 나중에 우리가 import할 때 numpy가 두 번째 초기화로 인식해
"CPU dispatcher tracer already initlized"로 죽는다.

그래서 어떤 훅보다 먼저 numpy를 한 번 제대로 적재해 sys.modules에 박아둔다.
이후의 모든 import는 캐시에 맞고, 확장 모듈은 한 번만 초기화된다.

실패해도 조용히 넘어간다. 여기서 터지면 창조차 못 뜨고, 그러면 자가진단으로
원인을 알려줄 기회도 없어진다. 진짜 진단은 lyricsync/selftest.py가 한다.
"""

import os
import sys

# OpenMP 런타임이 두 벌(ctranslate2와 torch)이라 중복을 허용해야 한다.
# numpy/torch를 건드리기 전에 설정해야 효과가 있다.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "4")

_record = {"hook_ran": True, "before": [m for m in sys.modules if m in (
    "numpy", "numpy._core", "numpy._core._multiarray_umath",
    "pkg_resources", "setuptools", "torch",
)]}

try:
    import numpy as _numpy

    _record["numpy"] = getattr(_numpy, "__version__", "?")
except BaseException as exc:
    import traceback

    _record["error"] = f"{type(exc).__name__}: {exc}"
    _record["traceback"] = traceback.format_exc()

# 자가진단이 읽어간다 (lyricsync/selftest.py의 _boot_info 참고)
sys._lyricsync_rthook = _record
