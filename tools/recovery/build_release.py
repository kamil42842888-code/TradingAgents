"""Build a pinned, source-only recovery ZIP from reviewed repository files."""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "95bce9dda270d5c07e732131390d2af2cc2e006d"
RELEASE = "2026-11-19"
DOCS = (
    "RECOVERY_2026-11-12.md",
    "RECOVERY_VALIDATION_2026-11-12.md",
    "CONTINUATION_2026-11-19.md",
    "CONTINUATION_VALIDATION_2026-11-19.md",
)
FILES = [
    "KE_DAILY_PAPER.py",
    "KE_Observer_Scheduler_2026-10-26.py",
    "Register_Koral_Tasks.ps1",
    "Verify_Koral_Recovery.py",
    "tools/recovery/build_release.py",
    "tools/recovery/release_history.json",
    "tools/recovery/restore_sources.py",
    *[
        "tradingagents/paper/autopilot/" + name
        for name in (
            "daily_support.py",
            "daily_roster.py",
            "daily_research.py",
            "daily_plan.py",
            "daily_supervisor.py",
            "daily_health.py",
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
            "test_daily_plan.py",
            "test_daily_health.py",
            "test_daily_roster_capacity.py",
            "test_forward_source_continuity.py",
            "test_daily_supervisor_progress.py",
            "test_daily_status_cli.py",
            "test_windows_task_status.py",
            "test_recovery_build.py",
        )
    ],
]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def base_tree() -> dict[str, str]:
    """Require the pinned commit and enumerate its actual regular-file blobs.

    A shallow checkout may lack an older baseline. Never turn that failure into
    an apparently successful release with an empty prerequisite manifest.
    """
    proc = subprocess.run(
        ["git", "cat-file", "-e", BASE + "^{commit}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise ValueError("Pinned recovery baseline is unavailable: " + BASE)
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--full-tree", BASE],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    result = {}
    for entry in proc.stdout.split(b"\0"):
        if not entry:
            continue
        header, filename = entry.split(b"\t", 1)
        mode, kind, digest = header.decode("ascii").split()
        rel = filename.decode("utf-8")
        if kind == "blob" and mode in {"100644", "100755"}:
            result[rel] = digest
        elif rel in FILES or (rel.startswith("tradingagents/") and rel.endswith(".py")):
            raise ValueError("Non-regular source in recovery baseline: " + rel)
    if not any(p.startswith("tradingagents/") and p.endswith(".py") for p in result):
        raise ValueError("Pinned recovery baseline has no application sources")
    return result


def build(destination: Path):
    tree = base_tree()
    history = json.loads((ROOT / "tools/recovery/release_history.json").read_text())
    files = {rel: (ROOT / rel).read_bytes().replace(b"\r\n", b"\n") for rel in FILES}
    docs = {rel: (ROOT / rel).read_bytes() for rel in DOCS}
    manifest = {
        "schema": "ke-manual-source-recovery-v1",
        "release": RELEASE,
        "base_commit": BASE,
        "files": {r: sha(b) for r, b in files.items()},
        "known_sha256": history["known_sha256"],
        "known_git_blobs": history["known_git_blobs"],
        "prerequisites": {},
    }
    for rel, digest in sorted(tree.items()):
        if not rel.startswith("tradingagents/") or not rel.endswith(".py"):
            continue
        if rel in files:
            continue
        path = ROOT / rel
        if (
            not path.is_file()
            or path.is_symlink()
            or blob(path.read_bytes().replace(b"\r\n", b"\n")) != digest
        ):
            raise ValueError("Unpackaged core source differs from pinned baseline: " + rel)
        manifest["prerequisites"][rel] = [digest]
    for path in (ROOT / "tradingagents").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel not in tree and rel not in files:
            raise ValueError("New application source is missing from recovery payload: " + rel)
    if not manifest["prerequisites"]:
        raise ValueError("Recovery release would have no protected core prerequisites")
    for rel in FILES:
        if rel in tree:
            known = manifest["known_git_blobs"].setdefault(rel, [])
            if tree[rel] not in known:
                known.append(tree[rel])
    raw_manifest = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    installer = files["tools/recovery/restore_sources.py"]
    if installer.count(b"BUILD_REPLACES_MANIFEST_SHA256") != 1:
        raise ValueError("Recovery installer manifest placeholder is missing or ambiguous")
    installer = installer.replace(b"BUILD_REPLACES_MANIFEST_SHA256", sha(raw_manifest).encode())
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / ("Koral_Equities_Recovery_" + RELEASE + ".zip")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Koral_Recovery/restore_sources.py", installer)
        archive.writestr("Koral_Recovery/MANIFEST.json", raw_manifest)
        for rel, raw in files.items():
            archive.writestr("Koral_Recovery/payload/" + rel, raw)
        for rel, raw in docs.items():
            archive.writestr("Koral_Recovery/" + rel, raw)
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
