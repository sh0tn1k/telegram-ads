# Telegram Ads typed wrapper envelope diagnostics

Use this when a typed `telegram_ads_*` live call returns a legacy empty envelope like:

```json
{"ok": false, "error": "INTERNAL_ERROR", "message": ""}
```

This diagnostic is **read-only code-level**: no raw Playwright, no browser UI, no CPM/budget/status changes, no ad launch/stop.

## Goal

Separate three layers:

1. **Package logic** (`hermes_telegram_ads.hermes_tools.TelegramAdsToolset`).
2. **Hermes wrapper/registry** (`tools/telegram_ads_typed_tool.py`).
3. **Live gateway runtime state** (stale singleton, event loop, imported module cache).

## Proven checks

Run in the Hermes venv from `~/.hermes/hermes-agent`:

```python
import hermes_telegram_ads
from hermes_telegram_ads.hermes_tools import TELEGRAM_ADS_TOOLS, TelegramAdsToolset

print(hermes_telegram_ads.__file__)
print(getattr(hermes_telegram_ads, "__version__", None))
print(len(TELEGRAM_ADS_TOOLS))
print("telegram_ads_recover_browser_session" in [t.name for t in TELEGRAM_ADS_TOOLS])
```

Expected healthy surface after `fix/browser-recovery`:

- package path points at editable `hermes_telegram_ads_pkg/hermes_telegram_ads/__init__.py`;
- tool count is `58`;
- `telegram_ads_recover_browser_session` exists.

## Direct package vs wrapper comparison

Call the package directly through `TelegramAdsToolset` using the same config/profile factory as the wrapper, and compare to registry dispatch for `telegram_ads_get_browser_profile_info`.

Interpretation:

- **Direct package OK + wrapper FAIL** → bug in `tools/telegram_ads_typed_tool.py` or live gateway wrapper state.
- **Direct package structured FAIL** → package logic bug in the called tool or its profile-info implementation.
- **Direct package empty FAIL** → installed package or exception serialization is wrong.
- **Fresh registry OK + live tool FAIL** → running gateway has stale singleton/event-loop/module state; patch/restart is required.

## Common root cause found 2026-06-05

`tools/telegram_ads_typed_tool.py` used:

```python
def _run_async_in_thread(coro, timeout=120):
    loop = asyncio.new_event_loop()
    try:
        outcome["result"] = loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
    finally:
        loop.close()
```

Together with a process-level `_toolset_singleton`, this can leave adapter/Playwright objects tied to a closed per-call loop. Later typed calls can hang or log:

```text
RuntimeError: Event loop is closed
Tool telegram_ads_get_browser_profile_info returned error (120s):
{"ok": false, "error": "INTERNAL_ERROR", "message": ""}
```

The wrapper also converted exceptions with:

```python
{"ok": False, "error": "INTERNAL_ERROR", "message": str(exc)}
```

If `str(exc)` is empty, the user sees an empty `message`.

## Patch direction

1. Use one persistent background event loop thread for typed wrapper calls (`asyncio.run_coroutine_threadsafe`) instead of creating/closing a loop per call.
2. Preserve structured package errors (`to_dict()` / dict envelopes) instead of flattening them.
3. Never return empty `INTERNAL_ERROR.message`; use `str(exc).strip() or repr(exc) or f"Unhandled {type(exc).__name__}"`.
4. Include `operation`, `tool_name`, `exception_type`, `retryable`, and `recovery_hint` when wrapping unexpected errors.
5. Check `BrowserProfileManager` API compatibility. If the installed manager has no `.shared()`, do not blindly fall back to starting an independent adapter/browser. Align the wrapper with the package's canonical manager API.

## Restart requirement

After patching `tools/telegram_ads_typed_tool.py`, restart the gateway profile explicitly. The running gateway has already imported the module and may hold stale `_toolset_singleton`, handler closures, loop/thread state, and cached function schemas.

Do **not** continue snapshot/list/stats acceptance until the patch is loaded and item 1–3 of the read-only acceptance pass succeed.