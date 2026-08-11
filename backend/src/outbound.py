"""
Outbound Dialer for Day 6 - Voice for Bharat Challenge (Health Access Track).

Places a medication reminder call: dispatches Aanya into a fresh room, then dials
the caller in as a SIP participant so both meet in the same LiveKit room.

The trunk is read from the environment, so the same code works against a Twilio,
Plivo or Telnyx trunk with no edits - only .env.local changes.

Usage:
    python src/outbound.py --reminder-id 3
    python src/outbound.py --to +919999999999 --medicine Metformin --name Ramesh
    python src/outbound.py --list
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import timedelta
from typing import Any, Optional

from dotenv import load_dotenv
from livekit import api

import reminders

load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("outbound")

AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "Aanya")
SIP_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID", "")
SIP_FROM_NUMBER = os.getenv("SIP_FROM_NUMBER", "")

# How long we let the phone ring before giving up and calling it a no-answer.
RINGING_TIMEOUT_SEC = 30

# We poll the room for the answer instead of holding one HTTP request open for
# the whole ringing period. See _wait_for_answer for why.
ANSWER_POLL_INTERVAL_SEC = 1.0

# SIP status codes mapped onto our normalized outcomes. 486/603 mean the callee
# actively pressed decline; 408/480 mean the line was never picked up.
SIP_STATUS_OUTCOMES: dict[str, str] = {
    "486": reminders.OUTCOME_REJECTED,
    "603": reminders.OUTCOME_REJECTED,
    "408": reminders.OUTCOME_NO_ANSWER,
    "480": reminders.OUTCOME_NO_ANSWER,
    "487": reminders.OUTCOME_NO_ANSWER,
}


def classify_dial_error(error: Exception) -> tuple[str, str]:
    """Map a failed dial onto (outcome, sip_status).

    Note: livekit-api 1.1.0 has no SipCallError class despite what the docs
    show, so we inspect the TwirpError message for a SIP status code instead.
    """
    message = str(error)
    for code, outcome in SIP_STATUS_OUTCOMES.items():
        if code in message:
            return outcome, code

    lowered = message.lower()
    if "unavailable" in lowered or "no answer" in lowered or "timeout" in lowered:
        return reminders.OUTCOME_NO_ANSWER, ""
    if "busy" in lowered or "reject" in lowered or "declin" in lowered:
        return reminders.OUTCOME_REJECTED, ""
    return reminders.OUTCOME_TRUNK_FAILURE, ""


def build_call_metadata(reminder: dict[str, Any]) -> str:
    """Serialize the reminder into job metadata the agent reads on pickup.

    Only the fields Aanya needs to open the call are included - no health facts
    travel over metadata.
    """
    return json.dumps(
        {
            "call_type": "medication_reminder",
            "reminder_id": reminder["reminder_id"],
            "user_id": reminder["user_id"],
            "name": reminder["name"],
            "phone_number": reminder["phone_number"],
            "medicine_name": reminder["medicine_name"],
            "dosage": reminder.get("dosage", ""),
            "schedule_time": reminder["schedule_time"],
            "language_preference": reminder.get("language_preference", "Hindi"),
        },
        ensure_ascii=False,
    )


async def _wait_for_answer(
    lkapi: "api.LiveKitAPI",
    room_name: str,
    identity: str,
    timeout: float = RINGING_TIMEOUT_SEC,
) -> tuple[str, str]:
    """Poll the room until the dialled participant answers, hangs up or times out.

    Returns (state, sip_status) where state is 'answered', 'hangup' or 'timeout'.

    This deliberately replaces create_sip_participant(wait_until_answered=True).
    That flag holds a single HTTP request open for the entire ringing period, and
    on an unstable connection Windows aborts it mid-ring with WinError 1236 - which
    we then had to classify as a trunk failure even though the phone was ringing
    normally. A sequence of one-second polls survives a connection that drops.
    """
    deadline = time.monotonic() + timeout
    last_status = ""
    while time.monotonic() < deadline:
        try:
            listed = await lkapi.room.list_participants(
                api.ListParticipantsRequest(room=room_name)
            )
        except Exception as e:
            # A failed poll is not a failed call - the ring is server-side.
            logger.debug(f"Answer poll failed, retrying: {e}")
            await asyncio.sleep(ANSWER_POLL_INTERVAL_SEC)
            continue

        for participant in listed.participants:
            if participant.identity != identity:
                continue
            attributes = dict(participant.attributes or {})
            status = attributes.get("sip.callStatus", "")
            sip_status = attributes.get("sip.callStatusCode", "")
            if status and status != last_status:
                logger.info(f"SIP call status: {status}")
                last_status = status
            if status == "active":
                return "answered", sip_status or "200"
            if status == "hangup":
                return "hangup", sip_status

        await asyncio.sleep(ANSWER_POLL_INTERVAL_SEC)

    return "timeout", ""


async def place_reminder_call(
    reminder: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Dispatch Aanya into a room and dial the caller into it.

    Returns a result dict with the outcome, which is also written to the attempt
    log so the retry policy can read it on the next scheduler pass.
    """
    reminder_id = reminder["reminder_id"]
    phone_number = reminder["phone_number"]
    room_name = f"reminder-{reminder_id}-{int(time.time())}"

    # Checked before the trunk, so --dry-run works with no telephony configured.
    if dry_run:
        logger.info(
            f"[dry-run] Would call {reminder['name']} at {phone_number} "
            f"about {reminder['medicine_name']} (room={room_name})."
        )
        logger.info(f"[dry-run] Job metadata: {build_call_metadata(reminder)}")
        return {"outcome": "dry_run", "room_name": room_name, "sip_status": ""}

    if not SIP_TRUNK_ID:
        raise RuntimeError(
            "SIP_OUTBOUND_TRUNK_ID is not set in .env.local. Create an outbound "
            "trunk first:  lk sip outbound create outbound-trunk.json"
        )

    metadata = build_call_metadata(reminder)
    started_at = time.monotonic()

    async with api.LiveKitAPI() as lkapi:
        # 1. Boot Aanya into the room first, so she is listening the moment the
        #    callee picks up. Dispatching after the dial would clip the opening.
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
        logger.info(f"Dispatched agent '{AGENT_NAME}' into room '{room_name}'.")

        # 2. Ring the caller. We return from this call as soon as the dial is
        #    accepted and then poll for the answer, so a dropped connection
        #    during a long ring cannot be mistaken for a dial failure.
        request = api.CreateSIPParticipantRequest(
            sip_trunk_id=SIP_TRUNK_ID,
            sip_call_to=phone_number,
            room_name=room_name,
            participant_identity=phone_number,
            participant_name=reminder["name"],
            wait_until_answered=False,
            # protobuf Duration, not an int - passing a bare int raises
            # "Fail to convert to Duration" before the dial is even attempted.
            ringing_timeout=timedelta(seconds=RINGING_TIMEOUT_SEC),
        )
        if SIP_FROM_NUMBER:
            request.sip_number = SIP_FROM_NUMBER

        try:
            participant = await lkapi.sip.create_sip_participant(request)
        except Exception as exc:  # every dial failure is classified, never raised
            outcome, sip_status = classify_dial_error(exc)
            logger.warning(
                f"Call to {phone_number} failed: outcome={outcome}, "
                f"sip_status={sip_status or 'unknown'} ({exc})"
            )
            reminders.record_attempt(
                reminder_id,
                outcome,
                sip_status=sip_status,
                detail=str(exc)[:500],
            )
            return {"outcome": outcome, "room_name": room_name, "sip_status": sip_status}

        logger.info(
            f"Ringing {phone_number} (participant={participant.participant_identity})."
        )
        state, sip_status = await _wait_for_answer(lkapi, room_name, phone_number)

        if state != "answered":
            # A hangup while still ringing is a decline or a missed call; the SIP
            # status tells us which. No status at all means the ring timed out.
            outcome = SIP_STATUS_OUTCOMES.get(sip_status, reminders.OUTCOME_NO_ANSWER)
            logger.warning(
                f"Call to {phone_number} not answered: state={state}, "
                f"sip_status={sip_status or 'none'}, outcome={outcome}"
            )
            reminders.record_attempt(
                reminder_id,
                outcome,
                sip_status=sip_status,
                detail=f"Ring ended without an answer: {state}",
            )
            return {"outcome": outcome, "room_name": room_name, "sip_status": sip_status}

    ring_duration = time.monotonic() - started_at
    logger.info(
        f"{reminder['name']} answered after {ring_duration:.1f}s "
        f"(participant={participant.participant_identity})."
    )

    # The agent owns the conversation from here. It records the final outcome
    # itself, since only the agent can tell an engaged caller from voicemail.
    return {
        "outcome": reminders.OUTCOME_ANSWERED,
        "room_name": room_name,
        "sip_status": sip_status or "200",
        "ring_duration_sec": round(ring_duration, 1),
    }


def _reminder_from_args(args: argparse.Namespace) -> Optional[dict[str, Any]]:
    """Build an ad-hoc reminder from CLI flags, for testing without a DB row."""
    if not args.to:
        return None
    return {
        "reminder_id": 0,
        "user_id": (args.name or "test_caller").strip().lower(),
        "name": args.name or "Caller",
        "phone_number": args.to,
        "medicine_name": args.medicine or "your medicine",
        "dosage": args.dosage or "",
        "schedule_time": args.at or "",
        "language_preference": args.language,
        "active": True,
        "opted_out": False,
        "attempt_count": 0,
        "last_outcome": "",
        "last_called_at": "",
    }


def _print_reminders() -> None:
    rows = reminders.list_reminders(include_inactive=True)
    if not rows:
        print("No reminders registered yet. Add one with --register.")
        return
    print(f"{'ID':<4} {'Name':<12} {'Medicine':<16} {'Time':<7} {'Phone':<16} Status")
    for row in rows:
        if row["opted_out"]:
            status = "OPTED OUT"
        elif not row["active"]:
            status = "inactive"
        else:
            status = row["last_outcome"] or "pending"
        print(
            f"{row['reminder_id']:<4} {row['name'][:11]:<12} "
            f"{row['medicine_name'][:15]:<16} {row['schedule_time']:<7} "
            f"{row['phone_number']:<16} {status}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Place a medication reminder call (Day 6)."
    )
    parser.add_argument("--reminder-id", type=int, help="Dial an existing reminder by ID.")
    parser.add_argument("--to", help="Phone number in E.164 format, e.g. +919999999999.")
    parser.add_argument("--name", help="Caller's name, used in the opening.")
    parser.add_argument("--medicine", help="Medicine name to remind about.")
    parser.add_argument("--dosage", help="Dosage, e.g. '1 tablet after dinner'.")
    parser.add_argument("--at", help="Reminder time as HH:MM, used when registering.")
    parser.add_argument(
        "--language", default="Hindi", help="'Hindi' or 'English' (default: Hindi)."
    )
    parser.add_argument("--list", action="store_true", help="List registered reminders.")
    parser.add_argument(
        "--register",
        action="store_true",
        help="Save the reminder to the database instead of dialling now.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the reminder and log the call without dialling.",
    )
    args = parser.parse_args()

    if args.list:
        _print_reminders()
        return 0

    if args.register:
        if not (args.to and args.medicine and args.at):
            parser.error("--register needs --to, --medicine and --at.")
        reminder_id = reminders.create_reminder(
            user_id=(args.name or args.to).strip().lower(),
            name=args.name or "Caller",
            phone_number=args.to,
            medicine_name=args.medicine,
            schedule_time=args.at,
            dosage=args.dosage or "",
            language_preference=args.language,
        )
        print(
            f"Registered reminder {reminder_id}: {args.medicine} for "
            f"{args.name or args.to} at {args.at} daily."
        )
        return 0

    if args.reminder_id:
        reminder = reminders.get_reminder(args.reminder_id)
        if not reminder:
            print(f"No reminder found with ID {args.reminder_id}.", file=sys.stderr)
            return 1
        if reminder["opted_out"]:
            print(
                f"Reminder {args.reminder_id} is opted out - "
                f"{reminder['name']} asked us to stop calling.",
                file=sys.stderr,
            )
            return 1
    else:
        reminder = _reminder_from_args(args)
        if not reminder:
            parser.error("Pass either --reminder-id or --to.")

    result = asyncio.run(place_reminder_call(reminder, dry_run=args.dry_run))
    print(f"Outcome: {result['outcome']}  (room: {result['room_name']})")
    return 0 if result["outcome"] in (reminders.OUTCOME_ANSWERED, "dry_run") else 2


if __name__ == "__main__":
    sys.exit(main())
