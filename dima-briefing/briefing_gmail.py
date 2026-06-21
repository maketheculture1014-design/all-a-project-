#!/usr/bin/env python3
"""
Gmail IMAP — 오늘 수신된 미읽음 메일 중 답장·확인이 필요한 것 반환.

환경변수:
  GMAIL_USER    — Gmail 주소 (예: xxx@gmail.com)
  GMAIL_APPPASS — Gmail 앱 비밀번호 (16자리, 공백 없이)

반환: list[dict]
  [{"from": "...", "subject": "...", "snippet": "..."}]
"""

import imaplib
import email
import os
import re
from datetime import datetime, timezone, timedelta
from email.header import decode_header

KST = timezone(timedelta(hours=9))


def _decode(value) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for b, enc in parts:
        if isinstance(b, bytes):
            out.append(b.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(b)
    return "".join(out)


def fetch_unread(max_results: int = 20) -> list[dict]:
    user = os.environ.get("GMAIL_USER", "")
    password = os.environ.get("GMAIL_APPPASS", "")
    if not user or not password:
        return [{"error": "GMAIL_USER / GMAIL_APPPASS 환경변수 없음"}]

    today = datetime.now(KST).strftime("%d-%b-%Y")  # e.g. 20-Jun-2026 (KST 기준)

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(user, password)
        mail.select("INBOX")

        # 오늘 수신된 미읽음
        _, data = mail.search(None, f'(UNSEEN SINCE "{today}")')
        uids = data[0].split() if data[0] else []
        uids = uids[-max_results:]  # 최신순 cap

        results = []
        for uid in reversed(uids):  # 최신 먼저
            _, msg_data = mail.fetch(uid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = _decode(msg.get("Subject", ""))
            sender = _decode(msg.get("From", ""))

            # 본문 스니펫 (text/plain 첫 200자)
            snippet = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            snippet = payload.decode(
                                part.get_content_charset() or "utf-8", errors="replace"
                            )[:200].strip()
                            break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    snippet = payload.decode(
                        msg.get_content_charset() or "utf-8", errors="replace"
                    )[:200].strip()

            results.append({"from": sender, "subject": subject, "snippet": snippet})

        mail.logout()
        return results
    except Exception as e:
        return [{"error": str(e)}]


if __name__ == "__main__":
    import json
    print(json.dumps(fetch_unread(), ensure_ascii=False, indent=2))
