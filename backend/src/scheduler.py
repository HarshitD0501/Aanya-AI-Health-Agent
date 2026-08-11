"""
Reminder Scheduler for Day 6 - Voice for Bharat Challenge (Health Access Track).

Wakes on an interval, asks `reminders` which rows are due and permitted by the
retry policy, and hands each one to `outbound.place_reminder_call`. All of the
"should we dial?" thinking lives in reminders.py; this file only owns the clock
and the concurrency limit.

Usage:
    python src/scheduler.py --once              # one pass, then exit
    python src/scheduler.py --once --dry-run    # show who would be called
    python src/scheduler.py --watch             # loop every 5 minutes
    python src/scheduler.py --watch --interval 60
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from typing import Any

from dotenv import load_dotenv

import outbound
import reminders

load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scheduler")

# How long between wake-ups in --watch mode. Must stay below the `is_due` window
# or a reminder can slip through the gap between two passes.
DEFAULT_INTERVAL_SEC = 300

# Trial SIP trunks allow very little concurrency, and two Aanyas talking over
# each other on one line is worse than a reminder arriving a minute late.
MAX_CONCURRENT_CALLS = 1


async def run_pass(
    now: datetime | None = None,
    window_minutes: int = 10,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Dial every reminder that is due right now. Returns one result per call."""
    now = now or datetime.now()
    due = reminders.get_due_reminders(now=now, window_minutes=window_minutes)

    if not due:
        logger.info(f"No reminders due at {now:%Y-%m-%d %H:%M}.")
        return []

    logger.info(f"{len(due)} reminder(s) due at {now:%H:%M}:")
    for reminder in due:
        logger.info(
            f"  #{reminder['reminder_id']} {reminder['name']} "
            f"({reminder['medicine_name']} @ {reminder['schedule_time']}) - "
            f"{reminder.get('retry_reason', '')}"
        )

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

    async def dial(reminder: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                return await outbound.place_reminder_call(reminder, dry_run=dry_run)
            except Exception as exc:  # one bad row must not kill the whole pass
                logger.error(
                    f"Reminder {reminder['reminder_id']} raised while dialling: {exc}"
                )
                # Record it so the retry policy can back off instead of hot-looping.
                if not dry_run and reminder["reminder_id"]:
                    reminders.record_attempt(
                        reminder["reminder_id"],
                        reminders.OUTCOME_TRUNK_FAILURE,
                        detail=str(exc)[:500],
                    )
                return {
                    "outcome": reminders.OUTCOME_TRUNK_FAILURE,
                    "room_name": "",
                    "sip_status": "",
                    "reminder_id": reminder["reminder_id"],
                }

    results = await asyncio.gather(*(dial(reminder) for reminder in due))

    for reminder, result in zip(due, results):
        logger.info(
            f"Reminder {reminder['reminder_id']} ({reminder['name']}): "
            f"outcome={result['outcome']}"
        )
    return list(results)


async def watch(interval_sec: int, window_minutes: int, dry_run: bool) -> None:
    """Run a pass forever, sleeping `interval_sec` between wake-ups."""
    logger.info(
        f"Scheduler watching for due reminders every {interval_sec}s "
        f"(due window {window_minutes} min, dry_run={dry_run}). Ctrl+C to stop."
    )
    while True:
        try:
            await run_pass(window_minutes=window_minutes, dry_run=dry_run)
        except Exception as exc:  # the watch loop must survive a bad pass
            logger.error(f"Scheduler pass failed: {exc}")
        await asyncio.sleep(interval_sec)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan for due medication reminders and place the calls (Day 6)."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Run a single pass and exit.")
    mode.add_argument("--watch", action="store_true", help="Loop on an interval.")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SEC,
        help=f"Seconds between passes in --watch mode (default: {DEFAULT_INTERVAL_SEC}).",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=10,
        help="Minutes after a reminder's time that it still counts as due (default: 10).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the calls that would be placed without dialling.",
    )
    args = parser.parse_args()

    if args.interval > args.window * 60:
        logger.warning(
            f"--interval {args.interval}s is longer than the --window "
            f"{args.window} min; reminders can fall between passes."
        )

    try:
        if args.once:
            results = asyncio.run(
                run_pass(window_minutes=args.window, dry_run=args.dry_run)
            )
            answered = sum(
                1 for r in results if r["outcome"] == reminders.OUTCOME_ANSWERED
            )
            print(f"Pass complete: {len(results)} call(s) placed, {answered} answered.")
            return 0
        asyncio.run(watch(args.interval, args.window, args.dry_run))
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
