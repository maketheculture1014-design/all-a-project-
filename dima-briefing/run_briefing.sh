#!/bin/bash
# 노트북을 깨우면(on-wake) '그날 최초 1회만' 아침 브리핑을 생성한다.
# - 하루 단위(달력 날짜) 기준: 같은 날 두 번째 깨움부터는 건너뜀.
# - 며칠간 못 깨웠으면: 지난 브리핑 이후 ~ 오늘까지의 정보를 모두 종합.
# Claude 비전으로 DIMA 포스터까지 읽어 Gmail 초안으로 저장. (tesseract 미사용)
set -u
export PATH="/Users/maketheculture/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

DIR="/Users/maketheculture/my-workspace/dima-briefing"
OUT="$DIR/output"
mkdir -p "$OUT"
touch "$OUT/briefed_log.md"   # 중복방지 로그(없으면 생성)
TODAY="$(date +%Y%m%d)"
LASTFILE="$OUT/.last_briefed"     # 마지막 성공한 브리핑 날짜(YYYYMMDD) 1개만 보관
LOG="$OUT/wake.log"

# ── 동시/중복 실행 방지 락 (mkdir은 원자적 연산) ─────────────────────────
# 깨움+부팅+다중 깨움 트리거가 7시에 겹쳐도, 락을 못 잡은 나머지는 즉시 종료.
# → 하루에 단 하나의 브리핑만 생성된다. (기존 .last_briefed는 '그날 완료' 가드로 유지)
LOCKDIR="$OUT/.briefing.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  if [ -n "$(find "$LOCKDIR" -prune -mmin +15 2>/dev/null)" ]; then
    # 15분 이상 묵은 락 = 비정상 종료로 간주하고 회수
    rmdir "$LOCKDIR" 2>/dev/null
    mkdir "$LOCKDIR" 2>/dev/null || { echo "$(date '+%F %T') 락 회수 실패 → 건너뜀" >> "$LOG"; exit 0; }
  else
    echo "$(date '+%F %T') 다른 브리핑 실행 중(락 점유) → 건너뜀" >> "$LOG"
    exit 0
  fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT
# ────────────────────────────────────────────────────────────────────────

LAST=""
[ -f "$LASTFILE" ] && LAST="$(cat "$LASTFILE" 2>/dev/null | tr -dc 0-9)"

# 그날 최초 '성공'만: 오늘 이미 브리핑에 성공했으면 건너뜀.
#   성공해야 .last_briefed가 기록되므로, 실패한 날은 LAST가 어제로 남아
#   다음 트리거(boot/wake/7시)에 자동으로 재시도된다.
if [ "$LAST" = "$TODAY" ]; then
  echo "$(date '+%F %T') 오늘 이미 브리핑 성공함 → 건너뜀" >> "$LOG"
  exit 0
fi

# 지난 브리핑 이후 경과 일수(N) 계산 → 그 기간 전체를 종합
if [ -n "$LAST" ]; then
  LAST_EPOCH="$(date -j -f "%Y%m%d" "$LAST" +%s 2>/dev/null || true)"
  TODAY_EPOCH="$(date -j -f "%Y%m%d" "$TODAY" +%s 2>/dev/null || true)"
  if [ -n "${LAST_EPOCH:-}" ] && [ -n "${TODAY_EPOCH:-}" ]; then
    DAYS=$(( (TODAY_EPOCH - LAST_EPOCH) / 86400 ))
  else
    DAYS=1
  fi
  LAST_FMT="$(date -j -f "%Y%m%d" "$LAST" "+%Y-%m-%d" 2>/dev/null || echo "$LAST")"
else
  DAYS=1
  LAST_FMT="(첫 실행)"
fi
[ "$DAYS" -lt 1 ] && DAYS=1
TODAY_FMT="$(date "+%Y-%m-%d")"
# 요일은 모델이 추측하면 틀리므로(LLM 요일 산술 불안정) 시스템에서 계산해 주입한다.
# 로케일(ko_KR) 의존 없이 %u(월=1..일=7)를 한국어로 매핑.
WD_ARR=( "" "월" "화" "수" "목" "금" "토" "일" )
WD_KO="${WD_ARR[$(date "+%u")]}"
TITLE_FIXED="${TODAY_FMT} (${WD_KO}) 브리핑☀️"

echo "$(date '+%F %T') 브리핑 시작 (대상기간 ${DAYS}일: ${LAST_FMT} ~ ${TODAY_FMT}, 제목=${TITLE_FIXED})" >> "$LOG"

WINDOW="【대상 기간】 지난 브리핑은 ${LAST_FMT}, 오늘은 ${TODAY_FMT}이며 약 ${DAYS}일이 지났습니다(며칠간 노트북을 못 켰을 수 있음). 이번 브리핑은 '오늘 하루'가 아니라 '지난 브리핑 이후 ~ 오늘'의 ${DAYS}일간 정보를 빠짐없이 종합하세요. 구체적으로: (a) Gmail은 newer_than:${DAYS}d 로 조회, (b) DIMA 공지는 그 기간에 새로 올라온 공지를 우선, (c) 뉴스는 그 기간 누적이 아니라 '오늘 시점의 최신' 기준. 단 '[1] 오늘 일정'과 '[5] 오늘의 핵심 3가지'는 오늘(${TODAY_FMT}) 기준으로 정리하되, 대상 기간이 2일 이상이면 그 사이 지나간 일정은 '지난 일정' 한 줄 요약으로 덧붙이세요."

PROMPT="${WINDOW}

【제목 고정】 Notion 페이지 제목은 요일을 추측하지 말고 반드시 정확히 '${TITLE_FIXED}' 로 하세요(요일은 시스템에서 계산된 값입니다). 본문 머리글의 날짜도 ${TODAY_FMT} 기준입니다.

$(cat "$DIR/briefing_prompt.txt")

$(cat "$DIR/user_profile.txt" 2>/dev/null)"

cd "$DIR" || exit 1
# 이번 실행 출력은 임시 파일에도 받아 둔다 → 성공 시 Notion 링크 추출용.
RUNLOG="$OUT/.lastrun.out"
claude -p "$PROMPT" \
  --permission-mode bypassPermissions \
  --model sonnet \
  > "$RUNLOG" 2>&1
RC=$?
cat "$RUNLOG" >> "$LOG"

# 성공한 날짜만 기록 → 실패하면 LAST가 그대로라 다음 트리거에 재시도된다.
if [ $RC -eq 0 ]; then
  echo "$TODAY" > "$LASTFILE"
  echo "$(date '+%F %T') 브리핑 완료 (last_briefed=$TODAY)" >> "$LOG"

  # ── 완료 알림: macOS 배너 + Notion 링크 ──────────────────────────────
  # 본문에서 Notion 페이지 URL을 추출(없으면 빈 값). 첫 번째 것만 사용.
  NOTION_URL="$(grep -oE 'https://(app\.notion\.com|www\.notion\.so)/[A-Za-z0-9/_?=&%.-]+' "$RUNLOG" | head -n1)"
  NOTIF_MSG="${TITLE_FIXED}"
  [ -n "$NOTION_URL" ] && NOTIF_MSG="${NOTIF_MSG} · 탭하여 열기"
  # AppleScript 문자열 안전화(따옴표·역슬래시 이스케이프)
  esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
  osascript -e "display notification \"$(esc "$NOTIF_MSG")\" with title \"오늘 브리핑 준비됨 ☀️\" sound name \"Glass\"" 2>>"$LOG" || true
  # 클릭하면 Notion 페이지가 열리도록, 링크가 있으면 클립보드에도 복사해 둔다.
  if [ -n "$NOTION_URL" ]; then
    printf '%s' "$NOTION_URL" | pbcopy 2>/dev/null || true
    echo "$(date '+%F %T') 알림 전송 + Notion 링크 클립보드 복사: $NOTION_URL" >> "$LOG"
  else
    echo "$(date '+%F %T') 알림 전송(본문에서 Notion 링크 못 찾음)" >> "$LOG"
  fi
  # ─────────────────────────────────────────────────────────────────────
else
  echo "$(date '+%F %T') 브리핑 실패 (기록 안 함 → 다음 트리거에 재시도)" >> "$LOG"
  osascript -e 'display notification "다음 깨움 때 자동 재시도됩니다" with title "브리핑 실패 ⚠️" sound name "Basso"' 2>>"$LOG" || true
fi
