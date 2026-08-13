"""
Automated Unit Tests for Day 6 - Outbound Medication Reminder Calls (Health Access Track).

Covers the reminder store, the due-time window, the opt-out path, every retry
rule, and the outcome classification that outbound needs but inbound never does.
No Twilio trunk and no LiveKit connection are required: dial-time failures are
simulated by feeding classify_dial_error the exception text LiveKit produces.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import reminders
from outbound import build_call_metadata, classify_dial_error


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_day6.db"
    reminders.db.init_db(db_file)
    return db_file


def _make_reminder(test_db, **overrides):
    """Register a reminder and return the fresh row."""
    kwargs = {
        "user_id": "ramesh",
        "name": "Ramesh",
        "phone_number": "+919999999999",
        "medicine_name": "Metformin",
        "schedule_time": "20:00",
        "dosage": "1 tablet after dinner",
        "language_preference": "Hindi",
    }
    kwargs.update(overrides)
    reminder_id = reminders.create_reminder(db_path=test_db, **kwargs)
    return reminders.get_reminder(reminder_id, db_path=test_db)


# ---------------------------------------------------------------------------
# Reminder store
# ---------------------------------------------------------------------------


def test_create_and_fetch_reminder(test_db):
    """A registered reminder round-trips with every field intact."""
    reminder = _make_reminder(test_db)
    assert reminder["reminder_id"] > 0
    assert reminder["name"] == "Ramesh"
    assert reminder["medicine_name"] == "Metformin"
    assert reminder["schedule_time"] == "20:00"
    assert reminder["language_preference"] == "Hindi"
    assert reminder["active"] is True
    assert reminder["opted_out"] is False
    assert reminder["attempt_count"] == 0
    assert reminder["last_outcome"] == ""


def test_list_reminders_hides_opted_out(test_db):
    """Opted-out rows are kept as a record but never returned for dialling."""
    _make_reminder(test_db)
    _make_reminder(test_db, user_id="suresh", name="Suresh", phone_number="+918888888888")

    assert len(reminders.list_reminders(db_path=test_db)) == 2

    reminders.opt_out("+918888888888", db_path=test_db)

    active = reminders.list_reminders(db_path=test_db)
    assert len(active) == 1
    assert active[0]["name"] == "Ramesh"
    # The row still exists, so we have proof they asked us to stop.
    assert len(reminders.list_reminders(include_inactive=True, db_path=test_db)) == 2


def test_opt_out_matches_by_name_and_user_id(test_db):
    """Opt-out works off whichever identifier the caller is known by."""
    reminder = _make_reminder(test_db)

    assert reminders.opt_out("RAMESH", db_path=test_db) == 1
    after = reminders.get_reminder(reminder["reminder_id"], db_path=test_db)
    assert after["opted_out"] is True
    assert after["active"] is False
    assert after["last_outcome"] == reminders.OUTCOME_OPTED_OUT


def test_opt_out_ignores_blank_input(test_db):
    """A blank identifier must never disable every reminder in the table."""
    _make_reminder(test_db)
    assert reminders.opt_out("", db_path=test_db) == 0
    assert reminders.opt_out("   ", db_path=test_db) == 0
    assert len(reminders.list_reminders(db_path=test_db)) == 1


# ---------------------------------------------------------------------------
# Attempt logging & adherence
# ---------------------------------------------------------------------------


def test_record_attempt_rolls_counters(test_db):
    """Each attempt is logged and the parent row's counters move with it."""
    reminder = _make_reminder(test_db)
    rid = reminder["reminder_id"]

    reminders.record_attempt(
        rid, reminders.OUTCOME_NO_ANSWER, sip_status="408", db_path=test_db
    )

    after = reminders.get_reminder(rid, db_path=test_db)
    assert after["attempt_count"] == 1
    assert after["last_outcome"] == reminders.OUTCOME_NO_ANSWER
    assert after["last_called_at"] != ""

    attempts = reminders.get_attempts(rid, db_path=test_db)
    assert len(attempts) == 1
    assert attempts[0]["sip_status"] == "408"


def test_quick_hangup_reclassification(test_db):
    """An 'answered' call shorter than the threshold is really a hang-up.

    This matters because otherwise a callee who rejects by picking up and
    immediately hanging up would count as a delivered reminder and never retry.
    """
    reminder = _make_reminder(test_db)
    rid = reminder["reminder_id"]

    reminders.record_attempt(
        rid, reminders.OUTCOME_ANSWERED, duration_sec=2.1, db_path=test_db
    )

    after = reminders.get_reminder(rid, db_path=test_db)
    assert after["last_outcome"] == reminders.OUTCOME_QUICK_HANGUP


def test_genuine_answer_is_not_reclassified(test_db):
    """A real conversation stays 'answered'."""
    reminder = _make_reminder(test_db)
    rid = reminder["reminder_id"]

    reminders.record_attempt(
        rid, reminders.OUTCOME_ANSWERED, duration_sec=42.0, db_path=test_db
    )

    after = reminders.get_reminder(rid, db_path=test_db)
    assert after["last_outcome"] == reminders.OUTCOME_ANSWERED


def test_record_medicine_response(test_db):
    """Adherence is tracked separately from whether the call connected."""
    reminder = _make_reminder(test_db)
    rid = reminder["reminder_id"]

    reminders.record_medicine_response(
        rid, taken=False, note="will take it after dinner", db_path=test_db
    )

    after = reminders.get_reminder(rid, db_path=test_db)
    assert after["last_medicine_response"].startswith(reminders.MEDICINE_NOT_TAKEN)
    assert "after dinner" in after["last_medicine_response"]
    assert after["last_response_at"] != ""

    reminders.record_medicine_response(rid, taken=True, db_path=test_db)
    assert (
        reminders.get_reminder(rid, db_path=test_db)["last_medicine_response"]
        == reminders.MEDICINE_TAKEN
    )


# ---------------------------------------------------------------------------
# Retry policy — the Day 6 "advanced" requirement: every outcome needs a rule
# ---------------------------------------------------------------------------


def test_first_attempt_always_allowed(test_db):
    """A never-dialled reminder is always eligible."""
    reminder = _make_reminder(test_db)
    allowed, reason = reminders.should_retry(reminder)
    assert allowed is True
    assert "First attempt" in reason


def test_no_answer_retries_up_to_three_times(test_db):
    """no_answer → 3 attempts, 15 minutes apart."""
    reminder = _make_reminder(test_db)
    now = datetime.now(timezone.utc)

    # Attempt 1 done, still inside the backoff window.
    reminder.update(
        {
            "attempt_count": 1,
            "last_outcome": reminders.OUTCOME_NO_ANSWER,
            "last_called_at": now.isoformat(),
        }
    )
    allowed, reason = reminders.should_retry(reminder, now=now)
    assert allowed is False
    assert "Backing off" in reason

    # Same attempt, but the 15 minutes have now elapsed.
    later = now + timedelta(minutes=16)
    allowed, reason = reminders.should_retry(reminder, now=later)
    assert allowed is True
    assert "Retry 2/3" in reason

    # Third attempt is the last one permitted.
    reminder["attempt_count"] = 3
    allowed, reason = reminders.should_retry(reminder, now=later)
    assert allowed is False
    assert "Retry limit reached" in reason


def test_quick_hangup_retries_once(test_db):
    """quick_hangup → 2 attempts, 10 minutes apart."""
    reminder = _make_reminder(test_db)
    now = datetime.now(timezone.utc)
    reminder.update(
        {
            "attempt_count": 1,
            "last_outcome": reminders.OUTCOME_QUICK_HANGUP,
            "last_called_at": (now - timedelta(minutes=11)).isoformat(),
        }
    )
    allowed, reason = reminders.should_retry(reminder, now=now)
    assert allowed is True
    assert "Retry 2/2" in reason

    reminder["attempt_count"] = 2
    allowed, _ = reminders.should_retry(reminder, now=now)
    assert allowed is False


def test_trunk_failure_retries_quickly(test_db):
    """trunk_failure → 2 attempts, only 5 minutes apart; it is our fault, not theirs."""
    reminder = _make_reminder(test_db)
    now = datetime.now(timezone.utc)
    reminder.update(
        {
            "attempt_count": 1,
            "last_outcome": reminders.OUTCOME_TRUNK_FAILURE,
            "last_called_at": (now - timedelta(minutes=6)).isoformat(),
        }
    )
    allowed, _ = reminders.should_retry(reminder, now=now)
    assert allowed is True


def test_rejected_is_never_retried(test_db):
    """A declined call is a decision, not a failure. Re-dialling is harassment."""
    reminder = _make_reminder(test_db)
    reminder.update(
        {
            "attempt_count": 1,
            "last_outcome": reminders.OUTCOME_REJECTED,
            "last_called_at": (
                datetime.now(timezone.utc) - timedelta(hours=5)
            ).isoformat(),
        }
    )
    allowed, reason = reminders.should_retry(reminder)
    assert allowed is False
    assert "declined" in reason


def test_voicemail_is_never_retried(test_db):
    """The message is already on the machine; calling again just annoys them."""
    reminder = _make_reminder(test_db)
    reminder.update(
        {"attempt_count": 1, "last_outcome": reminders.OUTCOME_POSSIBLE_VOICEMAIL}
    )
    allowed, reason = reminders.should_retry(reminder)
    assert allowed is False
    assert "voicemail" in reason.lower()


def test_answered_is_never_retried(test_db):
    """The reminder was delivered. Done for today."""
    reminder = _make_reminder(test_db)
    reminder.update({"attempt_count": 1, "last_outcome": reminders.OUTCOME_ANSWERED})
    allowed, reason = reminders.should_retry(reminder)
    assert allowed is False
    assert "delivered" in reason


def test_opted_out_blocks_every_retry(test_db):
    """Opt-out outranks every other rule, including 'first attempt'."""
    reminder = _make_reminder(test_db)
    reminder["opted_out"] = True
    allowed, reason = reminders.should_retry(reminder)
    assert allowed is False
    assert "opted out" in reason.lower()


def test_unparseable_timestamp_allows_retry(test_db):
    """Corrupt data must not silently stop a caller's reminders."""
    reminder = _make_reminder(test_db)
    reminder.update(
        {
            "attempt_count": 1,
            "last_outcome": reminders.OUTCOME_NO_ANSWER,
            "last_called_at": "not-a-timestamp",
        }
    )
    allowed, reason = reminders.should_retry(reminder)
    assert allowed is True
    assert "unreadable" in reason


def test_every_outcome_has_a_retry_rule():
    """Day 6 requires a defined behaviour for every outcome, so none may be missing."""
    known_outcomes = {
        reminders.OUTCOME_ANSWERED,
        reminders.OUTCOME_NO_ANSWER,
        reminders.OUTCOME_REJECTED,
        reminders.OUTCOME_TRUNK_FAILURE,
        reminders.OUTCOME_POSSIBLE_VOICEMAIL,
        reminders.OUTCOME_QUICK_HANGUP,
        reminders.OUTCOME_OPTED_OUT,
    }
    assert known_outcomes == set(reminders.RETRY_POLICY)
    for outcome, (max_attempts, gap) in reminders.RETRY_POLICY.items():
        assert max_attempts >= 1, outcome
        assert gap >= 0, outcome


# ---------------------------------------------------------------------------
# Due-time window
# ---------------------------------------------------------------------------


def test_is_due_at_exact_time(test_db):
    reminder = _make_reminder(test_db, schedule_time="20:00")
    assert reminders.is_due(reminder, datetime(2026, 8, 11, 20, 0)) is True


def test_is_due_inside_window(test_db):
    """The scheduler wakes on an interval, so 20:00 must still fire at 20:04."""
    reminder = _make_reminder(test_db, schedule_time="20:00")
    assert reminders.is_due(reminder, datetime(2026, 8, 11, 20, 4)) is True
    assert reminders.is_due(reminder, datetime(2026, 8, 11, 20, 10)) is True


def test_is_not_due_outside_window(test_db):
    """Too early, or long past — a 20:00 dose must not be delivered at 23:00."""
    reminder = _make_reminder(test_db, schedule_time="20:00")
    assert reminders.is_due(reminder, datetime(2026, 8, 11, 19, 59)) is False
    assert reminders.is_due(reminder, datetime(2026, 8, 11, 20, 11)) is False
    assert reminders.is_due(reminder, datetime(2026, 8, 11, 23, 0)) is False


def test_is_due_rejects_bad_schedule_time(test_db):
    """A malformed time is skipped with a warning rather than crashing the pass."""
    reminder = _make_reminder(test_db, schedule_time="8 PM")
    assert reminders.is_due(reminder, datetime(2026, 8, 11, 20, 0)) is False

    reminder["schedule_time"] = ""
    assert reminders.is_due(reminder, datetime(2026, 8, 11, 20, 0)) is False


def test_get_due_reminders_selects_only_due_rows(test_db):
    """The scheduler's work-list contains only rows that are due AND permitted."""
    _make_reminder(test_db, schedule_time="20:00", name="DueNow")
    _make_reminder(
        test_db,
        schedule_time="09:00",
        name="NotDue",
        user_id="suresh",
        phone_number="+918888888888",
    )

    due = reminders.get_due_reminders(
        now=datetime(2026, 8, 11, 20, 2), db_path=test_db
    )
    assert [r["name"] for r in due] == ["DueNow"]
    assert due[0]["retry_reason"] == "First attempt."


def test_get_due_reminders_skips_already_answered(test_db):
    """A reminder delivered in this window is not called a second time."""
    reminder = _make_reminder(test_db, schedule_time="20:00")
    reminders.record_attempt(
        reminder["reminder_id"],
        reminders.OUTCOME_ANSWERED,
        duration_sec=30.0,
        db_path=test_db,
    )

    due = reminders.get_due_reminders(
        now=datetime(2026, 8, 11, 20, 5), db_path=test_db
    )
    assert due == []


def test_get_due_reminders_skips_opted_out(test_db):
    """Opt-out removes the row from the work-list entirely."""
    _make_reminder(test_db, schedule_time="20:00")
    reminders.opt_out("ramesh", db_path=test_db)

    due = reminders.get_due_reminders(
        now=datetime(2026, 8, 11, 20, 0), db_path=test_db
    )
    assert due == []


def test_get_due_reminders_retries_a_no_answer(test_db):
    """A missed call comes back into the work-list once the backoff expires."""
    reminder = _make_reminder(test_db, schedule_time="20:00")
    reminders.record_attempt(
        reminder["reminder_id"],
        reminders.OUTCOME_NO_ANSWER,
        sip_status="408",
        db_path=test_db,
    )

    # Immediately after the miss, the 15-minute backoff suppresses the retry.
    assert (
        reminders.get_due_reminders(
            now=datetime.now().replace(hour=20, minute=1), db_path=test_db
        )
        == []
    )


# ---------------------------------------------------------------------------
# Dial-failure classification
#
# livekit-api 1.1.0 has no SipCallError class despite what the docs show, so
# outbound.classify_dial_error parses the SIP status out of the TwirpError text.
# These tests pin that parsing, since a wrong mapping means a declined call gets
# re-dialled.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected_outcome", "expected_status"),
    [
        ("twirp error internal: sip status 486 Busy Here", reminders.OUTCOME_REJECTED, "486"),
        ("twirp error internal: 603 Decline", reminders.OUTCOME_REJECTED, "603"),
        ("twirp error internal: 408 Request Timeout", reminders.OUTCOME_NO_ANSWER, "408"),
        ("twirp error internal: 480 Temporarily Unavailable", reminders.OUTCOME_NO_ANSWER, "480"),
        ("twirp error canceled: 487 Request Terminated", reminders.OUTCOME_NO_ANSWER, "487"),
    ],
)
def test_classify_dial_error_by_sip_status(message, expected_outcome, expected_status):
    outcome, status = classify_dial_error(Exception(message))
    assert outcome == expected_outcome
    assert status == expected_status


def test_classify_dial_error_by_wording():
    """Some failures carry no SIP code, only prose."""
    outcome, status = classify_dial_error(Exception("call timeout waiting for answer"))
    assert outcome == reminders.OUTCOME_NO_ANSWER
    assert status == ""

    outcome, _ = classify_dial_error(Exception("callee declined the invite"))
    assert outcome == reminders.OUTCOME_REJECTED


def test_classify_dial_error_defaults_to_trunk_failure():
    """An unrecognised failure is ours to fix, so it retries quickly."""
    outcome, status = classify_dial_error(Exception("twirp error unauthenticated"))
    assert outcome == reminders.OUTCOME_TRUNK_FAILURE
    assert status == ""


# ---------------------------------------------------------------------------
# Job metadata — the inbound/outbound discriminator
# ---------------------------------------------------------------------------


def test_call_metadata_carries_only_what_the_agent_needs(test_db):
    """Metadata opens the call; no health facts may travel over it."""
    import json

    reminder = _make_reminder(test_db)
    payload = json.loads(build_call_metadata(reminder))

    assert payload["call_type"] == "medication_reminder"
    assert payload["reminder_id"] == reminder["reminder_id"]
    assert payload["name"] == "Ramesh"
    assert payload["medicine_name"] == "Metformin"
    assert payload["language_preference"] == "Hindi"
    assert payload["phone_number"] == "+919999999999"

    # No triage history, conditions, or transcripts.
    assert "facts" not in payload
    assert "ongoing_conditions" not in payload
    assert "last_triage_outcome" not in payload


def test_metadata_survives_devanagari_names(test_db):
    """ensure_ascii=False must keep Hindi names readable, not \\uXXXX escaped."""
    import json

    reminder = _make_reminder(test_db, name="रमेश", user_id="रमेश")
    raw = build_call_metadata(reminder)
    assert "रमेश" in raw
    assert json.loads(raw)["name"] == "रमेश"


# ---------------------------------------------------------------------------
# The opening line
#
# Day 6's hard requirement: within the first two sentences the callee must hear
# WHO is calling, WHY, and HOW TO STOP it. The opening is spoken from code rather
# than generated, so it can be asserted here instead of hoped for at runtime.
# ---------------------------------------------------------------------------


def test_hindi_opening_states_who_why_and_optout():
    from agent import build_outbound_opening

    opening = build_outbound_opening(
        {
            "name": "Ramesh",
            "medicine_name": "Metformin",
            "schedule_time": "20:00",
            "language_preference": "Hindi",
        }
    )
    assert "आन्या" in opening                    # who
    assert "हेल्थ रिमाइंडर सेवा" in opening        # who, on whose behalf
    assert "Metformin" in opening                # why
    assert "रिमाइंडर बंद करें" in opening          # how to stop
    assert "रात आठ बजे" in opening                # spoken time, not "20:00"
    assert "20:00" not in opening


def test_english_opening_states_who_why_and_optout():
    from agent import build_outbound_opening

    opening = build_outbound_opening(
        {
            "name": "Ramesh",
            "medicine_name": "Metformin",
            "schedule_time": "08:30",
            "language_preference": "English",
        }
    )
    assert "Aanya" in opening
    assert "health reminder service" in opening
    assert "Metformin" in opening
    assert "stop the reminders" in opening
    assert "8:30 in the morning" in opening


def test_opening_survives_a_missing_schedule_time():
    """An ad-hoc test call has no schedule_time; the opening must still be sane."""
    from agent import build_outbound_opening

    opening = build_outbound_opening(
        {"name": "Ramesh", "medicine_name": "Metformin", "language_preference": "Hindi"}
    )
    assert "आन्या" in opening
    assert "रिमाइंडर बंद करें" in opening
    assert "::" not in opening


def test_spoken_time_covers_the_clock():
    """Murf reads '20:00' as 'twenty colon zero zero', so times are spelled out."""
    from agent import _spoken_time

    assert _spoken_time("20:00", "Hindi") == "रात आठ बजे"
    assert _spoken_time("08:00", "Hindi") == "सुबह आठ बजे"
    assert _spoken_time("13:15", "Hindi") == "दोपहर एक बजकर 15 मिनट"
    assert _spoken_time("18:00", "Hindi") == "शाम छह बजे"
    assert _spoken_time("09:00", "English") == "9 in the morning"
    # Bad input falls through untouched rather than raising mid-call.
    assert _spoken_time("8 PM", "Hindi") == "8 PM"
    assert _spoken_time("", "Hindi") == ""


# ---------------------------------------------------------------------------
# Setting a reminder from inside a conversation
#
# Before this existed, asking Aanya to remind you got "मैं रिमाइंडर सेट नहीं कर
# सकती" and a pointer to Google Assistant, because the capability lived only in
# the CLI. These cover the guards that stop her saving a reminder that could
# never ring.
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_db(tmp_path, monkeypatch):
    """Point the agent's module-level DB at a throwaway file.

    The function tools take no db_path - they run mid-call - so the default path
    is redirected instead.
    """
    import db as db_module

    db_file = tmp_path / "test_agent_reminders.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_file)
    db_module.init_db()
    return db_file


def _tool_context(**userdata):
    """The bit of RunContext the reminder tools actually touch."""
    from types import SimpleNamespace

    return SimpleNamespace(session=SimpleNamespace(userdata=dict(userdata)))


def test_phone_numbers_spoken_over_a_call_reach_e164():
    from agent import _normalize_phone_e164

    assert _normalize_phone_e164("9454535137") == "+919454535137"
    assert _normalize_phone_e164("+91 94545 35137") == "+919454535137"
    assert _normalize_phone_e164("094545 35137") == "+919454535137"
    assert _normalize_phone_e164("919454535137") == "+919454535137"
    # Unusable digits are refused rather than dialled at.
    assert _normalize_phone_e164("12345") == ""
    assert _normalize_phone_e164("") == ""


def test_clock_parsing_tolerates_what_the_llm_hands_back():
    from agent import _normalize_clock

    assert _normalize_clock("20:00") == "20:00"
    assert _normalize_clock("8pm") == "20:00"
    assert _normalize_clock("8.30") == "08:30"
    assert _normalize_clock("12am") == "00:00"
    assert _normalize_clock("9") == "09:00"
    assert _normalize_clock("25:00") == ""
    assert _normalize_clock("raat aath baje") == ""


async def test_scheduling_a_reminder_mid_call_saves_a_dialable_row(agent_db):
    from agent import Assistant

    result = await Assistant().schedule_medicine_reminder(
        _tool_context(phone_number=""),
        medicine_name="दवा",
        time_24h="8pm",
        caller_name="Harshit",
        phone_number="94545 35137",
        dosage="खाने के बाद एक टैबलेट",
        language="Hindi",
    )

    rows = reminders.list_reminders(db_path=agent_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["phone_number"] == "+919454535137"   # normalized, so it can be dialled
    assert row["schedule_time"] == "20:00"          # spoken "8pm" stored as 24h
    assert row["language_preference"] == "Hindi"
    assert "रात आठ बजे" in result                    # confirmed back in words, not "20:00"


async def test_reminder_is_refused_without_a_number_a_name_or_a_time(agent_db):
    """Each missing field asks for that field instead of saving a broken row."""
    from agent import Assistant

    assistant = Assistant()
    no_number = await assistant.schedule_medicine_reminder(
        _tool_context(phone_number=""), medicine_name="दवा", time_24h="20:00", caller_name="H"
    )
    no_name = await assistant.schedule_medicine_reminder(
        _tool_context(), medicine_name="दवा", time_24h="20:00", phone_number="9454535137"
    )
    bad_time = await assistant.schedule_medicine_reminder(
        _tool_context(), medicine_name="दवा", time_24h="raat aath", caller_name="H",
        phone_number="9454535137",
    )

    assert "number" in no_number.lower()
    assert "name" in no_name.lower()
    assert "not a clock time" in bad_time
    assert reminders.list_reminders(db_path=agent_db) == []


async def test_asking_twice_does_not_ring_them_twice(agent_db):
    from agent import Assistant

    assistant = Assistant()
    args = {
        "medicine_name": "दवा",
        "time_24h": "20:00",
        "caller_name": "Harshit",
        "phone_number": "+919454535137",
    }
    await assistant.schedule_medicine_reminder(_tool_context(), **args)
    repeat = await assistant.schedule_medicine_reminder(_tool_context(), **args)

    assert "already exists" in repeat
    assert len(reminders.list_reminders(db_path=agent_db)) == 1


async def test_listing_reminders_uses_the_number_from_the_live_call(agent_db):
    from agent import Assistant

    assistant = Assistant()
    await assistant.schedule_medicine_reminder(
        _tool_context(),
        medicine_name="दवा",
        time_24h="08:00",
        caller_name="Harshit",
        phone_number="9454535137",
    )

    # Phone number omitted: it must come from the call's own userdata.
    listed = await assistant.list_my_reminders(_tool_context(phone_number="+919454535137"))
    assert "दवा" in listed
    assert "सुबह आठ बजे" in listed

    empty = await assistant.list_my_reminders(_tool_context(phone_number="+911111111111"))
    assert "No active reminders" in empty


# ---------------------------------------------------------------------------
# Answer detection: do not speak to a ringing phone
#
# The regression these cover: Aanya used to treat a published audio track as
# "answered". LiveKit publishes the SIP leg's track while sip.callStatus is still
# 'dialing', so the whole Hindi opening played into a ringing line and the callee
# picked up to total silence. sip.callStatus == 'active' is the only answer.
# ---------------------------------------------------------------------------


class _FakePublication:
    """A published audio track - present during the ring, so never proof of pickup."""

    def __init__(self) -> None:
        from livekit import rtc

        self.kind = rtc.TrackKind.KIND_AUDIO


class _FakeSipParticipant:
    def __init__(self, call_status: str = "dialing", with_audio: bool = True) -> None:
        self.identity = "+919999999999"
        self.name = "Ramesh"
        self.attributes = {"sip.callStatus": call_status} if call_status else {}
        self.track_publications = {"TR_1": _FakePublication()} if with_audio else {}

    def set_status(self, status: str) -> None:
        self.attributes["sip.callStatus"] = status


class _FakeRoom:
    """Just enough rtc.Room for _wait_until_sip_answered: handlers plus lookup."""

    def __init__(self, participant: _FakeSipParticipant) -> None:
        self.name = "reminder-test"
        self.metadata = ""
        self.remote_participants = {participant.identity: participant}
        self._handlers: dict[str, list] = {}

    def on(self, event: str, callback=None):
        if callback is None:
            def decorator(fn):
                self._handlers.setdefault(event, []).append(fn)
                return fn

            return decorator
        self._handlers.setdefault(event, []).append(callback)
        return callback

    def off(self, event: str, callback) -> None:
        handlers = self._handlers.get(event, [])
        if callback in handlers:
            handlers.remove(callback)

    def emit(self, event: str, *args) -> None:
        for handler in list(self._handlers.get(event, [])):
            handler(*args)

    def set_metadata(self, metadata: str, emit: bool = True) -> None:
        """Mirror rtc.Room: the property updates, then the event fires."""
        old, self.metadata = self.metadata, metadata
        if emit:
            self.emit("room_metadata_changed", old, metadata)

    def handler_count(self) -> int:
        return sum(len(handlers) for handlers in self._handlers.values())


def _fake_ctx(participant: _FakeSipParticipant):
    from types import SimpleNamespace

    return SimpleNamespace(room=_FakeRoom(participant))


async def test_a_ringing_phone_with_an_audio_track_is_not_answered():
    """The exact regression: track published, status still 'dialing' -> not answered."""
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="dialing", with_audio=True)
    ctx = _fake_ctx(participant)

    state = await _wait_until_sip_answered(ctx, participant, timeout=1.0)

    assert state == "timeout", "a track published during the ring must not count as pickup"


async def test_pickup_mid_ring_is_detected_from_the_status_event():
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="dialing")
    ctx = _fake_ctx(participant)

    async def pick_up_after_a_moment():
        await asyncio.sleep(0.2)
        participant.set_status("active")
        ctx.room.emit(
            "participant_attributes_changed", {"sip.callStatus": "active"}, participant
        )

    _, state = await asyncio.gather(
        pick_up_after_a_moment(),
        _wait_until_sip_answered(ctx, participant, timeout=3.0),
    )
    assert state == "active"


async def test_pickup_is_still_detected_when_the_event_never_fires():
    """The poll backstop: a missed attributes event must not leave Aanya mute."""
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="dialing")
    ctx = _fake_ctx(participant)

    async def pick_up_silently():
        await asyncio.sleep(0.2)
        participant.set_status("active")  # no event emitted at all

    _, state = await asyncio.gather(
        pick_up_silently(),
        _wait_until_sip_answered(ctx, participant, timeout=3.0),
    )
    assert state == "active"


async def test_already_active_returns_immediately():
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="active")
    ctx = _fake_ctx(participant)

    assert await _wait_until_sip_answered(ctx, participant, timeout=1.0) == "active"


async def test_declined_while_ringing_is_a_hangup():
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="dialing")
    ctx = _fake_ctx(participant)

    async def decline():
        await asyncio.sleep(0.2)
        ctx.room.emit(
            "participant_attributes_changed", {"sip.callStatus": "hangup"}, participant
        )

    _, state = await asyncio.gather(
        decline(),
        _wait_until_sip_answered(ctx, participant, timeout=3.0),
    )
    assert state == "hangup"


async def test_a_missing_status_attribute_speaks_rather_than_staying_mute():
    """No sip.callStatus ever arrives: speak anyway, do not sit silent on a live call."""
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="", with_audio=False)
    ctx = _fake_ctx(participant)

    assert await _wait_until_sip_answered(ctx, participant, timeout=1.0) == "unknown"


async def test_handlers_are_removed_when_the_wait_ends():
    """A leaked handler would fire against a finished call on the next attempt."""
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="dialing")
    ctx = _fake_ctx(participant)

    await _wait_until_sip_answered(ctx, participant, timeout=1.0)

    assert ctx.room.handler_count() == 0


# ---------------------------------------------------------------------------
# The dialer's answer relay
#
# The second, harder bug: on this project's Twilio trunk sip.callStatus NEVER
# leaves 'dialing', even on a call the callee physically answered and talked on.
# Proven with src/sip_probe.py running with no agent in the room at all. So the
# dialer - which holds the synchronous dial, and therefore sees the carrier's SIP
# 200 OK - relays the answer to the agent through room metadata.
# ---------------------------------------------------------------------------


async def test_metadata_answer_unblocks_a_trunk_that_never_reports_active():
    """The real-world case: status stuck on 'dialing', dialer says answered."""
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="dialing")
    ctx = _fake_ctx(participant)

    async def dialer_reports_the_answer():
        await asyncio.sleep(0.2)
        # Exactly what outbound._announce_answer writes.
        ctx.room.set_metadata(json.dumps({"sip_answered": True, "answered_at": 1.0}))

    _, state = await asyncio.gather(
        dialer_reports_the_answer(),
        _wait_until_sip_answered(ctx, participant, timeout=3.0),
    )
    assert state == "active", "the dialer's 200 OK must be enough to start speaking"


async def test_metadata_answer_is_seen_even_if_the_event_is_missed():
    """Poll backstop covers the metadata channel too, not just the attribute."""
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="dialing")
    ctx = _fake_ctx(participant)

    async def dialer_reports_silently():
        await asyncio.sleep(0.2)
        ctx.room.set_metadata(json.dumps({"sip_answered": True}), emit=False)

    _, state = await asyncio.gather(
        dialer_reports_silently(),
        _wait_until_sip_answered(ctx, participant, timeout=3.0),
    )
    assert state == "active"


async def test_answer_already_in_metadata_returns_immediately():
    """The dialer can win the race - the answer may land before we start waiting."""
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="dialing")
    ctx = _fake_ctx(participant)
    ctx.room.metadata = json.dumps({"sip_answered": True})

    assert await _wait_until_sip_answered(ctx, participant, timeout=1.0) == "active"


@pytest.mark.parametrize(
    "metadata",
    [
        "",
        "not json at all",
        json.dumps({"sip_answered": False}),
        json.dumps({"something_else": True}),
        json.dumps(["a", "list"]),
    ],
)
async def test_junk_metadata_is_not_an_answer(metadata):
    """Room metadata is shared state; only our own key, set to true, counts."""
    from agent import _wait_until_sip_answered

    participant = _FakeSipParticipant(call_status="dialing")
    ctx = _fake_ctx(participant)
    ctx.room.metadata = metadata

    assert await _wait_until_sip_answered(ctx, participant, timeout=0.8) == "timeout"


def test_the_two_sides_agree_on_the_metadata_key():
    """Two processes, one contract. A rename on one side would mute every call."""
    import agent
    import outbound

    assert agent.SIP_ANSWERED_METADATA_KEY == outbound.ANSWERED_METADATA_KEY


def test_the_teardown_threshold_sits_between_the_ring_and_the_wait():
    """The last-resort branch must be reachable, and never during the ring.

    Below the dialer's ring timeout it would fire on a phone still ringing; above
    the agent's own wait it could never fire at all.
    """
    import agent
    import outbound

    assert outbound.RINGING_TIMEOUT_SEC < agent.SIP_RING_TEARDOWN_SEC
    assert agent.SIP_RING_TEARDOWN_SEC < agent.SIP_ANSWER_TIMEOUT_SEC


async def test_a_leg_still_connected_past_any_ring_is_spoken_to(monkeypatch):
    """Belt and braces: both signals lost, but the line is demonstrably still up.

    A ring cannot outlive the dialer's teardown, so a leg still present past it is
    a live call. Speaking is right; sitting mute in a live caller's ear is not.
    """
    import agent

    monkeypatch.setattr(agent, "SIP_RING_TEARDOWN_SEC", 0.5)
    participant = _FakeSipParticipant(call_status="dialing")
    ctx = _fake_ctx(participant)

    state = await agent._wait_until_sip_answered(ctx, participant, timeout=0.8)

    assert state == "active"


async def test_a_leg_that_left_is_still_a_no_answer(monkeypatch):
    """The same branch must not fire for a call that actually ended."""
    import agent

    monkeypatch.setattr(agent, "SIP_RING_TEARDOWN_SEC", 0.5)
    participant = _FakeSipParticipant(call_status="dialing")
    ctx = _fake_ctx(participant)
    ctx.room.remote_participants.clear()  # carrier tore the call down

    state = await agent._wait_until_sip_answered(ctx, participant, timeout=0.8)

    assert state == "timeout"


# ---------------------------------------------------------------------------
# Transport hiccup vs carrier rejection
#
# wait_until_answered=True holds one HTTP request open for the whole ring, which
# is why it was abandoned: Windows aborts it with WinError 1236 mid-ring and the
# old code called that a trunk failure on a phone that was ringing normally. The
# dial is now allowed to die without ending the call - but only for genuine
# transport errors, never for a carrier's "no".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        OSError("[WinError 1236] The network connection was aborted by the local system"),
        ConnectionResetError("connection reset by peer"),
        asyncio.TimeoutError(),
        Exception("Server disconnected"),
        Exception("Cannot connect to host cloud.livekit.io"),
    ],
)
def test_transport_hiccups_do_not_end_a_ringing_call(error):
    from outbound import is_transport_hiccup

    assert is_transport_hiccup(error) is True


@pytest.mark.parametrize(
    "error",
    [
        Exception("twirp error internal: sip status 486 Busy Here"),
        Exception("sip status 603 Decline"),
        Exception("twirp error unauthenticated: invalid api key"),
    ],
)
def test_carrier_answers_are_never_treated_as_hiccups(error):
    """A 486 means the callee declined. Retrying the poll would ring a dead call."""
    from outbound import is_transport_hiccup

    assert is_transport_hiccup(error) is False


def test_a_dropped_connection_carrying_a_sip_code_is_still_a_rejection():
    """Both markers present: the SIP code wins, because it is the call's verdict."""
    from outbound import is_transport_hiccup

    error = Exception("connection reset after sip status 486 Busy Here")
    assert is_transport_hiccup(error) is False


def test_the_dial_timeout_outlives_the_ring():
    """A transport timeout shorter than the ring would abort every long ring."""
    import outbound

    assert outbound.DIAL_HTTP_TIMEOUT_SEC > outbound.RINGING_TIMEOUT_SEC


# ---------------------------------------------------------------------------
# Racing the two answer signals
#
# _dial_and_wait_for_answer runs the synchronous dial and the attribute poll at
# the same time and takes whichever fires. These drive it with fakes so every
# branch is exercised without a trunk.
# ---------------------------------------------------------------------------


class _FakeSipApi:
    """A create_sip_participant that answers after a delay, or raises."""

    def __init__(self, answer_after: float = 0.0, error: Exception = None) -> None:
        self.answer_after = answer_after
        self.error = error
        self.request = None

    async def create_sip_participant(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        await asyncio.sleep(self.answer_after)
        if self.error:
            raise self.error
        from types import SimpleNamespace

        return SimpleNamespace(participant_identity=request.participant_identity)


def _fake_lkapi(sip: _FakeSipApi):
    from types import SimpleNamespace

    return SimpleNamespace(sip=sip, room=SimpleNamespace())


def _dial_request():
    from livekit import api

    return api.CreateSIPParticipantRequest(
        sip_trunk_id="ST_test",
        sip_call_to="+919999999999",
        room_name="reminder-test",
        participant_identity="+919999999999",
        participant_name="Ramesh",
    )


def _stub_poll(monkeypatch, state: str, sip_status: str = "", after: float = 0.05):
    """Replace the attribute poll with a fixed verdict after a delay."""
    import outbound

    async def fake_poll(lkapi, room_name, identity, timeout=None):
        await asyncio.sleep(after)
        return state, sip_status

    monkeypatch.setattr(outbound, "_poll_room_for_answer", fake_poll)


async def test_the_dial_wins_when_the_attribute_never_reports_active(monkeypatch):
    """The production case: poll times out, the synchronous dial gets the answer."""
    import outbound

    _stub_poll(monkeypatch, "timeout", after=0.05)
    sip = _FakeSipApi(answer_after=2.2)  # past MIN_PLAUSIBLE_ANSWER_SEC

    state, sip_status, participant, error = await outbound._dial_and_wait_for_answer(
        _fake_lkapi(sip), _dial_request(), "reminder-test", "+919999999999"
    )

    assert state == "answered"
    assert sip_status == "200"
    assert participant is not None
    assert error is None
    assert sip.request.wait_until_answered is True, "the answer gate must be requested"


async def test_an_instant_dial_return_is_not_treated_as_a_pickup(monkeypatch):
    """If wait_until_answered is ignored, the dial returns at once. Do not speak.

    Without this guard the whole fix would reintroduce the original bug in a new
    place: announcing an answer while the phone is still ringing.
    """
    import outbound

    _stub_poll(monkeypatch, "timeout", after=0.3)
    sip = _FakeSipApi(answer_after=0.0)

    state, _, _, _ = await outbound._dial_and_wait_for_answer(
        _fake_lkapi(sip), _dial_request(), "reminder-test", "+919999999999"
    )

    assert state == "timeout", "an instant return means 'dial accepted', not 'answered'"


async def test_the_poll_wins_when_the_attribute_does_report_active(monkeypatch):
    """A well-behaved trunk still works: whichever signal fires first is used."""
    import outbound

    _stub_poll(monkeypatch, "answered", sip_status="200", after=0.05)
    sip = _FakeSipApi(answer_after=10.0)  # would outlast the test

    state, sip_status, _, _ = await outbound._dial_and_wait_for_answer(
        _fake_lkapi(sip), _dial_request(), "reminder-test", "+919999999999"
    )

    assert (state, sip_status) == ("answered", "200")


async def test_a_carrier_rejection_ends_the_call_immediately(monkeypatch):
    """486 means declined. Polling on would waste 30s on a call that is over."""
    import outbound

    _stub_poll(monkeypatch, "timeout", after=10.0)
    sip = _FakeSipApi(error=Exception("twirp error: sip status 486 Busy Here"))

    state, _, _, error = await outbound._dial_and_wait_for_answer(
        _fake_lkapi(sip), _dial_request(), "reminder-test", "+919999999999"
    )

    assert state == "dial_failed"
    assert "486" in str(error)


async def test_a_dropped_dial_lets_the_poll_finish_the_call(monkeypatch):
    """WinError 1236 mid-ring: the phone is still ringing, so keep watching."""
    import outbound

    _stub_poll(monkeypatch, "answered", sip_status="200", after=0.3)
    sip = _FakeSipApi(
        answer_after=0.05,
        error=OSError("[WinError 1236] The network connection was aborted"),
    )

    state, sip_status, _, _ = await outbound._dial_and_wait_for_answer(
        _fake_lkapi(sip), _dial_request(), "reminder-test", "+919999999999"
    )

    assert state == "answered", "a dead HTTP request must not abort a ringing call"
    assert sip_status == "200"


async def test_a_decline_seen_by_the_poll_is_reported_as_a_hangup(monkeypatch):
    import outbound

    _stub_poll(monkeypatch, "hangup", sip_status="486", after=0.05)
    sip = _FakeSipApi(answer_after=10.0)

    state, sip_status, _, _ = await outbound._dial_and_wait_for_answer(
        _fake_lkapi(sip), _dial_request(), "reminder-test", "+919999999999"
    )

    assert (state, sip_status) == ("hangup", "486")


# ---------------------------------------------------------------------------
# Speaking early
#
# The failure these lock down was heard on a real call: the callee picked up and
# got 46 seconds of silence. Both answer signals are dead on this trunk, so the
# 45s gate always ran to its timeout, and the timeout was treated as "never
# answered" - Aanya shut the job down or spoke far too late to matter. Now the
# gate is a few seconds long, only a hangup silences her, and an unconfirmed
# answer gets the opening repeated.
# ---------------------------------------------------------------------------


class _FakeSession:
    """Records what was said. say() returns an awaitable, like SpeechHandle."""

    def __init__(self, error: Exception | None = None) -> None:
        self.spoken: list[str] = []
        self.in_chat_ctx: list[bool] = []
        self.error = error

    def say(self, text: str, *, add_to_chat_ctx: bool = True):
        self.spoken.append(text)
        self.in_chat_ctx.append(add_to_chat_ctx)
        if self.error:
            raise self.error

        async def _played_out():
            return None

        return _played_out()


def test_the_speak_early_gate_is_short_enough_that_nobody_hangs_up():
    """A few seconds of dead air is forgivable; the old 45s was not."""
    import agent

    assert agent.SIP_SPEAK_EARLY_SEC <= 5.0
    assert agent.SIP_SPEAK_EARLY_SEC < agent.SIP_ANSWER_TIMEOUT_SEC


def test_the_opening_is_repeated_a_bounded_number_of_times():
    """Unbounded repeats would read a voicemail box the same paragraph forever."""
    import agent

    assert 1 < agent.OUTBOUND_OPENING_ATTEMPTS <= 4
    assert agent.OUTBOUND_OPENING_RETRY_SEC > 0
    # Every attempt plus its gap has to fit inside the call cap.
    assert (
        agent.OUTBOUND_OPENING_ATTEMPTS * agent.OUTBOUND_OPENING_RETRY_SEC
        < agent.MAX_OUTBOUND_CALL_SEC
    )


async def test_a_confirmed_answer_says_the_opening_exactly_once():
    """The callee is demonstrably listening; repeating would talk over them."""
    import agent

    session = _FakeSession()
    await agent._deliver_opening(
        session,
        "नमस्ते",
        answer_confirmed=True,
        engaged=asyncio.Event(),
        disconnected=asyncio.Event(),
    )

    assert session.spoken == ["नमस्ते"]


async def test_an_unconfirmed_answer_repeats_the_opening(monkeypatch):
    """No answer signal: we may have spoken into the ring, so say it again."""
    import agent

    monkeypatch.setattr(agent, "OUTBOUND_OPENING_RETRY_SEC", 0.05)
    session = _FakeSession()

    await agent._deliver_opening(
        session,
        "नमस्ते",
        answer_confirmed=False,
        engaged=asyncio.Event(),
        disconnected=asyncio.Event(),
    )

    assert len(session.spoken) == agent.OUTBOUND_OPENING_ATTEMPTS


async def test_only_the_first_opening_enters_the_chat_history(monkeypatch):
    """A repeat is us covering for a lost signal, not Aanya choosing to repeat.

    Three identical assistant turns in the history would teach the LLM that
    repeating itself is the house style for the rest of the call.
    """
    import agent

    monkeypatch.setattr(agent, "OUTBOUND_OPENING_RETRY_SEC", 0.05)
    session = _FakeSession()

    await agent._deliver_opening(
        session,
        "नमस्ते",
        answer_confirmed=False,
        engaged=asyncio.Event(),
        disconnected=asyncio.Event(),
    )

    assert session.in_chat_ctx[0] is True
    assert all(flag is False for flag in session.in_chat_ctx[1:])


async def test_the_callee_speaking_stops_the_repeats(monkeypatch):
    """One word from the callee proves they heard it. Stop, and let them talk."""
    import agent

    monkeypatch.setattr(agent, "OUTBOUND_OPENING_RETRY_SEC", 5.0)
    session = _FakeSession()
    engaged = asyncio.Event()

    async def callee_answers():
        await asyncio.sleep(0.05)
        engaged.set()

    await asyncio.gather(
        callee_answers(),
        agent._deliver_opening(
            session,
            "नमस्ते",
            answer_confirmed=False,
            engaged=engaged,
            disconnected=asyncio.Event(),
        ),
    )

    assert len(session.spoken) == 1, "a repeat would have talked over the callee"


async def test_a_hangup_stops_the_repeats(monkeypatch):
    import agent

    monkeypatch.setattr(agent, "OUTBOUND_OPENING_RETRY_SEC", 5.0)
    session = _FakeSession()
    disconnected = asyncio.Event()

    async def callee_hangs_up():
        await asyncio.sleep(0.05)
        disconnected.set()

    await asyncio.gather(
        callee_hangs_up(),
        agent._deliver_opening(
            session,
            "नमस्ते",
            answer_confirmed=False,
            engaged=asyncio.Event(),
            disconnected=disconnected,
        ),
    )

    assert len(session.spoken) == 1


async def test_a_failed_say_reports_the_hangup_instead_of_raising():
    """say() blows up when the leg is gone; the caller must learn it, not crash."""
    import agent

    session = _FakeSession(error=RuntimeError("session closed"))
    disconnected = asyncio.Event()

    await agent._deliver_opening(
        session,
        "नमस्ते",
        answer_confirmed=False,
        engaged=asyncio.Event(),
        disconnected=disconnected,
    )

    assert disconnected.is_set()
    assert len(session.spoken) == 1, "a dead session must not be retried"


async def test_wait_for_any_returns_false_on_timeout():
    from agent import _wait_for_any

    assert await _wait_for_any((asyncio.Event(), asyncio.Event()), 0.05) is False


async def test_wait_for_any_sees_an_event_already_set():
    from agent import _wait_for_any

    already = asyncio.Event()
    already.set()

    assert await _wait_for_any((already, asyncio.Event()), 5.0) is True
