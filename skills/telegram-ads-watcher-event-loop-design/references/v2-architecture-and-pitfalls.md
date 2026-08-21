# V2 Architecture & Pitfalls — session-specific reference

The concrete V2 drop on 2026-06-17 (commit `43ef86532`,
`feat(telegram-ads): add watcher event loop and post-action watch specs`,
local commit only, no push). Use this as the canonical "V3 reads this
before starting" reference.

## Files

| File | LOC | Role |
|---|---|---|
| `gateway/ads_watcher_v2/__init__.py` | 116 | Public surface (29 exports) |
| `gateway/ads_watcher_v2/envelope.py` | 311 | AutonomyEnvelope + MutationGuard |
| `gateway/ads_watcher_v2/events.py` | 305 | NormalizedEvent + scrub helpers |
| `gateway/ads_watcher_v2/report.py` | 163 | MiniReport + format_mini_report + flag |
| `gateway/ads_watcher_v2/watchspec.py` | 280 | PostActionWatchSpec + recipes |
| `tests/gateway/test_ads_watcher_v2.py` | 671 | 67 tests |
| **Total** | **1846** | 6 new files, 0 modified |

## Test totals (V2 drop)

| Suite | Tests | Result |
|---|---|---|
| `tests/gateway/test_ads_watcher_v2.py` | 67 | ✅ passed (0.62s) |
| `tests/test_ads_watcher_inprocess.py` (V1) | 37 | ✅ passed (7.31s) |
| `tests/test_telegram_ads_config_loader.py` | 12 | ✅ passed |
| `tests/test_telegram_ads_typed_wrapper.py` | 25 | ✅ passed |
| `tests/knowledge_compiler/test_kc_runtime.py` | 51 | ✅ passed (2.88s) |
| `tests/gateway/test_planned_stop_watcher.py` | 11 | ✅ passed |
| **Total** | **166** | **✅ 0 failed** (13.39s) |

## Module-by-module recap

### `__init__.py` (116 LOC, 29 exports)

Public surface mirrors `__all__`. **Required exports** (the test file
expects all of these; missing any triggers an `ImportError` at
collection time):

- `ALLOWED_FIX_TYPES`, `AutonomyEnvelope`, `DEFAULT_MAX_CPM`,
  `DEFAULT_MAX_DURATION_SECONDS`, `DEFAULT_MAX_RETRY_COUNT`,
  `DEFAULT_MAX_TOTAL_BUDGET`, `DEFAULT_WATCH_MAX_DURATION_SECONDS`
  (alias to avoid name clash with envelope's default),
  `FixType`, `MutationBlockedError`, `MutationGuard`, `guard_mutation`,
  `is_never_allowed_fix`, `known_fix_types`, `never_allowed_categories`,
  `parse_envelope` (envelope)
- `NormalizedEvent`, `build_normalized_event`, `category_for`,
  `is_autonomous_mini_report_allowed`, `report_categories`,
  `scrub_free_text`, `scrub_payload` (events)
- `REPORTS_ENABLED_FLAG`, `MiniReport`, `format_mini_report`,
  `is_reports_enabled`, `reports_enabled_flag_name`, `would_send_report`
  (report)
- `PostAction`, `PostActionWatchSpec`, `create_post_action_watchspecs`,
  `known_stop_conditions` (watchspec)

**Pitfall I hit:** shipped V2 without exporting
`category_for`, `REPORTS_ENABLED_FLAG`, `known_fix_types`,
`never_allowed_categories`, `known_stop_conditions`,
`would_send_report`, `scrub_free_text`, `DEFAULT_WATCH_MAX_DURATION_SECONDS`.
Pytest caught each one with a separate `ImportError` collection
failure. Fixed by iteratively adding to the import block + `__all__`
list. **V3 lesson: on first write, export every helper the test file
imports — don't ship then discover.**

### `envelope.py` (311 LOC)

- `FixType` literal: 5 items (copy_edit, creative_swap,
  audience_no_change, budget_no_change, cpm_no_change).
- `ALLOWED_FIX_TYPES`: frozenset, same 5 items.
- `NEVER_ALLOWED_CATEGORIES`: frozenset, 9 items:
  - launch_stop_edit_ads, change_cpm_bid_budget, change_audience,
    change_url, payments_refunds, login_otp_2fa,
    read_print_store_cookies_or_session_tokens,
    send_non_report_telegram_messages, actions_outside_envelope
- `AutonomyEnvelope`: frozen dataclass, deny-all defaults.
- `parse_envelope(payload)`: filters unknown fix types, clamps
  negatives, validates `allowed_time_window` shape (must be 2-tuple of
  strings), drops non-string stop/escalation conditions. **Never
  raises.**
- `MutationGuard.check(envelope, fix_type, retries_used)`: raises
  `MutationBlockedError` with structured reason — closed vocabulary of
  5: `envelope_missing`, `envelope_inactive`, `category_never_allowed`,
  `fix_type_not_whitelisted`, `retry_budget_exhausted`.

### `events.py` (305 LOC)

- `NormalizedEvent`: frozen dataclass with 15 fields (event_id,
  event_type, severity, project, account_id, ad_id, approval_id,
  watch_id, previous_state, current_state, detected_at,
  recommended_action, requires_approval, safe_summary, report_category).
- `REPORT_CATEGORIES`: closed tuple of 9 categories.
- `_EVENT_TO_CATEGORY` map: 8 entries (login_required, login_restored,
  ad_approved, ad_declined, ad_started, ad_status_changed,
  delivery_stalled, spend_threshold_reached, watch_error).
- `_classify_requires_approval`: derives flag from `_APPROVAL_REQUIRED_KEYWORDS`
  (`with_human_approval`, `ask_human_to_login_in_browser`,
  `recover_browser_session_or_request_restart`).
- `_scrub_free_text`: regex on
  `(?i)(session|token|cookie|password|secret|otp|code|csrf|xsrf)\s*[=:]\s*[^\s;,\]\)\}]+`,
  pre-compiled at module load.
- `safe_summary` cap: 200 chars; report body cap: 600 chars.

**Pitfall I hit:** I tried to patch `_scrub_free_text` and
`_SECRET_KV_RE` *inside* `_classify_requires_approval`'s function body
via a single big `patch` replacement. The replacement put the new
helpers at an extra indentation level, producing
`SyntaxError: '(' was never closed`. Recovery: full `write_file` rewrite
of `events.py`. **V3 lesson: when patching inside a function body, the
replacement string's indentation must match exactly. For multi-block
changes, prefer `write_file` over `patch`.**

### `report.py` (163 LOC)

- `REPORTS_ENABLED_FLAG = "HERMES_ADS_WATCHER_REPORTS_ENABLED"`.
- `is_reports_enabled()`: truthy values exactly
  `{"1", "true", "yes", "on"}` (lowercased + stripped).
- `format_mini_report(event, now=None)`: pure function (caller passes
  `now` for test determinism). Returns `MiniReport(body, category,
  deliverable, requires_approval, fields)`.
- `deliverable = eligible AND is_reports_enabled() AND NOT event.requires_approval`.

### `watchspec.py` (280 LOC)

- `PostActionWatchSpec`: frozen dataclass with 12 fields.
- `PostAction` literal: 10 kinds (mirrors the external package).
- `_STOP_CONDITIONS`: frozenset, 8 items.
- `_RECIPES`: 10-entry dict mapping action → (kinds, expected, stop_conditions).
- `create_post_action_watchspecs(action, ...)`: returns list of
  `PostActionWatchSpec`. Filters stop conditions against closed set;
  unknown action → `ValueError`.
- `should_terminate(elapsed_seconds, current_state)`: returns
  `max_duration_reached` / `expected_outcome_reached` /
  `expected_outcome_unreachable` (heuristic: declined ≠ active) /
  `None`.

## Pitfalls hit during V2 implementation

### 1. `__all__` export misses

The test file imports 25 names; my first `__init__.py` only exported
~15. Pytest collection failed 4 times with `ImportError: cannot import
name 'X' from 'gateway.ads_watcher_v2'`. Recovery: iteratively added
each missing name to the import block AND `__all__`. Fix-forward:
**export every helper the test file uses on first write**.

### 2. Secret-substring false-fail on legitimate event types

First cut of `test_every_approval_event_type_is_handled` asserted
`"session" not in ev.safe_summary.lower()`. Failed for
`session_lost` and `session_restored` because the summary's first
component is `event=session_lost`. Recovery: changed to structural
checks: `"session=" not in low`, `"session:" not in low`,
`"token=" not in low`, `"token:" not in low`. Bare event-type words
are fine; only kv-pair patterns leak. **V3 lesson: when scrubbing
secrets, distinguish between "word in event name" (OK) and
"word=value in payload" (leak).**

### 3. Accidental indentation in `patch` replacement

Patching `events.py` to add `_SECRET_KV_RE` and `_scrub_free_text`
inside the body of `_classify_requires_approval` produced
`SyntaxError: '(' was never closed`. Recovery: full `write_file`
rewrite. **V3 lesson: when adding multiple top-level helpers near an
existing function, prefer `write_file` over `patch`. Verify with
`python -c "import <module>"` after every patch, not just at the end.**

### 4. `terminal` CWD assumption

`source venv/bin/activate` ran from default cwd failed with
`No such file or directory`. Hermes terminal defaulted to a different
working directory than `~/.hermes/hermes-agent/`. Recovery: explicit
absolute path `source /home/hermes/.hermes/hermes-agent/venv/bin/activate`
or `workdir=` parameter. **V3 lesson: always pin absolute venv path
or use `workdir=` for pytest invocations.**

### 5. `_safe_summary` initially didn't scrub reason strings

The original `_safe_summary` happily inlined a reason like
`"with session=abc and token=xyz"` into the summary. Test
`test_no_secrets_in_event_payload` caught this. Recovery: added
`_scrub_free_text` and called it on the reason snippet before joining.
**V3 lesson: free-text fields are the most common leak vector. Apply
the scrub regex to every free-text input, not just structured dict
payloads.**

## Regression-test pins (worth preserving)

These specific tests caught real bugs in V2 — keep them as-is in V3
plus any V-N extensions:

1. `TestAutonomyEnvelopeAndGuard::test_envelope_does_not_execute_mutation`
   — pins no `execute`/`apply`/`run`/`trigger`/`fix` attrs on envelope.
2. `TestWatcherEventDetection::test_watcher_emits_login_required_without_login_attempt`
   — pins no `login`/`submit_phone`/`enter_otp`/`enter_code` attrs on
   the package (catches accidental Telegram login helpers).
3. `TestNormalizedEventModel::test_no_secret_fields_on_envelope`
   — pins no `password`/`cookie`/`session`/`token`/`otp`/`code`/`secret`
   attrs on `AutonomyEnvelope` dataclass.
4. `TestReportRoutingDisabledByDefault::test_flag_default_false` —
   pins `is_reports_enabled()` returns `False` when env var is unset.
5. `TestReportRoutingDisabledByDefault::test_flag_rejects_garbage_values`
   — parametrize over `["", "0", "false", "no", "off", "ONCE",
   "garbage"]` to confirm only `1`/`true`/`yes`/`on` enable.
6. `TestNoSideEffectsInV2::test_import_is_pure` — confirms module
   import doesn't create files outside `__pycache__/` (catches
   accidental DB init / browser profile open at import).

## Approval-stage contract (verified)

V2 was shipped after the following gated approvals from AR-ADS-WATCHER-EVENT-LOOP-V2:

- Approved: code-only KC runtime cleanup (no push, no daemon, no
  external actions).
- Approved: post-action watcher V2 implementation (models + tests,
  no mutation execution, no production report sending).
- **NOT approved (per AR §'Not approved'):**
  - push to fork
  - launch/stop/edit ads
  - change CPM/bid/budget
  - create real ads
  - run real campaign/ad polling if it requires unsafe browser actions
  - send Telegram messages unless only formatting is tested with fakes
  - enable production report sending
  - run login assist
  - request/read/print/store OTP/2FA/cookies/session tokens
  - start standalone watcher daemon
  - create watcher systemd service/timer
  - restart deepseek / Xvfb
  - touch payments/refunds

## Next-step proposal (V3 ideas, NOT approved)

1. Wire V2 watches into the existing
   `ads_watcher_integration.create_post_action_watches()` via a thin
   adapter that maps `PostActionWatchSpec` → `WatchSpec`.
2. Add a `BoundedRemediationExecutor` that calls
   `MutationGuard.check()` before any future mutation. Currently the
   guard is only in tests.
3. Hook `format_mini_report` into a real AGI Team Bot send path behind
   a separate approval with explicit Telegram target list.
4. Add `tests/test_ads_watcher_v2_integration.py` that imports
   `hermes_telegram_ads.watcher.recipes.create_post_action_watches`
   and asserts V2's recipe table matches the external package's
   recipes exactly (catch drift).

Each of these is a separate AR. Do not bundle.
