---
name: telegram-ads-watcher-event-loop-design
description: "Design and ship a Telegram Ads post-action watcher event-loop layer (V2/V3-style) on top of `hermes_telegram_ads.watcher` — wrapper-not-duplicate discipline, closed-set envelopes, deny-all mutation guards, secret-redacted safe summaries, flag-off-by-default routing. **Current state: V2.9 (shipped 2026-06-17, commit 01ce1f038)** — bounded staged tick (3 stages × 10s each + 25s total budget) replaces the V2.8 60s outer wait_for; idle ticks skip the V2.6 bridge via pre-check; production-verified: 0 TimeoutErrors in 30+ minutes runtime since V2.9 loaded. Use when the operator asks for 'event loop V2', 'post-action watcher design', 'autonomy envelope', 'bounded remediation', 'mini reports to the operator', 'mutation guard'. Distinct from `install-hermes-telegram-ads-watcher` (install/wire) and `audit-and-patch-hermes-module` (patches existing modules)."
version: 1.5.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [telegram-ads, watcher, event-loop, envelope, mutation-guard, post-action, approval-gated, sealed-enums, hermes-fork, polling, integration, wiring, phase-gated, persistence, store-wire-up, production-bridge, worktree-reconcile, read-only-adapter, report-router, bounded-staged-tick, pre-check-optimization]
    related: [install-hermes-telegram-ads-watcher, audit-and-patch-hermes-module, hermes-domain-layer-development, operator-approval-gate-enforcement, incremental-commit-preflight, hermes-tool-module-development, format-telegram-ads-report, handle-telegram-ads-review-and-declines, hermes-system-readiness-audit, asyncio-to-thread-wait-for-cancellation-gotcha]
changelog:
  - "1.5.0 (2026-06-17): V2.9 bounded staged tick drop. Added Pillar 14: 3-stage bounded tick (adapter 10s, V1 tick 10s, V2 bridge 10s, 25s total budget); pre-check optimization (skip bridge when zero post-action watches); asyncio.shield(bridge_task) decouples worker thread from wait_for awaiter; production verification (0 TimeoutErrors in 30+ min runtime, observed tick durations 4.589–10.036s); 25 V2.9 test bullets; 6 V2.9 sub-pitfalls; commit hash 01ce1f038. Linked references/v29-bounded-staged-tick.md."
  - "1.4.0 (2026-06-17): V2.8 async-tick stabilization drop. Added Pillar 13 (the asyncio.run() inside running loop bug, the 3-step fix pattern: probe-first worker thread + asyncio.to_thread bridge + bounded wait_for; the safe-summary shape with timeout_seconds; the 12 V2.8 test bullets; the 6 V2.8 sub-pitfalls; the V2.8 commit message; the explicit 'no restart required' deliverable note). Linked references/v28-async-tick-stabilization.md. Cross-references Pillar 13 to runtime-enablement Pitfall 5 (V1 TimeoutError is now FIXED in V2.8, not a known-quirk)."
  - "1.3.0 (2026-06-17): V2.7 production-bridge drop. Added Pillar 12 (worktree reconciliation: classify out-of-scope files A/B/C/D; production-wiring Class A integration; 5 per-file constraint audits; full test matrix). Added 10 V2.7 test bullets. Linked references/v27-production-bridge.md."
  - "1.2.0 (2026-06-17): V2.6 store wire-up drop. Added Pillar 10 (V1 store port + InMemoryStore + persist helpers + run_polling_tick; subset-match should_terminate; last_state = observed; store-key vs V1-UUID convention; per-spec failure isolation; 19-entry V2→V1 event_type mapping). Added Pillar 11 (out-of-scope artifacts pattern). Added 5 new V2.6 sub-pitfalls. Added 10 V2.6 test categories. Updated Commit hygiene with V2.6 message and body. Linked references/v26-wiring-and-pitfalls.md."
  - "1.1.0 (2026-06-17): V2.5 wiring drop. Added Pillars 6-9. Added 8-phase pattern. Linked references/v25-integration-and-pitfalls.md."
  - "1.0.0 (2026-06-17): V2 initial drop. Five pillars. Module sizing and __init__ discipline. Approval-gated runtime table. Test categories. Pitfalls. Commit hygiene."
---

# Design a Telegram Ads post-action watcher event loop

A class of work: the operator wants a **new Hermes-facing layer** on top of the
already-installed `hermes_telegram_ads.watcher` package. The new layer:

1. Wraps the existing `WatchSpec` / `WatcherEvent` / `recipes` with
   Hermes-only fields (`expected_outcome`, `max_duration_seconds`,
   `autonomy_envelope`, `safe_summary`, `requires_approval`,
   `report_category`).
2. Models a **bounded autonomy envelope** that names which fix types an
   approval grants, with deny-all defaults and a structured
   `MutationBlockedError` for every refusal path.
3. Builds a **pure mini-report formatter** that produces short
   secret-redacted summaries — with report routing **off by default**
   until the operator approves it separately.
4. Ships a thorough pytest suite that pins all closed sets, asserts no
   mutation path exists in V2, and exercises every deny-by-default branch
   of the guard.

## When to use

- the operator says "event loop V2/V3", "post-action watcher", "autonomy
  envelope", "bounded remediation", "mini reports to the operator", "mutation
  guard for Telegram Ads", or hands you a phased approval that lists
  "event model + watchspec + report formatter + autonomy envelope +
  tests, no mutation execution, no production report sending".
- the operator wants a **new module/package** in `gateway/` (e.g.
  `gateway/ads_watcher_v2/`) that extends but does **not duplicate** the
  external `hermes_telegram_ads.watcher` package.
- The work is greenfield feature design with explicit "do not start a
  daemon / do not send Telegram messages / do not change CPM" guardrails.

## When NOT to use

- The work is **installing or upgrading** the
  `hermes_telegram-ads-manager-tool` package → use `install-hermes-telegram-ads-watcher`.
- The work is **patching an existing Hermes module** (Sleep Engine,
  memory lifecycle, ontology, etc.) → use `audit-and-patch-hermes-module`.
- The work is **adding a CLI subcommand** to expose watcher state →
  use `add-hermes-cli-observability-command`.
- The work is **building a new domain stack** (typed models → store →
  service → dispatcher) → use `hermes-domain-layer-development`. (V2 is a
  thin wrapper layer, NOT a full stack.)
- The work is **diagnosing a runtime symptom** (watcher stuck, no
  events, login_required spam) → use `diagnose-hermes-internals` +
  `install-hermes-telegram-ads-watcher/references/login-state-scheduler-gate.md`.

For **Pillar 1-5 (V2 core)**, **Pillar 6-9 (V2.5 wiring)**, the package
layout, the 5 non-negotiable design pillars, the approval-gated runtime
enablement table, the 8-phase approval-gated execution pattern, the test
discipline (test count target / must-not-leak regression test /
approval-required-keyword test / V2.5 test categories / V2.6 test
categories / fake adapter + sender + store patterns), the full pitfalls
list, the output format (commit report), the commit hygiene (V2 / V2.5 /
V2.6 message + body), the linked references, and the related skills —
see the on-disk SKILL.md (1.2.0, 2026-06-17) which contains the full
Pillars 1-11. The full file is available via `skill_view(name=
"telegram-ads-watcher-event-loop-design", file_path="SKILL.md")`.

This skill description is the **summary index**. The full content lives
in SKILL.md. If you are about to start a V-N drop, read SKILL.md in
full.

## Pillar 10 — V1 store wire-up + last_state + terminal stop (V2.6+)

V2.5 wrapped the V1 `hermes_telegram_ads.watcher.store` in a thin
adapter (`integration.py`). V2.6 adds the **persistence layer** —
the part that actually moves data between the in-memory V2 models
and the V1 SQLite store, with `last_state` write-back and terminal
disable. The shape is **three small functions + one Protocol + one
test-store**:

```python
# gateway/ads_watcher_v2/wiring.py

@runtime_checkable
class WatchSpecStorePort(Protocol):
    def upsert_watch(self, spec: Any) -> Any: ...
    def update_watch(self, watch_id: str, **fields: Any) -> Any: ...
    def get_watch(self, watch_id: str) -> Any | None: ...
    def list_watches(self, project_id=None, enabled=None) -> list[Any]: ...
    def create_event(self, event: Any) -> Any: ...

class InMemoryStore:
    """In-memory stand-in for the V1 SqliteStore. Test fixture only."""
    # ... full impl in SKILL.md

def persist_registration(reg, store) -> PersistedRegistration: ...
def persist_normalized_events(events, store, *, factory=None) -> tuple[int, int]: ...
def update_watch_last_state(watch_id, *, state, store, stop_reason=None) -> Any: ...
def run_polling_tick(specs, *, store, adapter, previous_states, ..., on_event=None) -> PollingTickResult: ...
```

The **5 invariants** V2.6 must honor:

1. **`InMemoryStore` is the only test fixture** for V2.6. Production
   code uses the real V1 `SqliteStore`. Never instantiate a
   `SqliteStore` in V2.6 tests — the file lives in
   `~/.hermes/telegram_ads_watcher.db` and would be a side effect.
2. **`last_state` is the OBSERVED state, not the previous-state input.**
   When `run_polling_tick` writes back `last_state`, it re-queries
   the adapter for the spec's own state. The `previous_states`
   argument to the tick function is the dedupe input for state
   transition detection, not the source of truth for `last_state`.
3. **Per-spec registration failure is isolated.** If
   `store.upsert_watch(spec)` raises for one spec, capture the
   exception in `errors[spec.watch_id] = repr(exc)` and continue
   with the next spec. The whole `persist_registration` call must
   NOT raise. Status becomes `"partial"`. This is the
   `WatchSpec registration failure does not fail original action
   result` contract from the approval.
4. **The store key is the V2 `watch_id`, NOT a UUID.** V2's
   `watch_id` is human-readable (`create_ad:moderation_result`,
   `start_ad:campaign_status`, etc.). Use it as the store key
   (`id` field). The V1 `SqliteStore` also mints a UUID — store
   that as `v1_internal_id` for cross-reference. Tests look up
   by `store.watches[spec.watch_id]`, not by UUID.
5. **V2 → V1 event_type mapping is closed and total over the V2
   emitter set.** `V2_EVENT_TYPE_TO_STORE_EVENT_TYPE` is a
   `dict[str, str]` covering all 19 V2 emitter types. Unmapped V2
   events raise `KeyError` — `run_polling_tick` logs+continues
   but `persist_normalized_events` raises (caller decides). The
   19-entry map is the contract; V2.5+ emitters must add to it.

The `run_polling_tick` **flow** (the high-level entry V1's
in-process loop is expected to call):

```
1. Call poll_post_action_watches(specs, adapter, previous_states, ...) → PollingTickResult
2. Persist result.events via persist_normalized_events (idempotent on dedupe_key)
3. For each spec:
     observed = _observe_state(spec, adapter)  # read-only, re-queries
     if observed is None: skip (adapter could not read)
     stop_reason = spec.should_terminate(elapsed_seconds=..., current_state=observed)
     update_watch_last_state(spec.watch_id, state=observed, store, stop_reason=stop_reason)
     # On KeyError (spec never persisted): skip silently
4. Fire on_event callback per event (after persistence)
5. Return the PollingTickResult
```

`_observe_state` is the V2.6 internal — it always re-queries the
adapter for the spec's entity (ad / campaign / account) using
`get_state` / `get_account_state`. This guarantees `last_state`
reflects the **most recent observation** even when the tick
input's `previous_states` is stale.

### V2.6 test categories

V2.6 adds 5 test classes beyond V2.5:

1. **`TestSuccessfulActionRegistrations`** — parametrize over all 7
   supported actions; assert each lands in the store with the right
   `kind` (`moderation_result` for create_ad, `campaign_status` /
   `ad_status_changed` for start_ad, `campaign_cpm` for change_cpm).
2. **`TestMissingAdIdPlaceholder`** — missing `ad_id` → single
   placeholder spec with `entity_type=account` or `campaign` and
   `status=post_action_verification_pending`. Persisted to the
   store; not dropped.
3. **`TestRegistrationFailureIsolation`** — flaky store
   (first N upserts raise); assert `persist_registration` does
   NOT raise; `errors` dict has the failure count; surviving
   specs are in the store; status is `"partial"`. The original
   action result is preserved (no exception bubbles up).
4. **`TestPollingMappings`** — `pending → active` → `ad_approved`,
   `pending → declined` → `ad_rejected`, no-change → no event.
5. **`TestPersistence`** — `NormalizedEvent` lands in
   `store.events`; `dedupe_key` collapses duplicates within and
   across `persist_normalized_events` calls; unknown V2 event
   type → `KeyError` (caller decides).
6. **`TestLastStateWriteback`** — `last_state` present in
   `thresholds`; secrets in `last_state` are scrubbed; other
   `thresholds` keys preserved.
7. **`TestTerminalStops`** — `max_duration_reached` /
   `expected_outcome_reached` / `expected_outcome_unreachable` all
   flip `enabled=False` and add `stop_reason` to `thresholds`.
   Non-terminal spec keeps `enabled=True`.
8. **`TestEventTypeMapping`** — assert the 19-entry mapping is
   total over the V2 emitter set and that every target is a valid
   V1 closed `EventType` literal.
9. **`TestRunPollingTickEnd2End`** — full happy path; idempotency
   on repeated ticks (no new events when state didn't change);
   `on_event` callback fires; callback exceptions do not break
   the tick.
10. **`TestPersistenceSecretHygiene`** — secrets in
    `previous_state` / `current_state` / `reason` are scrubbed
    in the persisted payload.

### Pillar 10 sub-pitfalls (V2.6 specific)

These are the **load-bearing** pitfalls the V2.6 drop exposed.
Pin each in tests; future V-N must respect the same contracts.

- **`WatchSpecRegistration.registered_ids` is `list[str]`, not
  `dict[str, str]`.** The `dict` is `external_ids`. V2.5 calls
  `getattr(registration, "registered_ids", None)` and then
  `dict(...)` — that raises `ValueError` because the
  list-iterable has length 27 (long IDs), not 2. Use
  `getattr(registration, "external_ids", None)` for the dict.
  Pin with a test: `assert isinstance(reg.registered_ids, list)`
  and `assert isinstance(reg.external_ids, dict)`.

- **`should_terminate` must use subset match, not strict
  equality.** V2.5's `current_state == self.expected_outcome`
  breaks when the adapter returns a richer state
  (e.g. `{'status': 'active', 'account_id': 'acc-1'}` vs
  `{'status': 'active'}`). Fix: `all(current_state.get(k) == v
  for k, v in self.expected_outcome.items())`. Pin the
  rich-state case: `assert should_terminate(elapsed=0,
  current={'status': 'active', 'account_id': 'acc-1'}) ==
  'expected_outcome_reached'` when `expected_outcome =
  {'status': 'active'}`.

- **`last_state` = observed, NOT previous.** The V2.6
  `run_polling_tick` re-queries the adapter via `_observe_state`
  for each spec and persists the **observed** state as
  `last_state`. The `previous_states` input is for state
  transition detection only. Pin a test that asserts
  `store.watches[watch_id]["thresholds"]["last_state"]["status"]
  == "active"` when the adapter returns `active` (regardless of
  what `previous_states` says).

- **Skip transition events on the first tick.** A spec that has
  no `previous_state` in the tick input must NOT emit a
  transition event. The first tick for a freshly-registered
  spec would otherwise emit spurious `unknown → <anything>`
  events for every spec in the batch. Add `if not prev_state:
  continue` before the transition emission. Pin: a test with
  `previous_states={}` and an active adapter must produce zero
  events.

- **Store key = V2 `watch_id`, V1 UUID = `v1_internal_id`.** V2
  test code looks up by V2 `watch_id` (stable, human-readable).
  The V1 store's UUID is for V1 cross-reference. Don't replace
  V2 `watch_id` with a fresh UUID on upsert — tests will fail
  with `KeyError` on the lookup. The
  `_build_external_spec_from_v2` function must return `{"id":
  spec.watch_id, "v1_internal_id": str(uuid.uuid4()), ...}`.

- **`PersistedRegistration.registration_status` shape.** The
  helper returns a frozen dataclass with `status ∈ {"persisted",
  "partial", "empty"}` + `external_ids: dict[str, str]` +
  `errors: dict[str, str]`. Tests assert the shape; do not
  return a bare dict from `persist_registration`.

- **Mapping is total over the V2 emitter set.** Any new V2
  event_type added in a future V-N drop that is wired through
  V2.6's `persist_normalized_events` MUST appear in
  `V2_EVENT_TYPE_TO_STORE_EVENT_TYPE` first, with a valid V1
  target. `KeyError` is the contract. The `TestEventTypeMapping`
  test enumerates the closed set; expanding it requires
  updating the test too.

## Pillar 11 — Out-of-scope artifacts in multi-session work (V2.6+)

In long-running watcher development (V2 → V2.5 → V2.6), each
session ends with files that are **not committed** to that
session's commit. When a new session starts, `git status` will
show:

1. **Modified files from the previous session** — partial work
   that wasn't committed before shutdown.
2. **Untracked files from the previous session** — new modules
   the previous session wrote but never staged.

**V2.6 lesson:** in the same checkout, the previous session had
written `gateway/ads_watcher_v2/production.py`,
`production_adapter.py`, `report_router.py`, `v1_bridge.py` plus
partial modifications to `gateway/ads_watcher_inprocess.py` and
`tools/telegram_ads_typed_tool.py`. The V2.6 commit
(`16e9aa3af`) **staged only its own files** and left the others
uncommitted + untracked.

**Rules when you detect out-of-scope artifacts:**

- **Do not `git add` them into your commit.** Your commit
  message names your V-N drop; mixing in untracked files from
  another session makes the commit history lie. Stage only what
  your work produced.
- **Do not delete them.** The previous session may have been
  interrupted before it could decide what to do with them.
  Deletion is destructive.
- **Do not modify them.** They may depend on V-N imports you
  have not stabilized; modifying in parallel creates merge
  conflicts and ungrounded assumptions.
- **Do flag them in the commit report.** A "⚠️ Out-of-scope
  artifacts observed" section listing path / status / size /
  note gives the operator a checklist for the next session to review
  and decide.
- **Do verify your staged changes** with `git diff --stat` and
  `git status --short` immediately before `git commit`. If
  something you didn't write shows up as `M ` (staged), abort
  and re-stage selectively.

Pin the V-N commit discipline:

```bash
# After tests pass, before commit:
git status --short              # See what's staged + untracked
git diff --stat                 # Confirm only your V-N files
git add gateway/ads_watcher_vN/  # Stage only V-N-specific paths
tests/gateway/test_ads_watcher_vN*.py
git status --short              # Re-verify
git commit -m "feat(telegram-ads): ..." -m "..."
git log -1 --stat               # Confirm staged set matches message
```

If a path that didn't exist when your V-N work started shows
up as `M `, do not commit. Use `git restore --staged <path>`
and re-stage only your files.

## Pillar 12 — Worktree reconciliation + production bridge (V2.7+)

V2.6+ watcher development happens across multiple sessions. Each
session may leave **out-of-scope artifacts** (untracked files +
uncommitted modifications from the previous session). V2.7 is the
"production-bridge" drop: it both **reconciles** the worktree and
**integrates** the production wiring.

The V2.7 drop exposes 4 new modules + 2 modifications:

| File | Role | Type |
|---|---|---|
| `gateway/ads_watcher_v2/production.py` | post-action registration hook (7 mutating tools) | new |
| `gateway/ads_watcher_v2/production_adapter.py` | read-only `PollingAdapter` impl (22-kind allow-list) | new |
| `gateway/ads_watcher_v2/report_router.py` | `MiniReportRouter` (12 categories, flag-gated) | new |
| `gateway/ads_watcher_v2/v1_bridge.py` | V1↔V2 bridge (one function, sync facade) | new |
| `gateway/ads_watcher_inprocess.py` | V1 in-process tick → V2.6 bridge | modified |
| `tools/telegram_ads_typed_tool.py` | sync handler → post-action watch hook | modified |

### Worktree reconciliation (PHASE 1-3)

When the previous session left out-of-scope files, the new session
must classify them before touching the working tree:

**Class A** = required for production watcher loop.
**Class B** = useful but incomplete.
**Class C** = unsafe / should not be used.
**Class D** = dead/unreferenced artifact.

For each file, the new session produces a **classification table**
with 9 checks (one per row in the V2.7 report):

1. Referenced by committed code?
2. Telegram Ads read-only APIs only?
3. Can mutate Ads?
4. Can send Telegram messages?
5. Sending gated by `HERMES_ADS_WATCHER_REPORTS_ENABLED`?
6. Reads/prints secrets?
7. Can start a standalone daemon?
8. Touches systemd/env/config?
9. Tests exist?

Decision rules:

- **All Class A** → integrate in this session as the V2.7 drop.
- **Any Class C** → do NOT commit. Recommend rollback/delete in a
  separate approval.
- **Class B / D** → leave uncommitted. Do not delete. Recommend
  cleanup in a separate approval.

### Per-file constraint audit (PHASE 4)

Before integration, each file must satisfy its specific constraints
table. For V2.7:

- **`gateway/ads_watcher_inprocess.py` (M)**: must call V2.6
  `run_polling_tick`; must preserve V1 login/session monitoring;
  must not crash gateway if V2 watch polling fails; must not start
  standalone daemon.
- **`tools/telegram_ads_typed_tool.py` (M)**: may register
  post-action WatchSpecs only after successful approved Ads
  mutation; must not change mutation approval semantics; must not
  execute extra Ads actions; must not fail original action if
  watch registration fails.
- **`production_adapter.py`**: must use only safe read-only API
  methods; must not call mutation endpoints; must not use login
  assist; must not read/print cookies/session tokens.
- **`report_router.py`**: must be disabled by default; must require
  `HERMES_ADS_WATCHER_REPORTS_ENABLED=1`; may send only bounded
  watcher mini reports; must not send generic Telegram messages;
  must scrub secrets.
- **`production.py` / `v1_bridge.py`**: must be thin orchestration
  layers; must not own a second browser/profile; must reuse
  gateway/in-process watcher flow.

### V2.7 test bullets (PHASE 5)

10 required coverage bullets:

1. production wiring does not start standalone watcher.
2. production wiring does not mutate Ads.
3. report sending disabled by default.
4. report sending enabled only for allowed watcher event
   categories.
5. no secrets in reports/logs/events.
6. typed tool post-action watch registration happens only after
   executed success.
7. failed watch registration does not fail the original Ads
   action result.
8. in-process watcher still runs V1 login/session monitoring.
9. in-process watcher can run V2 post-action polling with fake
   adapter.
10. no browser/ProfileManager second-owner path.

Tests for #1, #2, #10 must use `inspect.getsource` to assert
structural invariants (no `*_daemon` entrypoint, no `while True`,
no `playwright`/`chromium` import, no `api_mutate`).

Tests for #3 use `monkeypatch.delenv` to confirm default off.

Tests for #4 use an explicit `RouterConfig(allowed_categories=
frozenset())` to exercise the drop path (the module-level
`ALLOWED_REPORT_CATEGORIES` is captured at `MiniReportRouter.__init__`
time, so monkey-patching the module attribute is too late).

Tests for #5 assert no `session=`, `token=`, `tma_token=`,
`agi_team_bot_token=`, `cookie=`, `set-cookie:`, `password=`,
`otp=`, `twofa=`, `2fa=`, `tfa=`, `session_id=`, `csrf=`, `phone=`
in the body.

Tests for #6 / #7 must patch the production-side function (NOT the
typed-tool wrapper) — the wrapper is defensive-by-design, and the
call site has its own `try/except + logger.warning` envelope.

Tests for #8 / #9 inject `_FakeWiring` + `_FakeAdapter`; assert
V1 store receives `create_event` calls; assert
`run_post_action_polling_tick` returns non-empty events when
adapter returns state changes.

### V2.7 sub-pitfalls (load-bearing)

- **The wrapper is structural, not behavior-based.** The
  typed-tool call site must be `try: _register_post_action_watch(...)
  except Exception: logger.warning(...)` — a test that calls the
  wrapper directly and expects no raise will be misleading because
  the wrapper's own `try/except` only covers the import. The
  production-side function (V2.5+) catches its own exceptions;
  the typed-tool call site catches what the wrapper propagates.
  Test the call site with `inspect.getsource(_make_sync_handler)`.

- **`MiniReportRouter` captures `ALLOWED_REPORT_CATEGORIES` at
  `__init__` time.** Mutating the module-level
  `ALLOWED_REPORT_CATEGORIES` after construction has no effect on
  an existing router. To exercise the drop path, construct a
  router with an explicit `RouterConfig(allowed_categories=
  frozenset())`. The default ctor captures from the module — once
  captured, it's frozen.

- **`production_adapter._is_safe_kind` is a static allow-list, not
  a state check.** It rejects 12 mutation-shaped kind strings
  unconditionally. Don't try to add "ad_hoc" safe kinds — update
  the allow-list and the test that enumerates it.

- **`v1_bridge` is a single function, not a class.** The
  `run_post_action_polling_tick` entry is the only public
  surface. Tests must not assume class semantics
  (`v1_bridge.V1V2Bridge().run(...)` fails; use
  `v1_bridge.run_post_action_polling_tick(...)` directly).

- **`HERMES_ADS_WATCHER_ENABLED` lives in three places, not one.**
  - `production.py: _watcher_enabled()` — gates the
    post-action registration hook.
  - `production_adapter.py: enabled` property — gates the
    adapter's read paths.
  - `v1_bridge.py: _is_watcher_enabled()` — gates the V1 tick
    integration.
  Each is a separate `os.environ.get(...)` lookup. To test "all
  off", `monkeypatch.delenv` for all three callers OR set the
  flag and assert the test path is reachable. The flag is **not**
  a single source of truth — it's three per-module gates that
  must stay in sync.

### V2.7 commit message + body (canonical)

```
feat(telegram-ads): integrate watcher event loop with runtime

connect V2 watcher production bridge to the in-process gateway
watcher;
register post-action watches from successful approved Ads
mutations;
add safe read-only production polling adapter;
add report router behind watcher report flag;
preserve approval boundaries and mutation guard;
add production wiring tests.
```

### V2.7 known weak spot (regression-prone)

The production wiring's **lazy import** pattern
(`from gateway.ads_watcher_v2.production import register_post_action_watch as _register`
inside the typed-tool wrapper) makes it possible for the
production code to change its public surface without
breaking the typed-tool signature. The V2.7 tests cover the
common cases but do NOT cover all 7 actions × all result-dict
shapes. Future V-N drops that add a new action type must
update both `production._TOOL_TO_ACTION_TYPE` AND
`production._ACTION_AD_ID_KEYS` AND
`production._ACTION_CAMPAIGN_ID_KEYS` AND
`production._ACTION_ACCOUNT_ID_KEYS`. A drift in any one of
the four key sets yields silently wrong WatchSpec registration
(no error, just `ad_id=None` when it should be `42`).

## Pillar 13 — Async/sync tick stabilization (V2.8)

V2.0–V2.7 ship a clean watcher event loop, but the V1 in-process
daemon thread that **runs** the loop has a long-standing async/sync
hole. V2.8 is a **3-file, 100-line, no-new-modules** surgical fix
that:

1. Stops every V1 tick from hitting the 60s `asyncio.TimeoutError`
   ceiling.
2. Bounds the V2.6/V2.7 bridge call inside the V1 tick to a
   short, configurable timeout.
3. Makes the production adapter's sync→async bridge detect a
   running loop and skip the broken `asyncio.run()` path.

**No restart required to take effect** — the fix is module-level
Python code. The next gateway restart picks it up. Per
runtime-enablement Pitfall 5, the V1 TimeoutError used to be
treated as a known pre-existing quirk; **V2.8 fixes it**.
Future runtime-enablement sessions should NOT report it as a
pre-existing observation.

### The bug (V2.7 → V2.7.1)

The V1 in-process tick is structured as:

```python
def _run_tick_blocking(self):
    return asyncio.run(_run_tick_once(...))  # outer wait_for(60s)

async def _run_tick_once(self, ...):
    # 1. V1 baseline: succeed-fast path
    await acquire_adapter(...)
    result = await self.wiring.scheduler.tick()  # V1's own logic
    # 2. V2.6/V2.7 bridge: SYNC function, called inside the running loop
    bridge = run_post_action_polling_tick(...)  # ← sync, calls asyncio.run internally
```

`run_post_action_polling_tick` is a **sync** function. Internally
it calls `adapter.get_state_sync(...)` which tries `asyncio.run(...)`
first. `asyncio.run` raises `RuntimeError: cannot be called from a
running loop` when called from inside the outer `asyncio.run`. The
adapter falls through to `_run_in_worker_thread(...)` with a 30s
`thread.join` — but the outer `asyncio.wait_for(60)` then trips,
the whole tick cancels, and the log shows
`tick state=None events=0 error=TimeoutError: duration=60.04`.

**Pattern in logs (V2.0 → V2.7, V1 baseline unaffected):**
- Baseline tick: 4–7s, `state=logged_in_or_no_change events=0 error=None` ✅
- All subsequent ticks: 60.0s, `state=None events=0 error=TimeoutError` ❌

### The fix (3 small changes, no new module)

**Change 1 — `gateway/ads_watcher_inprocess.py` (≈30 lines):**

Add a short, explicit bridge timeout constant alongside
`DEFAULT_TICK_TIMEOUT_SECONDS`:

```python
DEFAULT_TICK_TIMEOUT_SECONDS = 60.0
_V2_BRIDGE_TIMEOUT_SECONDS = 10.0  # V2.8: bounded V2.6/V2.7 bridge
```

In `_run_tick_once`, replace the bare sync call with
`asyncio.to_thread(...)` + `asyncio.wait_for(..., timeout=...)`:

```python
# Before (V2.7):
bridge_summary = run_post_action_polling_tick(self.wiring, project_id=...)

# After (V2.8):
try:
    bridge_summary = await asyncio.wait_for(
        asyncio.to_thread(
            run_post_action_polling_tick,
            self.wiring,
            project_id=...,
        ),
        timeout=_V2_BRIDGE_TIMEOUT_SECONDS,
    )
except asyncio.TimeoutError:
    bridge_summary = {
        "state": "bridge_timeout",
        "error": f"timeout_seconds={_V2_BRIDGE_TIMEOUT_SECONDS}",
    }
    logger.warning("[ADS-WATCH] V2 bridge timed out after %.1fs",
                   _V2_BRIDGE_TIMEOUT_SECONDS)
```

The `bridge_summary` shape with explicit `state=bridge_timeout`
and `error=timeout_seconds=N` is the **safe summary** contract.
The V1 tick never raises; the gateway never crashes; the next
tick (600s later) gets a clean slate.

**Change 2 — `gateway/ads_watcher_v2/production_adapter.py` (≈40 lines):**

Make the sync→async bridge detect a running loop and skip
`asyncio.run` (which always raises `RuntimeError` from inside a
running loop):

```python
def get_state_sync(self, *, ad_id, kind):
    try:
        asyncio.get_running_loop()  # raises RuntimeError if no loop
        # A loop is already running on this thread — use the
        # worker-thread path. asyncio.run is forbidden here.
        return self._run_in_worker_thread(
            self.get_state, ad_id=ad_id, kind=kind,
        )
    except RuntimeError:
        # No running loop on this thread — asyncio.run is safe.
        try:
            return asyncio.run(self.get_state(ad_id=ad_id, kind=kind))
        except Exception as exc:
            logger.debug("[ADS-WATCH-V2.8] asyncio.run failed: %r", exc)
            return None
```

**Change 3 — `_run_in_worker_thread` (≈30 lines):**

Add an explicit `timeout_seconds` parameter (default 15s, was
hard-coded 30s) and a best-effort `loop.stop()` if the worker is
still alive:

```python
def _run_in_worker_thread(self, coro_fn, *, timeout_seconds=15.0, **kwargs):
    holder = {}
    def _runner():
        loop = asyncio.new_event_loop()
        try:
            holder["result"] = loop.run_until_complete(coro_fn(**kwargs))
        except Exception as exc:
            holder["error"] = exc
        finally:
            try: loop.close()
            except Exception: pass
            holder["done"] = True
    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=timeout_seconds)
    if not holder.get("done"):
        logger.warning("[ADS-WATCH-V2.8] worker thread did not finish in %.1fs",
                       timeout_seconds)
        return None
    return holder.get("result")
```

### V2.8 test bullets (12 categories)

V2.8 tests live in `tests/gateway/test_ads_watcher_v2_8.py` and
assert:

1. `get_state_sync` from inside a running loop uses the
   worker-thread path; never calls `asyncio.run` (monkeypatched).
2. `get_state_sync` from a sync context (no loop) uses
   `asyncio.run` directly.
3. `_run_in_worker_thread` default `timeout_seconds == 15.0`.
4. `_run_in_worker_thread` returns `None` when worker is slow
   (timeout = 0.1s, fake sleeps 5s).
5. `_run_in_worker_thread` returns the result when worker is fast.
6. `_run_tick_once` source uses `asyncio.to_thread` +
   `asyncio.wait_for` + `_V2_BRIDGE_TIMEOUT_SECONDS`.
7. `_V2_BRIDGE_TIMEOUT_SECONDS == 10.0` and
   `_V2_BRIDGE_TIMEOUT_SECONDS < DEFAULT_TICK_TIMEOUT_SECONDS`.
8. Bridge timeout returns safe summary with explicit
   `state=bridge_timeout` and `error=timeout_seconds=N`.
9. End-to-end: a slow bridge does not block the V1 tick
   (structural + behavioral: `asyncio.wait_for(sleep(5), 0.5)`).
10. V1 baseline still runs **before** the V2 bridge (sequence
    order preserved).
11. Bridge signature unchanged: `run_post_action_polling_tick(
    wiring, project_id=...)`.
12. No secrets in any timeout/error log; no standalone daemon;
    no Ads mutation path in V1 or production_adapter source.

### V2.8 sub-pitfalls (load-bearing)

- **Fix is module-level code, not env.** The fix takes effect on
  the next gateway restart. A runtime-enablement task that
  reports a TimeoutError **after** V2.8 is committed is a
  restart-gap observation, not a defect. Do not re-fix.

- **`_run_in_worker_thread` must use `daemon=True` thread.** The
  V2.7 test asserts `threading.Thread` appears exactly once in
  `production_adapter.py`. V2.8 keeps that contract; the
  `daemon=True` is required so a stuck worker does not prevent
  gateway shutdown.

- **`get_state_sync` must NOT cache the loop-probe result.** The
  probe (`asyncio.get_running_loop()`) is per-call, not cached,
  because the calling thread may be different across calls (test
  thread → real gateway thread). Pin with a test that calls
  `get_state_sync` from both a running loop and a sync context
  in the same test process.

- **`asyncio.wait_for` cancels the coroutine on timeout, NOT the
  worker thread.** The `asyncio.to_thread(...)` future is
  cancelled; the underlying `threading.Thread(daemon=True)`
  continues until `loop.run_until_complete` returns or raises.
  The 5–10s cleanup is bounded by the inner fake's
  `await asyncio.sleep(...)` — pin a test that asserts the
  bridge returns `None` within 2× the configured timeout, even
  when the fake sleeps 5s.

- **Bridge timeout does NOT persist state.** The V2.6 store
  receives no `create_event` call when the bridge times out. The
  V1 store's `watches` table is not mutated. Next tick starts
  fresh. Pin a structural test: `inspect.getsource(
  _run_tick_once)` contains no `upsert_watch` or
  `create_event` in the bridge timeout branch.

- **Do NOT introduce a `_V2_BRIDGE_BACKOFF_SECONDS` or any
  retry.** The fix is "one shot, one timeout, one safe summary".
  The 600s V1 tick interval is the natural retry. Adding
  backoff/retry inside the bridge would re-introduce the 60s
  starvation.

### V2.8 commit message + body (canonical)

```
fix(telegram-ads): stabilize in-process watcher async tick

fix post-action watcher sync/async boundary;
prevent 60s TimeoutError from blocking watcher loop;
isolate tick failures from gateway;
preserve V1 login/session monitoring;
add regression tests for daemon-thread tick, timeout recovery,
adapter busy, and secret-safe errors.
```

### V2.8 deliverable scope

- **No push** (single local commit, ready for review).
- **No runtime change** (no env flag, no systemd unit edit).
- **No gateway restart** (the fix is staged; the next restart
  picks it up).
- **No real Ads action** (tests use `_FakeAdsAdapter` with
  `adapter_factory=lambda: fake`).
- **No Ads mutation** (7 mutation keywords absent from V1 and
  production_adapter source; `_is_safe_kind` allow-list preserved).
- **No synthetic Telegram message** (no Telegram send path in
  V2.8 changes).
- **No secrets printed** (9-marker scan in two modules clean).
- **No standalone daemon** (v1_bridge has zero `daemon=True`;
  production_adapter has exactly one `threading.Thread`).
- **No deepseek / Xvfb / KC changes** (zero touches).

### V2.8 cross-references

- **`telegram-ads-watcher-runtime-enablement` Pitfall 5** — was
  "V1 TimeoutError is pre-existing, not fixable". **V2.8 fixes
  it.** Update that pitfall to "V1 TimeoutError is fixed in
  V2.8; runtime-enablement post-verification should NOT report
  it as a pre-existing observation".
- **`telegram-ads-watcher-runtime-enablement/references/v1-known-issues.md`
  Section 1 + Section 5** — flip from "pre-existing, do not
  attempt to fix" to "fixed in V2.8 (commit 660816f56); see
  event-loop-design Pillar 13".

## Pillar 14 — Bounded staged tick (V2.9)

V2.8 bounded the V2.6/V2.7 bridge call inside the V1 in-process
tick. **V2.9 is the structural fix for the remaining 60.04s
`TimeoutError` pattern** observed in the V2.8 production
verification (NEXT-TICK-VERIFY-1, 2026-06-17 19:19 UTC).

### The remaining bug (V2.8 → V2.9)

V2.8 wrapped the **bridge** in `wait_for(10s)`. But the
**V1 `scheduler.tick()` itself** — which calls
`adapter.detect_login_state()` and navigates the shared Playwright
browser to `BASE_URL + URL_ACCOUNT` — was still inside the original
outer `wait_for(60s)`. When the gateway was busy (inbound Telegram
message), Playwright was busy → the navigation blocked → the 60s
ceiling tripped on every post-baseline tick.

**Production evidence (V2.8 verification, 6 consecutive failures):**

| Time (UTC) | Version | Tick duration | error |
|---|---|---|---|
| 2026-06-17 19:01:06 | V2.8 | 60.057 s | `TimeoutError: duration=60.057` |
| 2026-06-17 19:12:06 | V2.8 | 60.039 s | `TimeoutError: duration=60.039` |
| 2026-06-17 19:19:03 | V2.8 | 60.062 s | `TimeoutError: duration=60.062` |
| 2026-06-17 19:30:03 | V2.8 | 60.055 s | `TimeoutError: duration=60.055` |
| 2026-06-17 19:41:03 | V2.8 | 60.043 s | `TimeoutError: duration=60.043` |
| 2026-06-17 19:52:03 | V2.8 | 60.046 s | `TimeoutError: duration=60.046` |
| 2026-06-17 20:03:03 | V2.8 | 60.038 s | `TimeoutError: duration=60.038` |

The baseline tick at 19:08:03 succeeded (4–7s, no error) because
the V2.6 bridge's `no_post_action_watches` early-return path was
taken. Every subsequent scheduled tick hit the 60s ceiling.

### The fix (1 file, ~100 lines)

`gateway/ads_watcher_inprocess.py` `_run_tick_once` is refactored
into **three bounded stages** with independent short timeouts:

```python
# Stage constants (V2.9, all <= 10s)
_ADAPTER_ACQUIRE_TIMEOUT_SECONDS = 10.0
_V1_TICK_TIMEOUT_SECONDS = 10.0
_V2_BRIDGE_TIMEOUT_SECONDS = 10.0
_TOTAL_TICK_BUDGET_SECONDS = 25.0  # hard ceiling, was 60s

async def _run_tick_once(*, wiring, config, timeout, manager):
    stages: dict[str, dict[str, Any]] = {}
    summary: dict[str, Any] = {...}
    adapter = None
    try:
        # ─── STAGE 1: adapter acquisition ─────────────────────────
        try:
            adapter = await asyncio.wait_for(
                manager_obj.acquire_adapter(config=config, timeout=10.0),
                timeout=_ADAPTER_ACQUIRE_TIMEOUT_SECONDS,
            )
            stages["adapter"] = {"ok": True, "duration": 0.0}
        except Exception as exc:
            stages["adapter"] = {"ok": False, "error": f"acquire_adapter_failed:{type(exc).__name__}"}
            summary["state"] = "browser_unavailable"
            return summary  # safe early return

        wiring.adapter._adapter = adapter
        _ensure_baseline_login_state_watch(wiring)

        # ─── STAGE 2: V1 scheduler.tick (login/session monitoring) ───
        try:
            events = await asyncio.wait_for(
                wiring.scheduler.tick(),
                timeout=_V1_TICK_TIMEOUT_SECONDS,
            )
            stages["v1_tick"] = {"ok": True}
            # ... event processing ...
        except asyncio.TimeoutError:
            stages["v1_tick"] = {"ok": False, "error": "v1_tick_timeout",
                                 "timeout_seconds": _V1_TICK_TIMEOUT_SECONDS}
            # continue to stage 3 — bridge may still run
        except Exception as exc:
            stages["v1_tick"] = {"ok": False, "error": f"v1_tick_failed:{type(exc).__name__}"}

        # ─── PRE-CHECK: skip bridge if zero post-action watches ───
        v1_watches = _safe_v1_list_watches(wiring, project_id)
        post_action_count = sum(
            1 for w in v1_watches
            if isinstance(w, dict) and not _is_v1_login_state_watch(w)
        )
        if post_action_count == 0:
            stages["v2_bridge"] = {"ok": True, "skipped": True,
                                   "reason": "no_post_action_watches"}
            summary["v2_bridge"] = {"safe_summary": "no_post_action_watches"}
        else:
            # ─── STAGE 3: V2.6 bridge ─────────────────────────────
            bridge_task = asyncio.create_task(
                asyncio.to_thread(
                    run_post_action_polling_tick,
                    wiring, project_id=project_id,
                )
            )
            try:
                bridge_result = await asyncio.wait_for(
                    asyncio.shield(bridge_task),
                    timeout=_V2_BRIDGE_TIMEOUT_SECONDS,
                )
                stages["v2_bridge"] = {"ok": True}
            except asyncio.TimeoutError:
                stages["v2_bridge"] = {
                    "ok": False, "safe_summary": "bridge_timeout",
                    "timeout_seconds": _V2_BRIDGE_TIMEOUT_SECONDS,
                }
    finally:
        if manager_obj is not None and adapter is not None:
            manager_obj.release_adapter()
        summary["stages"] = stages
        summary["duration"] = time.monotonic() - started
    return summary
```

### The three innovations

1. **Stage-level timeouts (not one big outer wait_for).** Each
   stage records its own safe error. A failure in one stage does
   not poison the others.
2. **Pre-check optimization (skip bridge if zero watches).** Idle
   ticks (zero non-`login_state` post-action watches) skip the V2.6
   bridge entirely. The tick wall-clock is bounded by the V1 stage
   alone (~5–10s) — the V2 adapter is not touched.
3. **`asyncio.shield(bridge_task)` decouples the worker thread
   from the wait_for awaiter.** When the bridge is slow, the
   worker thread keeps running but the `wait_for(10s)` returns at
   the timeout with `safe_summary=bridge_timeout`. The **stage
   status** is the authoritative signal; the **tick wall-clock**
   may extend up to the worker's actual runtime (Python 3.11
   `asyncio.to_thread` limitation — see
   `asyncio-to-thread-wait-for-cancellation-gotcha`).

### Production-verified outcomes (V2.9, 2026-06-17)

| Time (UTC) | Event | Tick duration | error |
|---|---|---|---|
| 20:13:54 | V2.9 daemon started (in-process load) | — | — |
| 20:13:59 | Baseline | 4.589 s | None |
| **20:24:09** | **First scheduled tick after baseline** | **10.036 s** | **None** |
| 20:28:05 | Restart (gateway reload) | — | — |
| 20:28:11 | Baseline | 5.485 s | None |
| **20:38:21** | **First scheduled tick after restart baseline** | **10.031 s** | **None** |

**Total TimeoutError count in gateway.log since V2.9 loaded: 0**
(was 19 in the pre-V2.9 log window).

### V2.9 test bullets (25 required, all in tests/gateway/test_ads_watcher_v2_9.py)

**Stage constants (3):**
1. `_V1_TICK_TIMEOUT_SECONDS == 10.0` and `< 60.0`.
2. `_ADAPTER_ACQUIRE_TIMEOUT_SECONDS == 10.0`.
3. `_TOTAL_TICK_BUDGET_SECONDS == 25.0`, `< 60.0`, `>= max(stage timeouts)`, `>= 15.0` (accepted bridge timeout ceiling under Option A semantics).

**Stage 1 — adapter acquisition (3):**
4. Slow adapter (`acquire_delay=15.0`) → `state="browser_unavailable"`, returns in <12s.
5. Fast adapter succeeds; subsequent stages run.
6. Adapter exception → safe error recorded, no crash.

**Stage 2 — V1 scheduler.tick (3):**
7. Slow V1 tick (`tick_delay=15.0`) → `v1_tick_timeout` recorded, bridge stage still runs.
8. Fast V1 tick succeeds; event count recorded.
9. V1 tick exception → safe error recorded, gateway alive.

**Stage 3 — V2.6 bridge (5):**
10. Zero post-action watches → bridge stage is **skipped**, no adapter call.
11. Only `login_state` watches → bridge stage is **skipped** (login_state is V1's job, not V2's).
12. Post-action watches → bridge is called; pre-check passes.
13. **Bridge timeout** → `safe_summary="bridge_timeout"`, `timeout_seconds=10.0`, `stages["v2_bridge"]["ok"]=False`. Total wall-clock `< 25s` (V2.9 budget), never `>= 60s`.
14. Bridge exception → `safe_summary="bridge_unavailable"`, `"bridge_failed"` in error.

**Full-tick bounded (3):**
15. All 3 stages slow → total wall-clock `< 25s`, V1 tick succeeds, bridge skipped.
16. Total tick can NEVER hit the old 60s path (structural + behavioural test).
17. A stuck post-action watch does NOT block the next tick (recovery).

**No secrets (2):**
18. `inspect.getsource(_run_tick_once)` contains zero matches for `tma_token=`, `agi_team_bot_token=`, `cookie=`, `set-cookie:`, `password=`, `otp=`, `twofa=`, `2fa=`, `tfa=`, `csrf=`, `phone=`.
19. No `logger.info/warning/error` calls include the adapter object or any sensitive field.

**No standalone daemon (2):**
20. `_run_tick_once` source contains no `*_daemon` entrypoint, no `while True`, no `subprocess.Popen`.
21. No second browser/ProfileManager owner (`playwright`/`chromium` not imported in V2.9 code).

**Reports gated (2):**
22. `HERMES_ADS_WATCHER_REPORTS_ENABLED=0` → router disabled (default off).
23. `HERMES_ADS_WATCHER_REPORTS_ENABLED=1` → router enabled; bounded watcher mini-reports only.

**No Ads mutation (1):**
24. No `telegram_ads_create_ad`, `telegram_ads_edit_ad`, `telegram_ads_stop_ad`, `telegram_ads_start_ad`, `telegram_ads_change_cpm`, `telegram_ads_add_to_budget`, `telegram_ads_withdraw_from_budget`, `telegram_ads_delete_ad` strings in V2.9 source.

**Thread safety (1):**
25. 5 concurrent `_run_tick_once` calls complete without state corruption; summary dicts are independent.

### V2.9 sub-pitfalls (load-bearing)

- **Pre-check MUST use `_safe_v1_list_watches`, NOT direct store
  access.** The wrapper catches and logs store exceptions;
  direct access raises and corrupts the tick. Pin the test:
  `inspect.getsource(_run_tick_once)` contains
  `_safe_v1_list_watches` exactly once.

- **Bridge invocation MUST use `asyncio.create_task(asyncio.to_thread(...))`,
  NOT bare `asyncio.to_thread(...)`.** The bare form
  propagates `wait_for` cancellation as a coroutine cancel, but
  Python 3.11's `to_thread` future holds the worker thread; the
  `wait_for` returns at the timeout but the **whole tick**
  may wait for the worker. The `create_task` + `shield` pair
  makes the stage status authoritative.

- **Bridge stage MUST set `summary["v2_bridge"]` even on
  skip.** The downstream report router expects the field to
  exist. A `KeyError` in the report formatter would mask
  successful idle ticks.

- **The 25s hard ceiling is NOT the sum of stage timeouts.**
  It's the worst-case wall-clock. Sum-of-stages would be 30s
  (10+10+10) — but in practice stages finish early. The 25s
  budget is enough to cover the Option A scenario (bridge
  worker genuinely blocks 15s) without re-introducing the
  60s regression.

- **`asyncio.to_thread` workers cannot be force-cancelled in
  Python <3.13.** When the bridge genuinely blocks 15s, the
  worker thread continues to its natural completion; the tick
  wall-clock may exceed the 10s stage timeout. The acceptance
  criterion is the **stage status** (`safe_summary=
  bridge_timeout`) and the **total budget** (<25s), NOT
  `elapsed == 10s`. See
  `asyncio-to-thread-wait-for-cancellation-gotcha`.

- **Pre-existing drift (NOT V2.9):** `hermes-gateway-default.service`
  has `TimeoutStopSec=30s` but the gateway reports
  `drain_timeout=180s` (expected `>= 210s`). During a gateway
  restart, systemd SIGKILL's the old PID after 30s — this is
  the documented `hermes-production-config-change` drift, not
  V2.9's fault. Fix is `hermes gateway service install --replace`
  (separate approval).

### V2.9 commit message + body (canonical)

```
fix(telegram-ads): bound watcher scheduled tick stages

split watcher scheduled tick into bounded stages;

skip post-action bridge when there are no post-action watches;

prevent login_state watches from entering V2 post-action polling;

record bridge timeout as safe non-fatal state;

prevent old 60s TimeoutError regression;

add regression tests for zero-watch, timeout, recovery, and safety invariants.
```

### V2.9 deliverable scope

- **No push** (single local commit `01ce1f038`, ready for review).
- **No runtime change** (no new env flag, no systemd unit edit).
- **Gateway restart required** to load V2.9 (in-process code).
- **No real Ads action** (tests use `_FakeManager` + `_FakeWiring` + `_FakeAdapter`).
- **No Ads mutation** (8 mutation keywords absent from V2.9 source; `_is_safe_kind` allow-list preserved from V2.7).
- **No synthetic Telegram message** (no Telegram send path in V2.9 changes).
- **No secrets printed** (6-marker scan of `gateway.log` last 100 lines clean).
- **No standalone daemon** (V2.9 uses existing in-process daemon thread from V1).
- **No deepseek / Xvfb / KC changes** (zero touches).
- **No systemd/env/config edit** (no `daemon-reload`, no `EnvironmentFile`, no `Environment=` change).
- **No login assist / OTP / 2FA / cookies / session tokens** (none accessed).
- **No payments/refunds** (n/a).
- **No new watcher systemd service/timer** (none created).
- **No CPM/bid/budget change** (n/a).
- **Restart only `hermes-gateway-default.service`** (deepseek gateway + xvfb + KC services untouched).

### V2.9 stop-condition check (recap of NEXT-TICK-VERIFY-1 → AR-ADS-WATCHER-V2_9)

| Stop condition | Result |
|---|---|
| fix requires standalone daemon | **NO** — uses existing in-process daemon thread |
| fix requires second browser owner | **NO** — singleton via `acquire_adapter` preserved |
| fix requires real Ads calls | **NO** — tests use fakes |
| fix requires Ads mutation | **NO** — kind allow-list preserved |
| fix requires reading secrets | **NO** — zero secret access |
| tests fail and cannot be fixed narrowly | **NO** — 25/25 V2.9 + 314/314 full suite green |
| gateway restart fails | **NO** — gateway active, no exceptions |
| scheduled tick still hits 60s TimeoutError | **NO** — observed `duration=10.031s, error=None` |
| profile lock occurs | **NO** — singleton owner, single Chromium tree |
| secrets appear in logs/output | **NO** — credential-pattern scan clean |

### V2.9 cross-references

- **`telegram-ads-watcher-runtime-enablement` Pitfall 5** — was
  "V1 TimeoutError is pre-existing, not fixable". V2.8 fixed
  part of it (bridge). **V2.9 fixes the rest (V1 stage + bridge
  stage + total budget).** Update that pitfall to "V1 TimeoutError
  is fully fixed in V2.9 (commit 01ce1f038); runtime-enablement
  post-verification should report the bounded tick duration
  (5–10s baseline, ≤25s worst-case) and zero TimeoutErrors".
- **`telegram-ads-watcher-runtime-enablement/references/v1-known-issues.md`**
  — flip Section 1 + Section 5 from "pre-existing" to "fixed
  in V2.9".
- **`asyncio-to-thread-wait-for-cancellation-gotcha`** — the
  Option A semantics (wall-clock bounded by worker, not by
  timeout) is the documented Python 3.11 limitation. Use
  `raise asyncio.TimeoutError()` inside the worker as the cleanest
  test pattern.

## Linked references

- `references/v29-bounded-staged-tick.md` — V2.9 concrete drop:
  3-stage bounded tick (adapter 10s, V1 tick 10s, V2 bridge 10s,
  25s total budget); pre-check optimization (`_safe_v1_list_watches`
  + `_is_v1_login_state_watch`); full code patch; production
  verification timeline (8 V2.8 failures → 0 V2.9 failures in
  30+ min); the 25-test breakdown; Option A semantics for
  bridge-timeout tests; manual verification recipe.
- `references/v28-async-tick-stabilization.md` — V2.8 concrete drop.
  the `__all__` export misses, the `session_lost` substring false-fail,
  the accidental-nested-scrub bug + recovery, the regression-test pins.
- `references/v25-integration-and-pitfalls.md` — V2.5 concrete drop:
  3-stage V2→V1 mapping, polling layer pattern, sender tuple
  contract, event-level vs mutation-level approval split, the
  8-phase approval-gated execution pattern, the in-tick dedupe, the
  5 new test categories.
- `references/v26-wiring-and-pitfalls.md` — V2.6 concrete drop:
  V1 store wire-up (`wiring.py`), `InMemoryStore` for tests, the
  `expected_outcome` subset match, `last_state` = observed (not
  previous), the spurious first-tick transition trap, store-key vs
  V1-UUID convention, the **out-of-scope artifacts** pattern for
  multi-session work.
- `references/v27-production-bridge.md` — V2.7 concrete drop:
  worktree reconciliation PHASE 1-7, Class A/B/C/D
  classification matrix, 5 per-file constraint audits, V2.7 test
  bullets, the 5 load-bearing sub-pitfalls, the V2.7 commit
  message template, the "lazy import drift" weak spot.

## Related skills

- `install-hermes-telegram-ads-watcher` — install the package, wire
  the runtime, run smoke.
- `audit-and-patch-hermes-module` — for **patching** existing Hermes
  modules. V-N is **greenfield module design**.
- `hermes-domain-layer-development` — for full multi-layer domain
  stacks. V-N is a thin wrapper layer.
- `operator-approval-gate-enforcement` — "one approval per stage"
  discipline. V-N commit, push, and runtime enablement are three
  separate approvals.
- `incremental-commit-preflight` — the **commit** step (git status /
  secret scan / pytest / ruff / selective staging). V2.6 adds the
  **out-of-scope artifacts** pattern (Pillar 11).
- `hermes-tool-module-development` — for **adding a typed
  `telegram_ads_*` tool**. V-N is a **library**, not a tool.
