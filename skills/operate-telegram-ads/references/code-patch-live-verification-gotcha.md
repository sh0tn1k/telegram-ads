# Code patch live-verification gotcha

## The gotcha

After applying a code patch to the installed
`hermes_telegram_ads` package, the **unit tests pass** but a
**live `telegram_ads_*` tool call still returns old behavior**.

This is **not** a bug in the patch and **not** a Hermes wrapper
issue. It is the expected lifecycle: the running gateway has
already imported `TELEGRAM_ADS_TOOLS` into its process memory.
A new patch on disk does not retroactively update the LLM's
function-calling schema or the gateway's per-tool handler
closure. The schema is recomputed at the next gateway restart
(via `model_tools._compute_tool_definitions()`).

## Verified pattern (2026-06-06)

Scenario: applied a placement-aware media guard to the package:

- `hermes_telegram_ads/media.py` — added
  `SUPPORTED_UPLOADED_MEDIA_TARGET_TYPES = {"channels"}`
  and `assert_uploaded_media_supported_for_target_type()`.
- `hermes_telegram_ads/adapter.py` — `_resolve_media()`
  now calls the guard before upload.
- `hermes_telegram_ads/hermes_tools.py` — `validate_ad`,
  `preview_ad`, `create_ad` all call the guard before
  upload/checkAdPost/approval.
- `hermes_telegram_ads/schemas.py` — added
  `unsupported_media_for_target_type` to the `ErrorCode`
  literal.
- `tests/test_creative_options.py` — added unit tests for
  the new guard.

Results:

- `pytest`: `405 passed in 7.05s` — patch is correct at the
  package level.
- Live `telegram_ads_estimate_cpm` with
  `target_type=search + media_path=/.../img.jpg`:
  ```json
  {
    "ok": true,
    "data": {
      "estimated_effective_cpm": 97.5,
      "modifiers_applied": ["media_photo"],
      "warnings": ["Uploaded photo — +50% CPM."]
    }
  }
  ```
  Old behavior — package guard did not run.

## What the agent should do

1. **Do not conclude the patch is broken from a single live
   tool call.** Run the unit tests first; if they pass, the
   patch is correct.
2. **Surface the live/restart gap explicitly** to the user:
   "patch is verified at the package level; live tool surface
   needs a gateway restart to re-import the package. Awaiting
   your approval for `systemctl --user restart
   hermes-gateway-default.service`."
3. **Do not request a gateway restart as part of a routine
   patch commit.** It is a separate explicit approval, per
   Operating Discipline and the gateway-restart discipline in
   the main SKILL.
4. **For pre-restart offline validation**, use the
   `execute_code` path with `FakeAdapter` (from
   `tests/fake_adapter.py`) to exercise the new guard
   logic end-to-end without touching the live browser. This
   was the canonical "proof" that the patch works:
   ```python
   from hermes_telegram_ads.hermes_tools import TelegramAdsToolset
   from tests.fake_adapter import FakeAdapter
   import asyncio, tempfile
   from pathlib import Path

   async def main():
       with tempfile.TemporaryDirectory() as td:
           img = Path(td) / "x.png"
           img.write_bytes(b"PNGDATA")
           ts = TelegramAdsToolset(
               _adapter_factory=lambda: FakeAdapter(tmpdir=td)
           )
           r = await ts.call("telegram_ads_create_ad", draft={
               "title": "t", "text": "x", "promote_url": "@b",
               "cpm": 65, "target_type": "search",
               "targets": ["q"], "media_path": str(img),
           })
           print(r["error"]["error"])  # 'unsupported_media_for_target_type'
   asyncio.run(main())
   ```

## Why this happens at the architecture level

- The package is `editable`-installed at
  `~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/`.
- On gateway start, `hermes_telegram_ads.hermes_tools` is
  imported, the `TELEGRAM_ADS_TOOLS` list is computed, and
  per-tool handlers are bound to closures over the imported
  module's symbols.
- File edits on disk do **not** trigger a re-import. The
  Python process keeps the old module object in `sys.modules`.
- The LLM's function-calling schema is computed once per turn
  via `model_tools._compute_tool_definitions()`, which reads
  from the registry (built at gateway start).
- Result: the disk has the new code; the running process has
  the old code. They diverge until restart.

## What this does NOT cover

- **Schema-cache refresh** after the gateway restart: even
  after restart, the LLM's tool list is the schema as
  registered. New tools added in the patch become visible
  in the next message turn.
- **`telegram_ads_typed` toolset visibility in `/tools` UI**:
  see the main SKILL's "Telegram `/tools` UI does not
  auto-pick up the typed toolset" pitfall.
- **Wrapper-vs-package divergence** (empty `INTERNAL_ERROR`
  envelope): see `references/typed-wrapper-envelope-diagnostics.md`.
