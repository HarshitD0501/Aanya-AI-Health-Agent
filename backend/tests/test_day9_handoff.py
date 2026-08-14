"""
Automated Unit Tests for Day 9 - Clinic & Appointment Specialist Handoff.

Two layers:
1. Offline store tests (`appointments.py` and tools) - booking, reference generation
   (APT-2001 pattern), scrubbing private secrets from reasons, fetching by ID/reference,
   spoken reference formatting in Hindi and English, and handoff tools.
2. Step 6 conversation tests (needs LLM) - asserting that an ordinary health question
   does not hand off and ends at no_more_events(), while a PHC/clinic lookup request
   triggers an agent handoff to ClinicAppointmentSpecialist (is_agent_handoff).
"""

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

load_dotenv(Path(__file__).parent.parent / ".env.local")

import appointments  # noqa: E402
import db  # noqa: E402
import escalations  # noqa: E402
from agent import (  # noqa: E402
    Assistant,
    ClinicAppointmentSpecialist,
    HealthAgentBase,
    _spoken_reference,
)


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_day9.db"
    db.init_db(db_file)
    return db_file


def _make_appointment(test_db, **overrides):
    """Book one appointment in test DB."""
    kwargs = {
        "caller_name": "Harshit",
        "phone_number": "+919454535137",
        "clinic_name": "Civil Hospital Lucknow",
        "appointment_date": "2026-08-16",
        "appointment_time": "10:30 AM",
        "reason": "Routine blood pressure checkup and wellness consultation.",
    }
    kwargs.update(overrides)
    return appointments.book_appointment(db_path=test_db, **kwargs)


class _MockSession:
    def __init__(self, userdata: dict[str, Any] | None = None):
        self.userdata = userdata or {}
        self.closed = False

    async def aclose(self):
        self.closed = True


class _MockRoom:
    def __init__(self):
        self.disconnected = False

    async def disconnect(self):
        self.disconnected = True


class _ToolContext:
    def __init__(self, userdata: dict[str, Any] | None = None):
        self.session = _MockSession(userdata)
        self.room = _MockRoom()


# ---------------------------------------------------------------------------
# Offline Store Tests
# ---------------------------------------------------------------------------


def test_appointment_round_trip(test_db):
    """Verify an appointment stores and returns all expected fields."""
    row = _make_appointment(test_db)
    assert row is not None
    assert row["appointment_id"] > 0
    assert row["caller_name"] == "Harshit"
    assert row["phone_number"] == "+919454535137"
    assert row["clinic_name"] == "Civil Hospital Lucknow"
    assert row["appointment_date"] == "2026-08-16"
    assert row["appointment_time"] == "10:30 AM"
    assert "blood pressure" in row["reason"].lower()
    assert row["created_at"] != ""


def test_appointment_reference_id_pattern(test_db):
    """Verify reference_id follows APT-{2000 + id} pattern and is fetchable."""
    first = _make_appointment(test_db)
    second = _make_appointment(test_db, caller_name="Ramesh")

    assert first["reference_id"] == "APT-2001"
    assert second["reference_id"] == "APT-2002"

    fetched1 = appointments.get_appointment("APT-2001", db_path=test_db)
    assert fetched1 is not None
    assert fetched1["caller_name"] == "Harshit"

    fetched2 = appointments.get_appointment("apt-2002", db_path=test_db)
    assert fetched2 is not None
    assert fetched2["caller_name"] == "Ramesh"


def test_appointment_scrubs_secrets_from_reason(test_db):
    """Verify OTP, PIN, card numbers and long digit runs are scrubbed."""
    row = _make_appointment(
        test_db,
        reason="Follow up visit. My OTP is 4821 and Aadhaar number is 9876 5432 1098.",
    )
    assert row is not None
    assert "4821" not in row["reason"]
    assert "9876" not in row["reason"]
    assert "[redacted]" in row["reason"]


def test_missing_mandatory_fields_refuses_booking(test_db):
    """Verify missing fields return None."""
    assert (
        appointments.book_appointment(
            caller_name="",
            phone_number="+919454535137",
            clinic_name="PHC Chandanpur",
            appointment_date="2026-08-16",
            appointment_time="10:00 AM",
            reason="Checkup",
            db_path=test_db,
        )
        is None
    )


def test_list_appointments_newest_first(test_db):
    """Verify list_appointments returns newest appointments first."""
    _make_appointment(test_db, caller_name="First")
    _make_appointment(test_db, caller_name="Second")
    rows = appointments.list_appointments(db_path=test_db)
    assert len(rows) == 2
    assert rows[0]["caller_name"] == "Second"
    assert rows[1]["caller_name"] == "First"


def test_spoken_reference_with_apt_prefix():
    """Verify _spoken_reference supports APT prefix in Hindi and English."""
    assert _spoken_reference("APT-2001", "Hindi") == "ए पी टी दो शून्य शून्य एक"
    assert _spoken_reference("APT-2001", "English") == "A P T two zero zero one"
    assert _spoken_reference("HLP-1001", "Hindi") == "एच एल पी एक शून्य शून्य एक"


# ---------------------------------------------------------------------------
# Agent and Tool Architecture Tests
# ---------------------------------------------------------------------------


def test_class_hierarchy():
    """Verify Assistant and ClinicAppointmentSpecialist inherit from HealthAgentBase."""
    assert issubclass(Assistant, HealthAgentBase)
    assert issubclass(ClinicAppointmentSpecialist, HealthAgentBase)


@pytest.mark.asyncio
async def test_transfer_tool_returns_specialist_with_clean_context():
    """Verify transfer_to_clinic_specialist returns ClinicAppointmentSpecialist with copied context."""
    assistant = Assistant()
    ctx = _ToolContext()
    result = await assistant.transfer_to_clinic_specialist(ctx)

    assert isinstance(result, tuple)
    new_agent, message = result
    assert isinstance(new_agent, ClinicAppointmentSpecialist)
    assert "transfer" in message.lower()


@pytest.mark.asyncio
async def test_return_to_health_advisor_returns_assistant():
    """Verify return_to_health_advisor transfers back to Assistant."""
    specialist = ClinicAppointmentSpecialist()
    ctx = _ToolContext()
    result = await specialist.return_to_health_advisor(ctx)

    assert isinstance(result, tuple)
    new_agent, message = result
    assert isinstance(new_agent, Assistant)
    assert "aanya" in message.lower() or "transfer" in message.lower()


# ---------------------------------------------------------------------------
# Step 6 Judged Conversation Tests
# ---------------------------------------------------------------------------

pytestmark_needs_llm = pytest.mark.skipif(
    not os.getenv("LIVEKIT_API_KEY"),
    reason="Judged conversation tests need LIVEKIT_API_KEY for inference.",
)


def _llm():
    from livekit.agents import inference

    return inference.LLM(model="openai/gpt-4.1-mini")


@pytestmark_needs_llm
@pytest.mark.asyncio
async def test_medicine_question_does_not_hand_off():
    """A medication / wellness question is answered directly by Aanya and ends without handoff."""
    from livekit.agents import AgentSession

    async with _llm() as llm, AgentSession(llm=llm) as session:
        await session.start(Assistant())
        result = await session.run(
            user_input="मुझे सिरदर्द है, मैं आराम करने के लिए क्या कर सकता हूँ?"
        )

        # First event is an assistant message with wellness advice
        await (
            result.expect.next_event()
            .is_message(role="assistant")
            .judge(
                llm,
                intent="""
                Provides general wellness or lifestyle advice for headache relief (such as rest, hydration, quiet room, or clarifying questions).
                The response is standard health advice given directly by the advisor in conversational Hindi.
                """,
            )
        )

        # Ends with no further handoff events
        result.expect.no_more_events()


@pytestmark_needs_llm
@pytest.mark.asyncio
async def test_phc_request_triggers_agent_handoff():
    """A PHC or hospital lookup request triggers transfer to ClinicAppointmentSpecialist."""
    from livekit.agents import AgentSession

    async with _llm() as llm, AgentSession(llm=llm) as session:
        await session.start(Assistant())
        result = await session.run(
            user_input="मुझे मेरे नजदीकी प्राथमिक स्वास्थ्य केंद्र (PHC) के बारे में जानना है, क्या आप बता सकती हैं?"
        )

        # Asserts agent handoff to ClinicAppointmentSpecialist
        result.expect.contains_agent_handoff(new_agent_type=ClinicAppointmentSpecialist)
