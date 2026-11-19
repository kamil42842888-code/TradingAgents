"""Read-only V3.1 web status must not touch V1/V2/V3 book files."""
import json
from datetime import UTC, datetime, timedelta

from tradingagents.paper.autopilot.state import create_new
from tradingagents.paper.dashboard import autopilot_paper_snapshot


def test_daily_controller_status_and_account_report_are_read_only(tmp_path):
    path = tmp_path / ".tradingagents/paper/v3_autopilot/portfolio.json"
    create_new(path, "2026-10-22", ["AAPL"])
    before = path.read_bytes()
    root = tmp_path / ".tradingagents/paper_operations/daily_v1"
    root.mkdir(parents=True)
    heartbeat = root / "heartbeat.json"
    pulse = {"utc": (datetime.now(UTC) - timedelta(minutes=4)).isoformat(),
             "paper_only": True, "broker_connected": False,
             "status": "WAITING_FOR_REQUIRED_OBSERVATIONS", "phase": "WAITING_FOR_OBSERVER"}
    heartbeat.write_text(json.dumps(pulse))
    result = autopilot_paper_snapshot(path)
    assert result["heartbeat_recent"] is True
    assert result["operations"]["status"] == pulse["status"]
    assert result["performance"]["closed_trades"] == 0
    assert result["risk"]["gross_exposure_gbp"] == 0
    assert result["risk"]["limits"]["risk_fraction"] == 0.02
    assert result["closed_trade_journal"] == []
    assert path.read_bytes() == before
    pulse["utc"] = (datetime.now(UTC) - timedelta(minutes=12)).isoformat()
    heartbeat.write_text(json.dumps(pulse))
    assert not autopilot_paper_snapshot(path)["heartbeat_recent"]
    heartbeat.write_text("bad json")
    result = autopilot_paper_snapshot(path)
    assert result["status"] == "paper_ready"
    assert result["operations"]["status"] == "UNREADABLE_HEARTBEAT"
    assert result["heartbeat_recent"] is False


def test_future_or_failed_controller_never_claims_healthy(tmp_path):
    path = tmp_path / ".tradingagents/paper/v3_autopilot/portfolio.json"
    create_new(path, "2026-10-22", ["AAPL"])
    root = tmp_path / ".tradingagents/paper_operations/daily_v1"
    root.mkdir(parents=True)
    for status, offset in [("DAILY_ROSTER_FROZEN", 60), ("DAILY_CONTROLLER_FAILED_CLOSED", -1)]:
        (root / "heartbeat.json").write_text(json.dumps({
            "utc": (datetime.now(UTC) + timedelta(seconds=offset)).isoformat(),
            "paper_only": True, "broker_connected": False, "status": status,
        }))
        assert not autopilot_paper_snapshot(path)["heartbeat_recent"]


def test_missing_autopilot_ledger_is_never_auto_initialised(tmp_path):
    path=tmp_path/"v3_autopilot"/"portfolio.json"
    state=autopilot_paper_snapshot(path)
    assert state["status"]=="not_initialized"
    assert state["broker_connected"] is False
    assert not path.exists()


def test_read_only_status_is_ledger_backed_and_never_mutates(tmp_path):
    path=tmp_path/"v3_autopilot"/"portfolio.json"
    create_new(path,"2026-10-22",["AAPL","NVDA"])
    before=path.read_bytes()
    result=autopilot_paper_snapshot(path)
    assert result["status"]=="paper_ready"
    assert result["equity_gbp"]==10000.0
    assert result["positions"]==0 and result["queued"]==0
    assert result["ai_receipts"]==0
    assert result["heartbeat_recent"] is False
    assert result["watchlist"]==["AAPL","NVDA"]
    assert result["broker_connected"] is False
    assert path.read_bytes()==before


def test_journal_tampering_is_shown_as_safe_halt(tmp_path):
    path=tmp_path/"v3_autopilot"/"portfolio.json"
    create_new(path,"2026-10-22",["AAPL"])
    raw=json.loads(path.read_text())
    raw["audit"][0]["details"]["date"]="2026-10-23"
    path.write_text(json.dumps(raw))
    result=autopilot_paper_snapshot(path)
    assert result["status"]=="invalid_state"
    assert result["broker_connected"] is False


def test_stale_heartbeat_cannot_claim_running_service(tmp_path):
    path=tmp_path/"v3_autopilot"/"portfolio.json"
    create_new(path,"2026-10-22",["AAPL"])
    heartbeat=path.parent/"service_heartbeat.json"
    heartbeat.write_text(json.dumps({"last_pulse_utc":"2025-01-01T12:00:00+00:00","mode":"PAPER_ONLY"}))
    assert autopilot_paper_snapshot(path)["heartbeat_recent"] is False
    heartbeat.write_text(json.dumps({"last_pulse_utc":datetime.now(UTC).isoformat(),
                                     "mode":"PAPER_ONLY"}))
    assert autopilot_paper_snapshot(path)["heartbeat_recent"] is True
