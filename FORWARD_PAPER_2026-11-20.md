# Enable prospective paper execution

The managed V3.1 workflow is the execution path for Koral Equities. The manual
V1 scan saves research and deliberately creates no rating-only orders. Its
account at `.tradingagents/paper/portfolio.json` is separate from the managed
account at `.tradingagents/paper/v3_autopilot/portfolio.json`.

## Research must author an executable plan

The general Trader and Portfolio Manager prompts previously did not request
all the terms that the V3 policy extractor needs. A positive rating, a proposed
limit price, a risk-review level, or a fraction of an undefined standard
allocation is insufficient for autonomous execution.

This change gives only V3 research an explicit conditional-long mandate. The
original Trader and Portfolio Manager must make the decision before the
separate extractor reads their reports. An accepted plan requires a future
close-above trigger, an explicit confirmation count, an executable protective
stop agreed by both decision makers, an actual account allocation fraction,
an entry expiry, and a holding period. The numeric terms must match the final
Portfolio Manager's canonical execution block. Missing, conflicting, duplicated
or invented terms produce no executable plan. An explicit `NO_TRADE` remains a
valid decision.

The mandate is scoped to `paper_execution_mode=koral_v3_conditional_long_v1` in
the V3 graph factories. General research and manual V1 scans retain their
existing behavior. Existing accepted plans and historical ledger entries are
not rewritten. The separate typed extractor, source-report hashes, earnings
checks, price checks and portfolio limits remain enforced.

The full readiness suite also exposed an existing completion-timestamp
normalization failure. Newly created `first_processed_utc` values are now
explicitly converted to UTC; existing stored values remain unchanged. The
failure was independently reproduced on the prior reviewed source. It does
not establish the cause of any separate Windows cutover failure without that
run's log.

Existing autonomous limits include at most 10% initial account allocation per
new plan, one to three future confirming closes, one to 21 calendar days of
entry validity, and two to 60 holding sessions. Engine constraints separately
cover stop risk, total exposure, cash, whole shares, position count, sector
concentration and drawdown. These are simulation policies, not a guarantee of
losses being limited to the intended stop risk.

## What a real forward paper trade looks like

1. Before the session's preopen collection window, freeze a dated observation
   roster that includes existing exposure and pending plans.
2. Capture genuine preopen earnings evidence, then after-close earnings,
   stock prices, SPY and GBP conversion evidence at the scheduled times.
3. Validate and preserve those observations. Mark the existing paper account,
   including standing risk and exits, independently of new research success.
4. Research eligible companies within the existing daily admission budget.
   A validated new conditional intent enters `WATCHING` with zero confirming
   closes. A rating alone never becomes an order.
5. Count only subsequent observed closes. When the required condition is met,
   queue the paper order. Recheck eligibility, expiry, opening price and risk
   before simulating a later session's opening fill.
6. Record the fill, shares, cash movements and modeled costs. Continue marking
   the position and processing its protective stop or time exit.

The simulation uses completed daily OHLC observations. A qualifying opening
fill is recorded when that session is processed after the close. Five-minute
Scheduler invocations collect observations and advance the daily workflow;
they do not make this an intraday broker connection. No order is sent to a
broker. No past report or missed preopen capture can authorize a retrospective
entry.

## Native installation and activation

Use the existing source-only package and the native procedure in
`RECOVERY_2026-11-12.md`. Build from the exact reviewed commit with the pinned
baseline available:

```powershell
python .\tools\recovery\build_release.py --release 2026-11-20 --output <review-directory>
```

Extract into a separate review directory. Inspect the installed source and
the exact Observer and Controller tasks before applying. Keep the owned
workload stopped and the controller disarmed during source replacement. The
installer accepts recognized prior files and preserves unknown revisions for
review; do not rewrite source hashes to accept unknown edits.

The installer changes source only. Preserve a separately verified dependency
overlay, including a validated Yahoo repair, instead of rebuilding the original
environment over it. Compare dependency versions and protected account,
environment and controller configuration hashes before and after placement.
Do not reset or initialize the recovered V3 account.

After the final source manifest is installed:

1. Resolve any actual failed native readiness case from its log. Run the
   installed `Verify_Koral_Recovery.py`, `--tests`, and `--account-check` modes
   as separate commands, checking each exit code. Verify final-volume evidence
   publication with isolated fixtures. Cloud test success does not supply a
   Windows readiness receipt.
2. Inspect the existing V3 enabled, paused and halted state, its last mark,
   positions, orders and active intents. If paper enablement alone is missing,
   the supported `autopilot.cli enable-paper` command can enable it under the
   operator's authorization. Resolve an unexplained pause, halt or exposure
   across missed sessions before proceeding. Do not blindly resume or clear a
   risk halt.
3. Run `Register_Koral_Tasks.ps1 -Mode Register` to register disabled owned
   tasks, then `-Mode Probe` to verify a real zero-spend scheduled launch.
   Both native test and Scheduler receipts must match the final installed
   manifest. A source update invalidates old receipts; never edit them.
4. Verify the intended Windows user's saved subscription sign-in and both
   model tiers. Before subscription-only activation, the account setting
   allowing other apps to use paid credits after the included limit must
   actually be off. The program cannot query that remote setting. The
   confirmation flag records an operator attestation; it does not change the
   setting.
5. Once those conditions hold, run:

   ```powershell
   .\Register_Koral_Tasks.ps1 -Mode Activate -ChatGPTPlanOnly -ConfirmPaidCreditsDisabled
   ```

6. Verify the enabled task definitions, armed controller and a subsequent
   normal recurring invocation with fresh heartbeats. Do not run the legacy
   `autopilot.cli serve` loop alongside the two managed tasks.

In PowerShell 5.1, explicitly check `$LASTEXITCODE` after a native Python
command. `$ErrorActionPreference = 'Stop'` alone does not stop a script after
a nonzero native exit. Register and Activate are not unconditional retry
no-ops: if the same source is already active, inspect and verify it instead
of repeating cutover.

The intended account must remain logged in, with the computer powered and
online. A lock screen is supported; signing out is not. Confirm that another
computer cannot run the same account's old schedule.

## Prospective timing and evidence

Activation before 08:45 New York can select that day's exchange session.
Activation at or after the cutoff selects the next session in the pinned
calendar. For an activation on the afternoon of Friday 20 November 2026,
that next session is Monday 23 November. Its configured UK windows are
13:45–14:25 for preopen earnings, 22:00–22:25 for after-close earnings, and
processing from 22:40. These are application collection windows, not exchange
opening or closing times. Holiday and early-close handling comes from
`daily_sessions.json`.

Activation does not guarantee a trade in the first session. A new plan still
needs its future confirmations and a later eligible opening. An operating
forward test can validly remain in cash when no plan or entry qualifies.
Distinguish that outcome from disabled automation, missing required data or a
failed model request, and retain each reason in the research/session receipt.

Report the following milestones separately:

| Milestone | Required evidence |
|---|---|
| Activated | Correct tasks enabled; controller armed; prospective roster frozen |
| Running | Normal recurring invocation; fresh matching Observer/controller heartbeats |
| Session processed | Genuine source captures; source-bound V3 mark; persisted session summary |
| Plan accepted | `V3_POLICY_ACCEPTED`; report hash; conditional intent in `WATCHING` |
| Order queued | `V2_CONDITIONAL_ORDER_QUEUED`; future confirmation record and order ID |
| Position opened | `BUY_FILLED`; observed entry session, price, shares, cash and costs |
| Position managed | Subsequent marks, stop/time exit and realized paper P&L |

An offline rehearsal proves implementation mechanics in an isolated synthetic
account. It is not a real forward session or a trade in the preserved account.
Historical audit findings should remain visible; they do not need to be erased
or backfilled to make a new prospective run look complete.

## Verification of this change

The 24-module installed-verifier selection passed **311 tests** on Linux, with
six Windows PowerShell cases skipped. A separate normal-agent and prompt
regression selection passed **75 tests**. These sets overlap and should not be
added together as a unique test count.

The joined execution regression runs the actual research archiver, strict
policy, managed forward controller, conditional ledger and fill/exit accounting
against synthetic committee responses and observations in temporary accounts.
It proves a complete future-confirmation entry followed by a stop or time exit,
and repeated processing does not duplicate a trade. Negative cases prove that
missing or invented original terms and prices borrowed from unrelated parts of
a report cannot create an order. Network access is blocked in these tests.

Runtime, new/expanded execution tests and packaging lint checks pass. The two
legacy dry-run fixture files retain their unrelated prior lint findings; no
new findings were introduced there. The source-only installer regression
preserves a synthetic dependency overlay and protected account/configuration
bytes across repeated application. Package tests check that the installed
verifier's test modules and their shared fixtures are included in the payload.

This verification does not establish Windows native publication, scheduled
recurrence, live model compliance with the new prompt, a current market-session
observation or a trade in the operator's preserved account. Those outcomes
require their corresponding native and prospective receipts.
