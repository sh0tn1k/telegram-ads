# Telegram `/tools` UI registry chain

When a toolset (e.g. `telegram_ads_typed`) is "registered" but doesn't appear in
the Telegram `/tools` slash command output, the cause is almost always a missing
link in the **5-layer chain** that the UI walks. This is a condensed, session-
tested map of what each layer is, where it lives, and how to verify it.

Author: the agent session 2026-06-03. Verified against
`~/.hermes/hermes-agent` and `~/.hermes/config.yaml` on
commit `f4aa1e0419bbef8e03ddda51b1267bc…` of `hermes-agent` repo.

---

## The 5 layers, highest-precedence first

### Layer 1 — `CONFIGURABLE_TOOLSETS` (Python source of truth for `/tools` UI)

- **File:** `hermes_cli/tools_config.py`
- **Type:** Static `List[Tuple[str, str, str]]` of `(toolset_name, label, description)`.
- **Read by:** `gateway/run.py::_handle_tools_command` and
  `cli.py::_handle_tools_command`. Iterates this list and only those entries
  become candidates for the UI.
- **Edit cost:** Python module change. **Requires gateway restart** to be
  picked up (Python modules are imported once at gateway startup, no hot-reload).
- **Symptom of missing entry:** `/tools` on Telegram does not show the toolset
  even when it's in `config.yaml` and the tools are registered.
- **Verified missing case:** `telegram_ads_typed` (2026-06-03). One-line add
  to `CONFIGURABLE_TOOLSETS` made it appear in `/tools`.

### Layer 2 — `platform_toolsets.telegram` (per-profile runtime filter)

- **File:** `~/.hermes/config.yaml` (and `~/.hermes/profiles/<name>/config.yaml`
  for non-default profiles).
- **Type:** `List[str]` of toolset names.
- **Read by:** Same `/tools` handler, after `CONFIGURABLE_TOOLSETS`.
  For each toolset from layer 1, the handler checks which profiles have it
  in their `platform_toolsets.telegram` list. Toolset appears in the UI
  per-profile based on this.
- **Edit cost:** YAML change. Gateway restart also required for runtime
  awareness, since config is read at startup.

### Layer 3 — `toolsets.py::TOOLSETS["<name>"]["tools"]` (canonical tool list)

- **File:** `toolsets.py`
- **Type:** Python `Dict[str, Dict[str, Any]]`.
- **Read by:** `toolsets.get_toolset(name)`, `resolve_toolset(name)`, and
  downstream by `model_tools._compute_tool_definitions()` when building the
  LLM function-calling schema.
- **Edit cost:** Python module change. Gateway restart required.
- **Distinction from layers 1/2:** Layers 1/2 control *visibility* in the
  `/tools` UI. Layer 3 controls *which tool names belong to the toolset* in
  the runtime registry. A toolset can exist in layers 1+2 with 0 tools in
  layer 3 (it would show as "Telegram Ads (typed) — 0 tools").

### Layer 4 — Tool module AST discovery + module-level `register` call

- **File:** `tools/<name>_tool.py` (and any helpers in the same file).
- **Type:** Python module with top-level `registry.register(...)` statement(s).
- **Read by:** `tools/registry.py::discover_builtin_tools()`.
  Iterates `tools/*.py` and asks `_module_registers_tools(module_path)`
  whether to import. **AST-based, not runtime-based** — see
  `hermes-tool-module-development` skill for the full pitfall list.
- **Edit cost:** File change on disk. Picked up on gateway restart, not before.
- **Critical pitfall:** `registry.register(...)` calls inside `if` / `try` /
  `def` / `with` blocks are **invisible** to AST inspection. The module-level
  `if _typed_available and _check_enabled(): _register_all()` pattern
  **fails** discovery silently. The fix is a top-level unconditional
  `Expr` statement, typically a `check_fn=lambda: False` discovery marker.
  Verified pattern: 57 typed tools in `telegram_ads_typed_tool.py` only
  registered after a marker was added at module top level.

### Layer 5 — `tools/registry.py` runtime registry

- **File:** `tools/registry.py`
- **Type:** Singleton `Registry` object populated by all `registry.register(...)`
  calls at module load.
- **Read by:** `model_tools._compute_tool_definitions()` rebuilds the LLM
  function-calling schema **on every turn** by combining registry entries
  with their toolset membership. So the LLM tool surface does refresh
  per-turn, but only from layers 1–4 already in memory; it does not
  auto-reimport modules from disk.
- **Edit cost:** N/A — this is the read-side. To populate, fix layers 1–4.

---

## How `/tools` actually builds the output (read in `gateway/run.py`)

```python
# Collect enabled toolsets per profile
profile_enabled: Dict[str, set] = {}
for profile_name, config_path in profiles_to_scan:
    raw = yaml.safe_load(config_path.read_text()) or {}
    pt = raw.get("platform_toolsets") or {}
    tg_list = pt.get("telegram")
    profile_enabled[profile_name] = set(str(t) for t in tg_list) if tg_list else None

# Iterate CONFIGURABLE_TOOLSETS — this is the entry filter
known_keys = {ts_key for ts_key, _, _ in CONFIGURABLE_TOOLSETS}
for ts_key, label, desc in CONFIGURABLE_TOOLSETS:        # ← layer 1
    profiles = []
    for pname in ("deepseek", "default"):
        enabled_set = profile_enabled.get(pname)         # ← layer 2
        if enabled_set is None or ts_key in enabled_set:
            profiles.append(pname)
    if not profiles:
        continue   # not enabled in any profile → skip
    all_tools.append({"name": ts_key, "label": label, ...})
```

Two `continue` / filter points:

1. **Layer 1 filter** — if `ts_key` isn't in `CONFIGURABLE_TOOLSETS`, the
   `for ts_key, ...` loop never visits it. Toolset invisible in `/tools`.
2. **Layer 2 filter** — if `ts_key` is in `CONFIGURABLE_TOOLSETS` but
   `enabled_set` exists in YAML and `ts_key not in enabled_set`, the
   `continue` skips it. Toolset invisible for that profile.

Neither filter consults the registry. Neither filter consults `toolsets.py`.
The Telegram UI is **driven entirely by `CONFIGURABLE_TOOLSETS` + per-profile
YAML**.

---

## Diagnostic recipe (read-only, no side effects)

Run this in a fresh Python sandbox (cwd irrelevant):

```python
import sys, yaml
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))

# Layer 1
from hermes_cli.tools_config import CONFIGURABLE_TOOLSETS
layer1 = {ts[0] for ts in CONFIGURABLE_TOOLSETS}
print(f"Layer 1 (CONFIGURABLE_TOOLSETS): {sorted(t for t in layer1 if 'telegram' in t)}")

# Layer 2
raw = yaml.safe_load(Path.home().joinpath(".hermes", "config.yaml").read_text())
layer2 = set(raw.get("platform_toolsets", {}).get("telegram", []))
print(f"Layer 2 (config.yaml):           {sorted(t for t in layer2 if 'telegram' in t)}")

# Layer 3
from toolsets import TOOLSETS
layer3 = {name for name, body in TOOLSETS.items() if "telegram_ads" in name}
print(f"Layer 3 (toolsets.py):           {sorted(layer3)}")

# Layer 4
from tools.registry import _module_registers_tools
mpath = Path.home() / ".hermes" / "hermes-agent" / "tools" / "telegram_ads_typed_tool.py"
print(f"Layer 4 (AST discovery on telegram_ads_typed_tool.py): {_module_registers_tools(mpath)}")

# Layer 5
from tools.registry import registry
layer5 = {t.name for t in registry.all() if t.toolset == "telegram_ads_typed"}
print(f"Layer 5 (registry entries in telegram_ads_typed): {len(layer5)}")
```

For a toolset to appear in `/tools` Telegram UI, all of these must be true:

- `layer1` ⊇ `{<toolset>}` — `CONFIGURABLE_TOOLSETS` has the name.
- `layer2` ⊇ `{<toolset>}` (for each profile) — `platform_toolsets.telegram` lists it.
- `layer3` ⊇ `{<toolset>}` — `toolsets.py::TOOLSETS` defines it (else 0 tools
  in UI even if visible).
- After gateway restart, `layer5` populates from the module on disk and the
  LLM function-calling schema picks it up on the **next** user turn.

---

## Restart requirements (Operating Discipline)

Per `~/.hermes/shared/TELEGRAM_ADS_TOOL_CONTRACT.md` and the
"Pre-installation / pre-mutation verification protocol" in
`operate-telegram-ads` SKILL.md, **any** gateway service restart is a
**separate explicit approval** — not covered by a "fix the UI" mandate.

Required approval phrase pattern:

- `approved restart hermes-gateway-default` — for the default-profile gateway.
- `approved restart hermes-gateway-deepseek` — for the DeepSeek companion.
- `approve Telegram Ads debug fallback` — only if you also need to inspect
  Playwright/browser/Xvfb internals (almost never needed for `/tools` UI).

Without one of these, the restart must not be performed; the only safe action
is to apply the disk changes and request the restart approval.

---

## Common confusions

- "Toolset in `config.yaml` but not in `/tools`" → missing from
  `CONFIGURABLE_TOOLSETS` (layer 1). Adding to layer 2 alone does not help.
- "Tools show up in `/tools list` but LLM says 'unknown tool'" → AST
  discovery failed (layer 4), or per-turn schema cache stale. See
  `hermes-tool-module-development` skill.
- "Tools show in `/tools` but `telegram_ads_get_ad` returns 'not in LLM surface'"
  → per-turn schema cache. Send another user message; the gateway recomputes
  tool definitions on the next turn. Do not retry the call mid-turn.
- "Added to `toolsets.py::TOOLSETS` but no tools visible" → layer 3 only
  declares the *name list*; the actual tool registrations come from layer 5
  via layer 4. A name in `TOOLSETS["x"]["tools"]` that has no
  `registry.register` call will show as "toolset: x, 0 tools present".
