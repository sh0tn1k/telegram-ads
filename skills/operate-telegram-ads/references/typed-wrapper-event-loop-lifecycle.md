# Telegram Ads typed wrapper event-loop lifecycle

Session pattern verified 2026-06-05 after `fix/browser-recovery` package update.

## Symptom

A typed read-only call such as `telegram_ads_get_browser_profile_info` returns an empty legacy envelope:

```json
{"ok": false, "error": "INTERNAL_ERROR", "message": ""}
```

Gateway logs around the failure can show Playwright callbacks trying to use a closed loop:

```text
RuntimeError: Event loop is closed
Tool telegram_ads_get_browser_profile_info returned error (120.11s):
{"ok": false, "error": "INTERNAL_ERROR", "message": ""}
```

## Layer split diagnostic

Do not assume the package is broken. Separate these layers:

1. Package import/path/version:
   - `hermes_telegram_ads.__file__`
   - package git path/commit/branch if available
   - `len(TELEGRAM_ADS_TOOLS)` should be 58
   - `telegram_ads_recover_browser_session` should exist.
2. Direct package call via `TelegramAdsToolset` with the same config/manager factory as the wrapper.
3. Hermes registry/wrapper call via `tools.telegram_ads_typed_tool` / `registry.dispatch`.
4. Live LLM function-call result.

Interpretation:

- direct package OK + fresh wrapper OK + live LLM FAIL = stale gateway/runtime singleton or wrapper lifecycle bug.
- direct package structured FAIL = package logic bug; preserve structured fields.
- direct package empty FAIL = installed package/version or package exception serialization problem.
- import path old/wrong = reinstall/restart issue.

## Wrapper root cause found

Old wrapper behavior:

- created a new `asyncio` event loop per typed tool call;
- closed the loop after each call;
- kept process-level singleton `TelegramAdsToolset` / adapter / Playwright state;
- next call could hit `RuntimeError: Event loop is closed`;
- wrapper serialized exceptions as `{"ok": false, "error": "INTERNAL_ERROR", "message": str(exc)}` so empty `str(exc)` produced an empty message.

A separate API mismatch existed: wrapper called `BrowserProfileManager.shared()` although the installed package exposed a constructor-based `TelegramAdsBrowserProfileManager` / `BrowserProfileManager` with `acquire_adapter`, not `.shared()`. The fallback to `TelegramAdsAdapter.start(config).__aenter__()` was risky because it can create an independent adapter/browser lifecycle.

## Durable patch pattern

For `tools/telegram_ads_typed_tool.py`:

- use one persistent background event loop thread per process;
- submit all typed calls with `asyncio.run_coroutine_threadsafe`;
- do not close the loop after each call;
- keep the singleton `TelegramAdsToolset` on that same loop;
- never emit empty `INTERNAL_ERROR.message`;
- if `str(exc)` is empty, use `Unhandled <ExceptionClass> in <tool_name>`;
- include `exception_type`, `operation`, `tool_name`, `retryable`, `recovery_hint`;
- if an exception has `to_dict()`, return that dict unchanged so `operation`, `retryable`, `recovery_hint`, `browser_state`, `artifact_path` survive;
- timeout envelope should be explicit:

```json
{
  "ok": false,
  "error": "TIMEOUT",
  "message": "<tool_name> timed out after <N>s",
  "operation": "<tool_name>",
  "tool_name": "<tool_name>",
  "recovery_hint": "Typed wrapper event-loop/Playwright lifecycle issue; do not retry blindly."
}
```

Manager factory should support both APIs and avoid independent adapter fallback:

```python
manager_cls = BrowserProfileManager
if hasattr(manager_cls, "shared"):
    manager = manager_cls.shared()
else:
    try:
        manager = manager_cls(config=config)
    except TypeError:
        manager = manager_cls()
return await manager.acquire_adapter(config=config)
```

## Wrapper-level tests to add

Use mocked coroutines / fake exceptions only; no live Telegram Ads calls, no raw Playwright.

- structured exception with `to_dict()` is preserved;
- exception with empty `str()` returns non-empty `INTERNAL_ERROR.message`;
- persistent loop id is reused across two sequential `_run_async_in_thread` calls;
- loop-bound state created in call 1 can be reused in call 2 (old closed-loop failure not reproduced);
- timeout returns explicit non-empty `TIMEOUT` envelope.

## Post-patch verification

After patch and gateway restart, run read-only typed smoke:

1. `telegram_ads_status`
2. `telegram_ads_ensure_login`
3. `telegram_ads_get_browser_profile_info`
4. repeat `telegram_ads_get_browser_profile_info` sequentially

Expected after fix:

- `browser_state=healthy`
- `session_active=true`
- no empty `INTERNAL_ERROR`
- no `TIMEOUT`
- repeated sequential calls pass.

Then rerun full read-only acceptance. Approval-gate negative tests are acceptable if they only request confirmation and do not call `apply_approved_action`; verify no `executed=true` appears.