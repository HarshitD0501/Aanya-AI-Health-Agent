"""
Medication Reminder Store & Retry Policy.

Holds the outbound-call domain logic: which reminders are due, what happened on
the last attempt, and whether we are allowed to dial again. Deliberately free of
any LiveKit or SIP imports so the retry rules can be unit-tested without a
telephony provider.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import db

logger = logging.getLogger("agent.reminders")

# ---------------------------------------------------------------------------
# Call outcomes
#
# LiveKit reports dial-time failures through rtc.DisconnectReason and Twirp
# errors; these are our normalized names for them. `answered` means a human
# picked up and spoke. `possible_voicemail` means the line answered at the SIP
# layer but nobody ever replied - voicemail cannot be detected any other way
# without answering-machine detection.
# ---------------------------------------------------------------------------
OUTCOME_ANSWERED = "answered"
OUTCOME_NO_ANSWER = "no_answer"
OUTCOME_REJECTED = "rejected"
OUTCOME_TRUNK_FAILURE = "trunk_failure"
OUTCOME_POSSIBLE_VOICEMAIL = "possible_voicemail"
OUTCOME_QUICK_HANGUP = "quick_hangup"
OUTCOME_OPTED_OUT = "opted_out"

# What the caller told us about the dose itself, recorded separately from the
# dial outcome - a delivered call where they say "I forgot" still succeeded.
MEDICINE_TAKEN = "taken"
MEDICINE_NOT_TAKEN = "not_taken"

# A hang-up faster than this means the callee never engaged, so it is worth one
# more try rather than counting as a delivered reminder.
QUICK_HANGUP_THRESHOLD_SEC = 5.0

# Retry policy: outcome -> (max_attempts_including_first, minutes_between_tries)
#
# Rejected is deliberate - the person declined, so re-dialling is harassment.
# Voicemail already carries the message, so there is nothing to retry.
RETRY_POLICY: dict[str, tuple[int, int]] = {
    OUTCOME_NO_ANSWER: (3, 15),
    OUTCOME_QUICK_HANGUP: (2, 10),
    OUTCOME_TRUNK_FAILURE: (2, 5),
    OUTCOME_REJECTED: (1, 0),
    OUTCOME_POSSIBLE_VOICEMAIL: (1, 0),
    OUTCOME_ANSWERED: (1, 0),
    OUTCOME_OPTED_OUT: (1, 0),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_reminder(row: Any) -> dict[str, Any]:
    keys = row.keys()
    return {
        "reminder_id": row["reminder_id"],
        "user_id": row["user_id"],
        "name": row["name"],
        "phone_number": row["phone_number"],
        "medicine_name": row["medicine_name"],
        "dosage": row["dosage"],
        "schedule_time": row["schedule_time"],
        "language_preference": row["language_preference"],
        "active": bool(row["active"]),
        "opted_out": bool(row["opted_out"]),
        "attempt_count": row["attempt_count"],
        "last_outcome": row["last_outcome"],
        "last_called_at": row["last_called_at"],
        # Added by a later migration, so old rows may not carry them yet.
        "last_medicine_response": (
            row["last_medicine_response"] if "last_medicine_response" in keys else ""
        ),
        "last_response_at": (
            row["last_response_at"] if "last_response_at" in keys else ""
        ),
    }


def create_reminder(
    user_id: str,
    name: str,
    phone_number: str,
    medicine_name: str,
    schedule_time: str,
    dosage: str = "",
    language_preference: str = "Hindi",
    db_path: Optional[Path] = None,
) -> int:
    """Register a medication reminder. Returns the new reminder_id.

    Args:
        schedule_time: Local 24-hour time the caller chose, as 'HH:MM'.
    """
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO medication_reminders
                (user_id, name, phone_number, medicine_name, dosage,
                 schedule_time, language_preference, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id.strip().lower(),
                name.strip(),
                phone_number.strip(),
                medicine_name.strip(),
                dosage.strip(),
                schedule_time.strip(),
                language_preference,
                _now_iso(),
            ),
        )
        reminder_id = cursor.lastrowid
    conn.close()
    logger.info(
        f"Created reminder {reminder_id} for {name} - {medicine_name} at {schedule_time}."
    )
    return int(reminder_id)


def get_reminder(
    reminder_id: int, db_path: Optional[Path] = None
) -> Optional[dict[str, Any]]:
    """Fetch a single reminder by its ID."""
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM medication_reminders WHERE reminder_id = ?", (reminder_id,)
    ).fetchone()
    conn.close()
    return _row_to_reminder(row) if row else None


def list_reminders(
    include_inactive: bool = False, db_path: Optional[Path] = None
) -> list[dict[str, Any]]:
    """List reminders, newest first. Skips opted-out and inactive by default."""
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    query = "SELECT * FROM medication_reminders"
    if not include_inactive:
        query += " WHERE active = 1 AND opted_out = 0"
    query += " ORDER BY reminder_id DESC"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [_row_to_reminder(row) for row in rows]


def opt_out(user_id_or_phone: str, db_path: Optional[Path] = None) -> int:
    """Stop all future reminder calls for a caller. Returns rows affected.

    Called when the caller says 'stop calling me' / 'रिमाइंडर बंद करें'. The row
    is kept rather than deleted so we have a record that they asked us to stop.
    """
    if not user_id_or_phone or not user_id_or_phone.strip():
        return 0

    db.init_db(db_path)
    conn = db.get_connection(db_path)
    clean = user_id_or_phone.strip().lower()
    with conn:
        cursor = conn.execute(
            """
            UPDATE medication_reminders
            SET opted_out = 1, active = 0, last_outcome = ?
            WHERE LOWER(user_id) = ? OR LOWER(phone_number) = ? OR LOWER(name) = ?
            """,
            (OUTCOME_OPTED_OUT, clean, clean, clean),
        )
        rows = cursor.rowcount
    conn.close()
    logger.info(f"Opt-out recorded for '{clean}': {rows} reminder(s) disabled.")
    return rows


def record_attempt(
    reminder_id: int,
    outcome: str,
    sip_status: str = "",
    duration_sec: float = 0.0,
    detail: str = "",
    db_path: Optional[Path] = None,
) -> None:
    """Log one dial attempt and roll the counters on the parent reminder.

    A hang-up inside QUICK_HANGUP_THRESHOLD_SEC is reclassified as
    OUTCOME_QUICK_HANGUP so it earns a retry instead of counting as delivered.
    """
    if outcome == OUTCOME_ANSWERED and 0 < duration_sec < QUICK_HANGUP_THRESHOLD_SEC:
        logger.info(
            f"Reminder {reminder_id} answered but lasted {duration_sec:.1f}s - "
            "treating as an immediate hang-up."
        )
        outcome = OUTCOME_QUICK_HANGUP

    now = _now_iso()
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO call_attempts
                (reminder_id, outcome, sip_status, duration_sec, detail, attempted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (reminder_id, outcome, sip_status, duration_sec, detail, now),
        )
        conn.execute(
            """
            UPDATE medication_reminders
            SET attempt_count = attempt_count + 1,
                last_outcome = ?,
                last_called_at = ?
            WHERE reminder_id = ?
            """,
            (outcome, now, reminder_id),
        )
    conn.close()
    logger.info(
        f"Reminder {reminder_id} attempt recorded: outcome={outcome}, sip={sip_status}."
    )


def record_medicine_response(
    reminder_id: int,
    taken: bool,
    note: str = "",
    db_path: Optional[Path] = None,
) -> None:
    """Record what the caller said about taking their medicine.

    This is the actual outcome the reminder exists for - a delivered call where
    the caller says "I forgot" is still a call worth having placed, so this is
    tracked separately from the dial outcome.
    """
    response = MEDICINE_TAKEN if taken else MEDICINE_NOT_TAKEN
    if note.strip():
        response = f"{response}: {note.strip()[:200]}"

    db.init_db(db_path)
    conn = db.get_connection(db_path)
    with conn:
        conn.execute(
            """
            UPDATE medication_reminders
            SET last_medicine_response = ?, last_response_at = ?
            WHERE reminder_id = ?
            """,
            (response, _now_iso(), reminder_id),
        )
    conn.close()
    logger.info(f"Reminder {reminder_id} adherence recorded: {response}")


def get_attempts(
    reminder_id: int, db_path: Optional[Path] = None
) -> list[dict[str, Any]]:
    """Return every logged attempt for a reminder, oldest first."""
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM call_attempts WHERE reminder_id = ? ORDER BY attempt_id ASC",
        (reminder_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "attempt_id": row["attempt_id"],
            "reminder_id": row["reminder_id"],
            "outcome": row["outcome"],
            "sip_status": row["sip_status"],
            "duration_sec": row["duration_sec"],
            "detail": row["detail"],
            "attempted_at": row["attempted_at"],
        }
        for row in rows
    ]


def should_retry(
    reminder: dict[str, Any], now: Optional[datetime] = None
) -> tuple[bool, str]:
    """Decide whether a reminder may be dialled again right now.

    Returns (allowed, reason). The reason is logged and shown by the scheduler so
    a skipped call is never silent.
    """
    now = now or datetime.now(timezone.utc)

    if reminder["opted_out"]:
        return False, "Caller opted out of reminder calls."
    if not reminder["active"]:
        return False, "Reminder is not active."

    last_outcome = reminder["last_outcome"]

    # Never dialled yet.
    if not last_outcome or reminder["attempt_count"] == 0:
        return True, "First attempt."

    if last_outcome == OUTCOME_ANSWERED:
        return False, "Reminder already delivered - caller answered and engaged."
    if last_outcome == OUTCOME_REJECTED:
        return False, "Caller actively declined the call; not dialling again today."
    if last_outcome == OUTCOME_POSSIBLE_VOICEMAIL:
        return False, "Message left on voicemail; no retry needed."

    max_attempts, gap_minutes = RETRY_POLICY.get(last_outcome, (1, 0))

    if reminder["attempt_count"] >= max_attempts:
        return False, (
            f"Retry limit reached for '{last_outcome}' "
            f"({reminder['attempt_count']}/{max_attempts} attempts)."
        )

    last_called_at = reminder["last_called_at"]
    if last_called_at:
        try:
            last_dt = datetime.fromisoformat(last_called_at)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning(
                f"Reminder {reminder['reminder_id']} has an unparseable "
                f"last_called_at='{last_called_at}'; allowing the retry."
            )
            return True, "Previous attempt timestamp unreadable; retrying."

        next_allowed = last_dt + timedelta(minutes=gap_minutes)
        if now < next_allowed:
            wait_min = (next_allowed - now).total_seconds() / 60
            return False, (
                f"Backing off after '{last_outcome}' - next retry in {wait_min:.0f} min."
            )

    return True, (
        f"Retry {reminder['attempt_count'] + 1}/{max_attempts} after '{last_outcome}'."
    )


def is_due(reminder: dict[str, Any], now: datetime, window_minutes: int = 10) -> bool:
    """True if the reminder's chosen 'HH:MM' falls within the window ending now.

    The window exists because the scheduler wakes on an interval rather than
    continuously - an 8:00 PM reminder still fires if we look at 8:04 PM.
    """
    schedule_time = (reminder.get("schedule_time") or "").strip()
    if not schedule_time:
        return False

    try:
        hour, minute = (int(part) for part in schedule_time.split(":", 1))
    except ValueError:
        logger.warning(
            f"Reminder {reminder.get('reminder_id')} has an invalid "
            f"schedule_time='{schedule_time}'; skipping it."
        )
        return False

    scheduled_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta_minutes = (now - scheduled_today).total_seconds() / 60
    return 0 <= delta_minutes <= window_minutes


def get_due_reminders(
    now: Optional[datetime] = None,
    window_minutes: int = 10,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Reminders whose time has come and whose retry policy permits a call.

    `now` should be local time, since callers pick reminder times in their own
    clock, not UTC.
    """
    now = now or datetime.now()
    due: list[dict[str, Any]] = []

    for reminder in list_reminders(db_path=db_path):
        if not is_due(reminder, now, window_minutes=window_minutes):
            continue

        # Suppress a repeat call for a reminder already handled in this window.
        last_called_at = reminder["last_called_at"]
        if last_called_at and reminder["last_outcome"] == OUTCOME_ANSWERED:
            continue

        allowed, reason = should_retry(reminder, now=datetime.now(timezone.utc))
        if not allowed:
            logger.info(f"Skipping reminder {reminder['reminder_id']}: {reason}")
            continue

        reminder["retry_reason"] = reason
        due.append(reminder)

    return due
