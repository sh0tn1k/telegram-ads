# V2.6 — Concrete drop: V1 store wire-up + out-of-scope artifact handling

Date: 2026-06-17
Commit: `16e9aa3af`
Pattern: code + tests + local commit only. No push, no restart, no env change, no production enablement, no standalone daemon, no systemd change, no deepseek / Xvfb / KC touch.

## Files

| Path | Status | LOC | Role |
|---|---|---|---|
| `gateway/ads_watcher_v2/wiring.py` | new | 591 | V1 store port + InMemoryStore + persist helpers + run_polling_tick |
| `tests/gateway/test_ads_watcher_v2_6.py` | new | 651 | 36 V2.6 tests |
| `gateway/ads_watcher_v2/__init__.py` | modified | +30 | Re-export V2.6 surface |
| `gateway/ads_watcher_v2/polling.py` | modified | +6 | Skip transition event when no previous state |
| `gateway/ads_watcher_v2/watchspec.py` | modified | +14 | Subset match for `expected_outcome_reached` |
| `tests/gateway/test_ads_watcher_v2_5.py` | modified | +8 | Update existing V2.5 test for new should_terminate semantic |
| **Total** | | **1300+** | (2 new + 4 modified) |

## WatchSpec creation + persistence flow

```
ApprovedAdsAction
  ↓ create_watchspecs_for_approved_ads_action(action)
WatchSpecRegistration(specs, external_ids={}, status)
  ↓ persist_registration(reg, store)  # V2.6
  ├─ for spec in registration.specs:
  │    try _build_external_spec_from_v2(spec) → store.upsert_watch(external)
  │    except: errors[spec.watch_id] = repr(exc)  # never raises
  └─ return PersistedRegistration(status, external_ids, errors)
```

The V2 `watch_id` is the store key (id); V1 still gets a UUID as `v1_internal_id` for cross-referencing. Per-spec failures are captured in `errors` and the action result remains usable.

## Event persistence flow

```
NormalizedEvent (V2)
  ↓ _dedupe_key_for(ev)  # "project|watch_id|store_event_type|state_sorted"
  ↓ V2_EVENT_TYPE_TO_STORE_EVENT_TYPE[ev.event_type]  # ad_rejected → ad_declined
  ↓ _default_event_factory(ev, dedupe) → dict shape
  ↓ store.create_event(event)  # idempotent on dedupe_key
  ├─ new row       → inserted++
  └─ dedupe hit    → deduped++
```

19-entry closed mapping. Unknown V2 event types raise `KeyError` — caller decides whether to log+drop. In `run_polling_tick` we log+continue; the V2.5 emitter set is fully covered.

## last_state + terminal flow

```
run_polling_tick(specs, store, adapter, previous_states, ...)
  ↓ poll_post_action_watches(specs, adapter, previous_states, ...)
  ↓ if result.events: persist_normalized_events(...)
  ↓ for spec in specs:
       if not spec.enabled: continue
       observed = _observe_state(spec, adapter)  # read-only, re-queries
       if observed is None: continue
       stop_reason = spec.should_terminate(elapsed_seconds, current_state=observed)
       update_watch_last_state(spec.watch_id, state=observed, store, stop_reason)
  └─ return result  # same surface as V2.5
```

`update_watch_last_state` merges into existing `thresholds`, scrubs secrets, sets `stop_reason` and flips `enabled=False` on terminal. Skips silently on `KeyError` (spec never persisted).

## Terminal stop semantics

| Condition | stop_reason | Watch enabled |
|---|---|---|
| `elapsed >= max_duration_seconds` | `max_duration_reached` | False |
| All `expected_outcome` keys present in current (subset) | `expected_outcome_reached` | False |
| Current status diverges (heuristic on `status` key) | `expected_outcome_unreachable` | False |
| None of the above | `None` | True |

## Tests

| Suite | Result |
|---|---|
| `tests/gateway/test_ads_watcher_v2_6.py` (new) | **36 passed** |
| `tests/gateway/test_ads_watcher_v2.py` (regression) | 67 passed |
| `tests/gateway/test_ads_watcher_v2_5.py` (regression, +8 lines) | 55 passed |
| `tests/test_ads_watcher_inprocess.py` (V1 regression) | 37 passed |
| `tests/test_telegram_ads_config_loader.py` | 12 passed |
| `tests/test_telegram_ads_typed_wrapper.py` | 25 passed |
| `tests/gateway/test_planned_stop_watcher.py` | 11 passed |
| `tests/knowledge_compiler/test_kc_runtime.py` | 51 passed |
| **Total** | **247 passed, 0 failed** |

## Approval §10 bullets coverage

| Bullet | Test | Result |
|---|---|---|
| successful create_ad registers moderation WatchSpec | `test_create_ad_registers_moderation_watchspec` | ✅ |
| successful start_ad registers delivery WatchSpec | `test_start_ad_registers_delivery_watchspec` | ✅ |
| successful change_cpm registers verification WatchSpec | `test_change_cpm_registers_verification_watchspec` | ✅ |
| missing ad_id creates placeholder watch | `test_missing_ad_id_creates_account_placeholder` | ✅ |
| WatchSpec registration failure does not fail original action result | `TestRegistrationFailureIsolation` (3 tests) | ✅ |
| polling maps pending→active to ad_approved | `test_pending_to_active_emits_ad_approved` | ✅ |
| polling maps pending→declined to ad_rejected | `test_pending_to_declined_emits_ad_rejected` | ✅ |
| watcher persists NormalizedEvent | `TestPersistence` (5 tests) | ✅ |
| watcher updates last_state | `TestLastStateWriteback` (3 tests) | ✅ |
| watcher stops terminal watch | `TestTerminalStops` (4 tests) | ✅ |

## V2.6 pitfalls (load-bearing, pin each in tests)

1. **`WatchSpecRegistration.registered_ids` is `list[str]`, not `dict`.**
   The dict is `external_ids`. V2.5 used
   `getattr(reg, "registered_ids", None)` then `dict(...)` → raised `ValueError`
   because the list has 27 long-ID items, not 2. Fix: use
   `getattr(reg, "external_ids", None)` and assume dict shape. Pin with
   `isinstance(reg.registered_ids, list)` and
   `isinstance(reg.external_ids, dict)`.

2. **`should_terminate` must use subset match, not strict equality.**
   V2.5's `current_state == self.expected_outcome` breaks when
   adapter returns a richer state. Fix:
   `all(current_state.get(k) == v for k, v in self.expected_outcome.items())`.
   Pin with `assert should_terminate(elapsed=0, current=
   {"status": "active", "account_id": "acc-1"}) ==
   "expected_outcome_reached"` when `expected_outcome={"status": "active"}`.

3. **`last_state` = observed, NOT previous.**
   `run_polling_tick` re-queries the adapter via `_observe_state` for
   each spec and persists the **observed** state as `last_state`.
   The `previous_states` input is for state transition detection only.
   Pin with `store.watches[watch_id]["thresholds"]["last_state"]
   ["status"] == "active"` when adapter returns `active` regardless
   of `previous_states`.

4. **Skip transition events on the first tick.**
   A spec with no `previous_state` in the tick input must NOT emit a
   transition event. Otherwise the first tick for a freshly-registered
   spec emits spurious `unknown → <anything>` events for every spec.
   Add `if not prev_state: continue` before the transition emission.
   Pin with `previous_states={}` and active adapter → zero events.

5. **Store key = V2 `watch_id`, V1 UUID = `v1_internal_id`.**
   V2 test code looks up by V2 `watch_id` (stable, human-readable).
   The V1 store's UUID is for V1 cross-reference. Don't replace V2
   `watch_id` with a fresh UUID on upsert. The
   `_build_external_spec_from_v2` function must return
   `{"id": spec.watch_id, "v1_internal_id": str(uuid.uuid4()), ...}`.

6. **`PersistedRegistration.registration_status` shape.**
   The helper returns a frozen dataclass with
   `status ∈ {"persisted", "partial", "empty"}` +
   `external_ids: dict[str, str]` + `errors: dict[str, str]`.
   Tests assert the shape; do not return a bare dict.

7. **Mapping is total over the V2 emitter set.**
   Any new V2 event_type added in a future V-N drop that is wired
   through V2.6's `persist_normalized_events` MUST appear in
   `V2_EVENT_TYPE_TO_STORE_EVENT_TYPE` first, with a valid V1 target.
   `KeyError` is the contract. The `TestEventTypeMapping` test
   enumerates the closed set; expanding it requires updating the test.

## Out-of-scope artifacts pattern

In long-running watcher development (V2 → V2.5 → V2.6), each session
ends with files that are **not committed** to that session's commit.
When a new session starts, `git status` shows:

1. **Modified files from the previous session** — partial work that
   wasn't committed before shutdown.
2. **Untracked files from the previous session** — new modules the
   previous session wrote but never staged.

**V2.6 lesson:** in the same checkout, the previous session had
written `gateway/ads_watcher_v2/production.py`,
`production_adapter.py`, `report_router.py`, `v1_bridge.py` plus
partial modifications to `gateway/ads_watcher_inprocess.py` and
`tools/telegram_ads_typed_tool.py`. The V2.6 commit (`16e9aa3af`)
**staged only its own files** and left the others uncommitted +
untracked.

**Rules:**

- **Do not `git add` them into your commit.** Your commit message
  names your V-N drop; mixing in untracked files from another session
  makes the commit history lie. Stage only what your work produced.
- **Do not delete them.** The previous session may have been
  interrupted before it could decide what to do with them.
  Deletion is destructive.
- **Do not modify them.** They may depend on V-N imports you have
  not stabilized; modifying in parallel creates merge conflicts
  and ungrounded assumptions.
- **Do flag them in the commit report.** A "⚠️ Out-of-scope
  artifacts observed" section listing path / status / size / note
  gives the operator a checklist for the next session to review and decide.
- **Do verify your staged changes** with `git diff --stat` and
  `git status --short` immediately before `git commit`. If
  something you didn't write shows up as `M ` (staged), abort and
  re-stage selectively.

**Discipline:**

```bash
git status --short              # See what's staged + untracked
git diff --stat                 # Confirm only your V-N files
git add gateway/ads_watcher_vN/  # Stage only V-N-specific paths
tests/gateway/test_ads_watcher_vN*.py
git status --short              # Re-verify
git commit -m "feat(telegram-ads): ..." -m "..."
git log -1 --stat               # Confirm staged set matches message
```

## Commit report shape (V2.6)

The V2.6 commit report should include:

1. **Files changed** — table with path / LOC / role.
2. **WatchSpec creation + persistence flow** — diagram of
   `ApprovedAdsAction → WatchSpecRegistration → persist_registration
   → store.upsert_watch` with per-spec failure isolation.
3. **Event persistence flow** — diagram of
   `NormalizedEvent → _dedupe_key_for → V2→V1 event_type map →
   store.create_event (idempotent on dedupe_key)`.
4. **last_state + terminal flow** — diagram of
   `run_polling_tick → _observe_state (re-queries adapter) →
   should_terminate → update_watch_last_state → store.update_watch`.
5. **Tests** — table with suite / count / result; total in big number.
6. **Approval §10 bullets coverage** — table mapping each of the 10
   approval bullets to the V2.6 test that pins it.
7. **Confirmation** — table of stop-condition guards, each ✅.
8. **Out-of-scope artifacts** — a `⚠️ Out-of-scope artifacts observed`
   section listing any uncommitted/untracked files from a previous
   session that the commit did NOT touch.
9. **Open approval-required items** — push to fork, runtime enablement
   (separate AR), DeepSeek review of V2.6, integration of the untracked
   V2.6-related files.

## Open follow-up items (for next session, with the operator review)

1. **Push to fork** — `git push origin main` to `example/hermes-fork`.
2. **Runtime enablement** — `HERMES_ADS_WATCHER_REPORTS_ENABLED=1`
   (V2.5 concern) + `HERMES_ADS_WATCHER_ENABLED=1` (V1 in-process) +
   gateway restart.
3. **DeepSeek review** of V2.6 (V2.6 has tighter `expected_outcome`
   subset match — could be a behavioural change worth a second pair
   of eyes).
4. **Review & integrate the untracked V2.6-related files**
   (`production.py`, `production_adapter.py`, `report_router.py`,
   `v1_bridge.py`, the partial V1 in-process patch in
   `ads_watcher_inprocess.py` and `telegram_ads_typed_tool.py`).
   They likely reference V2.6's `run_polling_tick` — verify import
   compatibility.
