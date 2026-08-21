# Telegram Ads typed wrapper: arg-name collision with `call(name=)`

Session pattern verified 2026-06-05.

## Symptom

A typed Telegram Ads call (typically `telegram_ads_save_screenshot`) raises:

```text
TypeError: TelegramAdsToolset.call() got multiple values for argument 'name'
```

The error surfaces through the typed wrapper as an empty legacy envelope:

```json
{"ok": false, "error": "INTERNAL_ERROR", "message": ""}
```

## Root cause

`TelegramAdsToolset.call(self, name: str, **kwargs)` always passes its own
`name=tool_name` kwarg when dispatching to the per-tool handler:

```python
# hermes_telegram_ads/hermes_tools.py
async def call(self, name: str, **kwargs: Any) -> dict[str, Any]:
    spec = TOOLS_BY_NAME.get(name)
    handler = getattr(self, spec.handler)
    return await self._invoke_with_recovery(spec, handler, kwargs)
```

`_invoke_with_recovery` then expands `kwargs` into the handler:

```python
return await handler(**kwargs)
```

If the LLM input schema for a tool also accepts `name=` from the LLM, both
the dispatcher's `name=tool_name` AND the LLM's `name=<filename>` end up
in the same `**kwargs` dict, and Python raises `TypeError: got multiple
values for argument 'name'`.

## Repro (read-only, no live browser)

```python
import asyncio
from hermes_telegram_ads.hermes_tools import TelegramAdsToolset
from hermes_telegram_ads.config import TelegramAdsConfig
from hermes_telegram_ads.browser_manager import TelegramAdsBrowserProfileManager
import yaml, os

cfg_path = "/home/hermes/.hermes/telegram_ads.yaml"
cfg_data = yaml.safe_load(open(cfg_path)) if os.path.exists(cfg_path) else {}
try:
    config = TelegramAdsConfig.from_dict(cfg_data.get("telegram_ads", cfg_data) or {})
except Exception:
    config = TelegramAdsConfig.default()

async def factory():
    manager = TelegramAdsBrowserProfileManager(config=config)
    return await manager.acquire_adapter(config=config)

toolset = TelegramAdsToolset(adapter_factory=factory, config=config)

# Reproduces the bug:
try:
    await toolset.call("telegram_ads_save_screenshot", **{"name": "x.png"})
except TypeError as e:
    print("REPRO:", e)
```

## Fix pattern — "fix in both places" (the operator's preferred workflow)

When the operator says "fix in both places" or asks for "double fix" on a
bug that spans the package source and the Hermes wrapper, apply the
fix in **both layers** in one patch cycle:

1. **Package source fix** (canonical):
   - Rename the LLM-facing input field in the package's handler signature
     and `ToolSpec` schema.
   - For `telegram_ads_save_screenshot` the canonical rename is
     `name` → `screenshot_name`. Update:
     - `_h_save_screenshot(self, screenshot_name: str | None = None, ...)`
     - `ToolSpec("telegram_ads_save_screenshot", ..., _obj({"screenshot_name": _STR, "full_page": _BOOL}), ...)`
   - Edit is in:
     `~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/hermes_telegram_ads/hermes_tools.py`
   - Cross-profile write requires `cross_profile=True` because the
     package lives under the `deepseek` profile's plugins/ dir but is
     shared via the editable `.pth` install.

2. **Hermes wrapper fix** (defensive, backward-compat):
   - Add a per-tool argument-rename map in `_make_sync_handler` so
     legacy `name=` payloads from older prompts still work after the
     schema rename:

     ```python
     _ARG_RENAMES: dict[str, dict[str, str]] = {
         "telegram_ads_save_screenshot": {"name": "screenshot_name"},
     }
     def _handler(args, **kwargs):
         call_args = dict(args or {})
         for src, dst in _ARG_RENAMES.get(tool_name, {}).items():
             if src in call_args and dst not in call_args:
                 call_args[dst] = call_args.pop(src)
         ...
     ```
   - This way, even if a cached LLM prompt still sends `name=`, the
     wrapper rewrites it before the dispatcher's `name=tool_name`
     collision can fire.

3. **Regression tests** (read-only, no browser, no live calls):
   - Wrapper rename test: feed a fake toolset, call
     `_make_sync_handler("telegram_ads_save_screenshot")(args={"name": "x.png"})`
     and assert the captured kwargs have `screenshot_name="x.png"`
     and **no** `name` key.
   - Schema test: assert
     `TOOLS_BY_NAME["telegram_ads_save_screenshot"].input_schema["properties"]`
     contains `screenshot_name` and **does not** contain `name`.

## General rule for new Telegram Ads tools

When adding a new `telegram_ads_*` tool whose LLM input schema includes
a field, **never** use a field name that matches any reserved kwarg on
`TelegramAdsToolset.call` or on the per-tool handler signature. Reserved
field names that will collide:

- `name` — collides with `TelegramAdsToolset.call(name, **kwargs)`.
- Any other positional kwarg on the dispatcher / `_invoke_with_recovery`.

If the natural name for an LLM input field would be `name`, rename it
in the LLM-facing schema (e.g. `artifact_name`, `screenshot_name`,
`ad_name`, `account_name`, `campaign_name`) and keep the internal
handler kwarg stable.

## Restart requirement

After fixing the package source, a gateway restart is required so
the live `TELEGRAM_ADS_TOOLS` registry re-imports the corrected
`ToolSpec`. The wrapper fix alone is not enough — the LLM-facing
schema is computed from the package, not from the wrapper.
