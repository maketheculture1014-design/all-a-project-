"""한 파일을 끝까지 처리: 오디오 추출 -> STT -> 분석 -> 아카이브.

단일 파일 실행:
    python -m archivist.pipeline <파일경로> --source lesson
"""
import argparse
import datetime as dt
from pathlib import Path

from archivist import config
from archivist.analyze import analyze
from archivist.archive import write_archive
from archivist.audio import prepare_for_stt
from archivist.state import is_processed, mark
from archivist.transcribe import transcribe


def _file_date(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def process_file(path: Path, source_label: str) -> Path:
    print(f"▶ 처리 시작: {path.name}  (출처: {source_label})")

    audio_path, is_temp = prepare_for_stt(path)
    try:
        print("  · STT(받아쓰기) 중…")
        tr = transcribe(audio_path)
    finally:
        if is_temp:
            audio_path.unlink(missing_ok=True)
    print(f"  · 전사 완료: {len(tr['segments'])}개 구간, {tr['duration']}초")

    meta = {
        "filename": path.name,
        "date": _file_date(path),
        "source_label": source_label,
    }

    print("  · Claude 분석/구조화 중…")
    notes = analyze(tr["segments"], meta)

    md = write_archive(notes, tr, meta)
    mark(path, {"archive": str(md), "processed_at": dt.datetime.now().isoformat()})
    print(f"✓ 저장 완료: {md}")
    return md


def main() -> None:
    ap = argparse.ArgumentParser(description="단일 파일 아카이빙")
    ap.add_argument("file", help="처리할 영상/오디오 파일 경로")
    ap.add_argument("--source", default="lesson", choices=list(config.SOURCES),
                    help="출처(inbox 하위 폴더명)")
    ap.add_argument("--force", action="store_true", help="이미 처리한 파일도 다시 처리")
    args = ap.parse_args()

    config.ensure_dirs()
    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"파일이 없습니다: {path}")
    if is_processed(path) and not args.force:
        print(f"이미 처리됨(건너뜀): {path.name}  (--force로 재처리)")
        return
    process_file(path, config.SOURCES[args.source])


if __name__ == "__main__":
    main()
