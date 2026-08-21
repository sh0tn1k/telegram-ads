# Operational read-only watcher policy (2026-06-09)

Session learning: the watcher has three distinct runtime modes and future agents must not conflate them.

## Runtime modes

1. **Idle scheduler**
   - `HermesTelegramAdsReadOnlyAdapter(adapter=None)`.
   - `run_forever()` is safe only because there are no real adapter calls.
   - Adding a watch to an idle scheduler can produce `watch_error` instead of data because the adapter is not attached.

2. **Login-only real scheduler**
   - Real `TelegramAdsAdapter` acquired via `BrowserProfileManager` and wrapped by `HermesTelegramAdsReadOnlyAdapter`.
   - Exactly one baseline `login_state` watch is allowed.
   - Successful logged-in ticks create `job_runs.status=ok` and usually **no** `resource_snapshots` / events, because `_run_login_state` only emits on unhealthy login.

3. **Operational read-only scheduler**
   - Real adapter + read-only wrapper.
   - Allows baseline `login_state` watches.
   - Allows campaign/account post-action watches **only** when tied to an approved Telegram Ads action using `thresholds.approved_action` metadata.
   - Rejects/disables arbitrary campaign/account watches before the scheduler loop.

## Required `thresholds.approved_action` metadata

Every post-action watch generated after an approved mutating action must carry:

```json
{
  "approved_action": {
    "source": "approved_telegram_ads_action",
    "action": "<create_ad|edit_ad|change_cpm|add_to_budget|withdraw_from_budget|start_ad|stop_ad|delete_ad>",
    "ad_id": "<matching ad_id for campaign-level watches>",
    "approved_by": "operator",
    "created_by": "agent"
  }
}
```

The upstream helper `create_post_action_watches(...)` creates good watch kinds but **does not add this metadata automatically**. the agent must add it before persistence or immediately after creation via an update path.

## Operational starter policy

The session produced this production-safe starter in the Hermes repo:

- `/home/hermes/.hermes/hermes-agent/start_ads_watcher_readonly_operational.py`

Policy enforced by starter:

- `login_state` always allowed as baseline health.
- Post-action watch kinds allowed only with valid `thresholds.approved_action`.
- Campaign-level watches require `spec.ad_id` and metadata `ad_id` match.
- `account_balance` / `account_budget` watches are only allowed for approved `add_to_budget` / `withdraw_from_budget` post-action verification.
- Disallowed enabled watches are disabled before `run_forever()`.
- Starter does not create watches and does not mutate Telegram Ads.

## Completion rule

After an approved Telegram Ads action, the agent must not report the action as complete until one of:

1. `post_action_verified` is observed;
2. the expected status / CPM / budget is observed by the watcher;
3. `post_action_not_verified` or `watch_error` is routed to a diagnostic task.

No auto-fix / auto-recreate / auto-budget / auto-CPM from watcher output without a fresh explicit operator approval.

## Commit / repo integration note

The operational skills live in the active profile under `~/.hermes/skills/devops/...`, but the Hermes fork commit must also copy the updated skill files into the repo under `skills/devops/.../SKILL.md` if they are meant to be version-controlled. The runtime SQLite DB and scheduler processes stay outside git.
