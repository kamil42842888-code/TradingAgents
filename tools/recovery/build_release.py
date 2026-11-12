"""Build a pinned, source-only recovery ZIP from reviewed repository files."""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "b5cb15b439947ba4fc9237c7ca0246dd507f6fdc"
FILES = [
    "KE_DAILY_PAPER.py",
    "KE_Observer_Scheduler_2026-10-26.py",
    "Register_Koral_Tasks.ps1",
    "Verify_Koral_Recovery.py",
    "tools/recovery/restore_sources.py",
    *[
        "tradingagents/paper/autopilot/" + name
        for name in (
            "daily_support.py",
            "daily_roster.py",
            "daily_research.py",
            "daily_supervisor.py",
            "daily_sessions.json",
            "daily_transaction.py",
            "paper_forward.py",
            "paper_bridge.py",
            "runner.py",
            "feeds.py",
            "live_dry_run.py",
        )
    ],
    *[
        "tests/" + name
        for name in (
            "test_daily_support.py",
            "test_daily_paper_integration.py",
            "test_recovery_release.py",
            "test_daily_supervisor_release.py",
        )
    ],
]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def git_source(rel):
    proc = subprocess.run(
        ["git", "show", BASE + ":" + rel], cwd=ROOT, capture_output=True, check=False
    )
    return proc.stdout if proc.returncode == 0 else None


def build(destination: Path):
    history = json.loads((Path(__file__).with_name("release_history.json")).read_text())
    files = {rel: (ROOT / rel).read_bytes().replace(b"\r\n", b"\n") for rel in FILES}
    manifest = {
        "schema": "ke-manual-source-recovery-v1",
        "release": "2026-11-12",
        "base_commit": BASE,
        "files": {r: sha(b) for r, b in files.items()},
        "known_sha256": history["known_sha256"],
        "known_git_blobs": history["known_git_blobs"],
        "prerequisites": {},
    }
    for path in sorted((ROOT / "tradingagents").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in files:
            continue
        raw = git_source(rel)
        if raw is not None:
            manifest["prerequisites"][rel] = [blob(raw)]
    for rel in FILES:
        raw = git_source(rel)
        if raw is not None:
            known = manifest["known_git_blobs"].setdefault(rel, [])
            if blob(raw) not in known:
                known.append(blob(raw))
    raw_manifest = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    installer = (
        (Path(__file__).with_name("restore_sources.py")).read_bytes().replace(b"\r\n", b"\n")
    )
    installer = installer.replace(b"BUILD_REPLACES_MANIFEST_SHA256", sha(raw_manifest).encode())
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "Koral_Equities_Recovery_2026-11-12.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Koral_Recovery/restore_sources.py", installer)
        archive.writestr("Koral_Recovery/MANIFEST.json", raw_manifest)
        for rel, raw in files.items():
            archive.writestr("Koral_Recovery/payload/" + rel, raw)
        doc = ROOT / "RECOVERY_2026-11-12.md"
        if doc.exists():
            archive.writestr("Koral_Recovery/RECOVERY_2026-11-12.md", doc.read_bytes())
    (destination / "SHA256_RECOVERY.txt").write_text(
        sha(target.read_bytes()) + "  " + target.name + "\n"
    )
    print(
        json.dumps(
            {
                "release": str(target),
                "sha256": sha(target.read_bytes()),
                "files": len(files),
                "prerequisites": len(manifest["prerequisites"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
