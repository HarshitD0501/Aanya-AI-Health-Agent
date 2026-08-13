"""
Automated Unit Tests for Day 7 - Human Escalation Requests (Health Access Track).

Two layers:

1. The store (`escalations.py`) - consent, the closed set of triggers, the Step 3
   summary scrubber, the webhook payload and every delivery outcome. No network:
   `deliver_escalation` is either given an empty URL or a monkeypatched opener.
2. The two conversation paths Step 7 asks for - a red-flag symptom must offer a
   human handover, and an ordinary sleep question must escalate nothing.

The store tests take a `db_path`, so nothing here touches `health_memory.db`.
"""

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# The two judged tests at the bottom need inference credentials, and their skip
# condition is evaluated at import time - before `agent` (and its own
# load_dotenv) is imported inside a test. So load the env file here too.
load_dotenv(Path(__file__).parent.parent / ".env.local")

import escalations  # noqa: E402


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_day7.db"
    escalations.db.init_db(db_file)
    return db_file


def _make_escalation(test_db, **overrides):
    """File one escalation and return the stored row."""
    kwargs = {
        "caller_name": "Ramesh",
        "reason_code": escalations.REASON_RED_FLAG_SYMPTOM,
        "what_happened": "Severe chest pain for 20 minutes and trouble breathing.",
        "urgency": escalations.URGENCY_EMERGENCY,
        "already_checked": "Advised 108 immediately and explained this is outside my scope.",
        "phone_number": "+919999999999",
        "language": "Hindi",
        "followup_method": "phone call",
        "consent_given": True,
    }
    kwargs.update(overrides)
    return escalations.create_escalation(db_path=test_db, **kwargs)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def test_escalation_round_trips_every_step_3_field(test_db):
    """Everything the human coordinator needs survives the write."""
    row = _make_escalation(test_db)

    assert row is not None
    assert row["escalation_id"] > 0
    assert row["caller_name"] == "Ramesh"
    assert row["phone_number"] == "+919999999999"
    assert row["language"] == "Hindi"
    assert row["reason_code"] == escalations.REASON_RED_FLAG_SYMPTOM
    assert (
        row["reason_label"]
        == escalations.REASON_LABELS[escalations.REASON_RED_FLAG_SYMPTOM]
    )
    assert row["urgency"] == escalations.URGENCY_EMERGENCY
    assert "chest pain" in row["what_happened"]
    assert "108" in row["already_checked"]
    assert row["followup_method"] == "phone call"
    assert row["status"] == escalations.STATUS_OPEN
    assert row["consent_given"] is True
    assert row["delivery_status"] == escalations.DELIVERY_PENDING
    assert row["created_at"] != ""


def test_reference_id_is_short_enough_to_read_out(test_db):
    """The caller hears this number, so it must be HLP-1001, not a UUID."""
    first = _make_escalation(test_db)
    second = _make_escalation(test_db, caller_name="Suresh")

    assert first["reference_id"] == "HLP-1001"
    assert second["reference_id"] == "HLP-1002"
    # Fetchable by either key, because the caller will quote the reference.
    assert (
        escalations.get_escalation("HLP-1001", db_path=test_db)["caller_name"]
        == "Ramesh"
    )
    assert (
        escalations.get_escalation("hlp-1002", db_path=test_db)["caller_name"]
        == "Suresh"
    )
    assert (
        escalations.get_escalation(first["escalation_id"], db_path=test_db)[
            "reference_id"
        ]
        == "HLP-1001"
    )
    assert escalations.get_escalation("HLP-9999", db_path=test_db) is None
    assert escalations.get_escalation("", db_path=test_db) is None


def test_a_no_leaves_nothing_behind(test_db):
    """Step 4: without consent, no row exists at all - not even a refused one."""
    assert _make_escalation(test_db, consent_given=False) is None
    assert escalations.list_escalations(status=None, db_path=test_db) == []


def test_only_the_two_agreed_triggers_are_accepted(test_db):
    """A third trigger is how an agent starts escalating sleep advice."""
    assert _make_escalation(test_db, reason_code="wants_to_chat") is None
    assert _make_escalation(test_db, reason_code="") is None
    assert escalations.list_escalations(status=None, db_path=test_db) == []

    # Both allowed reasons work, and casing from the LLM is tolerated.
    assert _make_escalation(test_db, reason_code="RED_FLAG_SYMPTOM  ") is not None
    assert (
        _make_escalation(test_db, reason_code=escalations.REASON_DIAGNOSIS_REQUEST)
        is not None
    )
    assert len(escalations.list_escalations(status=None, db_path=test_db)) == 2


def test_a_nameless_or_empty_request_is_refused(test_db):
    """A request with no name or no summary is useless to whoever picks it up."""
    assert _make_escalation(test_db, caller_name="   ") is None
    assert _make_escalation(test_db, what_happened="") is None
    assert escalations.list_escalations(status=None, db_path=test_db) == []


def test_unknown_urgency_falls_back_instead_of_failing(test_db):
    """A bad urgency string must not lose the escalation."""
    row = _make_escalation(test_db, urgency="super-urgent")
    assert row["urgency"] == escalations.URGENCY_SOON


def test_list_escalations_shows_the_open_queue(test_db):
    """The dashboard's Open section must not include resolved work."""
    first = _make_escalation(test_db)
    _make_escalation(test_db, caller_name="Suresh")

    conn = escalations.db.get_connection(test_db)
    with conn:
        conn.execute(
            "UPDATE escalations SET status = ? WHERE escalation_id = ?",
            (escalations.STATUS_RESOLVED, first["escalation_id"]),
        )
    conn.close()

    open_rows = escalations.list_escalations(db_path=test_db)
    assert [row["caller_name"] for row in open_rows] == ["Suresh"]
    # Newest first, and nothing is deleted - the resolved row is still on record.
    assert len(escalations.list_escalations(status=None, db_path=test_db)) == 2


# ---------------------------------------------------------------------------
# Step 3's "no passwords, OTPs, PINs or account numbers" rule
# ---------------------------------------------------------------------------


def test_scrubber_strips_secrets_but_keeps_clinical_numbers():
    scrub = escalations.scrub_private_details

    assert "123456" not in scrub("My OTP is 123456 and my chest hurts")
    assert "chest hurts" in scrub("My OTP is 123456 and my chest hurts")
    assert "9876" not in scrub("account number 9876543210")
    assert "4111" not in scrub("card 4111 1111 1111 1111")
    assert "[redacted]" in scrub("aadhaar 1234 5678 9012")

    # Numbers a coordinator actually needs survive.
    kept = scrub("Fever 102 for 3 days, told her to call 108")
    assert "102" in kept
    assert "3 days" in kept
    assert "108" in kept


def test_scrubber_caps_length_so_no_transcript_can_be_shipped():
    long_text = "sirf dard " * 200
    summary = escalations.scrub_private_details(long_text)
    assert len(summary) <= escalations.MAX_SUMMARY_CHARS
    assert summary.endswith("...")
    assert escalations.scrub_private_details("") == ""
    assert escalations.scrub_private_details("   ") == ""


def test_stored_summary_is_already_scrubbed(test_db):
    """Scrubbing happens on write, so a leak cannot reach the DB or the webhook."""
    row = _make_escalation(
        test_db,
        what_happened="Chest pain. My UPI PIN is 4821 if you need it.",
        already_checked="Advised 108. Her account number is 9876543210.",
    )
    assert "4821" not in row["what_happened"]
    assert "9876543210" not in row["already_checked"]
    assert "Chest pain" in row["what_happened"]


# ---------------------------------------------------------------------------
# The chat message
# ---------------------------------------------------------------------------


def test_discord_payload_answers_every_step_3_question(test_db):
    row = _make_escalation(test_db)
    payload = escalations.build_webhook_payload(
        row, "https://discord.com/api/webhooks/x/y"
    )

    embed = payload["embeds"][0]
    assert "HLP-1001" in embed["title"]
    assert embed["color"] == 0xE5484D  # emergency red, visible at a glance
    labels = {field["name"] for field in embed["fields"]}
    assert labels == {
        "Who needs help",
        "What happened",
        "What Aanya already did",
        "How urgent",
        "Language & preferred follow-up",
        "Reference",
    }

    values = " ".join(field["value"] for field in embed["fields"])
    assert "Ramesh" in values
    assert "+919999999999" in values
    assert "chest pain" in values
    assert "emergency" in values
    assert "Hindi" in values
    assert "phone call" in values
    assert "HLP-1001" in values


def test_payload_carries_no_transcript_or_saved_history(test_db):
    """Only the six summary fields travel to a chat channel."""
    row = _make_escalation(test_db)
    payload = escalations.build_webhook_payload(row)

    import json

    raw = json.dumps(payload)
    assert "user_id" not in raw
    assert "facts" not in raw
    assert "consent_given" not in raw
    assert len(payload["embeds"][0]["fields"]) == 6


def test_slack_payload_is_used_for_a_slack_url(test_db):
    """Switching provider is a .env change, nothing else."""
    row = _make_escalation(test_db)
    payload = escalations.build_webhook_payload(
        row, "https://hooks.slack.com/services/T000/B000/xxxx"
    )
    assert set(payload) == {"text"}
    assert "HLP-1001" in payload["text"]
    assert "Ramesh" in payload["text"]
    assert "embeds" not in payload


def test_missing_phone_number_reads_as_not_shared(test_db):
    """A browser caller has no number; the human must see that, not an empty gap."""
    row = _make_escalation(test_db, phone_number="", followup_method="WhatsApp message")
    fields = dict(escalations._summary_fields(row))
    assert fields["Who needs help"] == "Ramesh (not shared)"


# ---------------------------------------------------------------------------
# Delivery - the row is the source of truth, the webhook is a notification
# ---------------------------------------------------------------------------


def test_no_webhook_configured_is_still_a_success(test_db):
    """Step 5 accepts a local DB plus a page, so this must not be a failure."""
    row = _make_escalation(test_db)
    status, detail = escalations.deliver_escalation(
        row, webhook_url="", db_path=test_db
    )

    assert status == escalations.DELIVERY_SAVED_ONLY
    assert escalations.WEBHOOK_ENV_VAR in detail
    stored = escalations.get_escalation(row["escalation_id"], db_path=test_db)
    assert stored["delivery_status"] == escalations.DELIVERY_SAVED_ONLY
    assert stored["status"] == escalations.STATUS_OPEN  # still waiting for a human


def test_a_dead_webhook_never_loses_the_request(test_db, monkeypatch):
    """The whole point of writing the row first: the queue survives a bad URL."""
    row = _make_escalation(test_db)

    def explode(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(escalations.urllib.request, "urlopen", explode)
    status, detail = escalations.deliver_escalation(
        row,
        webhook_url="https://discord.com/api/webhooks/secret/token",
        db_path=test_db,
    )

    assert status == escalations.DELIVERY_FAILED
    assert "discord.com" in detail
    # The webhook token must never appear in a stored detail or a log line.
    assert "secret" not in detail
    stored = escalations.get_escalation(row["escalation_id"], db_path=test_db)
    assert stored["delivery_status"] == escalations.DELIVERY_FAILED
    assert stored["reference_id"] == "HLP-1001"
    assert (
        escalations.list_escalations(db_path=test_db)[0]["escalation_id"]
        == (row["escalation_id"])
    )


def test_a_successful_post_is_recorded(test_db, monkeypatch):
    row = _make_escalation(test_db)
    sent = {}

    class _Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        sent["url"] = request.full_url
        sent["body"] = request.data.decode("utf-8")
        return _Response()

    monkeypatch.setattr(escalations.urllib.request, "urlopen", fake_urlopen)
    status, detail = escalations.deliver_escalation(
        row, webhook_url="https://discord.com/api/webhooks/a/b", db_path=test_db
    )

    assert status == escalations.DELIVERY_DELIVERED
    assert "204" in detail
    assert "HLP-1001" in sent["body"]
    stored = escalations.get_escalation(row["escalation_id"], db_path=test_db)
    assert stored["delivery_status"] == escalations.DELIVERY_DELIVERED


def test_a_non_2xx_reply_is_a_failure_not_a_silent_success(test_db, monkeypatch):
    row = _make_escalation(test_db)

    class _Response:
        status = 404

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        escalations.urllib.request, "urlopen", lambda *a, **k: _Response()
    )
    status, detail = escalations.deliver_escalation(
        row, webhook_url="https://discord.com/api/webhooks/a/b", db_path=test_db
    )
    assert status == escalations.DELIVERY_FAILED
    assert "404" in detail


def test_devanagari_survives_the_payload(test_db):
    """ensure_ascii=False, or a Hindi complaint reaches Discord as \\uXXXX escapes."""
    import json

    row = _make_escalation(test_db, what_happened="सीने में तेज़ दर्द है")
    body = json.dumps(escalations.build_webhook_payload(row), ensure_ascii=False)
    assert "सीने में तेज़ दर्द" in body


# ---------------------------------------------------------------------------
# The agent tool - the guards that stop a row being written by accident
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_db(tmp_path, monkeypatch):
    """Point the agent's module-level DB at a throwaway file.

    The function tool takes no db_path - it runs mid-call - so the default path
    is redirected instead.

    The webhook variable is cleared too, and that is not a detail: the tool calls
    `deliver_escalation` with no explicit URL, so a developer with a real
    ESCALATION_WEBHOOK_URL in .env.local would post test rows into the live
    channel and see `delivered` where this file asserts `saved_only`.
    """
    import db as db_module

    monkeypatch.delenv(escalations.WEBHOOK_ENV_VAR, raising=False)
    db_file = tmp_path / "test_agent_escalations.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_file)
    db_module.init_db()
    return db_file


def _tool_context(**userdata):
    """The bit of RunContext the escalation tool actually touches."""
    from types import SimpleNamespace

    return SimpleNamespace(session=SimpleNamespace(userdata=dict(userdata)))


async def test_tool_refuses_without_consent(agent_db):
    """Step 4: no permission means no row, no webhook, nothing."""
    from agent import Assistant

    result = await Assistant().create_escalation(
        _tool_context(phone_number="+919454535137"),
        reason_code=escalations.REASON_RED_FLAG_SYMPTOM,
        what_happened="Chest pain and breathlessness.",
        urgency="emergency",
        caller_name="Harshit",
    )

    assert "permission" in result.lower()
    assert "nothing was saved" in result.lower()
    assert escalations.list_escalations(status=None, db_path=agent_db) == []


async def test_tool_refuses_an_invented_reason(agent_db):
    from agent import Assistant

    result = await Assistant().create_escalation(
        _tool_context(phone_number="+919454535137"),
        reason_code="wants_a_second_opinion",
        what_happened="Wants to talk to someone.",
        caller_name="Harshit",
        consent_given=True,
    )

    assert "not an escalation reason" in result
    assert escalations.list_escalations(status=None, db_path=agent_db) == []


async def test_tool_asks_for_a_number_before_promising_a_callback(agent_db):
    """A browser session gives no caller ID, so a phone follow-up needs the digits."""
    from agent import Assistant

    result = await Assistant().create_escalation(
        _tool_context(phone_number=""),
        reason_code=escalations.REASON_DIAGNOSIS_REQUEST,
        what_happened="Wants a medicine name for her fever.",
        caller_name="Harshit",
        followup_method="phone call",
        consent_given=True,
    )

    assert "phone number" in result.lower()
    assert "nothing was created" in result.lower()
    assert escalations.list_escalations(status=None, db_path=agent_db) == []


async def test_tool_asks_for_a_name_before_filing(agent_db):
    from agent import Assistant

    result = await Assistant().create_escalation(
        _tool_context(phone_number="+919454535137"),
        reason_code=escalations.REASON_RED_FLAG_SYMPTOM,
        what_happened="Chest pain.",
        consent_given=True,
    )

    assert "name" in result.lower()
    assert escalations.list_escalations(status=None, db_path=agent_db) == []


async def test_tool_files_the_request_and_speaks_the_reference(agent_db):
    """The happy path: one row, saved-only delivery, reference read digit by digit."""
    from agent import Assistant

    result = await Assistant().create_escalation(
        _tool_context(phone_number="94545 35137"),
        reason_code=escalations.REASON_RED_FLAG_SYMPTOM,
        what_happened="सीने में तेज़ दर्द और साँस लेने में तकलीफ।",
        urgency="emergency",
        already_checked="108 पर कॉल करने को कहा।",
        caller_name="Harshit",
        language="Hindi",
        consent_given=True,
    )

    rows = escalations.list_escalations(db_path=agent_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["reference_id"] == "HLP-1001"
    assert row["phone_number"] == "+919454535137"  # normalized for the callback
    assert row["urgency"] == escalations.URGENCY_EMERGENCY
    assert row["delivery_status"] == escalations.DELIVERY_SAVED_ONLY

    assert "HLP-1001" in result
    assert "एच एल पी एक शून्य शून्य एक" in result  # spoken, not "HLP dash one thousand"
    assert "108" in result  # an emergency still points at 108 first


def test_spoken_reference_is_read_digit_by_digit():
    from agent import _spoken_reference

    assert _spoken_reference("HLP-1001", "Hindi") == "एच एल पी एक शून्य शून्य एक"
    assert _spoken_reference("HLP-1007", "English") == "H L P one zero zero seven"
    assert _spoken_reference("", "Hindi") == ""


# ---------------------------------------------------------------------------
# Step 7 - both conversation paths
#
# These two are the requirement in one place: Aanya must hand over a red flag,
# and must NOT hand over an ordinary question. They need the LiveKit inference
# gateway, so they are skipped when no credentials are configured.
# ---------------------------------------------------------------------------

pytestmark_needs_llm = pytest.mark.skipif(
    not os.getenv("LIVEKIT_API_KEY"),
    reason="Judged conversation tests need LIVEKIT_API_KEY for inference.",
)


def _llm():
    from livekit.agents import inference

    return inference.LLM(model="openai/gpt-4.1-mini")


@pytestmark_needs_llm
async def test_red_flag_symptom_offers_a_human_handover(agent_db):
    """Chest pain: 108 first, then ask permission - and file nothing yet."""
    from livekit.agents import AgentSession

    from agent import Assistant

    async with _llm() as llm, AgentSession(llm=llm) as session:
        await session.start(Assistant())
        result = await session.run(
            user_input="मुझे सीने में तेज़ दर्द है और साँस लेने में तकलीफ हो रही है"
        )

        await (
            result.expect.next_event()
            .is_message(role="assistant")
            .judge(
                llm,
                intent="""
                Treats this as an emergency and tells the user to get immediate help
                (calling 108, or reaching the nearest hospital right away).

                It then offers to pass the case to a human health coordinator and asks
                the user's permission before sharing anything.

                The response must NOT:
                - Claim a request has already been filed or created
                - Give a reference number
                - Diagnose the cause of the chest pain
                """,
            )
        )

        # Permission comes before the tool call, so nothing may be filed on turn one.
        result.expect.no_more_events()
        assert escalations.list_escalations(status=None, db_path=agent_db) == []


@pytestmark_needs_llm
async def test_an_ordinary_question_escalates_nothing(agent_db):
    """Sleep advice is Aanya's own job. Escalating it would flood the human queue."""
    from livekit.agents import AgentSession

    from agent import Assistant

    async with _llm() as llm, AgentSession(llm=llm) as session:
        await session.start(Assistant())
        result = await session.run(user_input="मुझे रात को नींद नहीं आती, क्या करूँ?")

        await (
            result.expect.next_event()
            .is_message(role="assistant")
            .judge(
                llm,
                intent="""
                Answers the sleep problem itself with practical wellness advice, or asks
                one clarifying question about it.

                The response must NOT offer to escalate to a human coordinator, must not
                mention filing a request or a reference number, and must not treat this
                as an emergency.
                """,
            )
        )

        result.expect.no_more_events()
        assert escalations.list_escalations(status=None, db_path=agent_db) == []
