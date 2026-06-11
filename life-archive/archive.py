#!/usr/bin/env python3
"""라이프 최적화 팀 — Notion DB 행 기반 아카이버.

각 에이전트(life-optimizer-manager 제외)별 기록을 Notion 데이터베이스에 행(row)으로 추가한다.
"최신이 위" 멘탈 모델을 따르므로, Notion DB 뷰에서 날짜 내림차순으로 정렬하면 자동 최신순.

구조:
    [라이브러리 루트 페이지]
      └─ 🗂️ "아카이브 DB" (데이터베이스)
           ├─ 제목(title), 날짜(date), 에이전트(agent), 역할(role), 본문
           └─ 각 기록 = DB의 새 row (최신 = 맨 위, Notion 뷰 정렬에 의존)

이전의 "페이지 블록 누적" 방식(블록 삭제/재작성)에서 DB 행 방식으로 전환.
블록 ID 안정성·API 비용·실패 위험도가 모두 개선됨.

페이지/DB 생성·기록은 Notion API(파이썬)로 직접 → LLM 크레딧 거의 0.
파이썬이 직접 올리므로 Claude Code 세션 없이(launchd/cron 등) 자동화도 가능.

설정(.env, 이 파일과 같은 폴더):
    NOTION_TOKEN            Notion 내부 통합(integration) 토큰
    NOTION_LIBRARY_PAGE_ID  '개인 페이지 라이브러리'로 쓸 상위 페이지 ID
                            (그 페이지를 통합과 '연결'해야 권한이 생김)

사용:
    python3 archive.py init                 # 데이터베이스 생성(멱등)
    python3 archive.py push --agent diet --role 식단 --title "..." --file note.md
    echo "내용" | python3 archive.py push --agent mind-coach --role ADHD --title "..."
    python3 archive.py list                 # DB & 상태 확인
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / "state.json"
ROSTER_PATH = HERE / "roster.json"


def _env():
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")
    except Exception:
        pass
    return os.environ.get("NOTION_TOKEN"), os.environ.get("NOTION_LIBRARY_PAGE_ID")


def _client(token):
    from notion_client import Client
    return Client(auth=token)


def _load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _rt(text):
    """Rich text 블록 생성."""
    return [{"type": "text", "text": {"content": (text or "")[:2000]}}]


def _title_page(client, parent_id, title, icon=None):
    """상위 페이지 생성 (이제는 DB 부모로만 사용)."""
    kw = dict(
        parent={"type": "page_id", "page_id": parent_id},
        properties={"title": {"title": _rt(title)}},
    )
    if icon:
        kw["icon"] = {"type": "emoji", "emoji": icon}
    return client.pages.create(**kw)


def _content_blocks(title, content):
    """텍스트를 Notion 블록 배열로 변환 (DB 본문용)."""
    blocks = []
    # 메타 줄: 제목 + 날짜
    today = datetime.date.today().isoformat()
    blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rt(f"{title}  ·  {today}")},
    })
    # 본문 파싱
    for line in (content or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("# "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": _rt(s[2:])}
            })
        elif s.startswith(("- ", "* ")):
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": _rt(s[2:])}
            })
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rt(s)}
            })
    return blocks


def cmd_init(args):
    """데이터베이스 준비 및 안내 (멱등)."""
    token, root = _env()
    if not (token and root):
        sys.exit("NOTION_TOKEN / NOTION_LIBRARY_PAGE_ID 가 .env에 필요합니다.")
    roster = _load_json(ROSTER_PATH, {})
    if not roster:
        sys.exit("roster.json 이 비어 있습니다.")

    state = _load_json(STATE_PATH, {"database": None})

    # DB가 이미 있으면 그걸 쓴다
    if state.get("database"):
        db_id = state["database"]
        print(f"✅ 기존 데이터베이스 설정됨: {db_id}")
        print(f"   'python3 archive.py push'로 기록을 추가할 수 있습니다.")
        return

    print("⚠️  데이터베이스 준비 안내:")
    print(f"\n1. Notion에서 '{root}' 페이지를 엽니다.")
    print("2. 새 데이터베이스를 생성합니다:")
    print("   • '+' 버튼 → 데이터베이스 → 비어있는 DB")
    print("   • 제목: '아카이브 DB' (또는 원하는 이름)")
    print("   • 아이콘: 🗂️ 또는 📋")
    print("3. DB 스키마(속성)를 설정합니다:")
    print("   • 제목 (기본값, 필수)")
    print("   • 날짜 (Date 타입)")
    print("   • 에이전트 (Select 타입, 옵션: " + ", ".join(list(roster.keys())[:3]) + ", ...)")
    print("   • 역할 (Select 타입, 옵션은 기록 추가 시 자동 생성)")
    print("4. DB URL에서 ID를 복사합니다 (32자, ?v=로 앞의 부분):")
    print("   예: https://notion.so/3765aa904858801c8144d3ecff7c3184?v=...")
    print("5. 다음 명령을 실행합니다:")
    print(f"   NOTION_DATABASE_ID='3765aa904858801c8144d3ecff7c3184' python3 archive.py set-db")
    print("\n또는 state.json에 직접 추가:")
    print('   "database": "3765aa904858801c8144d3ecff7c3184"')
    print("\n이후 'python3 archive.py list'로 상태를 확인하세요.")


def cmd_set_db(args):
    """DB ID를 state.json에 저장."""
    db_id = args.db_id or os.environ.get("NOTION_DATABASE_ID")
    if not db_id:
        sys.exit("DB ID가 필요합니다: --db-id 또는 NOTION_DATABASE_ID 환경변수")

    state = _load_json(STATE_PATH, {})
    state["database"] = db_id
    _save_state(state)
    print(f"✅ 데이터베이스 ID 저장: {db_id}")
    print("   이제 'python3 archive.py push'로 기록을 추가할 수 있습니다.")


def cmd_push(args):
    """DB에 새 기록을 row로 추가 (블록 재작성 없음, 안전하고 빠름)."""
    token, _ = _env()
    if not token:
        sys.exit("NOTION_TOKEN 이 필요합니다.")

    state = _load_json(STATE_PATH, {"database": None})
    db_id = state.get("database")
    if not db_id:
        sys.exit("데이터베이스가 없습니다. 먼저 'python3 archive.py init' 하세요.")

    # 내용 읽기
    content = args.content
    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    if content is None and not sys.stdin.isatty():
        content = sys.stdin.read()
    if not content:
        sys.exit("기록할 내용이 없습니다 (--content / --file / stdin).")

    # DB row용 블록들 생성 (본문)
    blocks = _content_blocks(args.title, content)

    # 청크 단위로 page 생성 (최초 90개, 이후 append)
    client = _client(token)
    today = datetime.date.today().isoformat()

    # DB 속성값
    properties = {
        "제목": {"title": _rt(args.title)},
        "날짜": {"date": {"start": today}},
        "에이전트": {"select": {"name": args.agent}},
        "역할": {"select": {"name": args.role}},
    }

    # DB에 새 row(page) 생성
    try:
        page = client.pages.create(
            parent={"database_id": db_id},
            properties=properties,
            icon={"type": "emoji", "emoji": "📝"},
            children=blocks[:90],  # 처음 90개 블록
        )
        # 90개 초과 시 append
        if len(blocks) > 90:
            for i in range(90, len(blocks), 90):
                client.blocks.children.append(
                    block_id=page["id"],
                    children=blocks[i:i + 90]
                )
        print(f"✅ 기록 추가: {args.agent} > {args.role} — '{args.title}'")
        print(f"   URL: {page.get('url', page['id'])}")
    except Exception as e:
        sys.exit(f"❌ DB에 기록 추가 실패: {e}")


def cmd_list(args):
    """DB 상태 확인."""
    state = _load_json(STATE_PATH, {"database": None})
    db_id = state.get("database")
    if not db_id:
        print("❌ 데이터베이스가 아직 없습니다.")
        print("   'python3 archive.py init' 을 먼저 실행하세요.")
        return
    print(f"✅ 데이터베이스 설정됨: {db_id}")
    print("   Notion에서 '아카이브 DB'를 열어 기록을 확인하세요.")
    print("   (DB 뷰는 '날짜' 내림차순으로 정렬하면 최신이 맨 위에 표시됩니다)")


def cmd_stats(args):
    """(참고) 통계 — roster.json 기반 에이전트 목록."""
    roster = _load_json(ROSTER_PATH, {})
    if not roster:
        print("roster.json 이 비어 있습니다.")
        return
    total_roles = sum(len(roles) for roles in roster.values())
    print(f"📊 에이전트 {len(roster)}명, 역할 {total_roles}개:")
    for agent, roles in roster.items():
        print(f"  • {agent}: {', '.join(roles)}")


def main():
    p = argparse.ArgumentParser(description="라이프 아카이브 — Notion DB 기반 기록 관리자")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    sp_set_db = sub.add_parser("set-db")
    sp_set_db.add_argument("--db-id", help="데이터베이스 ID (또는 NOTION_DATABASE_ID 환경변수)")
    sp_set_db.set_defaults(func=cmd_set_db)

    sp = sub.add_parser("push")
    sp.add_argument("--agent", required=True, help="에이전트 이름 (예: mind-coach)")
    sp.add_argument("--role", required=True, help="역할 (예: ADHD)")
    sp.add_argument("--title", required=True, help="기록 제목")
    sp.add_argument("--content", help="기록 내용 (생략하면 --file 또는 stdin)")
    sp.add_argument("--file", help="내용을 읽을 파일 경로")
    sp.set_defaults(func=cmd_push)

    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
