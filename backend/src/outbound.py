"""
Outbound Dialer for Day 6 - Voice for Bharat Challenge (Health Access Track).

Places a medication reminder call: dispatches Aanya into a fresh room, then dials
the caller in as a SIP participant so both meet in the same LiveKit room.

The destination is resolved by sip_target, so the same code rings a PSTN number
through a Twilio/Plivo/Telnyx trunk or a plain SIP account (Linphone) with no
edits here - only .env.local changes. See sip_target.py for why the SIP path
exists: this project's Twilio trunk never reports a pickup, and its trial account
will only dial Verified Caller IDs.

Usage:
    python src/outbound.py --reminder-id 3
    python src/outbound.py --to +919999999999 --medicine Metformin --name Ramesh
    python src/outbound.py --to sip:aanya-demo@sip.linphone.org --medicine Metformin
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
import sip_target

load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("outbound")

AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "Aanya")


# How long we let the phone ring before giving up and calling it a no-answer.
RINGING_TIMEOUT_SEC = 30

# The synchronous dial holds one HTTP request open for the whole ring, so its
# transport timeout has to outlive the ring itself with room to spare.
DIAL_HTTP_TIMEOUT_SEC = RINGING_TIMEOUT_SEC + 20

# We ALSO poll the room attributes alongside the synchronous dial. See
# _resolve_answer for why neither signal alone is trustworthy on this trunk.
ANSWER_POLL_INTERVAL_SEC = 1.0

# Written into the room metadata the instant the carrier answers, so the agent
# (which cannot see this process) knows it may start speaking. agent.py reads the
# same key - keep the two in sync.
ANSWERED_METADATA_KEY = "sip_answered"

# A synchronous dial that returns faster than this did not wait for a human.
#
# Guard against a trunk or SDK where `wait_until_answered` is a no-op: it would
# return at once, we would announce an answer, and the opening would play into a
# ringing line - the exact bug this whole path exists to prevent. Even the fastest
# real pickup needs a ringback cycle first, so anything under two seconds is the
# flag being ignored, not a fast human.
MIN_PLAUSIBLE_ANSWER_SEC = 0.5

# SIP status codes mapped onto our normalized outcomes. 486/603 mean the callee
# actively pressed decline; 408/480 mean the line was never picked up.
SIP_STATUS_OUTCOMES: dict[str, str] = {
    "486": reminders.OUTCOME_REJECTED,
    "603": reminders.OUTCOME_REJECTED,
    "408": reminders.OUTCOME_NO_ANSWER,
    "480": reminders.OUTCOME_NO_ANSWER,
    "487": reminders.OUTCOME_NO_ANSWER,
}


def is_transport_hiccup(error: BaseException) -> bool:
    """True when a dial failed at the HTTP layer, not at the carrier.

    This is the distinction that made `wait_until_answered=True` unusable before:
    holding one request open for a 30-second ring means Windows can abort it with
    WinError 1236 ("the network connection was aborted by the local system") while
    the phone is ringing perfectly normally. Classifying that as a trunk failure
    reports a broken trunk on a call the callee is about to answer.

    A carrier rejection carries a SIP status code and is NOT a hiccup, so those
    still reach classify_dial_error untouched.
    """
    if isinstance(error, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    text = str(error).lower()
    if any(code in text for code in SIP_STATUS_OUTCOMES):
        return False
    return any(
        marker in text
        for marker in (
            "1236",
            "winerror",
            "connection aborted",
            "connection reset",
            "connection closed",
            "server disconnected",
            "cannot connect",
            "timed out",
        )
    )


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


async def _poll_room_for_answer(
    lkapi: "api.LiveKitAPI",
    room_name: str,
    identity: str,
    timeout: float = RINGING_TIMEOUT_SEC,
) -> tuple[str, str]:
    """Poll the room attributes until the callee answers, hangs up or times out.

    Returns (state, sip_status) where state is 'answered', 'hangup' or 'timeout'.

    This used to be the only answer signal. It is now the *secondary* one: on this
    project's Twilio Elastic SIP trunk `sip.callStatus` stays 'dialing' for the
    whole life of a call that was genuinely answered, so a poll-only gate reports
    a no-answer on every single call. It is kept because it is the only signal
    that distinguishes an explicit decline (486/603) from a ring that timed out.
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


async def _announce_answer(lkapi: "api.LiveKitAPI", room_name: str) -> None:
    """Tell the agent, via room metadata, that the carrier answered.

    The agent runs in a different process and cannot see this one's return value.
    Room metadata is the one channel it is already connected to. A failure here is
    logged, not raised: the agent's own attribute watch and its 'speak anyway'
    fallback still cover the call, so a metadata write must never abort a live
    conversation.
    """
    payload = json.dumps({ANSWERED_METADATA_KEY: True, "answered_at": time.time()})
    try:
        await lkapi.room.update_room_metadata(
            api.UpdateRoomMetadataRequest(room=room_name, metadata=payload)
        )
        logger.info("Told the agent the call was answered (room metadata).")
    except Exception as e:
        logger.warning(f"Could not publish the answer to room metadata: {e}")


async def _report_agent_presence(
    lkapi: "api.LiveKitAPI", room_name: str, callee_identity: str
) -> bool:
    """Log whether anyone but the callee is in the room. Diagnostic only.

    A call can be answered perfectly - 200 OK, metadata relayed, every log line
    green - and still be silent for one mundane reason this terminal previously
    could not show: no worker ever took the dispatch, so there is nobody in the
    room to speak. From here that is indistinguishable from an agent bug, which
    is exactly the confusion this check exists to end.
    """
    try:
        listed = await lkapi.room.list_participants(
            api.ListParticipantsRequest(room=room_name)
        )
    except Exception as e:
        # Never imply the agent is missing on the strength of a failed read.
        logger.debug(f"Could not list participants to check for the agent: {e}")
        return True

    others = [p.identity for p in listed.participants if p.identity != callee_identity]
    if others:
        logger.info(f"Agent is in the room: {', '.join(others)}.")
        return True

    logger.error(
        f"NOBODY IN THE ROOM BUT THE CALLEE: the dispatch for '{AGENT_NAME}' was "
        "never picked up, so this call will be silent whatever the agent code "
        "does. Start the worker in a second terminal with "
        "`uv run python src/agent.py dev` and confirm it logs 'registered worker' "
        f"with agent_name '{AGENT_NAME}'."
    )
    return False


async def _dial_and_wait_for_answer(
    lkapi: "api.LiveKitAPI",
    request: "api.CreateSIPParticipantRequest",
    room_name: str,
    identity: str,
) -> tuple[str, str, Optional[Any], Optional[Exception]]:
    """Dial synchronously and race that against an attribute poll.

    Returns (state, sip_status, participant_info, dial_error).

    Two signals run concurrently because on this trunk each one fails differently:

    - `wait_until_answered=True` returns on the carrier's SIP 200 OK. This is the
      documented answer gate and the ONLY signal that fires on this trunk, but it
      is a single long-lived HTTP request that a flaky local network can abort
      mid-ring (WinError 1236).
    - The attribute poll survives a dropped connection and is the only way to see
      an explicit decline, but `sip.callStatus` never reaches 'active' here.

    Whichever resolves first wins. A transport hiccup on the dial does not end the
    call - the poll keeps running, because the phone is still ringing.
    """
    request.wait_until_answered = True
    dial_started = time.monotonic()

    dial = asyncio.create_task(
        lkapi.sip.create_sip_participant(request, timeout=DIAL_HTTP_TIMEOUT_SEC),
        name="sip_dial",
    )
    poll = asyncio.create_task(
        _poll_room_for_answer(lkapi, room_name, identity),
        name="answer_poll",
    )

    participant_info: Optional[Any] = None
    dial_error: Optional[Exception] = None
    pending = {dial, poll}

    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )

            if dial in done:
                try:
                    participant_info = dial.result()
                    elapsed = time.monotonic() - dial_started
                    if elapsed >= MIN_PLAUSIBLE_ANSWER_SEC:
                        logger.info(
                            f"Dial returned after {elapsed:.1f}s: the carrier "
                            "answered (SIP 200 OK)."
                        )
                        return "answered", "200", participant_info, None
                    # Too fast to be a pickup: the flag was ignored and this is
                    # just "dial accepted". Fall back to the attribute poll rather
                    # than speaking into a ringing line.
                    logger.warning(
                        f"Dial returned in {elapsed:.1f}s - too fast for a real "
                        "pickup, so wait_until_answered was not honoured. Falling "
                        "back to the attribute poll for the answer."
                    )
                except Exception as exc:
                    if not is_transport_hiccup(exc):
                        # A real carrier rejection - the ring is over, stop polling.
                        return "dial_failed", "", None, exc
                    # The connection died, not the call. Let the poll carry on.
                    dial_error = exc
                    logger.warning(
                        f"Dial request dropped mid-ring ({type(exc).__name__}); "
                        "still watching the room for the answer."
                    )

            if poll in done:
                state, sip_status = poll.result()
                if state == "answered":
                    logger.info("Room attributes reported the answer.")
                    return "answered", sip_status, participant_info, None
                # Ring is over per the attributes. If the dial is still open it
                # cannot tell us anything new, so report what the poll saw.
                if dial.done() or state == "hangup":
                    return state, sip_status, participant_info, dial_error
                # The poll timed out but the dial is still live: give the dial the
                # remaining time, since it is the signal that actually works here.
                logger.info(
                    "Attribute poll timed out; waiting on the dial itself, which is "
                    "the reliable answer signal on this trunk."
                )
                pending = {dial}
    finally:
        for task in (dial, poll):
            if not task.done():
                task.cancel()

    return "timeout", "", participant_info, dial_error


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
        logger.info(f"[dry-run] {sip_target.describe_routing()}")
        try:
            target = sip_target.resolve_dial_target(phone_number)
            logger.info(f"[dry-run] Destination resolves to {target.label}.")
        except sip_target.SipConfigError as exc:
            logger.info(f"[dry-run] Destination would NOT be dialable: {exc}")
        logger.info(f"[dry-run] Job metadata: {build_call_metadata(reminder)}")
        return {"outcome": "dry_run", "room_name": room_name, "sip_status": ""}

    # Resolved before the dispatch: a misconfiguration must not leave a job waiting
    # in an empty room for a call that was never going to be placed.
    target = sip_target.resolve_dial_target(phone_number)
    logger.info(sip_target.describe_routing())
    if target.note:
        logger.warning(f"Routing note: {target.note}.")

    metadata = build_call_metadata(reminder)
    started_at = time.monotonic()

    async with api.LiveKitAPI() as lkapi:
        # 1. Boot Aanya into the room first, so she is already connected and
        #    listening the moment the answer signal arrives. Dispatching after the
        #    answer would add a second of silence to every answered call.
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
        logger.info(f"Dispatched agent '{AGENT_NAME}' into room '{room_name}'.")

        # 2. Ring the caller, watching both answer signals at once.
        request = api.CreateSIPParticipantRequest(
            room_name=room_name,
            participant_name=reminder["name"],
            # protobuf Duration, not an int - passing a bare int raises
            # "Fail to convert to Duration" before the dial is even attempted.
            ringing_timeout=timedelta(seconds=RINGING_TIMEOUT_SEC),
        )
        # Trunk, destination, caller ID and identity all come from the resolved
        # target: a PSTN call and a SIP-account call differ only in these fields.
        sip_target.apply_to_request(request, target)

        logger.info(f"Ringing {target.label}...")
        state, sip_status, participant, dial_error = await _dial_and_wait_for_answer(
            lkapi, request, room_name, target.identity
        )


        if state == "dial_failed" and dial_error is not None:
            outcome, sip_status = classify_dial_error(dial_error)
            logger.warning(
                f"Call to {target.label} failed: outcome={outcome}, "
                f"sip_status={sip_status or 'unknown'} ({dial_error})"
            )
            reminders.record_attempt(
                reminder_id,
                outcome,
                sip_status=sip_status,
                detail=str(dial_error)[:500],
            )
            return {"outcome": outcome, "room_name": room_name, "sip_status": sip_status}

        if state != "answered":
            # A hangup while still ringing is a decline or a missed call; the SIP
            # status tells us which. No status at all means the ring timed out.
            outcome = SIP_STATUS_OUTCOMES.get(sip_status, reminders.OUTCOME_NO_ANSWER)
            logger.warning(
                f"Call to {target.label} not answered: state={state}, "
                f"sip_status={sip_status or 'none'}, outcome={outcome}"
            )
            reminders.record_attempt(
                reminder_id,
                outcome,
                sip_status=sip_status,
                detail=f"Ring ended without an answer: {state}",
            )
            return {"outcome": outcome, "room_name": room_name, "sip_status": sip_status}

        # 3. Answered. Hand the answer to the agent immediately - it is waiting on
        #    exactly this before it speaks, and every millisecond here is silence
        #    in the callee's ear.
        await _announce_answer(lkapi, room_name)

        # Then say out loud whether there is anyone there to hear it.
        await _report_agent_presence(lkapi, room_name, target.identity)

    ring_duration = time.monotonic() - started_at
    identity = getattr(participant, "participant_identity", target.identity)
    logger.info(f"{reminder['name']} answered after {ring_duration:.1f}s ({identity}).")

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
    print(f"{'ID':<4} {'Name':<12} {'Medicine':<16} {'Time':<7} {'Destination':<28} Status")
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
            f"{row['phone_number'][:27]:<28} {status}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Place a medication reminder call (Day 6)."
    )
    parser.add_argument("--reminder-id", type=int, help="Dial an existing reminder by ID.")
    parser.add_argument(
        "--to",
        help=(
            "Where to ring: a phone number in E.164 (+919999999999) or a SIP "
            "account (aanya-demo@sip.linphone.org)."
        ),
    )
    parser.add_argument("--name", help="Caller's name, used in the opening.")
    parser.add_argument("--medicine", help="Medicine name to remind about.")
    parser.add_argument("--dosage", help="Dosage, e.g. '1 tablet after dinner'.")
    parser.add_argument("--at", help="Reminder time as HH:MM, used when registering.")
    parser.add_argument(
        "--language", default="Hindi", help="'Hindi' or 'English' (default: Hindi)."
    )
    parser.add_argument("--list", action="store_true", help="List registered reminders.")
    parser.add_argument(
        "--routing",
        action="store_true",
        help="Print where calls would be sent (trunk and provider) and exit.",
    )

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

    if args.routing:
        print(sip_target.describe_routing())
        if args.to:
            try:
                target = sip_target.resolve_dial_target(args.to)
                print(f"{args.to} -> {target.label}")
                if target.note:
                    print(f"Note: {target.note}.")
            except sip_target.SipConfigError as exc:
                print(f"{args.to} -> NOT DIALABLE: {exc}", file=sys.stderr)
                return 2
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
    try:
        sys.exit(main())
    except sip_target.SipConfigError as exc:
        # A configuration problem, not a crash: print the fix, not a traceback.
        print(f"\nSIP configuration error:\n  {exc}\n", file=sys.stderr)
        sys.exit(2)

