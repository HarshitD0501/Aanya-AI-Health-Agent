"""
SIP answer-signal probe - diagnostic only, not part of the reminder flow.

Dials a destination with NO agent in the room and dumps every attribute LiveKit
reports for the SIP participant, once a second. The question it answers: when you
physically pick up, does `sip.callStatus` become 'active'?

That matters because the agent refuses to speak until it does. If the status
never leaves 'dialing' on a call you answered, the pickup signal is broken at the
trunk or carrier level and no agent-side code can fix it. That is exactly what
this project's Twilio trunk does, and the reason the SIP-account (Linphone) path
in sip_target.py exists - run this against both to compare them.

Usage:
    uv run python src/sip_probe.py --to +919454535137
    uv run python src/sip_probe.py --to aanya-demo@sip.linphone.org
    uv run python src/sip_probe.py --to +919454535137 --wait-until-answered

Answer the call while it runs, then paste the whole output back.
"""

import argparse
import asyncio
import sys
import time
from datetime import timedelta

from dotenv import load_dotenv
from livekit import api

import sip_target

load_dotenv(".env.local")

WATCH_SEC = 60


def _stamp(started: float) -> str:
    return f"[{time.monotonic() - started:5.1f}s]"


async def probe(to_number: str, wait_until_answered: bool) -> int:
    try:
        target = sip_target.resolve_dial_target(to_number)
    except sip_target.SipConfigError as exc:
        print(f"SIP configuration error:\n  {exc}", file=sys.stderr)
        return 1

    room_name = f"sip-probe-{int(time.time())}"
    started = time.monotonic()
    print(f"Room: {room_name}")
    print(sip_target.describe_routing())
    if target.note:
        print(f"Note: {target.note}.")
    print(
        f"Trunk: {target.trunk_id or 'inline (from .env.local)'}   "
        f"From: {target.caller_id or '(trunk default)'}"
    )
    print(f"Dialing {target.label} - ANSWER IT, then watch the status lines.\n")

    async with api.LiveKitAPI() as lkapi:
        request = api.CreateSIPParticipantRequest(
            room_name=room_name,
            participant_name="probe",
            wait_until_answered=wait_until_answered,
            ringing_timeout=timedelta(seconds=WATCH_SEC),
        )
        sip_target.apply_to_request(request, target)
        identity = target.identity

        try:
            info = await lkapi.sip.create_sip_participant(request)
            print(f"{_stamp(started)} create_sip_participant returned:")
            print(f"    participant_id       = {info.participant_id}")
            print(f"    participant_identity = {info.participant_identity}")
            print(f"    sip_call_id          = {getattr(info, 'sip_call_id', '')}")
            print(f"    room_name            = {info.room_name}\n")
        except Exception as exc:
            print(f"{_stamp(started)} DIAL FAILED: {type(exc).__name__}: {exc}")
            return 2

        # Poll room state rather than trusting a single response: this is exactly
        # what the real dialer does, so the probe sees what it sees.
        last_dump = ""
        saw_active = False
        deadline = time.monotonic() + WATCH_SEC

        while time.monotonic() < deadline:
            try:
                listed = await lkapi.room.list_participants(
                    api.ListParticipantsRequest(room=room_name)
                )
            except Exception as exc:
                print(f"{_stamp(started)} list_participants failed: {exc}")
                await asyncio.sleep(1.0)
                continue

            found = False
            for participant in listed.participants:
                if participant.identity != identity:
                    continue
                found = True
                attributes = dict(participant.attributes or {})
                dump = (
                    f"state={api.ParticipantInfo.State.Name(participant.state)} "
                    f"kind={participant.kind} "
                    f"tracks={len(participant.tracks)} "
                    f"disconnect_reason={participant.disconnect_reason} "
                    f"attributes={attributes}"
                )
                if dump != last_dump:
                    print(f"{_stamp(started)} {dump}")
                    last_dump = dump
                if attributes.get("sip.callStatus") == "active":
                    saw_active = True

            if not found and last_dump:
                print(f"{_stamp(started)} participant gone from the room.")
                break

            await asyncio.sleep(1.0)

        print()
        if saw_active:
            print("RESULT: sip.callStatus reached 'active' - the pickup signal works.")
        else:
            print(
                "RESULT: sip.callStatus NEVER reached 'active'. If you answered the "
                "call, LiveKit was never told - the answer signal is missing "
                "upstream (trunk or carrier), not in the agent."
            )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the SIP answer signal.")
    parser.add_argument(
        "--to",
        required=True,
        help="Destination: E.164 number, or a SIP account like user@sip.linphone.org.",
    )

    parser.add_argument(
        "--wait-until-answered",
        action="store_true",
        help="Let LiveKit block until pickup instead of returning immediately.",
    )
    args = parser.parse_args()
    return asyncio.run(probe(args.to, args.wait_until_answered))


if __name__ == "__main__":
    sys.exit(main())
