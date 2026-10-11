"""테스트 실행 공통부.

각 테스트 파일이 따로 돌던 루프를 모았다. 원래는 AssertionError만 잡아서,
예상 못 한 예외(윈도우에서 ffmpeg가 없어 터진 FileNotFoundError 같은)가
나면 그 파일의 나머지 테스트가 통째로 건너뛰어졌다. 전부 잡아서 끝까지
돌리고, 마지막에 요약한다.
"""

from __future__ import annotations

import shutil
import sys
import traceback


class SkipTest(Exception):
    """이 환경에서는 의미가 없는 테스트. 실패로 치지 않는다."""


def require_tool(name: str) -> str:
    """PATH에 그 실행 파일이 없으면 건너뛴다. 원인을 분명히 알려준다."""
    path = shutil.which(name)
    if not path:
        raise SkipTest(
            f"'{name}'이 PATH에 없습니다. "
            f"이 테스트는 {name}으로 합성 음원을 만듭니다."
        )
    return path


def run(namespace: dict, title: str) -> int:
    """namespace에서 test_* 를 찾아 전부 돌린다. 종료 코드를 돌려준다."""
    tests = [v for k, v in sorted(namespace.items()) if k.startswith("test_")]
    print(f"{title} {len(tests)}건\n")

    failed: list[str] = []
    skipped: list[str] = []

    for fn in tests:
        name = fn.__name__
        try:
            fn()
        except SkipTest as exc:
            skipped.append(name)
            print(f"  [건너뜀] {name}: {exc}")
        except AssertionError as exc:
            failed.append(name)
            detail = str(exc) or "(조건 불만족)"
            print(f"  [실패] {name}: {detail}")
        except Exception as exc:
            # 예상 못 한 예외. 여기서 멈추지 않고 원인만 남기고 계속 간다.
            failed.append(name)
            print(f"  [오류] {name}: {type(exc).__name__}: {exc}")
            tb = traceback.format_exc(limit=3).rstrip().splitlines()
            for line in tb[-4:]:
                print(f"         {line.strip()}")

    print()
    parts = [f"{len(tests) - len(failed) - len(skipped)}건 통과"]
    if skipped:
        parts.append(f"{len(skipped)}건 건너뜀")
    if failed:
        parts.append(f"{len(failed)}건 실패 ({', '.join(failed)})")
    print(" · ".join(parts))
    return 1 if failed else 0


def main(namespace: dict, title: str) -> None:
    sys.exit(run(namespace, title))
