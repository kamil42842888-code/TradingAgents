# Continuation validation — 19 November 2026

## Scope and result

The initial runtime suite recorded **155 tests passed; 6 Windows-only cases
skipped on Linux**, in 4.24 seconds. The subsequent standalone audit suite
recorded **77 passed; 18 Windows-only cases skipped**, in 2.66 seconds; this
includes the same six task-status cases plus twelve new launcher cases.
These are isolated engineering tests, not genuine forward-paper performance.
The checkout starts from the verified recovery branch at
`95bce9dda270d5c07e732131390d2af2cc2e006d`.

The full focused command was:

```text
python -m pytest -q tests/test_daily_support.py tests/test_daily_paper_integration.py tests/test_recovery_release.py tests/test_daily_supervisor_release.py tests/test_daily_plan.py tests/test_daily_health.py tests/test_daily_roster_capacity.py tests/test_forward_source_continuity.py tests/test_daily_supervisor_progress.py tests/test_daily_status_cli.py tests/test_windows_task_status.py tests/test_recovery_build.py tests/test_paper_forward_integration.py tests/test_paper_bridge_integration.py tests/test_variable_roster_bridge.py
```

| Check | Observed result |
|---|---|
| Focused paper/runtime/source regression | 155 passed, 6 skipped; temporary inputs and fake models |
| Changed Python sources and new tests | Ruff 0.17.5 passed |
| Patch whitespace | `git diff --check` passed |
| Environment dependency consistency | `uv pip check` passed for 92 installed packages |
| Complete release upgrade rehearsal | Passed against the actual pinned 12 November Git tree: 32 payload files, 108 protected core prerequisites; synthetic ledger/config/environment bytes unchanged; repeated inspection reports all sources current |
| Windows task-status tests on Linux | 6 explicitly skipped; require Windows PowerShell |
| Windows task inspection on migrated user PC | Not performed; no access to that workstation in this session |
| Current ledger/account verification | Not performed; private account was not available |
| Real model, broker, or market-data requests | None |
| Actual schedule installation or activation | None |
| Public GitHub publication | Completed after the user's explicit approval: draft [PR #3](https://github.com/kamil42842888-code/TradingAgents/pull/3), branch `continuation/paper-session-health-2026-11-19`; initial implementation commit `2c8b1f0a8eb5c45afec4bbd8642ed35625703828` has the exact locally tested tree |
| Windows CI execution | Blocked: the repository Actions page reports workflows disabled on this fork because of Actions usage; no continuation run exists as of 15:38 UTC on 19 November |
| Follow-up Windows test assertion | Requires both expected task rows in all inspectable scenarios and an empty list only when inspection is unavailable; 6 cases collected on Linux, Ruff and patch-whitespace checks passed; Windows execution remains unperformed |

The new Windows workflow runs the focused daily suites on Windows/Python 3.13,
including six mocked-task scenarios: enabled matching tasks, missing tasks,
migrated paths, nonzero exit results, disabled triggers and unavailable Scheduler.
An added workflow is not evidence of a completed run; consult its exact commit's
Actions result before claiming Windows validation. Mocked task tests also do not
replace inspection of the actual user's task definitions and data.

The initial publication was paused by automatic approval review. The user then
explicitly approved publishing the prepared changes, and the authenticated
GitHub connector created the branch and draft PR. The earlier publication block
is resolved. The initial published tree is
`12b0aa6f50cbb594347ba4860713c18a39733311`, identical to the tested local tree.
The first follow-up changed only documentation and the nonvacuous task-row
assertion. The subsequent standalone audit adds separate diagnostic tools and
tests; production runtime behavior remains unchanged.

The CI blocker is visible at the repository's
[Actions page](https://github.com/kamil42842888-code/TradingAgents/actions).
The available connector cannot re-enable workflows, and the browser session is
not signed in. A repository maintainer can restore Actions, or a local isolated
Windows checkout can run the command above. Neither route was completed in this
session; the six Windows cases remain unverified.

## Standalone workstation audit continuation

The new `tools/audit` bundle is independent of the target application and uses
only the Python standard library. It is run with `-I -S -B`; its timing data is
precomputed from the existing pinned session calendar, so installed `tzdata`
or other site packages are not required for the audit.

At 15:58 UTC, the combined focused audit command completed with **77 passed,
18 Windows-only skipped in 2.66 seconds**:

```text
python -m pytest -q tests/test_audit_snapshot.py tests/test_workstation_audit.py tests/test_workstation_evidence.py tests/test_windows_audit_wrapper.py tests/test_windows_task_status.py
```

| Check | Observed result |
|---|---|
| Standard-library audit core and CLI | Passed, including an isolated `-I -S -B` subprocess and a target package whose import would create a sentinel file; the target code remained unexecuted |
| Reviewed source versus self-supplied manifest | Distinguished older reviewed source, current reviewed source, matching Git source without installation manifest, unreviewed source, and extra application modules |
| Private input/report boundaries | Environment files, balances, sign-in identifiers, raw task arguments and arbitrary error/model text were not exported; malicious manifest entries could not request environment-file reads |
| Stable reader | Detected changed content, replaced/missing/new files, changed inventories and redirects; rejected duplicate/nonfinite JSON and bounded oversized inputs |
| Actual producer compatibility | Actual temporary V3 creation, bootstrap, supervisor tick and Observer heartbeat serialization passed reconciliation; zero research companies and a fake capture subprocess |
| Historical migration evidence | Missing older market, calendar/archive or accepted-report bytes prevented a consistency result, including references before the configured effective session |
| Current controls and heartbeats | Missing/stale/future/failed heartbeats and unsuitable current control declarations remained separate from historical session evidence and prevented an overall consistency result |
| Windows launcher | 12 cases collected and skipped on Linux; tests cover metadata variants, redaction, missing interpreter, protected/reused outputs, junctions and concurrent-change exit propagation |
| Ruff and patch whitespace | Passed for the audit Python sources/tests and patch |

At the end of the Linux run, 18 Windows cases remained. The local Windows run
below records how many executed. These cases use mocked Scheduler APIs.

## Local Windows validation — 19 November 2026

The workstation review started from the published continuation
`98dd1eb5b9f48a37ecdc1f3e3834d4cf4d0a813f`. Tests ran on Windows 11 build 26300
with CPython 3.12.10, pytest 9.2.0 and Windows PowerShell 5.1.26100.9818. An
isolated virtual environment installed the 91 project and `dev` dependencies
from `pyproject.toml`, and `uv pip check` passed. Setuptools could not build an
editable wheel because the managed Windows profile path contains braces. The
suite imported the reviewed checkout in place.

```powershell
python -m pytest -q -rs --basetemp ..\fresh-windows-fixtures tests/test_audit_snapshot.py tests/test_workstation_audit.py tests/test_workstation_evidence.py tests/test_windows_audit_wrapper.py tests/test_windows_task_status.py
```

Use a new writable fixture directory for each run.

| Source and fixture location | Passed | Failed | Skipped |
|---|---:|---:|---:|
| Published audit commit, managed Windows temporary directory | 67 | 24 | 4 |
| Published audit commit, fresh workspace fixture directory | 77 | 14 | 4 |
| Windows audit fixes in this continuation, fresh workspace fixture directory | 91 | 1 | 4 |

The managed temporary directory caused Windows PowerShell to reject the mock
scripts with `AuthorizationManager check failed`. Moving synthetic fixtures to
the workspace let 15 of the 18 Windows cases execute and pass under the same
execution policy. The audit snapshot now compares like-for-like timestamps
within each filesystem API and uses comparable metadata across the path and
open handle. Windows can return creation time from `lstat()` and change time
from `fstat()` for the same unchanged file. The regression covers a file copied
with its original modification time. The calendar test hashes the canonical LF
form of the checked-out source so Git's CRLF conversion does not change it.

| Remaining case | Count | Observed reason |
|---|---:|---|
| Snapshot symbolic-link fixture | 1 skipped | Native Windows symbolic-link creation returned WinError 1314, required privilege absent |
| Launcher junction fixtures for project, private data, and review root | 3 skipped | Native Windows junction creation returned Access denied for fresh workspace fixtures |
| Actual V3 producer compatibility fixture | 1 failed | V3's immutable archive publication requires `os.link()`. Native hard-link creation returned WinError 5 both in the fixture and with an independent workspace file |

The fixture link checks remain in place. Changed Python files passed Ruff
0.17.6, and `git diff --check` passed. The remaining native link cases require
a Windows environment permitted to create those links in its test directory.

## Runtime used

| Component | Version |
|---|---|
| Python | 3.12.14 |
| pytest | 9.2.0 |
| Ruff | 0.17.5 |
| pandas | 3.1.1 |
| langchain-core | 1.8.3 |
| langchain-openai | 1.8.3 |
| langgraph | 1.2.23 |
| yfinance | 1.8.0 |

The existing 71-test recovery suite initially had one environment-only failure:
construction of a mocked provider client encountered the managed runtime's SOCKS
proxy without the optional `socksio` dependency. Installing that dependency in
the isolated local virtual environment resolved it. No application routing,
network restrictions, credentials, installed user environment or dependency
requirements were changed to address that environment issue.

## Regressions now exercised

- A full active roster carries all eight symbols without appending a ninth.
- Repeat research cannot switch market, preopen or preclose sources after a mark.
- A source change between preflight and processing is rejected.
- Unchanged repeated polls and interrupted research retain existing attempt limits.
- An earlier error cannot hide a later successful session, and later errors remain visible.
- Missing, malformed, future, stale, impossible early or mismatched-session records
  cannot be labelled current/completed evidence.
- Normal days, weekends, holidays and early-close processing use the pinned calendar.
- A preopen summary cannot leave the first completed processing timestamp null.
- Read-only diagnostics remain available for missing or structurally invalid manifests;
  tick/probe/activation/disarm still require source integrity.
- Missing pinned Git history or incomplete core files cannot silently weaken a release.
- Source-only installation inspection retains protected data and rejects unknown edits.

## Remaining acceptance boundary

These tests support reviewing and staging the change. They do not establish
current unattended operation or a successful installation on the migrated PC.
The next local action is the read-only reconciliation described in
`CONTINUATION_2026-11-19.md`, followed by the existing source-update procedure only
when its actual state and concurrency requirements are satisfied. Preserve the
original private account and evidence; do not backfill missed sessions.
