"""
Human Escalation Protocol & Coordinators Notification System.

Aanya has exactly two honest limits: a red-flag symptom, and a request for a
diagnosis, a prescription or a lab-report reading. This module turns either one
into a request a real human can act on.

The SQLite row is the source of truth. The Discord (or Slack) webhook is a
notification layered on top of it, so a webhook that fails can never lose an
escalation - the row is still in the database and on the /help-desk dashboard.

Deliberately free of any LiveKit imports so the store, the scrubber and the
payload builder can be unit-tested without a voice pipeline. HTTP goes through
stdlib `urllib`, matching health_services.py, so no new dependency is added.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import db

logger = logging.getLogger("agent.escalations")

# ---------------------------------------------------------------------------
# The two - and only two - reasons Aanya may hand a call to a human.
#
# Keeping this a closed set is the whole point: without it the LLM invents a
# third trigger and starts escalating ordinary sleep-and-diet questions.
# ---------------------------------------------------------------------------
REASON_RED_FLAG_SYMPTOM = "red_flag_symptom"
REASON_DIAGNOSIS_REQUEST = "diagnosis_request"
ALLOWED_REASONS = (REASON_RED_FLAG_SYMPTOM, REASON_DIAGNOSIS_REQUEST)

REASON_LABELS = {
    REASON_RED_FLAG_SYMPTOM: "Red-flag symptom reported",
    REASON_DIAGNOSIS_REQUEST: "Asked for a diagnosis, prescription or report reading",
}

URGENCY_EMERGENCY = "emergency"
URGENCY_SOON = "soon"
URGENCY_ROUTINE = "routine"
ALLOWED_URGENCY = (URGENCY_EMERGENCY, URGENCY_SOON, URGENCY_ROUTINE)

STATUS_OPEN = "open"
STATUS_IN_PROGRESS = "in_progress"
STATUS_RESOLVED = "resolved"

DELIVERY_PENDING = "pending"
DELIVERY_DELIVERED = "delivered"
DELIVERY_SAVED_ONLY = "saved_only"
DELIVERY_FAILED = "failed"

# Step 3 asks for a *short* summary. A hard character budget also guarantees a
# full transcript can never be shipped to a chat channel by accident.
MAX_SUMMARY_CHARS = 400

WEBHOOK_ENV_VAR = "ESCALATION_WEBHOOK_URL"

# Urgency -> Discord embed colour, so an emergency is visible at a glance.
_URGENCY_COLOURS = {
    URGENCY_EMERGENCY: 0xE5484D,
    URGENCY_SOON: 0xF5A524,
    URGENCY_ROUTINE: 0x6347EE,
}

# Anything after one of these words is a secret, not a symptom.
_SECRET_WORDS = (
    r"otp|pin|passcode|password|aadhaar|aadhar|account|a/c|debit|credit|card|cvv|upi"
)

# The chain in the middle matters: callers say "my UPI PIN is 4821", so one
# keyword is not enough - the pattern has to eat every following keyword and
# filler word before it reaches the value, or the value survives the redaction.
_SECRET_KEYWORD_RE = re.compile(
    rf"\b(?:{_SECRET_WORDS})\b"
    rf"(?:[\s:=-]*(?:{_SECRET_WORDS}|number|no\.?|code|id|is|was|my|mera|meri|ka)\b)*"
    r"[\s:=-]*[\w/@.-]*",
    re.IGNORECASE,
)

# Card, account and Aadhaar numbers all look like a long run of digits.
_LONG_DIGIT_RUN_RE = re.compile(r"\b\d[\d\s-]{5,}\d\b")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def scrub_private_details(text: str, max_chars: int = MAX_SUMMARY_CHARS) -> str:
    """Remove what Step 3 forbids from a summary, then cap its length.

    Strips anything following an OTP / PIN / password / Aadhaar / account / card
    keyword, plus any run of six or more digits. Short numbers survive on purpose
    - "108", "3 days", "102 fever" are all clinically useful.

    This is the Step 3 minimum ("do not include passwords, OTPs, PINs, account
    numbers"), not a general-purpose PII scrubber.
    """
    clean = (text or "").strip()
    if not clean:
        return ""
    clean = _SECRET_KEYWORD_RE.sub("[redacted]", clean)
    clean = _LONG_DIGIT_RUN_RE.sub("[redacted]", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > max_chars:
        clean = clean[: max_chars - 3].rstrip() + "..."
    return clean


def _row_to_escalation(row: Any) -> dict[str, Any]:
    return {
        "escalation_id": row["escalation_id"],
        "reference_id": row["reference_id"],
        "caller_name": row["caller_name"],
        "user_id": row["user_id"],
        "phone_number": row["phone_number"],
        "language": row["language"],
        "reason_code": row["reason_code"],
        "reason_label": REASON_LABELS.get(row["reason_code"], row["reason_code"]),
        "urgency": row["urgency"],
        "what_happened": row["what_happened"],
        "already_checked": row["already_checked"],
        "followup_method": row["followup_method"],
        "status": row["status"],
        "consent_given": bool(row["consent_given"]),
        "delivery_status": row["delivery_status"],
        "delivery_detail": row["delivery_detail"],
        "created_at": row["created_at"],
    }


def create_escalation(
    caller_name: str,
    reason_code: str,
    what_happened: str,
    urgency: str = URGENCY_SOON,
    already_checked: str = "",
    phone_number: str = "",
    language: str = "Hindi",
    followup_method: str = "phone call",
    user_id: str = "",
    consent_given: bool = False,
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Store one human-help request. Returns the row, or None if it was refused.

    Refuses (returns None) when the caller has not agreed to share, or when the
    reason is not one of the two allowed triggers. Step 4 says a "no" must leave
    nothing behind, so the guard lives here as well as in the agent tool - a
    future caller of this module cannot accidentally bypass consent.
    """
    if not consent_given:
        logger.info(
            f"Escalation for '{caller_name}' NOT created: caller did not consent."
        )
        return None

    reason = (reason_code or "").strip().lower()
    if reason not in ALLOWED_REASONS:
        logger.warning(f"Escalation refused: '{reason_code}' is not an allowed reason.")
        return None

    name = (caller_name or "").strip()
    summary = scrub_private_details(what_happened)
    if not name or not summary:
        logger.warning("Escalation refused: caller name and summary are both required.")
        return None

    level = (urgency or "").strip().lower()
    if level not in ALLOWED_URGENCY:
        level = URGENCY_SOON

    db.init_db(db_path)
    conn = db.get_connection(db_path)
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO escalations
                (caller_name, user_id, phone_number, language, reason_code,
                 urgency, what_happened, already_checked, followup_method,
                 status, consent_given, delivery_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                name,
                (user_id or name).strip().lower(),
                (phone_number or "").strip(),
                language or "Hindi",
                reason,
                level,
                summary,
                scrub_private_details(already_checked),
                (followup_method or "phone call").strip(),
                STATUS_OPEN,
                DELIVERY_PENDING,
                _now_iso(),
            ),
        )
        escalation_id = int(cursor.lastrowid)
        # Human-readable and short enough to read out digit by digit on a call.
        reference_id = f"HLP-{1000 + escalation_id}"
        conn.execute(
            "UPDATE escalations SET reference_id = ? WHERE escalation_id = ?",
            (reference_id, escalation_id),
        )
        row = conn.execute(
            "SELECT * FROM escalations WHERE escalation_id = ?", (escalation_id,)
        ).fetchone()
    conn.close()

    logger.info(
        f"Escalation {reference_id} created for {name}: reason={reason}, "
        f"urgency={level}, followup={followup_method}."
    )
    return _row_to_escalation(row)


def get_escalation(
    reference_or_id: Any, db_path: Optional[Path] = None
) -> Optional[dict[str, Any]]:
    """Fetch one escalation by numeric id or by reference ('HLP-1001')."""
    key = str(reference_or_id or "").strip()
    if not key:
        return None

    db.init_db(db_path)
    conn = db.get_connection(db_path)
    if key.isdigit():
        row = conn.execute(
            "SELECT * FROM escalations WHERE escalation_id = ?", (int(key),)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM escalations WHERE UPPER(reference_id) = ?", (key.upper(),)
        ).fetchone()
    conn.close()
    return _row_to_escalation(row) if row else None


def list_escalations(
    status: Optional[str] = STATUS_OPEN,
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """List escalations newest first. Pass status=None for every request."""
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    if status:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE status = ? "
            "ORDER BY escalation_id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM escalations ORDER BY escalation_id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [_row_to_escalation(row) for row in rows]


def mark_delivery(
    escalation_id: int,
    delivery_status: str,
    detail: str = "",
    db_path: Optional[Path] = None,
) -> None:
    """Record what happened when we tried to notify the humans."""
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    with conn:
        conn.execute(
            "UPDATE escalations SET delivery_status = ?, delivery_detail = ? "
            "WHERE escalation_id = ?",
            (delivery_status, detail[:300], int(escalation_id)),
        )
    conn.close()


def _summary_fields(escalation: dict[str, Any]) -> list[tuple[str, str]]:
    """The five things Step 3 asks the summary to answer, plus the reference.

    Note what is absent: no transcript, no saved health history, no free-text
    dump. Everything here has already been through scrub_private_details.
    """
    reason_label = escalation.get("reason_label") or REASON_LABELS.get(
        escalation.get("reason_code", ""), escalation.get("reason_code", "")
    )
    phone = escalation.get("phone_number") or "not shared"
    return [
        ("Who needs help", f"{escalation['caller_name']} ({phone})"),
        ("What happened", escalation["what_happened"]),
        ("What Aanya already did", escalation.get("already_checked") or "-"),
        ("How urgent", f"{escalation['urgency']} - {reason_label}"),
        (
            "Language & preferred follow-up",
            f"{escalation.get('language', 'Hindi')}, via "
            f"{escalation.get('followup_method', 'phone call')}",
        ),
        ("Reference", escalation.get("reference_id", "")),
    ]


def build_webhook_payload(
    escalation: dict[str, Any], webhook_url: str = ""
) -> dict[str, Any]:
    """Build the chat message body. Pure function - no network, easy to assert on.

    Discord embed by default; a Slack-shaped `{"text": ...}` body when the URL
    points at Slack, so switching provider is a .env change and nothing else.
    """
    fields = _summary_fields(escalation)
    reference = escalation.get("reference_id", "")
    title = f"Human help needed - {reference}" if reference else "Human help needed"

    if "slack.com" in (webhook_url or "").lower():
        lines = [f"*{title}*"] + [f"*{label}:* {value}" for label, value in fields]
        return {"text": "\n".join(lines)}

    return {
        "username": "Aanya Health Desk",
        "embeds": [
            {
                "title": title,
                "description": (
                    "Aanya stopped and asked for a human. The caller agreed to "
                    "share these details."
                ),
                "color": _URGENCY_COLOURS.get(
                    escalation.get("urgency", ""), _URGENCY_COLOURS[URGENCY_ROUTINE]
                ),
                "fields": [
                    {"name": label, "value": value or "-", "inline": False}
                    for label, value in fields
                ],
                "footer": {"text": "Aanya voice agent - Day 7 escalation"},
                "timestamp": escalation.get("created_at", _now_iso()),
            }
        ],
    }


def deliver_escalation(
    escalation: dict[str, Any],
    webhook_url: Optional[str] = None,
    timeout: float = 4.0,
    db_path: Optional[Path] = None,
) -> tuple[str, str]:
    """POST the escalation to the chat channel. Returns (delivery_status, detail).

    'saved_only' is a success, not a failure: with no webhook configured the row
    and the /help-desk dashboard are still a real destination, and Step 5 accepts
    "a local database with a page that shows open requests". A caller must never
    be told help failed because a webhook was missing.

    Pass webhook_url="" to force the saved-only path; pass None to read
    ESCALATION_WEBHOOK_URL from the environment.
    """
    url = os.getenv(WEBHOOK_ENV_VAR, "") if webhook_url is None else webhook_url
    url = url.strip()
    escalation_id = int(escalation.get("escalation_id") or 0)

    if not url:
        detail = f"No {WEBHOOK_ENV_VAR} configured; saved to the database only."
        logger.info(f"Escalation {escalation.get('reference_id')}: {detail}")
        if escalation_id:
            mark_delivery(escalation_id, DELIVERY_SAVED_ONLY, detail, db_path=db_path)
        return DELIVERY_SAVED_ONLY, detail

    # Never log the URL itself - it carries the webhook token.
    host = urllib.parse.urlsplit(url).netloc or "webhook"
    payload = build_webhook_payload(escalation, url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # The User-Agent is not decoration: Discord sits behind Cloudflare, which
    # answers urllib's default agent with HTTP 403 "error code: 1010". Send an
    # explicit agent string or every escalation silently degrades to 'failed'.
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "VoiceForBharatHealthAgent/1.0 (Day 7 escalation)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code = getattr(response, "status", 0) or 0
        status = DELIVERY_DELIVERED if 200 <= code < 300 else DELIVERY_FAILED
        detail = f"{host} returned HTTP {code}"
    except urllib.error.HTTPError as exc:
        status, detail = DELIVERY_FAILED, f"{host} returned HTTP {exc.code}"
    except Exception as exc:
        status, detail = DELIVERY_FAILED, f"{host} unreachable: {str(exc)[:150]}"

    if status == DELIVERY_DELIVERED:
        logger.info(f"Escalation {escalation.get('reference_id')} delivered: {detail}")
    else:
        # The row survives, so the dashboard still shows the request.
        logger.error(
            f"Escalation {escalation.get('reference_id')} webhook failed: {detail}. "
            "The request is still saved and visible on the help-desk page."
        )

    if escalation_id:
        mark_delivery(escalation_id, status, detail, db_path=db_path)
    return status, detail
