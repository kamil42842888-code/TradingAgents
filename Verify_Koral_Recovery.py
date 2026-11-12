"""Offline dependency/source verification and focused temporary-account tests.

No dependency installation, user-account imports, task setup or activation.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
TESTS = [
    "tests/test_daily_support.py",
    "tests/test_daily_paper_integration.py",
    "tests/test_recovery_release.py",
    "tests/test_daily_supervisor_release.py",
    "tests/test_daily_plan.py",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", action="store_true")
    parser.add_argument("--account-check", action="store_true")
    args = parser.parse_args()
    if args.account_check:
        if args.tests:
            raise ValueError("Account inspection and isolated tests are separate operations")
        os.environ["DOTENV_DISABLED"] = "1"
        from tradingagents.paper.autopilot.daily_supervisor import check_integrity
        from tradingagents.paper.autopilot.state import STATE, read

        check_integrity(PROJECT)
        before = STATE.read_bytes()
        state = read(STATE)
        if STATE.read_bytes() != before:
            raise ValueError("Account changed during inspection")
        print(
            json.dumps(
                {
                    "status": "EXISTING_ACCOUNT_AUDIT_AND_SOURCES_VERIFIED",
                    "sha256": hashlib.sha256(before).hexdigest(),
                    "audit_events": len(state.audit),
                    "cash_gbp": state.portfolio.book.cash_gbp,
                    "equity_gbp": state.portfolio.book.equity(),
                    "last_session": state.portfolio.book.last_session,
                    "positions": len(state.portfolio.book.positions),
                    "orders": len(state.portfolio.book.orders),
                    "auto_paper_enabled": state.auto_paper_enabled,
                    "paused": state.paused,
                },
                indent=2,
            )
        )
        return 0
    # Isolate before importing the application or pytest, including child processes.
    sandbox = Path(tempfile.mkdtemp(prefix="ke-validation-"))
    for key in list(os.environ):
        if any(word in key for word in ("API_KEY", "ACCESS_TOKEN", "SECRET_KEY")):
            del os.environ[key]
    os.environ.update(
        USERPROFILE=str(sandbox),
        HOME=str(sandbox),
        DOTENV_DISABLED="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        TEMP=str(sandbox),
        TMP=str(sandbox),
    )
    os.chdir(PROJECT)
    sys.path.insert(0, str(PROJECT))
    from tradingagents.paper.autopilot.daily_supervisor import check_integrity

    check_integrity(PROJECT)
    packages = (
        "pytest",
        "langchain-core",
        "langchain-openai",
        "langgraph",
        "python-dotenv",
        "pandas",
        "yfinance",
        "tzdata",
    )
    versions = {name: importlib.metadata.version(name) for name in packages}
    print(
        json.dumps({"status": "DEPENDENCIES_AND_SOURCE_HASHES_OK", "versions": versions}, indent=2)
    )
    if not args.tests:
        return 0
    import pytest

    result = pytest.main(
        [
            "-q",
            "--tb=short",
            "--maxfail=3",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(sandbox / "fixtures"),
            *TESTS,
        ]
    )
    if result:
        return result
    digest = hashlib.sha256((PROJECT / "KE_DAILY_SOURCE_MANIFEST.json").read_bytes()).hexdigest()
    from tradingagents.paper.autopilot.daily_support import atomic_json

    atomic_json(
        PROJECT / "_Koral_Equities_Source_Backups/validation/tests.json",
        {
            "status": "OFFLINE_WINDOWS_TESTS_PASSED" if os.name == "nt" else "OFFLINE_TESTS_PASSED",
            "manifest_sha256": digest,
            "utc": datetime.now(UTC).isoformat(),
            "test_files": TESTS,
            "versions": versions,
            "real_account_access": False,
            "paid_model_calls": 0,
        },
    )
    print("OFFLINE_TESTS_PASSED; NO_REAL_ACCOUNT_ACCESS; NO_MODEL_CALLS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
