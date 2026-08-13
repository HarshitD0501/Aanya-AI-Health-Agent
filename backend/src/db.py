import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("agent.db")

DB_PATH = Path(__file__).parent.parent / "health_memory.db"


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = db_path or DB_PATH
    conn = sqlite3.connect(str(target_path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db_schema(conn: sqlite3.Connection) -> None:
    """Ensure table and all columns exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS caller_memory (
            user_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            phone_number TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            language_preference TEXT DEFAULT 'Hindi',
            facts TEXT DEFAULT '{}',
            last_interaction TEXT NOT NULL,
            consent_given INTEGER DEFAULT 1
        )
        """
    )
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(caller_memory)")
    columns = [column[1] for column in cursor.fetchall()]
    if "phone_number" not in columns:
        conn.execute("ALTER TABLE caller_memory ADD COLUMN phone_number TEXT DEFAULT ''")
    if "ip_address" not in columns:
        conn.execute("ALTER TABLE caller_memory ADD COLUMN ip_address TEXT DEFAULT ''")

    # ---------------------------------------------------------------------
    # Day 6 — Outbound medication reminder calls
    # ---------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS medication_reminders (
            reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            phone_number TEXT NOT NULL,
            medicine_name TEXT NOT NULL,
            dosage TEXT DEFAULT '',
            schedule_time TEXT NOT NULL,
            language_preference TEXT DEFAULT 'Hindi',
            active INTEGER DEFAULT 1,
            opted_out INTEGER DEFAULT 0,
            attempt_count INTEGER DEFAULT 0,
            last_outcome TEXT DEFAULT '',
            last_called_at TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS call_attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reminder_id INTEGER NOT NULL,
            phone_number TEXT DEFAULT '',
            outcome TEXT NOT NULL,
            sip_status TEXT DEFAULT '',
            duration_sec REAL DEFAULT 0,
            detail TEXT DEFAULT '',
            attempted_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reminder_due ON medication_reminders (active, opted_out, schedule_time)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_attempts_reminder ON call_attempts (reminder_id, attempted_at)"
    )

    # ---------------------------------------------------------------------
    # Day 7 — Human escalation requests
    #
    # One row per "Aanya stopped and asked a human to take over". The row is the
    # source of truth: the Discord webhook and the /help-desk dashboard both read
    # from here, so a failed webhook can never lose an escalation.
    # ---------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS escalations (
            escalation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            reference_id    TEXT NOT NULL DEFAULT '',
            caller_name     TEXT NOT NULL,
            user_id         TEXT DEFAULT '',
            phone_number    TEXT DEFAULT '',
            language        TEXT DEFAULT 'Hindi',
            reason_code     TEXT NOT NULL,
            urgency         TEXT NOT NULL,
            what_happened   TEXT NOT NULL,
            already_checked TEXT DEFAULT '',
            followup_method TEXT DEFAULT 'phone call',
            status          TEXT DEFAULT 'open',
            consent_given   INTEGER DEFAULT 1,
            delivery_status TEXT DEFAULT 'pending',
            delivery_detail TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_escalation_open ON escalations (status, created_at)"
    )

    # Adherence columns were added after the first reminders shipped, so migrate
    # rather than relying on CREATE TABLE IF NOT EXISTS (which is a no-op here).
    cursor.execute("PRAGMA table_info(medication_reminders)")
    reminder_columns = [column[1] for column in cursor.fetchall()]
    if "last_medicine_response" not in reminder_columns:
        conn.execute(
            "ALTER TABLE medication_reminders ADD COLUMN last_medicine_response TEXT DEFAULT ''"
        )
    if "last_response_at" not in reminder_columns:
        conn.execute(
            "ALTER TABLE medication_reminders ADD COLUMN last_response_at TEXT DEFAULT ''"
        )

    # ---------------------------------------------------------------------
    # Day 8 — Call Analytics Dashboard Logging
    # ---------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS call_analytics (
            call_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            call_type TEXT NOT NULL,
            caller_identifier TEXT DEFAULT '',
            outcome TEXT NOT NULL,
            outcome_reason TEXT DEFAULT '',
            duration_sec REAL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_call_analytics_created ON call_analytics (created_at)"
    )


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize the SQLite table for storing caller memory with phone and IP address support."""
    conn = get_connection(db_path)
    with conn:
        ensure_db_schema(conn)
    conn.close()
    logger.info("SQLite database initialized with phone_number & ip_address support.")


def get_caller_memory(query: str, db_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Retrieve caller memory by user_id, phone_number, ip_address, or name (case-insensitive)."""
    if not query or not query.strip():
        return None

    init_db(db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()
    clean_query = query.strip().lower()

    cursor.execute(
        """
        SELECT user_id, name, phone_number, ip_address, language_preference, facts, last_interaction, consent_given
        FROM caller_memory
        WHERE LOWER(user_id) = ? OR LOWER(phone_number) = ? OR LOWER(ip_address) = ? OR LOWER(name) = ?
        """,
        (clean_query, clean_query, clean_query, clean_query),
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return None

    try:
        facts_dict = json.loads(row["facts"]) if row["facts"] else {}
    except Exception:
        facts_dict = {}

    return {
        "user_id": row["user_id"],
        "name": row["name"],
        "phone_number": row["phone_number"],
        "ip_address": row["ip_address"],
        "language_preference": row["language_preference"],
        "facts": facts_dict,
        "last_interaction": row["last_interaction"],
        "consent_given": bool(row["consent_given"]),
    }


def get_latest_caller_memory(db_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Retrieve the most recent valid caller memory record from SQLite."""
    init_db(db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT user_id, name, phone_number, ip_address, language_preference, facts, last_interaction, consent_given
        FROM caller_memory
        WHERE consent_given = 1 AND facts != '{}' AND facts IS NOT NULL
        ORDER BY last_interaction DESC
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return None

    try:
        facts_dict = json.loads(row["facts"]) if row["facts"] else {}
    except Exception:
        facts_dict = {}

    return {
        "user_id": row["user_id"],
        "name": row["name"],
        "phone_number": row["phone_number"],
        "ip_address": row["ip_address"],
        "language_preference": row["language_preference"],
        "facts": facts_dict,
        "last_interaction": row["last_interaction"],
        "consent_given": bool(row["consent_given"]),
    }


def save_caller_memory(
    user_id: str,
    name: str,
    phone_number: str = "",
    ip_address: str = "",
    language_preference: str = "Hindi",
    facts: Optional[dict[str, Any]] = None,
    consent_given: bool = True,
    db_path: Optional[Path] = None,
) -> bool:
    """Save or update a caller's memory record in SQLite."""
    if not consent_given:
        logger.info(f"Skipping save for {user_id} ({name}) as consent_given is False.")
        return False

    clean_user_id = user_id.strip().lower()
    clean_name = name.strip()
    clean_phone = phone_number.strip()
    clean_ip = ip_address.strip()

    # Merge with existing caller facts if present
    existing = get_caller_memory(clean_user_id, db_path=db_path) or get_caller_memory(clean_name, db_path=db_path)
    new_facts = facts or {}
    if existing and existing.get("facts"):
        merged_facts = existing["facts"].copy()
        merged_facts.update(new_facts)
        new_facts = merged_facts

    facts_json = json.dumps(new_facts)
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = get_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO caller_memory (user_id, name, phone_number, ip_address, language_preference, facts, last_interaction, consent_given)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                phone_number=excluded.phone_number,
                ip_address=excluded.ip_address,
                language_preference=excluded.language_preference,
                facts=excluded.facts,
                last_interaction=excluded.last_interaction,
                consent_given=excluded.consent_given
            """,
            (clean_user_id, clean_name, clean_phone, clean_ip, language_preference, facts_json, now_iso, 1 if consent_given else 0),
        )
    conn.close()
    logger.info(f"Successfully saved caller memory for user_id='{clean_user_id}', name='{clean_name}', phone='{clean_phone}', ip='{clean_ip}'.")
    return True


def delete_caller_memory(query: str, db_path: Optional[Path] = None) -> bool:
    """Delete a caller's memory record from SQLite by user_id, phone_number, ip_address, or name."""
    if not query or not query.strip():
        return False

    conn = get_connection(db_path)
    clean_query = query.strip().lower()
    with conn:
        cursor = conn.execute(
            """
            DELETE FROM caller_memory
            WHERE LOWER(user_id) = ? OR LOWER(phone_number) = ? OR LOWER(ip_address) = ? OR LOWER(name) = ?
            """,
            (clean_query, clean_query, clean_query, clean_query),
        )
        rows_affected = cursor.rowcount
    conn.close()
    logger.info(f"Deleted caller memory for query='{clean_query}', rows affected: {rows_affected}.")
    return rows_affected > 0


def mask_identifier(raw: str) -> str:
    """Mask phone number or identity for caller privacy (Step 6)."""
    if not raw:
        return "Anonymous Caller"
    clean = raw.strip()
    if clean.startswith("+") or clean.isdigit():
        if len(clean) > 6:
            return clean[:3] + " **** " + clean[-3:]
        return clean[:2] + "****"
    # Name or text identity
    if len(clean) > 2:
        return clean[0] + "***" + clean[-1]
    return clean[0] + "***"


def record_call_analytics(
    session_id: str,
    call_type: str,
    caller_identifier: str = "",
    outcome: str = "success",
    outcome_reason: str = "",
    duration_sec: float = 0.0,
    db_path: Optional[Path] = None,
) -> bool:
    """Record the outcome of a call in SQLite for the Day 8 Analytics Dashboard."""
    init_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    masked_id = mask_identifier(caller_identifier)

    conn = get_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO call_analytics (session_id, call_type, caller_identifier, outcome, outcome_reason, duration_sec, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id or "session_unknown",
                call_type or "inbound_browser",
                masked_id,
                outcome.lower(),
                outcome_reason,
                round(duration_sec, 1),
                now_iso,
            ),
        )
    conn.close()
    logger.info(
        f"Recorded call analytics: session='{session_id}', type='{call_type}', "
        f"outcome='{outcome}', duration={duration_sec:.1f}s"
    )
    return True


def get_call_analytics_summary(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Retrieve summary counts and recent calls for analytics dashboard."""
    init_db(db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM call_analytics")
    total_calls = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM call_analytics WHERE outcome = 'success'")
    successful_calls = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM call_analytics WHERE outcome = 'failed'")
    failed_calls = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT call_id, session_id, call_type, caller_identifier, outcome, outcome_reason, duration_sec, created_at
        FROM call_analytics
        ORDER BY call_id DESC
        LIMIT 50
        """
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    success_rate = (
        round((successful_calls / total_calls) * 100, 1) if total_calls > 0 else 0.0
    )

    return {
        "total_calls": total_calls,
        "successful_calls": successful_calls,
        "failed_calls": failed_calls,
        "success_rate": f"{success_rate}%",
        "recent_calls": rows,
    }

