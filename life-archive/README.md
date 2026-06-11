# 🗂️ life-archive — Notion DB 기반 아카이버 (개선 v2)

라이프 최적화 팀의 각 에이전트(`life-optimizer-manager` 제외)별 기록을 Notion **데이터베이스**에
행(row)으로 추가합니다. "최신이 위" 멘탈 모델을 따르며, Notion DB 뷰에서 날짜 내림차순 정렬로
자동 최신순 표시.

```
[라이브러리 루트]
 └─ 🗂️ 아카이브 DB (데이터베이스)
      ├─ 제목 | 날짜 | 에이전트 | 역할 | 본문
      └─ Row 1, Row 2, …  (기록을 행으로 저장, 최신이 맨 위)
```

**이전 방식과의 차이**:
- ❌ 이전: 페이지 블록 누적 → 블록 삭제/재작성 (API 비용 높음, 블록 ID 손상 위험)
- ✅ 새 방식: DB 행 추가만 → 안전, 빠름, API 호출 최소 (1~2회)

## 왜 이 방식인가 (크레딧 효율)
페이지 생성·기록을 **Notion API(파이썬)** 로 직접 한다 → LLM 토큰/크레딧이 거의 안 든다.
- ❌ 모든 에이전트에 Notion MCP 장착: 매 호출마다 도구 스키마 로드 → 가장 비쌈(기준 1.0x)
- ❌ 중앙 LLM 아카이버(MCP): ~0.5x
- ✅ **이 방식(파이썬 API)**: ~0.04x (약 25배 저렴)

learning-archivist가 이 도구의 "주인"이다. 서브에이전트는 서로 직접 호출할 수 없으므로,
본부장(`life-optimizer-manager`)이 다른 에이전트의 산출물을 받아 learning-archivist에게
`[잡무모드]`로 넘기고, learning-archivist가 이 스크립트로 기록한다.

## 설치 & 설정 (1회)
```bash
cd life-archive
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # NOTION_TOKEN, NOTION_LIBRARY_PAGE_ID 입력
```
1. https://www.notion.so/my-integrations 에서 내부 통합 생성 → 토큰 복사 → `NOTION_TOKEN`
2. Notion에 빈 페이지 생성(예: "AI 팀 라이브러리") → 그 페이지 ⋯ → **연결(Connections)** 에 통합 추가
3. 그 페이지 ID(URL 끝 32자)를 `NOTION_LIBRARY_PAGE_ID` 에 입력

## 사용

### 1단계: DB 생성 (Notion UI에서 수동, 1회만)
```bash
python3 archive.py init
```
이 명령이 안내 문구를 출력합니다. 다음을 따르세요:

1. Notion 라이브러리 페이지 진입
2. 새 데이터베이스 생성 (+ → DB → 비어있는)
   - 제목: "아카이브 DB"
   - 스키마(속성) 추가:
     - **제목** (기본값, 필수)
     - **날짜** (Date 타입, 내림차순 정렬 설정 — 최신이 맨 위)
     - **에이전트** (Select, 옵션: image-advisor, wellness-coach, … roster.json 참고)
     - **역할** (Select, 옵션은 나중에 수동 추가 또는 자동 생성)
3. DB URL에서 ID 복사 (32자, `https://notion.so/3765aa904858801c...?v=...` 형태)
4. 다음 중 하나로 ID 저장:

   **옵션 A: 명령어 사용**
   ```bash
   NOTION_DATABASE_ID='<DB_ID>' python3 archive.py set-db
   ```

   **옵션 B: state.json 직접 편집**
   ```json
   {
     "database": "<DB_ID>",
     "agents": { ... }
   }
   ```

5. 검증:
   ```bash
   python3 archive.py list
   ```

### 2단계: 기록 추가 (DB 설정 후)
```bash
# 파일에서 읽기
python3 archive.py push --agent mind-coach --role ADHD --title "집중 루틴 정리" --file note.md

# 직접 텍스트
python3 archive.py push --agent wellness-coach --role 식단 --title "아침 메뉴" --content "귀리·우유·베리"

# stdin (파이프)
echo "오늘 배운 것…" | python3 archive.py push --agent learning-archivist --role "강의 노트" --title "MIDI 기초"
```
- 에이전트/역할은 `roster.json` 참고
- 새 기록은 DB에 새 row로 추가 (기존 내용 영향 없음)
- `--role`에 새 값을 쓰면 Notion에서 Select 옵션 자동 생성

### 3단계: 상태 확인
```bash
python3 archive.py list    # DB 설정 및 상태 확인
python3 archive.py stats   # 에이전트/역할 목록 확인
```

## DB 스키마

각 기록(row)은 다음 속성을 갖습니다:

| 속성 | 형식 | 설명 |
|------|------|------|
| 제목 | Text (required) | 기록 제목 (DB 기본 필드) |
| 날짜 | Date | 기록 생성 날짜 (내림차순 정렬 추천) |
| 에이전트 | Select | 에이전트 이름 (roster.json 기반 옵션) |
| 역할 | Select | 역할 (사용자가 추가 가능) |
| 본문 | Blocks | 마크다운 스타일로 파싱된 내용 |

내용 형식: `--content/--file/stdin` 텍스트는 마크다운처럼 파싱
- `# 제목` → 헤딩 2
- `- 항목` 또는 `* 항목` → 불릿 리스트
- 나머지 → 문단

## 자동화 (선택)

파이썬이 직접 Notion API를 호출하므로, LLM 세션 없이 `cron`/`launchd`로 무인 실행 가능:

```bash
# 매일 7am 기록 업로드 (macOS launchd 예)
/path/to/venv/bin/python3 /path/to/archive.py push \
  --agent learning-archivist --role 강의_노트 --title "오늘의 노트" --file /tmp/note.md
```

- 토큰(`NOTION_TOKEN`)과 상태(`state.json`)는 `.gitignore`에서 제외됨 (git 미적용)
- 자동화는 파이썬 런타임만 필요 (Claude Code 세션 불필요)
