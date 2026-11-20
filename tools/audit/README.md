# Koral Equities read-only workstation audit

This audit reads the migrated Windows installation and produces a small report
without importing its application, running research or changing its tasks or
paper account. Run it from the separately reviewed continuation checkout on
branch `continuation/paper-session-health-2026-11-19` (draft PR #3).

The audit is a diagnostic. It does not install the continuation or authorise a
schedule to resume. Its local consistency findings do not prove continuous
unattended execution, independently authenticated market observations, correct
portfolio accounting or investment returns.

## One Windows command

Use the actual existing project directory. `-DataRoot` is the `.tradingagents`
directory containing `paper`, `paper_operations` and `observations`; it is not
the project checkout. If omitted, it defaults to the current Windows profile's
`.tradingagents` directory.

```powershell
& "$review\tools\audit\Invoke_Koral_ReadOnly_Audit.ps1" `
  -Project $project `
  -DataRoot $dataRoot
```

Here `$review` is the separate reviewed checkout, `$project` is the actual
installation, and `$dataRoot` is the discovered private-data directory. The
launcher reports the newly created report directory in its console result.
It never scans drives to guess those locations.

The default audit interpreter is the project's existing
`.venv\Scripts\python.exe`. If that environment did not survive migration,
select another existing Python 3.11 or later interpreter explicitly:

```powershell
& "$review\tools\audit\Invoke_Koral_ReadOnly_Audit.ps1" `
  -Project $project -DataRoot $dataRoot -Python $existingPython
```

The interpreter runs the bundled auditor with `-I -S -B`: Python ignores
environment-based import paths and user site packages, skips site startup hooks,
and writes no bytecode. The audit has no third-party Python dependencies. Its
New York timing table is precomputed from the existing pinned 2026–2027 session
calendar, including daylight-saving changes and shortened sessions. It rejects
inspection times outside that coverage.

If PowerShell policy blocks the script, report that specific restriction to the
local operator. Do not change machine execution policy as part of this audit.

## What is written

Only a new report directory, outside the project, private-data and review
directories, receives output. Existing report directories are never reused.

| File | Contents |
|---|---|
| `SUMMARY.md` | Main findings and the next diagnostic action |
| `audit.json` | Source provenance, local evidence by session, task findings and input stability |
| `tasks.json` | Read-only metadata for the two exact Koral task names |
| `launcher.json` | Whether the launcher completed or encountered a concrete blocker |

The reports omit cash, equity, positions, ticker lists, account-identity hashes,
sign-in details, raw task arguments, arbitrary error text and model prose.
The private `.env` files are never read. These are local output files; the audit
does not upload them.

## What the checks mean

**Source provenance.** The audit checks inventories for the 124-file recovery
baseline, the 125-file continuation, the reconciled cutover candidate and the
20 November scan diagnostics followup. Both
LF and CRLF checkouts are recognised. The bundled `source_reference.json` pins
the runtime bytes for each version and identifies the current candidate. A
matching earlier version remains identifiable. A self-consistent installed
manifest is a separate check. A matching Git checkout without an installed
manifest is reported distinctly.

**Session evidence.** The report uses the declared effective session and the
pinned New York due windows. It checks the local ledger's hash chain, sealed
rosters, unique mark and source-binding events, original observations, archived
copies, normalized market inputs, saved summaries, research receipts and report
hashes. Missing or interrupted research remains visible. Pending paper
transactions are reported without being recovered. Hashes establish agreement
among supplied local records, not independent authenticity of their history.
Full portfolio accounting is outside this audit.

The audit reviews at most the latest 31 due sessions. Older due dates are counted
as unreviewed and prevent an all-clear result. The complete local audit chain is
checked within explicit size/event limits; source and evidence reads also have
byte and inventory limits.

**Scheduler.** Only `KoralEquities_ResearchObserver_5min` and
`KoralEquities_PaperController_5min` are queried. The launcher checks their
interpreter, script, working directory, principal, recurrence, enabled state,
instance policy and last result. Equivalent five-minute ISO durations and
Windows SID principals are handled. Matching task configuration or a zero last
result is not proof of a completed paper session.

**Input stability.** Read files and inspected inventories are checked again
before reporting. A change produces `CONCURRENT_CHANGE_RETRY`; rerun into a new
directory after current work finishes normally. The audit does not acquire
production locks or stop running tasks. It rejects redirected input paths and
Windows reparse points. The check detects changes during its observation pass;
it does not claim an atomic filesystem snapshot.

| Exit code | Meaning |
|---|---|
| `0` | The implemented local checks were consistent; the stated verification limits still apply |
| `2` | Findings or an unavailable/invalid required input; inspect the saved report |
| `3` | Inputs changed during inspection; retry into a new directory |

## Direct Python use

For copied evidence or an environment without Windows Scheduler, the Python
auditor can run directly. Scheduler status will be `NOT_INSPECTED` unless a
fresh report from the bundled Windows launcher is supplied.

```text
python -I -S -B tools/audit/audit_workstation.py --project ACTUAL_PROJECT --data-root ACTUAL_TRADINGAGENTS_DATA --output-dir NEW_REPORT_DIRECTORY
```

Optional `--as-of` must be timezone aware. It affects inspection timing only;
it does not make new task metadata historical evidence. Future or older-than-
14-minute task reports cannot establish current task status.

## Windows verification

The focused audit tests are:

```text
python -m pytest -q tests/test_audit_snapshot.py tests/test_workstation_audit.py tests/test_workstation_evidence.py tests/test_windows_audit_wrapper.py
```

Run these from the separate reviewed checkout with its development dependencies.
Tests use temporary synthetic files and task API mocks. The Windows cases must
actually execute; Linux skips are not Windows validation. Preserve the source
commit, Python version, passed/failed/skipped totals and any skip reasons.

The existing focused Windows workflow includes these tests. GitHub currently
reports workflows disabled on this fork; a maintainer must re-enable them for
future qualifying pushes to run. The manual workflow button is not relied on
while the workflow is absent from the default branch. An isolated local Windows
run is a valid way to obtain the outstanding platform evidence.

## Local Codex continuation task

```text
Continue Koral Equities using draft PR #3 and branch
continuation/paper-session-health-2026-11-19. Fetch into a separate review
checkout/worktree and record the exact current commit. Read this README,
CONTINUATION_2026-11-19.md and both validation records before proceeding.

Identify the actual migrated Koral Equities installation and its private
.tradingagents data root from the local project context and the two exact task
definitions. Keep Koral Quant, Research Lab and Control Panel work untouched.
Do not assume the old absolute Windows paths still apply.

Run the focused Windows tests in the reviewed checkout. Run the bundled
read-only audit against the actual installation into a new report directory.
Retain SUMMARY.md, audit.json, tasks.json and launcher.json. If the expected
virtual environment is missing, use another already installed Python 3.11+
for the dependency-free audit and report the original interpreter gap.

Use the observed findings to identify the smallest repair. Preserve unknown
local source changes, original private account/configuration and all captured
observations. Follow the existing source backup/rollback, exposure and
concurrency requirements before any installation update. Preserve the existing
subscription-only model routing and recorded paid-credit confirmation.

Reconcile each due session from the declared activation date. Keep absent,
failed, incomplete and internally consistent records distinct. Do not infer
historical disarming from today's configuration. Do not synthesize missed
observations or research, reset the account, force an entry or change strategy.

Complete any already-authorised routine repair that the evidence supports,
verify it, then report the actual state, tests, affected dates, source revision
and next legitimate collection window. If a capability or necessary evidence
is missing, name that exact gap and preserve the completed audit.
```
