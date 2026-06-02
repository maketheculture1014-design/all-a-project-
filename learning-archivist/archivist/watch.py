"""inbox 폴더를 감시해 새 파일을 자동 처리.

한 번만 스캔:
    python -m archivist.watch --once
상시 감시(POLL_INTERVAL초마다):
    python -m archivist.watch
"""
import sys
import time

from archivist import config
from archivist.pipeline import process_file
from archivist.state import is_processed


def scan_once() -> int:
    processed = 0
    for sub, label in config.SOURCES.items():
        folder = config.INBOX / sub
        if not folder.exists():
            continue
        for f in sorted(folder.iterdir()):
            if not f.is_file() or f.suffix.lower() not in config.MEDIA_EXTS:
                continue
            if is_processed(f):
                continue
            try:
                process_file(f, label)
                processed += 1
            except Exception as e:  # 한 파일 실패가 전체를 멈추지 않게
                print(f"✗ 오류({f.name}): {e}")
    return processed


def main() -> None:
    config.ensure_dirs()
    once = "--once" in sys.argv
    print(f"👀 감시 시작: {config.INBOX}  (주기 {config.POLL_INTERVAL}s, once={once})")
    while True:
        n = scan_once()
        if n:
            print(f"  → {n}개 파일 처리 완료")
        if once:
            break
        time.sleep(config.POLL_INTERVAL)


if __name__ == "__main__":
    main()
