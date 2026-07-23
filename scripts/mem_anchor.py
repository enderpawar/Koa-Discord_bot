"""OCI Always Free A1 인스턴스의 유휴(idle) 회수를 피하기 위한 메모리 앵커.

Oracle 은 7일 기준 CPU/네트워크/메모리 사용률(95th percentile)이 모두 20% 미만이면
인스턴스를 유휴로 판정해 회수할 수 있다 (AND 조건). 이 중 메모리 사용률 하나만
20% 이상으로 유지해도 회수 대상에서 제외된다.

이 스크립트는 지정한 크기만큼 메모리를 할당하고 주기적으로 각 페이지를 건드려
OS 가 실제로 물리 메모리를 커밋한 상태로 유지한다 (건드리지 않으면 커널이 lazy
allocation 상태로 두거나 회수할 수 있어 사용률에 안 잡힐 수 있음).
"""
from __future__ import annotations

import os
import time

SIZE_BYTES = int(os.environ.get("MEM_ANCHOR_BYTES", str(2_600_000_000)))
TOUCH_INTERVAL_SEC = int(os.environ.get("MEM_ANCHOR_INTERVAL_SEC", "30"))
PAGE_SIZE = 4096


def main() -> None:
    buf = bytearray(SIZE_BYTES)
    for i in range(0, len(buf), PAGE_SIZE):
        buf[i] = 1  # 최초 커밋 — 전 구간 터치

    while True:
        time.sleep(TOUCH_INTERVAL_SEC)
        for i in range(0, len(buf), PAGE_SIZE):
            buf[i] = (buf[i] + 1) % 256  # 재터치 — 스왑/회수 방지


if __name__ == "__main__":
    main()
