# Continuation validation — 19 November 2026

## Scope and result

**155 tests passed; 6 Windows-only cases skipped on Linux**, in 4.24 seconds.
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
The follow-up changes only documentation and the nonvacuous task-row assertion;
they do not change production runtime behavior.

The CI blocker is visible at the repository's
[Actions page](https://github.com/kamil42842888-code/TradingAgents/actions).
The available connector cannot re-enable workflows, and the browser session is
not signed in. A repository maintainer can restore Actions, or a local isolated
Windows checkout can run the command above. Neither route was completed in this
session; the six Windows cases remain unverified.

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
