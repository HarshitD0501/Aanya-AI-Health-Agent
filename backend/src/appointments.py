"""
Clinic and Appointment Booking Store.

Mirrors escalations.py: provides an offline, database-backed store for scheduling
and listing clinic and PHC appointments booked by the Clinic & Appointment Specialist.

The SQLite row is the source of truth. Reference IDs follow the APT-2001 pattern
(short and readable digit by digit over a call). Free-text reasons are scrubbed using
escalations.scrub_private_details to prevent storing sensitive credentials or long digit runs.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import db
import escalations

logger = logging.getLogger("agent.appointments")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_appointment(row: Any) -> dict[str, Any]:
    return {
        "appointment_id": row["appointment_id"],
        "reference_id": row["reference_id"],
        "caller_name": row["caller_name"],
        "phone_number": row["phone_number"],
        "clinic_name": row["clinic_name"],
        "appointment_date": row["appointment_date"],
        "appointment_time": row["appointment_time"],
        "reason": row["reason"],
        "created_at": row["created_at"],
    }


def book_appointment(
    caller_name: str,
    phone_number: str,
    clinic_name: str,
    appointment_date: str,
    appointment_time: str,
    reason: str,
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Book a new clinic or PHC appointment in SQLite.

    Returns the stored appointment dict with reference_id (e.g. 'APT-2001'),
    or None if mandatory fields are missing.
    """
    name = (caller_name or "").strip()
    phone = (phone_number or "").strip()
    clinic = (clinic_name or "").strip()
    date_str = (appointment_date or "").strip()
    time_str = (appointment_time or "").strip()
    scrubbed_reason = escalations.scrub_private_details(reason or "")

    if (
        not name
        or not phone
        or not clinic
        or not date_str
        or not time_str
        or not scrubbed_reason
    ):
        logger.warning(
            "Appointment booking refused: name, phone, clinic, date, time, and reason are all required."
        )
        return None

    db.init_db(db_path)
    conn = db.get_connection(db_path)
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO clinic_appointments
                (caller_name, phone_number, clinic_name, appointment_date, appointment_time, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, phone, clinic, date_str, time_str, scrubbed_reason, _now_iso()),
        )
        appointment_id = int(cursor.lastrowid)
        reference_id = f"APT-{2000 + appointment_id}"
        conn.execute(
            "UPDATE clinic_appointments SET reference_id = ? WHERE appointment_id = ?",
            (reference_id, appointment_id),
        )
        row = conn.execute(
            "SELECT * FROM clinic_appointments WHERE appointment_id = ?",
            (appointment_id,),
        ).fetchone()
    conn.close()

    logger.info(
        f"Appointment {reference_id} booked for {name} at '{clinic}' on {date_str} at {time_str}."
    )
    return _row_to_appointment(row)


def get_appointment(
    reference_or_id: Any, db_path: Optional[Path] = None
) -> Optional[dict[str, Any]]:
    """Fetch one appointment by numeric id or by reference ('APT-2001')."""
    key = str(reference_or_id or "").strip()
    if not key:
        return None

    db.init_db(db_path)
    conn = db.get_connection(db_path)
    if key.isdigit():
        row = conn.execute(
            "SELECT * FROM clinic_appointments WHERE appointment_id = ?", (int(key),)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM clinic_appointments WHERE UPPER(reference_id) = ?",
            (key.upper(),),
        ).fetchone()
    conn.close()
    return _row_to_appointment(row) if row else None


def list_appointments(
    limit: int = 50, db_path: Optional[Path] = None
) -> list[dict[str, Any]]:
    """List appointments newest first."""
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM clinic_appointments ORDER BY appointment_id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [_row_to_appointment(row) for row in rows]
