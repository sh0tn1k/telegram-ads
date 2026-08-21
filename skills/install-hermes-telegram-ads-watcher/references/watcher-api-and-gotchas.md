# Reference notes for `install-hermes-telegram-ads-watcher`

Session-specific details, API gotchas, and reproduction recipes captured
during the initial wiring. Concise by design — link to upstream source
for canonical docs.

## WatcherScheduler API (pinned commit d6f7cdb66)

```python
from hermes_telegram_ads.watcher import WatcherScheduler
# Methods: tick(), run_forever(), stop()
# Signature: async def tick(self) -> list[WatcherEvent]
# Signature: async def run_forever(self) -> None
#            # Polls forever; cancellable via stop() or task.cancel()
# Signature: def stop(self) -> None
```

- `tick()` is the unit of work. Returns events from that cycle. Use it
  for one-shots and tests.
- `run_forever()` is the long-running loop. **Do not call from
  integration code without explicit operator approval** — it polls
  indefinitely and will hit real Telegram Ads once an adapter is wired.
- The skill text originally said `WatcherScheduler.run()`. That method
  does not exist; it was a hallucination from the agent. Always verify
  with `inspect.signature(...)` or `dir(...)` before assuming a method
  name on an upstream class.

## Service → adapter call contract (the 10 methods)

`TelegramAdsWatcherService` invokes exactly **10** methods on the adapter
during its tick paths. Source: `inspect.getsource(service)` + regex over
`self.adapter.<name>(...)`. All are `await`-ed except `browser_healthy`.

| Method | Kind | Used for watch kind |
|---|---|---|
| `browser_healthy()` | sync | login_state |
| `detect_login_state(*, navigate=True)` | async | login_state |
| `list_accounts()` | async | account |
| `get_account_budget()` | async | account |
| `get_account_stats()` | async | account_stats |
| `list_ads()` | async | ad_list, account |
| `get_ad(ad_id)` | async | ad_detail |
| `get_ad_stats(ad_id)` | async | ad_stats |
| `get_share_stats_url(ad_id)` | async | share_stats |
| `validate_ad(draft)` | async | draft_validation |

`get_ad_targeting` and `get_rejection_info` are **not** called by the
service tick path. They're exposed on `HermesTelegramAdsReadOnlyAdapter`
for the **consumer** to pull on `ad_declined` events. Don't add them to
service-level wiring; they would be unused and would duplicate reads.

## `get_account_stats` is not on `TelegramAdsAdapter`

The underlying `hermes_telegram_ads.hermes_tools.TelegramAdsAdapter`
exposes no `get_account_stats` method. The watcher service only calls
it for `kind == "account_stats"` watches, which we don't currently
create. The adapter synthesises a minimal dict from
`get_account_budget()`:

```python
async def get_account_stats(self) -> dict[str, Any]:
    budget = await self.get_account_budget()
    return {"url": None, "balance": budget.get("balance"),
            "currency": budget.get("currency")}
```

If real per-campaign stats URLs are needed, drive
`get_share_stats_url(ad_id)` from the consumer when an ad_approved
event arrives.

## Pydantic v2 WatcherEvent construction (tests)

When building a synthetic `WatcherEvent` for a consumer unit test, three
fields trip the validator:

```python
# source: Literal['telegram_ads_watcher']   — not free-form "test"
# dedupe_key: str (REQUIRED)                — not Optional
# created_at: datetime (required, has default factory)
```

The smoke script in `scripts/ads_watcher_smoke.py` shows the correct
construction. Common failure modes:

- `source='test'` → `literal_error`, value must equal
  `'telegram_ads_watcher'`.
- `dedupe_key=None` → `string_type` (it's a required `str`, not
  `Optional[str]`).
- `created_at=None` → `datetime_type`.

## Adapter idle-mode contract

`HermesTelegramAdsReadOnlyAdapter(adapter=None)` runs in idle mode. Any
data call raises `RuntimeError("...in idle mode: no underlying
TelegramAdsAdapter has been attached...")`. This is intentional: it
lets you import the module and run smoke checks without a real browser
profile, and it stops the scheduler tick from accidentally hitting
Telegram Ads if no adapter has been wired.

Idle mode is NOT a substitute for approval. Wiring a real
`TelegramAdsAdapter` is a separate step that requires explicit operator
approval — the adapter owns the browser profile manager, and the
browser session is the security boundary.

## Mutation guard: why `__getattr__` instead of explicit methods

The mutation list (`FORBIDDEN_MUTATION_TOOLS`, 17 names as of the
pinned commit) is small enough to enumerate, but using `__getattr__`
gives two extra properties:

1. **Coverage against unknown mutation methods**: if the upstream
   package gains a new mutation tool in a future version, the guard
   catches it via the `__getattr__` fallback. An explicit allow-list
   would silently permit it.
2. **No IDE/linter false positives** for `a.create_ad(...)` in code
   that accidentally imports the adapter. The attribute simply doesn't
   exist; the linter flags the typo.

The trade-off: legitimate typos on read-only methods (e.g. `get_ad` vs
`getadd`) raise `AttributeError` (from `__getattr__` after the read-only
guard), not a custom error. Acceptable — the linter catches most of
these.

## File-mutation verifier pattern (post-mortem)

When the file-mutation tool (`patch`) returns
`"Found N matches for old_string"`, the file is **unchanged** — the
patch did not apply. The agent's downstream report may still claim
"file updated". Always byte-level verify after a patch that didn't
return a clean diff:

```bash
wc -l path/to/file.py
grep -n "expected_symbol" path/to/file.py
```

This is also why smoke scripts are valuable: they re-import the module
fresh and exercise the new symbols, proving the patch actually landed.

## Browser profile manager is shared

`TelegramAdsAdapter` is constructed via
`BrowserProfileManager.shared().acquire_adapter(config=...)`. The same
manager is used by the typed `telegram_ads_*` tools in
`tools/telegram_ads_typed_tool.py`. Consequence: the watcher's adapter
and the agent's typed tools **share the browser session**. Do not
instantiate the adapter twice in the same process — it'll race on the
profile lock and the typed tool wrapper will raise
`BrowserProfileLockedError`.
