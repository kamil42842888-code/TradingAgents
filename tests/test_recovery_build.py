"""Release construction must retain a real, pinned source-installation boundary."""

import importlib.util
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "recovery_build", REPOSITORY / "tools/recovery/build_release.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def release_repo(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("The release-builder tests require Git; runtime does not")
    root = tmp_path / "source"
    root.mkdir()
    (root / "tradingagents").mkdir()
    (root / "tools/recovery").mkdir(parents=True)
    old = b"VALUE = 1\n"
    (root / "controller.py").write_bytes(old)
    (root / "tradingagents/core.py").write_bytes(b"CORE = True\n")
    (root / "tools/recovery/restore_sources.py").write_bytes(
        (REPOSITORY / "tools/recovery/restore_sources.py").read_bytes()
    )
    (root / "tools/recovery/release_history.json").write_text(
        json.dumps({"known_sha256": {}, "known_git_blobs": {}})
    )
    for rel in builder.DOCS:
        (root / rel).write_text("Synthetic release documentation\n")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "--quiet")
    git("add", ".")
    git(
        "-c", "user.name=Release test", "-c", "user.email=release-test@example.invalid",
        "commit", "--quiet", "--no-gpg-sign", "-m", "Synthetic pinned baseline",
    )
    base = git("rev-parse", "HEAD")
    (root / "controller.py").write_bytes(b"VALUE = 2\n")
    (root / "tradingagents/new_feature.py").write_bytes(b"NEW = True\n")
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder, "BASE", base)
    monkeypatch.setattr(
        builder,
        "FILES",
        ["controller.py", "tools/recovery/restore_sources.py", "tradingagents/new_feature.py"],
    )
    return root, tmp_path / "output", base, old


def test_built_manifest_accepts_prior_source_and_rejects_unknown_edits(release_repo, tmp_path):
    root, output, base, old = release_repo
    builder.build(output)
    archive = output / ("Koral_Equities_Recovery_" + builder.RELEASE + ".zip")
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(tmp_path / "unpacked")
    package = tmp_path / "unpacked/Koral_Recovery"
    spec = importlib.util.spec_from_file_location("built_restore", package / "restore_sources.py")
    restore = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(restore)
    manifest, payload = restore.payload(package)
    assert manifest["base_commit"] == base
    assert manifest["release"] == "2026-11-19"
    assert manifest["prerequisites"] == {
        "tradingagents/core.py": [builder.blob(b"CORE = True\n")]
    }
    assert builder.blob(old) in manifest["known_git_blobs"]["controller.py"]
    assert all((package / doc).is_file() for doc in builder.DOCS)

    project, home = tmp_path / "installation", tmp_path / "user"
    (project / ".venv/Scripts").mkdir(parents=True)
    (project / ".venv/Scripts/python.exe").write_bytes(b"synthetic interpreter")
    (project / "tradingagents").mkdir()
    (project / "tradingagents/core.py").write_bytes(b"CORE = True\r\n")
    (project / "controller.py").write_bytes(old.replace(b"\n", b"\r\n"))
    ledger = home / ".tradingagents/paper/v3_autopilot/portfolio.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({
        "schema": "koral-autopaper-v3.1-isolated", "paper_only": True,
        "portfolio": {"book": {"positions": {}, "orders": []}, "intents": {}},
    }))
    account = ledger.read_bytes()
    check = restore.inspect(project, home, manifest, payload)
    assert check["statuses"]["controller.py"] == "REPLACE_KNOWN_SOURCE"
    assert check["statuses"]["tradingagents/new_feature.py"] == "RESTORE_MISSING"
    assert ledger.read_bytes() == account
    (project / "controller.py").write_bytes(b"UNREVIEWED = True\n")
    with pytest.raises(ValueError, match="Unknown local source revision"):
        restore.inspect(project, home, manifest, payload)
    assert ledger.read_bytes() == account


def test_missing_baseline_fails_before_creating_release(release_repo, monkeypatch):
    _, output, _, _ = release_repo
    monkeypatch.setattr(builder, "BASE", "0" * 40)
    with pytest.raises(ValueError, match="Pinned recovery baseline is unavailable"):
        builder.build(output)
    assert not output.exists()


@pytest.mark.parametrize("change", ["missing", "changed"])
def test_missing_or_changed_core_cannot_disappear_from_prerequisites(release_repo, change):
    root, output, _, _ = release_repo
    core = root / "tradingagents/core.py"
    if change == "missing":
        core.unlink()
    else:
        core.write_bytes(b"CORE = 'unpackaged change'\n")
    with pytest.raises(ValueError, match="Unpackaged core source differs"):
        builder.build(output)
    assert not output.exists()


def test_new_runtime_module_must_be_in_release_payload(release_repo):
    root, output, _, _ = release_repo
    (root / "tradingagents/forgotten.py").write_bytes(b"FORGOTTEN = True\n")
    with pytest.raises(ValueError, match="New application source is missing"):
        builder.build(output)
    assert not output.exists()


def test_installer_hash_placeholder_is_required(release_repo):
    root, output, _, _ = release_repo
    (root / "tools/recovery/restore_sources.py").write_bytes(b"PIN = 'already-replaced'\n")
    with pytest.raises(ValueError, match="manifest placeholder"):
        builder.build(output)
    assert not output.exists()
