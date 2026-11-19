# Koral Equities continuation — 19 November 2026

## Current evidenced state

The reviewed continuation is published on
`continuation/paper-session-health-2026-11-19` in
[`kamil42842888-code/TradingAgents`](https://github.com/kamil42842888-code/TradingAgents).
Draft [PR #3](https://github.com/kamil42842888-code/TradingAgents/pull/3)
targets `recovery/long-only-paper-2026-11-12`. The audited starting commit is
`95bce9dda270d5c07e732131390d2af2cc2e006d`; the initial published implementation
commit is `2c8b1f0a8eb5c45afec4bbd8642ed35625703828`. Its Git tree exactly matches
the locally tested snapshot. Subsequent continuation commits record publication
and strengthen the Windows test assertions. Pin the current PR head when taking
the handover; do not assume that publication installs it on the workstation.
The default `main` branch is upstream TradingAgents; it is not the current
Koral paper implementation. The feature and recovery changes remain in draft
pull requests [#1](https://github.com/kamil42842888-code/TradingAgents/pull/1)
and [#2](https://github.com/kamil42842888-code/TradingAgents/pull/2).

The [12 November validation record](RECOVERY_VALIDATION_2026-11-12.md)
documents successful source recovery, isolated Windows tests, a real Windows
scheduled-launch probe, and subscription-only activation. It declares
13 November as the first effective session. It explicitly leaves genuine
unattended forward-session collection and trading unverified.

No current Windows task inventory, private paper account, post-activation
session evidence or new-PC installation was available in this work session.
The code was retrieved and tested in an isolated Linux checkout. Therefore:

- The old installation failure is not assumed still active.
- The present workstation schedule, account balance and paper positions are unknown.
- Recorded synthetic rehearsals are not genuine investment performance.
- No current profitability or investment edge is established by this work.

At the 19 November afternoon UK inspection time, the completed scheduled dates
to reconcile from the declared start are 13, 16, 17 and 18 November. The
19 November close-processing window is still in the future. The health report
calculates this from the pinned New York calendar instead of counting weekdays.

## Changes in this continuation

| Problem reproduced or inspected | Change | Result |
|---|---|---|
| An earlier `YYYY-MM-DD_error.json` outranked a later success because filenames were sorted. | Order valid events by aware timestamps; retain separate latest error and latest successful-session references. | A recovered run is no longer hidden by an older error, and a later error remains visible. |
| Eight active symbols plus a new ranked symbol produced an invalid ninth roster member. | Check capacity before adding a new candidate. | All existing exposure remains in the roster; new candidates only fill available slots. |
| After a session was marked, changed original Observer sources could feed additional research while the account retained different source evidence. | Require repeat polls to match all three source hashes bound to that mark; compare the second snapshot with preflight. | Changed market or earnings sources halt before additional research or account changes. Unchanged interrupted runs retain the existing attempt limits and can resume. |
| A routine preopen summary left `first_processed_utc` null at the first successful close. | Populate a missing or null first-success time and preserve it on repeated polls. | The first completed processing timestamp is retained. |
| Raw status had no heartbeat age or session continuity check. | Add `daily_health.py`, used by `KE_DAILY_PAPER.py --status`. | Missing, stale, invalid or future evidence and missing completed sessions become explicit. |
| Installation `Inspect` required stopped/disarmed tasks, making it unusable for routine health checks. | Add a separate read-only PowerShell `Status` mode. | Enabled tasks, missing tasks, migrated action paths and run results can be inspected without changing the schedule. |
| An unavailable Git baseline could silently remove source prerequisites from a release. | Enumerate prerequisites from the pinned baseline tree and fail on unavailable, missing, changed or unpackaged core sources. | The release retains its source-integrity boundary, including in shallow checkouts. |

## Meaning and limits of the new status

`operational_health` distinguishes configuration-file presence, heartbeat
freshness, saved errors, and expected completed sessions. It uses the existing
14-minute heartbeat age threshold. Future timestamps are flagged. Normal and
half-day processing use the existing pinned calendar; the current day's missing
summary is not classified as overdue during the first 40 minutes after close
processing starts, matching the Observer's existing market-capture allowance.

`REPORTED_COMPLETE` means that a locally saved, valid summary claims a completed
mark for that date. This command does **not** independently validate the paper
ledger, original observations, continuous schedule operation, fills or returns.
Those limitations are explicit fields in the JSON. Missing/invalid configuration
is reported even though the legacy configuration field retains defaults for a
missing file. An unreadable source manifest is reported alongside readable
operational evidence; execution still fails closed at that integrity gate.

The task-status command uses Microsoft's read APIs
[`Get-ScheduledTask`](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/get-scheduledtask)
and [`Get-ScheduledTaskInfo`](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/get-scheduledtaskinfo).
It compares task actions to the supplied project and reports states, trigger
intervals, principal settings, wake behavior, last run results and next run times.
It neither starts nor modifies tasks. Matching task configuration does not prove
that a market-data or research session completed.

## Verification

The focused suite uses temporary paper accounts, synthetic market/earnings
evidence and fake models. It covers the reproduced failures, repeated polls,
interrupted attempts, source-tamper rejection, missing/stale/future status,
calendar boundaries, installation rollback and source-package integrity.

Exact test totals, runtime versions and any platform skips are recorded in
`CONTINUATION_VALIDATION_2026-11-19.md`. No genuine historical sessions were
reprocessed, and no market-data or paid model request was made in this work.
Windows PowerShell behavior requires its Windows regression cases and an
inspection on the actual migrated workstation; Linux success cannot establish
those outcomes.

At 15:38 UTC on 19 November, the repository's
[Actions page](https://github.com/kamil42842888-code/TradingAgents/actions)
reported that workflows are disabled on this fork because of Actions usage and
can be re-enabled by a repository maintainer. No run exists for the published
continuation commit. This is a CI access/configuration blocker, not a Windows
test result. The connected GitHub tools do not expose re-enabling Actions, and
the browser is signed out. A maintainer must re-enable the workflows, or the
focused suite must be run in an isolated Windows checkout. Record that result
against its exact source revision before declaring Windows validation complete.

## Build and review

From this reviewed Git branch, with its pinned baseline available:

```powershell
python tools/recovery/build_release.py --output dist
```

The package is `Koral_Equities_Recovery_2026-11-19.zip`. It contains reviewed
source, tests, a pinned manifest, and both the original recovery procedure and
this continuation note. The previous 12 November source hashes are accepted;
unknown local edits remain protected. The installer still requires a flat,
disarmed existing non-Git installation and preserves the original ledger,
private environment and operation configuration. Source installation, task
registration, testing and activation remain separate.

Do not extract this package over the current installation. Use a separate review
directory and establish the actual new-PC project and data roots first. The
old absolute Windows path is not assumed to be current. A Git checkout uses its
own branch/worktree workflow; the non-Git recovery installer deliberately refuses it.

## Next workstation task

The next step is to reconcile the actual migrated Windows installation, not to
run another research batch immediately. A standalone audit now performs that
read-only reconciliation in one command. Run
`tools/audit/Invoke_Koral_ReadOnly_Audit.ps1` from the separate reviewed checkout,
with the actual `-Project` and `-DataRoot` paths. Its Python auditor does not
import the target application or load its environment files. It reports source
provenance, task definitions, controller/Observer heartbeats, current control
declarations, due-session evidence and historical ledger-bound files, including
archives predating the effective session. Outputs are sanitized and stay in a
new local report directory.

See [the audit instructions and complete local Codex task](tools/audit/README.md).
The standalone audit is distributed in the reviewed Git checkout; it does not
require installing the source-update package first. Its Python checks passed
on Linux, including compatibility with actual temporary V3/supervisor/Observer
producer output. The Windows launcher cases still require Windows execution.

The following is the local continuation handover:

> Continue Koral Equities from the reviewed 19 November continuation. First read
> draft PR #3, this document, `CONTINUATION_VALIDATION_2026-11-19.md` and the
> 12 November recovery instructions and validation record. Use branch
> `continuation/paper-session-health-2026-11-19` and record its exact current
> commit. Discover the actual
> project, interpreter, private data root, installed source manifest, Git state
> if present, active processes and the two Koral Equities task definitions.
> Preserve unrelated Koral Quant, Research Lab and Control Panel work. Compare
> source against the reviewed commit; do not overwrite unknown local changes.
> Run the standalone read-only audit from the reviewed checkout and retain
> `SUMMARY.md`, `audit.json`, `tasks.json` and `launcher.json` locally. Use the
> report to target the next inspection; do not infer completeness from a summary
> alone. Its local consistency status does not verify accounting or returns.
>
> Read the effective session, controller/Observer heartbeats, dated rosters,
> earnings and market observations, summaries, model-usage/attempt receipts, and
> existing account audit/bound-source evidence. Establish separately whether
> 13, 16, 17 and 18 November completed, failed, were deliberately disarmed, or
> lack evidence. Include 19 November only after its actual due window. A task's
> zero exit code or a summary alone is not proof of a verified paper session.
> Keep account values and sign-in details local; redact secrets from reports.
>
> Use the new read-only task Status mode from the separate reviewed package to
> diagnose missing tasks or stale paths without stopping active work. If an
> existing installation needs this source update, inspect its actual exposure,
> processes and local edits first, then follow the established backup/rollback
> procedure. Do not weaken its manifest, flat-account or concurrency guards.
> Run the focused Windows tests and validate the actual task actions before
> any continuation of an already-authorised schedule. Preserve subscription-only
> routing and the existing explicit paid-credit confirmation; never substitute
> API billing. Do not reset the account, invent missed observations, replay past
> model research, force a trade, or change strategy/risk settings.
>
> Deliver the concrete state, observed blocker and smallest verified repair,
> with commands, source revision, tests, affected dates and the next legitimate
> future collection window. If no repair is needed, record that evidence and
> let the existing forward schedule proceed normally.

With `$project` set to the actual installation and `$release` set to the separate
unpacked `Koral_Recovery` directory, the new task inspection is:

```powershell
& "$release\payload\Register_Koral_Tasks.ps1" -Mode Status -Project $project
```

After the reviewed continuation is installed, its controller inspection is:

```powershell
& "$project\.venv\Scripts\python.exe" -B "$project\KE_DAILY_PAPER.py" --status
```

Optional `--operations-root` and an aware `--as-of` timestamp are available only
for `--status`, permitting offline inspection of copied operation records. They
cannot redirect a tick, activation, probe or disarm operation.
