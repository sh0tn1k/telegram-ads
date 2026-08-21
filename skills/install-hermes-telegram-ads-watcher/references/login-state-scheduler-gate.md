# Login-state-only scheduler gate (session learning 2026-06-09)

Use this when the operator approves running the Telegram Ads watcher scheduler with a real read-only adapter for **only** an existing `login_state` watch.

## Approval boundary

Allowed:
- gracefully stop the previous scheduler session/PID;
- start a new scheduler process with `HermesTelegramAdsReadOnlyAdapter(real TelegramAdsAdapter)`;
- use existing SQLite store `~/.hermes/data/ads_watcher.sqlite3`;
- keep exactly one enabled watch: `kind=login_state`, `project_id=hermes_main`, expected id supplied by the operator;
- allow periodic `detect_login_state` only.

Forbidden:
- adding watches;
- campaign reads (`get_ad`, `list_ads`, campaign details/stats);
- account budget reads;
- mutations (`create_ad`, `edit_ad`, `start_ad`, `stop_ad`, `delete_ad`, CPM/budget/targeting/schedule/pixel/conversion changes);
- login_start/login_submit_phone/OTP;
- payments.

## Critical workflow

1. Record DB counts first:
   - `watch_specs`
   - `watcher_events`
   - `resource_snapshots`
   - `job_runs`
   - `ad_snapshots`
   - `account_snapshots`
2. Verify the old scheduler process is alive, then stop via Hermes `process(action='kill', session_id=...)`.
3. Poll old session and require `exit_code=0` plus logs containing `WatcherScheduler stopped` / `scheduler loop closed`.
4. Start a new script derived from `templates/start_ads_watcher_real_login_only.py` with `terminal(background=true)`.
5. The script must validate **exactly one enabled watch** before acquiring the long-running loop:
   - `len(watches) == 1`
   - `id == EXPECTED_WATCH_ID`
   - `kind == 'login_state'`
   - `project_id == EXPECTED_PROJECT_ID`
   - `interval_sec == EXPECTED_INTERVAL`
6. Run `service.run_watch_once(WATCH_ID)` immediately after attaching the real adapter. This verifies the gate without waiting until `next_run_at`. It uses the existing watch; it does not add watches.
7. Then start `WatcherScheduler.run_forever()` for periodic ticks.
8. Report DB counts and latest rows.

## Important package behavior

`login_state` watches do **not** persist `ResourceSnapshot` rows on success.

Implementation observed in `TelegramAdsWatcherService._run_login_state`:
- calls `adapter.detect_login_state()`;
- computes `observed = {'logged_in': logged_in}`;
- if `logged_in is True`: returns `([], observed)`;
- if not logged in: emits `login_required` event.

Therefore a successful logged-in tick is represented by:
- latest `job_runs.status == 'ok'`;
- `job_runs.events_created == 0`;
- no new `watcher_events`;
- no `resource_snapshots` row.

Do **not** report `resource_snapshots=0` as a failure for successful `login_state`; call out that this is current package design.

## Historical idle-mode errors

If an idle scheduler previously ran a `login_state` watch with `adapter=None`, it may leave a historical `watch_error` event:

```text
RuntimeError: HermesTelegramAdsReadOnlyAdapter is in idle mode: no underlying TelegramAdsAdapter has been attached.
```

Treat it as historical diagnostic after the real-adapter restart. Do not count it as active failure if a later `job_runs.status='ok'` exists for the same watch.

## Reporting checklist

Return:
- new scheduler PID/session_id;
- old PID stopped cleanly? yes/no;
- real adapter attached? yes/no;
- exactly one enabled `login_state` watch? yes/no;
- latest `job_run.status`;
- whether `resource_snapshots` contains login-state snapshot (usually **no by design**);
- `watcher_events` count before/after;
- mutating actions = 0;
- campaign/account budget/stats reads = 0;
- errors/historical diagnostics.

## Dedupe_key absorbs follow-on RuntimeError events

The `watcher_events.dedupe_key` column is `UNIQUE`. Key format is
`{project_id}:{watch_spec_id}:{event_type}:{error_short}`. Once the first
`watch_error` event is written with key
`hermes_main:<watch_id>:watch_error:RuntimeError`, every subsequent tick that
raises the same `RuntimeError` produces **no new event row** —
`store.create_event()` hits `IntegrityError` and the new error is silently
absorbed at the events table.

But `job_runs` rows ARE written (one per tick) regardless of dedupe. So if you
observe:

- `watcher_events` count = 1 (only the first event),
- but `job_runs.error = "RuntimeError: ..."` rows keep appearing,

the scheduler is **still failing every tick** — the events table is hiding it
via the unique constraint. **Always cross-check `job_runs.error` when
`watcher_events` looks stale.**

If `job_runs.status='ok'` rows exist interleaved with `status='error'` rows for
the same watch, the adapter was briefly attached then detached (e.g., a
foreground bash session acquired the adapter, ran a few successful ticks, then
was killed). Reattach the real adapter to recover — the dedupe key keeps
absorbing, so don't be misled by an empty events table.

## Idle-by-design signals (scheduler not running is OK)

`install-hermes-telegram-ads-watcher` does NOT install a systemd unit. The
scheduler runs only when explicitly launched via `start_ads_watcher*.py`
(manual or `terminal(background=true)`).

**Idle-by-design signs (NOT a bug):**

| Signal | Meaning |
|---|---|
| No `*ads*` or `*watcher*` unit in `systemctl --user list-units` | No systemd-managed daemon. Expected. |
| No `*ads*` or `*watcher*` unit in `systemctl --user list-timers --all` | No periodic fire. Expected. |
| `ps -ef \| grep -iE 'ads_watcher\|start_ads'` returns nothing | No daemon process. Expected. |
| `watch_specs.last_run_at` is hours/days old | Daemon not running since that timestamp. Expected. |
| `watch_specs.next_run_at` is in the past | Daemon not running to update it. Expected. |
| `watcher_events` count is small (often 1) | Dedupe absorbs most RuntimeErrors. See above. |

If all six are true, the watcher is **intentionally idle** and the
`HermesTelegramAdsReadOnlyAdapter is in idle mode` RuntimeError is the expected
diagnostic — not a bug to fix. To resume work, request an approval gate for
`real_adapter_smoke.py` (gate 1), `start_ads_watcher_readonly_operational.py`
(gate 2), and adding watches (gate 3) — each is a separate the operator approval.

## Watch policy enforcement (operational allowlist)

`start_ads_watcher_readonly_operational.py` adds a second gate on top of the
read-only adapter: `is_allowed_watch(spec)` validates each enabled watch
against an operational allowlist BEFORE the scheduler loop starts. Disables
disallowed watches in the SQLite store (unless `disable_unknown=False`).

**Allowed:**

- `login_state` — baseline health monitoring.
- 12 post-action kinds (`moderation_result`, `campaign_status`,
  `rejection_info`, `campaign_budget`, `campaign_stats`, `campaign_detail`,
  `campaign_cpm`, `campaign_performance`, `account_balance`,
  `account_budget`, `campaign_spend`, `campaign_list`) — ONLY when
  `thresholds.approved_action` has `action ∈ APPROVED_ACTIONS` AND
  `source == 'approved_telegram_ads_action'` AND (for campaign-level kinds)
  `ad_id` matches `spec.ad_id`.

**Disallowed:** any watch that does not match the allowlist. The starter
disables them in the SQLite store (logs a WARNING). This means even if a watch
was added in a previous session, restarting the daemon via this starter will
silently neuter it.

When diagnosing "why is my post-action watch not firing?", always check
`watch_specs.enabled` AND the policy log lines from the daemon.
