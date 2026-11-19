"""Inspect an existing Koral installation as data; never import its application.

Run with Python -I -S -B from a separately reviewed checkout. Only a new report
directory is written. No provider, application, Git or scheduler commands run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

# Isolated mode deliberately omits the script directory. Import only these
# reviewed sibling helpers; the target checkout is never added to sys.path.
BUNDLE = Path(__file__).absolute().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(BUNDLE))

from session_evidence import audit_evidence  # noqa: E402
from snapshot import AuditReadError, Snapshot  # noqa: E402

TASK_NAMES = (
    "KoralEquities_ResearchObserver_5min",
    "KoralEquities_PaperController_5min",
)
HEX = re.compile(r"[0-9a-f]{64}\Z")
MANIFEST_AUXILIARIES = {
    "tools/recovery/build_release.py",
    "tools/recovery/release_history.json",
    "tools/recovery/restore_sources.py",
}


def instant(value: str | datetime) -> datetime:
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return moment.astimezone(UTC)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _relative(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    parts = PurePosixPath(value)
    return not parts.is_absolute() and all(p not in {".", ".."} for p in value.split("/"))


def source_report(project: Path, reader: Snapshot, reference: dict) -> dict:
    versions = reference["versions"]
    expected_paths = set().union(*(v["sha256"] for v in versions.values()))
    actual, unavailable = {}, []
    for rel in sorted(expected_paths):
        try:
            raw = reader.read(project / rel)
            actual[rel] = {"raw": _sha(raw), "normalized": _sha(raw.replace(b"\r\n", b"\n"))}
        except (ValueError, OSError):
            unavailable.append(rel)
    # An additional module could change package resolution even if every pinned
    # file is present. Enumerate application Python sources, bounded by Snapshot.
    inventory_valid = True
    try:
        application = []
        pending_directories = [(project / "tradingagents", 0)]
        while pending_directories:
            folder, depth = pending_directories.pop()
            if depth > 10:
                raise ValueError("APPLICATION_TREE_DEPTH_LIMIT")
            for entry in reader.glob(folder, "*"):
                if entry.is_dir() and entry.name != "__pycache__":
                    pending_directories.append((entry, depth + 1))
                elif entry.suffix == ".py":
                    application.append(entry)
        extra = [p for p in application if p.relative_to(project).as_posix() not in expected_paths]
    except (ValueError, OSError):
        inventory_valid, extra = False, []
    matches = []
    for label, entry in versions.items():
        if inventory_valid and not extra and all(
            actual.get(rel, {}).get("normalized") == digest
            for rel, digest in entry["sha256"].items()
        ) and not any(rel not in entry["sha256"] for rel in actual):
            matches.append(label)
    matched = matches[0] if len(matches) == 1 else None
    manifest_status = "MISSING"
    manifest_entries = 0
    manifest_path = project / "KE_DAILY_SOURCE_MANIFEST.json"
    try:
        if reader.exists(manifest_path):
            manifest = reader.json(manifest_path)
            hashes = manifest.get("sha256") if isinstance(manifest, dict) else None
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema") != "koral-daily-installed-sources-v1"
                or not isinstance(hashes, dict)
                or not 1 <= len(hashes) <= 500
                or any(
                    not _relative(rel)
                    or rel not in expected_paths | MANIFEST_AUXILIARIES
                    or not isinstance(digest, str)
                    or not HEX.fullmatch(digest)
                    for rel, digest in hashes.items()
                )
            ):
                raise ValueError("INVALID_INSTALLED_MANIFEST")
            manifest_entries = len(hashes)
            required = set(versions[matched]["sha256"]) if matched else expected_paths
            consistent = all(
                (actual[rel]["raw"] if rel in actual else _sha(reader.read(project / rel))) == digest
                for rel, digest in hashes.items()
            )
            manifest_status = (
                "SOURCE_HASH_MISMATCH" if not consistent
                else "INCOMPLETE" if not required.issubset(hashes)
                else "CONSISTENT"
            )
    except (ValueError, OSError):
        manifest_status = "INVALID_OR_UNREADABLE"
    differing = []
    for rel, digest in versions["continuation_2026-11-19"]["sha256"].items():
        if actual.get(rel, {}).get("normalized") != digest:
            differing.append(rel)
    pending = reader.exists(project / ".koral-source-transaction.json")
    return {
        "status": "MATCHES_REVIEWED_SOURCE" if matched else "UNRECOGNIZED_OR_INCOMPLETE_SOURCE",
        "matched_version": matched,
        "reference_commit": versions[matched]["commit"] if matched else None,
        "checked_runtime_files": len(expected_paths),
        "missing_or_unreadable_files": unavailable,
        "different_from_continuation": differing,
        "additional_application_modules": len(extra),
        "application_inventory_readable": inventory_valid,
        "installed_manifest": manifest_status,
        "installed_manifest_entries": manifest_entries,
        "unfinished_source_transaction": pending,
        "git_checkout_marker_present": reader.exists(project / ".git"),
        "expected_project_interpreter_present": reader.exists(project / ".venv/Scripts/python.exe"),
        "target_code_executed": False,
    }


def task_report(path: Path | None, reader: Snapshot, now: datetime) -> dict:
    if path is None:
        return {"status": "NOT_INSPECTED", "tasks": []}
    try:
        doc = reader.json(path, limit=200_000)
        if (
            not isinstance(doc, dict)
            or doc.get("schema") != "koral-windows-task-status-v1"
            or doc.get("read_only") is not True
            or doc.get("account_writes") != 0
            or doc.get("model_calls") != 0
            or doc.get("status") not in {
                "TASK_CONFIGURATION_MATCHES", "ATTENTION_REQUIRED", "INSPECTION_UNAVAILABLE"
            }
        ):
            raise ValueError("INVALID_TASK_REPORT")
        checked = instant(doc.get("checked_utc"))
        age = (now - checked).total_seconds()
        if age < 0 or age > 14 * 60:
            return {"status": "STALE_OR_FUTURE_INSPECTION", "tasks": []}
        rows = doc.get("tasks")
        if not isinstance(rows, list):
            raise ValueError("TASK_ROWS_REQUIRED")
        if doc["status"] == "INSPECTION_UNAVAILABLE":
            if rows:
                raise ValueError("UNAVAILABLE_INSPECTION_WITH_ROWS")
            return {"status": "INSPECTION_UNAVAILABLE", "tasks": [], "checked_utc": checked.isoformat()}
        if len(rows) != 2 or {r.get("name") for r in rows if isinstance(r, dict)} != set(TASK_NAMES):
            raise ValueError("EXACT_TASK_ROWS_REQUIRED")
        result = []
        allowed_findings = {
            "TASK_ABSENT", "TASK_LOOKUP_UNAVAILABLE", "TASK_ACTION_MISMATCH",
            "PROJECT_PYTHON_MISSING", "TASK_SCRIPT_MISSING", "TRIGGER_INTERVAL_MISMATCH",
            "TASK_TRIGGER_DISABLED", "TASK_PRINCIPAL_MISMATCH", "MULTIPLE_INSTANCE_POLICY_MISMATCH",
            "WAKE_TO_RUN_DISABLED", "TASK_NOT_ENABLED", "LAST_TASK_RESULT_NONZERO",
            "TASK_RUNTIME_INFO_UNAVAILABLE", "TASK_DUPLICATE", "TASK_STRUCTURE_UNREADABLE",
            "TASK_INSPECTION_UNAVAILABLE", "TASK_IDENTITY_AMBIGUOUS",
            "PRINCIPAL_IDENTITY_UNVERIFIED",
        }
        for row in rows:
            findings = row.get("findings")
            if (
                "present" not in row
                or (type(row.get("present")) is not bool and row.get("present") is not None)
                or not isinstance(findings, list)
                or any(f not in allowed_findings for f in findings)
            ):
                raise ValueError("INVALID_TASK_FIELDS")
            clean = {"name": row["name"], "present": row["present"], "findings": findings}
            findings = list(findings)
            state = row.get("state")
            if state in {"Ready", "Running", "Disabled", "Unknown", "Queued"}:
                clean["state"] = state
            runtime = row.get("runtime")
            if isinstance(runtime, dict) and type(runtime.get("last_task_result")) is int:
                clean["last_task_result"] = runtime["last_task_result"]
            for key in ("action_matches_project", "principal_matches_user", "wake_to_run"):
                if type(row.get(key)) is bool:
                    clean[key] = row[key]
            if row["present"] is True:
                for key, diagnostic in (
                    ("action_matches_project", "TASK_ACTION_MISMATCH"),
                    ("principal_matches_user", "TASK_PRINCIPAL_MISMATCH"),
                    ("wake_to_run", "WAKE_TO_RUN_DISABLED"),
                ):
                    if row.get(key) is not True:
                        findings.append(diagnostic)
                if state not in {"Ready", "Running"}:
                    findings.append("TASK_NOT_ENABLED")
                if row.get("trigger_interval_seconds") != 300:
                    findings.append("TRIGGER_INTERVAL_MISMATCH")
                if row.get("multiple_instances") != "IgnoreNew":
                    findings.append("MULTIPLE_INSTANCE_POLICY_MISMATCH")
                if "last_task_result" not in clean:
                    findings.append("TASK_RUNTIME_INFO_UNAVAILABLE")
                elif state != "Running" and clean["last_task_result"] != 0:
                    findings.append("LAST_TASK_RESULT_NONZERO")
            if row["present"] is None and "TASK_INSPECTION_UNAVAILABLE" not in findings:
                raise ValueError("UNKNOWN_TASK_PRESENCE_WITHOUT_DIAGNOSTIC")
            if row["present"] is False and "TASK_ABSENT" not in clean["findings"]:
                findings.append("TASK_ABSENT")
            clean["findings"] = sorted(set(findings))
            result.append(clean)
        has_findings = any(row["findings"] for row in result)
        status = "ATTENTION_REQUIRED" if has_findings else doc["status"]
        return {"status": status, "checked_utc": checked.isoformat(), "tasks": result,
                "basis": "SUPPLIED_READ_ONLY_SCHEDULER_INSPECTION"}
    except (ValueError, TypeError, OSError, AttributeError, KeyError):
        return {"status": "INVALID_OR_UNREADABLE_INSPECTION", "tasks": []}


def inspect(project: Path, data_root: Path, *, now: datetime, tasks: Path | None = None) -> dict:
    roots = {"source": project, "private_evidence": data_root, "audit_bundle": BUNDLE}
    if tasks is not None:
        roots["task_inspection"] = tasks.parent
    reader = Snapshot(roots)
    reference = reader.json(BUNDLE / "source_reference.json")
    calendar = reader.json(BUNDLE / "calendar_utc.json")
    sources = source_report(project, reader, reference)
    try:
        evidence = audit_evidence(data_root, now=now, reader=reader, calendar=calendar)
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        evidence = {"status": "INVALID_OR_UNREADABLE_EVIDENCE", "scope": "LOCAL_FILE_CONSISTENCY_ONLY"}
    scheduler = task_report(tasks, reader, now)
    consistency = reader.verify()
    source_ready = (
        sources["matched_version"] == "continuation_2026-11-19"
        and sources["installed_manifest"] == "CONSISTENT"
        and not sources["unfinished_source_transaction"]
        and sources["expected_project_interpreter_present"]
    )
    # The evidence helper deliberately exposes limited local consistency only.
    local_evidence_ok = evidence.get("status") in {"LOCAL_EVIDENCE_CONSISTENT", "NO_SESSIONS_DUE"}
    status = (
        "CONCURRENT_CHANGE_RETRY" if consistency["status"] != "STABLE"
        else "LOCAL_CHECKS_CONSISTENT" if source_ready and local_evidence_ok
        and scheduler["status"] == "TASK_CONFIGURATION_MATCHES"
        else "ATTENTION_REQUIRED"
    )
    return {
        "schema": "koral-read-only-workstation-audit-v1",
        "checked_utc": now.isoformat(),
        "status": status,
        "read_only_inputs": True,
        "account_writes": 0,
        "model_calls": 0,
        "provider_requests": 0,
        "task_changes": 0,
        "runtime": {"python": sys.version.split()[0], "platform": sys.platform},
        "source": sources,
        "scheduler": scheduler,
        "evidence": evidence,
        "snapshot": consistency,
        "limits": {
            "portfolio_accounting_validation": "NOT_PERFORMED",
            "independent_provider_authentication": "NOT_PERFORMED",
            "unattended_operation": "NOT_PROVEN_BY_LOCAL_FILES",
            "windows_regression_tests": "NOT_RUN_BY_THIS_AUDIT",
            "investment_performance": "NOT_VERIFIED",
            "credentials_and_account_values_exported": False,
        },
    }


def summary(report: dict) -> str:
    source = report["source"]
    evidence = report["evidence"]
    lines = [
        "# Koral Equities read-only workstation audit", "",
        f"**{report['status']}** — {report['checked_utc']}", "",
        "| Check | Result |", "|---|---|",
        f"| Source provenance | {source['matched_version'] or source['status']} |",
        f"| Installed source manifest | {source['installed_manifest']} |",
        f"| Scheduler inspection | {report['scheduler']['status']} |",
        f"| Local account/session evidence | {evidence.get('status', 'NOT_ASSESSED')} |",
        f"| Historical bound files | {evidence.get('historical_bound_evidence', {}).get('status', 'NOT_ASSESSED')} |",
        f"| Current control declarations | {evidence.get('current_controls', {}).get('status', 'NOT_ASSESSED')} |",
        f"| Controller heartbeat | {evidence.get('heartbeats', {}).get('controller', {}).get('status', 'NOT_ASSESSED')} |",
        f"| Observer heartbeat | {evidence.get('heartbeats', {}).get('observer', {}).get('status', 'NOT_ASSESSED')} |",
        f"| Input stability | {report['snapshot']['status']} |", "",
        "This report reads local records. It does not establish independently authenticated",
        "market data, continuous unattended execution, validated accounting or investment returns.",
        "No application code from the inspected installation was executed.", "",
    ]
    if evidence.get("sessions"):
        lines.extend(["## Due sessions", "",
                      "| New York date | Overall | Summary | Bound sources | Research |",
                      "|---|---|---|---|---|"])
        for row in evidence["sessions"]:
            lines.append(
                f"| {row['session']} | {row['status']} | {row['summary']['status']} | "
                f"{row['sources']['status']} | {row['research']['status']} |"
            )
        lines.append("")
    lines.extend(["## Next action", ""])
    if report["status"] == "CONCURRENT_CHANGE_RETRY":
        lines.append("Inputs changed during inspection. Retry into a new report directory after the current activity finishes normally.")
    else:
        if source["matched_version"] != "continuation_2026-11-19":
            lines.append("- Compare the installed source with the reviewed continuation before considering a source update.")
        if source["installed_manifest"] != "CONSISTENT" or source["unfinished_source_transaction"]:
            lines.append("- Reconcile the installed manifest or unfinished source transaction using the existing recovery procedure.")
        if report["scheduler"]["status"] != "TASK_CONFIGURATION_MATCHES":
            lines.append("- Review the two task rows and their concrete findings in audit.json.")
        if evidence.get("status") not in {"LOCAL_EVIDENCE_CONSISTENT", "NO_SESSIONS_DUE"}:
            lines.append("- Reconcile the dated evidence findings in audit.json with the original local records; missing records cannot be reconstructed by this audit.")
        lines.append("- Run the outstanding Windows regression suite in the separate reviewed checkout and retain its exact source revision and results.")
    lines.extend(["", "The detailed JSON excludes account values, sign-in details, model prose and raw task arguments.", ""])
    return "\n".join(lines)


def _output_directory(destination: Path, inputs: list[Path]) -> None:
    # Validate existing ancestors before resolving; resolving first would hide
    # a symlink/junction. This also works before the final directory exists.
    for candidate in (destination, *destination.parents):
        if candidate.exists() or candidate.is_symlink():
            attrs = candidate.lstat()
            if candidate.is_symlink() or getattr(attrs, "st_file_attributes", 0) & 0x400:
                raise ValueError("UNSAFE_REPORT_DIRECTORY")
    target = destination.resolve()
    for source in inputs:
        root = source.resolve()
        if target == root or target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError("REPORT_DIRECTORY_OVERLAPS_INPUT")
    if destination.exists():
        if not destination.is_dir() or any(
            p.name not in {"tasks.json", "launcher.json"} for p in destination.iterdir()
        ):
            raise ValueError("REPORT_DIRECTORY_ALREADY_USED")
    else:
        destination.mkdir(parents=True, exist_ok=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--task-status", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--as-of", help="Timezone-aware inspection time; defaults to actual UTC now")
    args = parser.parse_args(argv)
    try:
        now = instant(args.as_of or datetime.now(UTC))
        project = args.project.expanduser().absolute()
        data_root = args.data_root.expanduser().absolute()
        destination = args.output_dir.expanduser().absolute()
        review_root = BUNDLE.parents[1] if BUNDLE.parent.name == "tools" else BUNDLE.parent
        _output_directory(destination, [project, data_root, review_root])
        report = inspect(
            project, data_root, now=now,
            tasks=args.task_status.expanduser().absolute() if args.task_status else None,
        )
        with (destination / "audit.json").open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        with (destination / "SUMMARY.md").open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(summary(report))
        print(json.dumps({"status": report["status"], "report_written": True,
                          "account_writes": 0, "model_calls": 0}))
        return 0 if report["status"] == "LOCAL_CHECKS_CONSISTENT" else 3 if report["status"] == "CONCURRENT_CHANGE_RETRY" else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        known = {
            "AWARE_TIMESTAMP_REQUIRED", "UNSAFE_REPORT_DIRECTORY",
            "REPORT_DIRECTORY_OVERLAPS_INPUT", "REPORT_DIRECTORY_ALREADY_USED",
        }
        code = str(exc) if type(exc) is ValueError and str(exc) in known else "AUDIT_INPUT_UNAVAILABLE_OR_INVALID"
        if isinstance(exc, AuditReadError):
            code = "AUDIT_INPUT_UNAVAILABLE_OR_INVALID"
        print(json.dumps({"status": "AUDIT_NOT_COMPLETED", "reason": code,
                          "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
