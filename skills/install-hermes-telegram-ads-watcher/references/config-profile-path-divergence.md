# Telegram Ads config path resolution — `from_yaml` vs `from_dict` vs `default`

The `TelegramAdsConfig` class has a subtle API surface that causes silent fallbacks if you call the wrong method. This reference documents the actual API as observed on 2026-06-17 and the resulting profile-path divergence.

## API surface (verified 2026-06-17)

```python
from hermes_telegram_ads.config import TelegramAdsConfig

# Exists:
TelegramAdsConfig.from_yaml(path: str | PathLike) -> Self  # reads file, calls model_validate internally
TelegramAdsConfig.model_validate(block: dict) -> Self       # pydantic v2 API
TelegramAdsConfig.default() -> Self                        # pydantic defaults; profile_dir is relative
TelegramAdsConfig()                                         # same as default(), but storage.resolve() not called

# Does NOT exist (despite the natural-sounding name):
TelegramAdsConfig.from_dict(...)  # AttributeError
```

## Default paths

```python
class BrowserConfig(BaseModel):
    profile_dir: Path = Field(default=Path("./browser_profiles/telegram_ads"))

class StorageConfig(BaseModel):
    base_path: Path = Field(default=Path("./data/telegram_ads"))
    # screenshots/reports/drafts auto-derived from base_path
```

`Path("./browser_profiles/telegram_ads")` is **relative** and resolves against the process's CWD. This is the source of the divergence.

## Concrete divergence (observed 2026-06-17)

| Source | YAML / default | CWD | Absolute path actually used |
|---|---|---|---|
| `telegram_ads.yaml` `browser.profile_dir` | `/home/hermes/.hermes/data/telegram_ads/browser_profile` | n/a (absolute) | `/home/hermes/.hermes/data/telegram_ads/browser_profile` |
| `telegram_ads_tool.py` (legacy) → `TelegramAdsConfig.from_yaml(SHARED_CONFIG_PATH)` | yaml profile | gateway CWD | yaml profile (absolute) |
| `telegram_ads_typed_tool.py` → `TelegramAdsConfig.from_dict(block)` (BROKEN) → `except Exception: default()` | relative default | gateway CWD = `hermes-agent` | `/home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads` |
| `real_adapter_smoke.py` → `TelegramAdsConfig.default()` | relative default | smoke CWD = `hermes-agent` (via explicit `cd`) | `/home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads` |
| `start_ads_watcher_readonly_operational.py` → `TelegramAdsConfig.default()` | relative default | watcher CWD = `hermes-agent` (if started from there) | `/home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads` |

## The bug in `tools/telegram_ads_typed_tool.py:140` (verified)

```python
def _make_toolset() -> TelegramAdsToolset:
    cfg_data = _load_shared_config() or {}
    block = cfg_data.get("telegram_ads", cfg_data)
    try:
        config = TelegramAdsConfig.from_dict(block)  # ← AttributeError: from_dict does not exist
    except Exception:
        config = TelegramAdsConfig.default()  # ← silent fallback to relative default
```

`from_dict` does not exist on this package version. The except branch swallows the AttributeError and silently returns `default()`. The `telegram_ads.yaml` is loaded but its `browser.profile_dir` override is **dropped**.

## Verifying the bug (read-only, no code edits)

```python
from hermes_telegram_ads.config import TelegramAdsConfig
print('from_dict exists:', hasattr(TelegramAdsConfig, 'from_dict'))
# → False

import yaml
with open('/home/hermes/.hermes/telegram_ads.yaml') as fh:
    block = (yaml.safe_load(fh) or {}).get('telegram_ads', {})
try:
    cfg = TelegramAdsConfig.from_dict(block)
except AttributeError as e:
    print('Confirmed bug:', e)

cfg = TelegramAdsConfig.default()
print('Effective profile_dir:', cfg.browser.profile_dir)
# → browser_profiles/telegram_ads  (relative)
```

## The fix — APPLIED 2026-06-17 (AR-ADS-WATCHER-ARCH-1 + AR-ADS-WATCHER-ARCH-2)

Three-way preference ladder applied to all three entrypoints (typed tool, real_adapter_smoke, start_ads_watcher_readonly_operational):

```python
# Preferred: from file (cleanest; YAML is the source of truth)
try:
    config = TelegramAdsConfig.from_yaml(SHARED_CONFIG_PATH)
except Exception:
    # Fallback A: validate the already-parsed block (if we have _load_shared_config() payload)
    try:
        block = cfg_data.get("telegram_ads", cfg_data)
        config = TelegramAdsConfig.model_validate(block)
        config.storage.resolve()
    except Exception:
        config = None
# Fallback B: defaults (last resort; profile_dir is relative)
if config is None:
    config = TelegramAdsConfig.default()
```

Files changed (code-only, all 3 modules in hermes-agent/):

- `tools/telegram_ads_typed_tool.py:_make_toolset()` — uses `SHARED_CONFIG_PATH = "/home/hermes/.hermes/telegram_ads.yaml"`, prefers `from_yaml`, falls back to `model_validate(block)` (preserves the previously-parsed `cfg_data` payload), then `default()`.
- `real_adapter_smoke.py` — added `_load_config()` helper with same preference order; called instead of `TelegramAdsConfig.default()`.
- `start_ads_watcher_readonly_operational.py` — added `_load_config()` helper with same preference order; called instead of `TelegramAdsConfig.default()`. Also cleaned a duplicate `PROJECT_ID`/`LOGIN_STATE_KIND` definition that was already in the module before the patch.

**Tests pinned (2026-06-17, all green, 0.44s):** `tests/test_telegram_ads_config_loader.py` — 7 tests:

1. `test_typed_tool_loader_uses_from_yaml_not_from_dict` — typed toolset resolves profile_dir to yaml path; not the default relative path. Also asserts `TelegramAdsConfig.from_dict` does not exist (regression guard).
2. `test_typed_tool_loader_falls_back_to_default_when_yaml_missing` — when from_yaml raises, loader does not crash; produces a valid TelegramAdsConfig.
3. `test_real_adapter_smoke_loader_uses_yaml_profile_path` — smoke's `_load_config()` resolves to yaml path.
4. `test_real_adapter_smoke_loader_falls_back_when_yaml_missing` — smoke fallback to default() works.
5. `test_watcher_operational_loader_uses_yaml_profile_path` — watcher's `_load_config()` resolves to yaml path.
6. `test_watcher_operational_loader_falls_back_when_yaml_missing` — watcher fallback to default() works.
7. `test_all_three_loaders_resolve_to_same_profile_path` — cross-entrypoint consistency: all three resolve to the **same** yaml path.

**Existing tests still green:** `tests/test_telegram_ads_typed_wrapper.py` — 7/7 (no regressions in wrapper event-loop, exception envelopes, handler renames, etc.).

## Gateway restart still required

The patch is code-only. The running default Hermes gateway (PID 4068802) was started before the patch and its `_toolset_singleton` was already constructed using the old `_make_toolset()` (which fell back to defaults with profile_dir = `/home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads`).

After the patch, **new** toolset constructions honor the yaml. But the running gateway's cached singleton still has the old config. The fix only takes effect after a gateway restart (`systemctl --user restart hermes-gateway-default.service`) — which is a separate approval gate (not part of AR-ADS-WATCHER-ARCH-1/2).

After restart, the next typed tool call would launch Chromium with profile `/home/hermes/.hermes/data/telegram_ads/browser_profile` (the yaml profile). The currently live chromium (PID 4164123) on the OLD profile would not be referenced again; its SingletonLock would naturally clear when chromium eventually exits.

## Lock collision risk — reduced, not eliminated

**Pre-patch:** gateway typed tools and watcher/smoke both used default relative path → **always** the same path (`./browser_profiles/telegram_ads` resolved under gateway CWD) → guaranteed collision if both alive.

**Post-patch:** all three entrypoints target the yaml-resolved path (`/home/hermes/.hermes/data/telegram_ads/browser_profile`). Collision only happens when both are alive simultaneously AND Chromium is actively holding the lock — i.e., during the long-running daemon scenario. The in-process `BrowserProfileManager` serializes acquire/release; cross-process collision only if daemon and smoke run **at the same time** against the same live gateway (rare).

## Why the divergence matters (preserved from pre-fix)

1. **Lock collisions** (see above; now reduced).
2. **Two login sessions** — separate profiles would mean two cookie jars; Telegram Ads may flag multi-session as suspicious and trigger `app_approval_pending` / review / decline cycles.
3. **Inconsistent audit logs** — when the audit log says "TelegramAdsAdapter launched (profile_dir=/home/hermes/.hermes/data/telegram_ads/browser_profile)" (legacy tool) but the live chromium uses `/home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads` (typed tool), it's hard to debug login-state or session-expiry issues. After the patch this divergence is gone.

## Next architectural step (post-patch)

With config consistency restored, the next decision is **where the watcher should run** (separate approval, not part of ARCH-1/2):

| Model | Browser ownership | Cross-process lock? | Restart coupling |
|---|---|---|---|
| **In-process watcher** (recommended) | gateway owns Chromium; watcher runs as asyncio.Task inside gateway | none | watcher shares gateway lifecycle |
| Standalone daemon with serialized access | separate process; checks `manager.check_profile_lock()` before acquire | only when gateway holds lock | independent; opportunistic |
| No daemon (on-demand only) | gateway only | none | n/a |
