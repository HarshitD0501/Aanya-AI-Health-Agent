"""
Automated Unit Tests for Day 8 - Call Analytics Dashboard (Health Access Track).

Three layers:

1. The store (`db.record_call_analytics` / `db.get_call_analytics_summary`) -
   the schema, the counts the dashboard shows, and the success-rate maths.
2. Privacy (Step 6) - `db.mask_identifier` must never let a full phone number,
   a full name, or any raw identifier reach a stored row.
3. The success rule itself - "caller spoke AND call lasted >= 5s" - imported
   from `agent` rather than re-implemented, so a change to the rule the demo
   video describes fails a test instead of silently invalidating the dashboard.

Every test takes a `db_path`, so nothing here touches the real
`health_memory.db`.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db

# The success rule under test. Imported from the agent so there is exactly one
# definition in the repo; if importing the agent module is too heavy in an
# environment without the LiveKit plugins, the tests that need only the
# predicate still run against the same constant.
from agent import (
    MIN_SUCCESS_DURATION_SEC,
    classify_call,
    is_successful_call,
)


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_day8.db"
    db.init_db(db_file)
    return db_file


def _record(test_db, **overrides):
    kwargs = {
        "session_id": "room-abc123",
        "call_type": "inbound_browser",
        "caller_identifier": "Harshit",
        "outcome": "success",
        "outcome_reason": "Health guidance & consultation provided",
        "duration_sec": 42.0,
    }
    kwargs.update(overrides)
    return db.record_call_analytics(db_path=test_db, **kwargs)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def test_schema_exists_after_init(test_db):
    """init_db must create call_analytics, or the dashboard reads a missing table."""
    conn = db.get_connection(test_db)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='call_analytics'"
    ).fetchone()
    conn.close()
    assert row is not None


def test_empty_db_reports_zeros_not_an_error(test_db):
    """A fresh DB is an empty dashboard, not a broken one."""
    summary = db.get_call_analytics_summary(db_path=test_db)
    assert summary["total_calls"] == 0
    assert summary["successful_calls"] == 0
    assert summary["failed_calls"] == 0
    assert summary["success_rate"] == "0.0%"
    assert summary["recent_calls"] == []


def test_recording_a_call_increments_total_and_success(test_db):
    """Step 5: a successful call must move the counters the video points at."""
    before = db.get_call_analytics_summary(db_path=test_db)
    assert _record(test_db) is True
    after = db.get_call_analytics_summary(db_path=test_db)

    assert after["total_calls"] == before["total_calls"] + 1
    assert after["successful_calls"] == before["successful_calls"] + 1
    assert after["failed_calls"] == before["failed_calls"]


def test_failed_call_increments_failed_only(test_db):
    _record(test_db, outcome="failed", outcome_reason="Early disconnect / no response", duration_sec=2.1)
    summary = db.get_call_analytics_summary(db_path=test_db)

    assert summary["total_calls"] == 1
    assert summary["failed_calls"] == 1
    assert summary["successful_calls"] == 0


def test_counts_are_real_not_hardcoded(test_db):
    """Step 4: every number must be derived from rows actually written."""
    for _ in range(3):
        _record(test_db, outcome="success")
    for _ in range(2):
        _record(test_db, outcome="failed", duration_sec=1.0)

    summary = db.get_call_analytics_summary(db_path=test_db)
    assert summary["total_calls"] == 5
    assert summary["successful_calls"] == 3
    assert summary["failed_calls"] == 2
    assert summary["success_rate"] == "60.0%"


def test_success_rate_never_divides_by_zero(test_db):
    summary = db.get_call_analytics_summary(db_path=test_db)
    assert summary["success_rate"] == "0.0%"


def test_outcome_is_normalised_to_lowercase(test_db):
    """The dashboard filters on 'success' / 'failed', so casing must not leak through."""
    _record(test_db, outcome="SUCCESS")
    summary = db.get_call_analytics_summary(db_path=test_db)
    assert summary["successful_calls"] == 1
    assert summary["recent_calls"][0]["outcome"] == "success"


def test_recent_calls_are_newest_first(test_db):
    _record(test_db, session_id="room-first")
    _record(test_db, session_id="room-second")
    rows = db.get_call_analytics_summary(db_path=test_db)["recent_calls"]
    assert rows[0]["session_id"] == "room-second"


def test_all_three_channels_are_recorded_separately(test_db):
    """Browser, inbound SIP and outbound reminder all land in the same table."""
    _record(test_db, call_type="inbound_browser")
    _record(test_db, call_type="inbound_sip", caller_identifier="+919454535137")
    _record(test_db, call_type="outbound_reminder", caller_identifier="+919454535137")

    rows = db.get_call_analytics_summary(db_path=test_db)["recent_calls"]
    assert {r["call_type"] for r in rows} == {
        "inbound_browser",
        "inbound_sip",
        "outbound_reminder",
    }


def test_missing_session_id_still_records(test_db):
    """A call that ended before the room name was known must not be lost."""
    _record(test_db, session_id="")
    rows = db.get_call_analytics_summary(db_path=test_db)["recent_calls"]
    assert rows[0]["session_id"] == "session_unknown"


# ---------------------------------------------------------------------------
# Privacy — Step 6
# ---------------------------------------------------------------------------


def test_phone_number_is_masked_in_stored_row(test_db):
    """A full number must never be readable on the public dashboard."""
    _record(test_db, caller_identifier="+919454535137")
    stored = db.get_call_analytics_summary(db_path=test_db)["recent_calls"][0]

    assert "+919454535137" not in stored["caller_identifier"]
    assert "9454535" not in stored["caller_identifier"]
    assert "*" in stored["caller_identifier"]


def test_name_is_masked_in_stored_row(test_db):
    _record(test_db, caller_identifier="Harshit")
    stored = db.get_call_analytics_summary(db_path=test_db)["recent_calls"][0]
    assert stored["caller_identifier"] == "H***t"


def test_mask_identifier_handles_every_shape():
    assert db.mask_identifier("") == "Anonymous Caller"
    assert "9454535" not in db.mask_identifier("+919454535137")
    assert "9454535" not in db.mask_identifier("9454535137")
    assert db.mask_identifier("Ab") == "A***"
    # Devanagari names must mask too, not crash.
    assert "*" in db.mask_identifier("हर्षित")


def test_no_transcript_column_exists(test_db):
    """Step 6 forbids exposing transcripts, so the table must have nowhere to put one."""
    conn = db.get_connection(test_db)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(call_analytics)")]
    conn.close()

    forbidden = {"transcript", "conversation", "messages", "otp", "pin", "password"}
    assert not forbidden.intersection(columns)


def test_outcome_reason_is_a_label_not_a_transcript(test_db):
    """The reason column is for short categories; nothing the caller said belongs in it."""
    _record(test_db, outcome_reason="Health guidance & consultation provided")
    stored = db.get_call_analytics_summary(db_path=test_db)["recent_calls"][0]
    assert len(stored["outcome_reason"]) < 80


# ---------------------------------------------------------------------------
# The success rule — Step 1 / Step 2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caller_spoke,duration,expected",
    [
        (True, 42.0, True),      # a real consultation
        (True, 5.0, True),       # exactly at the boundary
        (True, 4.9, False),      # spoke, but hung up too fast to be helped
        (False, 60.0, False),    # long silence — voicemail or an open tab
        (False, 1.0, False),     # instant drop
    ],
)
def test_success_condition(caller_spoke, duration, expected):
    assert is_successful_call(caller_spoke, duration) is expected


def test_silent_long_call_is_a_failure_not_a_success(test_db):
    """A 60s call where nobody spoke is voicemail; counting it as success would inflate the rate."""
    caller_spoke, duration = False, 60.0
    outcome = "success" if is_successful_call(caller_spoke, duration) else "failed"
    _record(test_db, outcome=outcome, duration_sec=duration)

    summary = db.get_call_analytics_summary(db_path=test_db)
    assert summary["failed_calls"] == 1
    assert summary["successful_calls"] == 0


def test_duration_is_rounded_for_display(test_db):
    _record(test_db, duration_sec=42.4567)
    stored = db.get_call_analytics_summary(db_path=test_db)["recent_calls"][0]
    assert stored["duration_sec"] == 42.5


# ---------------------------------------------------------------------------
# Outcome classification
#
# The dashboard shows a reason next to every failure, and the clock only starts
# once audio is live. A caller who hangs up while the worker is still starting
# up never heard Aanya at all, so that case must be told apart from a caller who
# heard the greeting and chose to say nothing.
# ---------------------------------------------------------------------------


def test_caller_who_left_during_setup_is_not_blamed_for_silence():
    outcome, reason = classify_call(caller_spoke=False, duration_sec=0.0, audio_went_live=False)
    assert outcome == "failed"
    assert reason == "Caller left before agent was ready"


def test_setup_time_alone_can_never_produce_a_success():
    """A long cold start must not fake a successful call."""
    outcome, _ = classify_call(caller_spoke=False, duration_sec=120.0, audio_went_live=False)
    assert outcome == "failed"


def test_successful_call_is_classified_with_the_guidance_reason():
    outcome, reason = classify_call(caller_spoke=True, duration_sec=42.0, audio_went_live=True)
    assert outcome == "success"
    assert reason == "Health guidance & consultation provided"


def test_silence_after_greeting_is_an_early_disconnect():
    outcome, reason = classify_call(caller_spoke=False, duration_sec=9.0, audio_went_live=True)
    assert (outcome, reason) == ("failed", "Early disconnect / no response")


def test_talking_briefly_then_hanging_up_is_a_short_call():
    outcome, reason = classify_call(caller_spoke=True, duration_sec=3.0, audio_went_live=True)
    assert outcome == "failed"
    assert reason == f"Short call (<{MIN_SUCCESS_DURATION_SEC:.0f}s)"


@pytest.mark.parametrize("audio_went_live", [True, False])
def test_every_classification_is_storable(test_db, audio_went_live):
    """Whatever classify_call returns must satisfy the store's own constraints."""
    outcome, reason = classify_call(True, 42.0, audio_went_live)
    assert outcome in {"success", "failed"}
    assert len(reason) < 80
    assert _record(test_db, outcome=outcome, outcome_reason=reason) is True
