#!/usr/bin/env python3
"""Paper runtime; source installation and task registration are separate commands."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manual-source recovery; no automatic setup")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true")
    group.add_argument("--tick", action="store_true")
    group.add_argument("--probe-once", action="store_true")
    group.add_argument("--activate", action="store_true")
    group.add_argument("--disarm", action="store_true")
    parser.add_argument("--probe-id")
    parser.add_argument("--accept-model-charges", action="store_true")
    parser.add_argument("--chatgpt-plan-only", action="store_true")
    parser.add_argument("--confirm-paid-credits-disabled", action="store_true")
    args = parser.parse_args(argv)
    from tradingagents.paper.autopilot import (
        daily_supervisor as supervisor,
        daily_support as support,
    )

    try:
        supervisor.check_integrity(PROJECT)
        if args.probe_once:
            import re

            if not re.fullmatch(r"[0-9a-f]{32}", args.probe_id or ""):
                raise ValueError("Valid probe ID required")
            # No .env, account, vendor or model imports in a scheduled probe.
            result = {
                "status": "WINDOWS_RUNTIME_PROBE_PASSED",
                "probe_id": args.probe_id,
                "utc": datetime.now(UTC).isoformat(),
                "project": str(PROJECT),
                "manifest_sha256": support.sha(
                    (PROJECT / "KE_DAILY_SOURCE_MANIFEST.json").read_bytes()
                ),
                "model_calls": 0,
                "account_writes": 0,
                "python": sys.executable,
            }
            support.atomic_json(
                PROJECT
                / "_Koral_Equities_Source_Backups"
                / "validation"
                / ("probe-" + args.probe_id + ".json"),
                result,
            )
        elif args.disarm:
            with support.os_lock(support.home() / ".supervisor.lock"):
                cfg = support.load_config()
                cfg["armed"] = False
                support.atomic_json(support.home() / "config.json", cfg)
            result = {"status": "PAPER_SUPERVISOR_DISARMED", "account_flags_changed": False}
        elif args.activate:
            if args.accept_model_charges == args.chatgpt_plan_only:
                raise ValueError("Choose --chatgpt-plan-only or explicit --accept-model-charges")
            if args.chatgpt_plan_only and not args.confirm_paid_credits_disabled:
                raise ValueError(
                    "Confirm other apps cannot spend credits in ChatGPT settings first"
                )
            digest = support.sha((PROJECT / "KE_DAILY_SOURCE_MANIFEST.json").read_bytes())
            for filename, status in [
                ("tests.json", "OFFLINE_WINDOWS_TESTS_PASSED"),
                ("scheduler.json", "WINDOWS_SCHEDULER_PROBE_PASSED"),
            ]:
                proof = support.read_json(
                    PROJECT / "_Koral_Equities_Source_Backups" / "validation" / filename
                )
                if proof.get("status") != status or proof.get("manifest_sha256") != digest:
                    raise ValueError("Current source has not passed " + filename)
            with support.os_lock(support.home() / ".supervisor.lock"):
                result = supervisor.arm(PROJECT, plan_only=args.chatgpt_plan_only)
        elif args.status:
            result = supervisor.status(PROJECT)
            result["scheduler_status"] = "NOT_INSPECTED_BY_THIS_COMMAND"
            result["release"] = "RECOVERY_2026_11_12_SEPARATE_ACTIVATION"
        else:
            try:
                from dotenv import load_dotenv

                load_dotenv(PROJECT / ".env", override=False)
            except ImportError:
                pass
            day = datetime.now(UTC).astimezone(support.NY).date().isoformat()
            log = support.home() / "logs" / (day + "_controller.log")
            log.parent.mkdir(parents=True, exist_ok=True)
            with (
                log.open("a", encoding="utf-8") as stream,
                contextlib.redirect_stdout(stream),
                contextlib.redirect_stderr(stream),
            ):
                result = supervisor.tick(PROJECT)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "MANUAL_RECOVERY_SAFE_HALT",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:800],
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
