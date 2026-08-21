# Telegram Ads Tool — Error Diagnostics Methodology

> **Why this file exists.** On 2026-06-02 the operator reported
> `telegram_ads_workflow(workflow="snapshot")` returning
> `{ok: false, workflow: "", error: "UNKNOWN_WORKFLOW", available: [...]}`.
> The tool code looked correct. The tool definition looked correct. The
> first instinct was to restart the gateway, open ads.telegram.org in a
> browser, or patch the tool. None of that was needed. The actual cause
> was that the LLM (running in a DeepSeek/Chinese-provider profile) had
> not passed the `workflow` parameter in `function_args`, and the tool
> silently forwarded `workflow=""` into the dispatcher. The methodology
> below catches this class of bug *without* touching the gateway, the
> browser, the Xvfb layer, or any live Telegram Ads state.

---

## 1. When to use this file

Use it whenever the user reports an error from a `telegram_ads*` tool
call and the error message looks like one of:

- `workflow: ""`, `error: UNKNOWN_WORKFLOW`, `Available: …`
- `error: browser_profile_locked` or `browser_profile_busy`
- `error: ADAPTER_ACQUISITION_FAILED` / `ADAPTER_UNAVAILABLE`
- `error: telegram_ads_tool_schema_defect` (parameterless tool in registry)
- `error: LOGIN_REQUIRED` / `FORBIDDEN` / `API_ERROR`
- `error: WORKFLOW_ERROR` (catch-all from `run_workflow`)
- `error: NO_ADAPTER`
- `error: IMPORT_ERROR` / `MISSING_HANDLER`
- `error: INFRA_MISSING` (Xvfb preflight)
- `error: ADAPTER_ACQUISITION_FAILED` / `MISSING_REQUIRED_ARG`
- the tool simply hangs / times out

**Do NOT use this file** when the error clearly comes from the LLM
returning malformed JSON, the network dropping, or the Telegram Ads UI
itself rejecting an action (e.g. moderation). Those have their own
diagnostic paths.

---

## 2. The Operating Discipline still applies

All of the standard rules remain in force while running this methodology:

- ❌ No `browser_navigate` to `ads.telegram.org`.
- ❌ No `pkill`, no `systemctl … restart` without explicit approval.
- ❌ No second Chromium, no `xvfb-run` ad-hoc browser.
- ❌ No "let me look at the actual UI" via `computer_use` or Playwright.

The whole point of this methodology is to **diagnose without touching
the browser stack**. The browser is downstream of the tool; if the tool
returns a JSON error, the browser is irrelevant until you've proved
the error is browser-side.

---

## 3. The three diagnostic reproductions (in order)

When the user reports a tool error, run these three checks in order.
Each is read-only, side-effect-free, and runs entirely inside
`execute_code` with mocked dependencies.

### 3.1 — Read the registry's stored schema (source of truth)

```python
from tools.registry import registry
entry = registry.get_entry("telegram_ads_workflow")
# Properties live at entry.schema["function"]["parameters"]["properties"]
# required:     entry.schema["function"]["parameters"]["required"]
# enum:         entry.schema["function"]["parameters"]["properties"]["workflow"]["enum"]
```

This tells you what the tool *was registered with* — independent of
caching, sanitization, or anything downstream. If `required` is wrong
here, the bug is in the tool file, and you patch the tool. If `required`
is correct here, the bug is downstream — go to 3.2.

### 3.2 — Read `get_tool_definitions` (what LLM actually receives)

```python
import model_tools
defs = model_tools.get_tool_definitions(quiet_mode=True)
# Find telegram_ads_workflow entry (note the double-wrap)
for d in defs:
    fn = d.get("function", {})
    name = fn.get("name") or fn.get("function", {}).get("name")
    if name == "telegram_ads_workflow":
        # The "real" function block is at d["function"]["function"]
        # (registry.get_definitions wraps entry.schema in {type, function})
        actual = fn.get("function", fn)
        params = actual.get("parameters", {})
        properties = params.get("properties", {})
        required = params.get("required", [])
        break
```

This tells you what the LLM provider actually receives. If properties
are correct here but the LLM still didn't pass them → the LLM is the
source of the bug, not the tool. If properties are missing or empty
here but present in 3.1 → there's a cache staleness, sanitizer issue,
or toolset filter stripping the tool.

**Pitfall: the double-wrap.** `entry.schema` in the registry is the
*inner* dict (`{"type": "function", "function": {...}}`).
`registry.get_definitions` wraps that *again*. So:

- `entry.schema["function"]["parameters"]` is correct.
- `defs[0]["function"]["function"]["parameters"]` is correct.
- `defs[0]["function"]["parameters"]` is the *outer* `function` block,
  which does **not** have `parameters` at all.

Reading the wrong level makes you think the schema is broken when it
isn't. This is a recurring red herring.

### 3.3 — Reproduce `handle_function_call` end-to-end with mocked deps

```python
import json, os, asyncio
from unittest.mock import patch, MagicMock
os.environ["DISPLAY"] = ":99"   # bypass Xvfb preflight in the test env

import model_tools

async def fake_run_workflow(workflow, params, adapter=None):
    return {"ok": True, "workflow": workflow, "data": {"mocked": True}}

mock_adapter = MagicMock()
with patch("hermes_telegram_ads.workflows.run_workflow", fake_run_workflow):
    future = asyncio.Future(); future.set_result(mock_adapter)
    with patch("hermes_telegram_ads.browser_manager.TelegramAdsBrowserProfileManager") as MockMgr:
        MockMgr.get_instance.return_value.acquire_adapter.return_value = future
        MockMgr.get_instance.return_value.release_adapter = MagicMock()
        result = model_tools.handle_function_call(
            function_name="telegram_ads_workflow",
            function_args={"workflow": "snapshot"},
        )
        print(json.dumps(json.loads(result), indent=2))
```

This reproduces **the exact path the gateway uses** when an LLM tool
call lands. If it succeeds with `workflow="snapshot"` but the real call
fails, the difference is in `function_args` (the LLM didn't pass what
you assumed) or in the live infrastructure (browser, Xvfb, gateway).
Both diagnoses are now in different categories.

---

## 4. The four common error shapes and what they mean

| Error shape | Layer | What it usually means | Action |
|---|---|---|---|
| `error: UNKNOWN_WORKFLOW`, `workflow: ""` | dispatcher | LLM didn't pass `workflow` (or passed it under wrong key like `action`) | Diagnose via §3.3 with empty/wrong args. Patch `handle_function_call` to add `required` validation if you want to catch this earlier. |
| `error: browser_profile_locked`, `owner_pid: …` | adapter acquisition | Another process (or another profile) holds the SingletonLock | Do NOT restart. Do NOT kill. Wait or surface to the operator. Locked state is terminal. |
| `error: browser_profile_busy`, `timeout: 30.0` | adapter acquisition | Another `telegram_ads_workflow` in the same process holds the asyncio.Lock | Wait. Don't dispatch parallel workflows. |
| `error: telegram_ads_tool_schema_defect` | tool registration | Tool is in registry but its `parameters` is `{}` or missing | Patch the tool file (don't restart gateway — editable package). Verify with §3.1, then §3.2. |
| `error: INFRA_MISSING`, `hint: "Set DISPLAY=:99…"` | preflight | Xvfb is down or unreachable | Surface to the operator; do NOT auto-start Xvfb without approval. |
| `error: LOGIN_REQUIRED` | adapter | Telegram session expired | Surface to the operator. Login is not auto-resumable from this skill. |
| `error: ADAPTER_ACQUISITION_FAILED` (catch-all) | adapter acquisition | Unexpected exception in `acquire_adapter` | Read full message; usually config or import error. |
| `error: WORKFLOW_ERROR` (catch-all from `run_workflow`) | workflow body | Workflow raised an exception | Read full message; usually a parser issue or missing data. |

---

## 5. Common LLM-side param issues (the "wrong key" class)

When the LLM provider is DeepSeek / Moonshot / Xiaomi / OpenRouter (Chinese
provider class), it has a known weak `required` enforcement. Common
mistakes the LLM makes:

- Drops `required` parameters entirely. `function_args` is `{}`.
- Uses synonyms: `action="snapshot"` instead of `workflow="snapshot"`.
- Wraps in nested object: `{"params": {"workflow": "snapshot"}}` (no
  precedent in our schemas, but possible if the LLM confuses this with
  another tool's schema).
- Uses `None` for an optional field, which `coerce_tool_args` happily
  leaves as `None`. `_handler` then sees `args.get("workflow", "")` → `""`.

If the user is on a Chinese provider, this is the most likely cause of
`UNKNOWN_WORKFLOW` errors. Verify by:

1. Asking the user to paste the raw `tool_call` from the LLM response
   (if they have it). Look for missing or misnamed keys.
2. Running §3.3 with `function_args={}` to confirm the tool-side
   behavior (it should return `workflow: ""` and `UNKNOWN_WORKFLOW`).

**Hardening options (offer to the operator, do not apply unilaterally):**

- **A. Schema validation in `handle_function_call`** — check
  `function_args` against `entry.schema["function"]["parameters"]["required"]`
  and return a structured `MISSING_REQUIRED_ARG` error before dispatch.
  Pro: catches the issue early, LLM gets explicit feedback. Con: more
  code in `model_tools`, may interact badly with tool_search bridge.
- **B. Parameter alias resolution in `_handler`** — accept
  `{"action": "snapshot"}` and remap to `{"workflow": "snapshot"}`.
  Pro: tolerant of common LLM mistakes. Con: hides bugs, can mask
  genuine naming issues.
- **C. Soft warning** — `_handler` returns a soft error
  `{"ok": false, "error": "MISSING_WORKFLOW_PARAM", "hint": "..."}`
  instead of forwarding to `run_workflow("")`. Pro: explicit. Con:
  same effect as A but only for this one tool.

Default recommendation: A. It is a single function, no cross-tool
impact, and makes the failure mode self-explanatory.

**Status (2026-06-02):** A, B, and C have been **applied** for the
`telegram_ads_workflow` tool:

- A lives in `model_tools.handle_function_call` (after
  `coerce_tool_args`, before `edit_approval`/dispatch). It uses a
  per-tool `alias_keys` map so it does **not** block B.
- B + C live in `tools/telegram_ads_workflow_tool._handler`:
  - `args.get("workflow") or args.get("action") or args.get("name")` →
    fallback chain.
  - Empty result → `{"ok": false, "error": "MISSING_WORKFLOW_PARAM",
    "available": [...], "hint": "..."}`.
- Tests: `tests/test_telegram_ads_workflow_args.py` (13 tests, all
  green as of 2026-06-02). Full suite: 193/193 passed.

Future Telegram Ads tools should follow the same pattern: register
under `alias_keys` in `model_tools` (so A doesn't block them) and
implement B + C in their own `_handler`.

---

## 6. The "double-wrap" red herring in detail

The shape you see in `entry.schema`:

```python
{
    "type": "function",                # ← outer wrap, from WORKFLOW_SCHEMA
    "function": {
        "name": "telegram_ads_workflow",
        "description": "...",
        "parameters": {
            "type": "object",
            "properties": {
                "workflow": {"type": "string", "enum": [...]},
                ...
            },
            "required": ["workflow"],
        },
    },
}
```

The shape you see in `registry.get_definitions` output (per-tool):

```python
{
    "type": "function",                # ← registry.get_definitions wrap
    "function": {                      # ← this IS entry.schema (still wrapped)
        "type": "function",            #   ← and it contains its own wrap
        "function": {
            "name": "telegram_ads_workflow",
            "parameters": {...},
        },
        "name": "telegram_ads_workflow",   # ← registry also adds name
    },
}
```

The double `{"type": "function", "function": ...}` is **not a bug**.
It is the natural shape: `WORKFLOW_SCHEMA` is one wrap, the registry
adds another. The LLM providers (OpenAI, Anthropic, DeepSeek) all
parse this correctly and look at the inner `function.parameters`.

**When you see the double wrap and think "that's a bug":** don't.
Verify by running §3.1 (raw `entry.schema`) and §3.2 (`get_definitions`)
and confirm both reach the same `parameters.properties` after
un-wrapping. If they do, the schema is fine.

---

## 7. The "parameterless tool" defect (schema empty after registration)

Distinct from §5. If `get_definitions` returns a tool with
`"parameters": {"type": "object", "properties": {}}` even though
`entry.schema` has full properties, you've hit a different bug class.
Likely causes:

- `_tool_defs_cache` is stale and not invalidated. Call
  `model_tools._tool_defs_cache.clear()` in the diagnostic step and
  re-fetch.
- `dynamic_schema_overrides` (if set on the entry) is returning
  empty `properties`. Check the override function.
- `sanitize_tool_schemas` is stripping properties. This is rare for
  well-formed schemas, but malformed `anyOf` / `oneOf` shapes can
  trigger it. Inspect the schema after sanitize.
- Tool is in a different `toolset` than the one the session enabled.
  Check `toolsets.resolve_toolset("telegram_ads")`.

For Telegram Ads specifically, the
`telegram_ads_tool_schema_defect` structured error (see
`operating_discipline_methodology.md` §5) is the correct response —
not a Playwright/Xvfb fallback.

---

## 8. What this methodology does NOT cover

- Live Telegram Ads UI errors (moderation rejections, spend throttling,
  etc.) — those come back as `API_ERROR` or `FORBIDDEN`, and you read
  the full message.
- Browser session that genuinely fails to load (network down, MFA
  required, etc.) — those come back from the adapter, not the tool
  layer, and need a different diagnostic path.
- Adapter code bugs (e.g. `choose_account` crash) — those come back
  as `WORKFLOW_ERROR` with a stack trace in the message. Read the
  message; the fix is in adapter code, not in the tool layer.
- Config file errors (`/home/hermes/.hermes/telegram_ads.yaml`
  malformed) — those surface as `ADAPTER_ACQUISITION_FAILED` with a
  yaml parse error. Read the message.

---

## 9. Quick checklist (copy-paste)

```
□ Read user-reported error. Classify into one of §4.
□ If UNKNOWN_WORKFLOW / missing param: do NOT restart gateway, do NOT
  open browser, do NOT patch tool yet.
□ Run §3.1 — confirm registry schema is correct.
□ Run §3.2 — confirm get_tool_definitions schema is correct.
□ Run §3.3 — confirm handle_function_call works with
  function_args={"workflow": "snapshot"} and mocked deps.
□ If §3.3 succeeds with the right args: ask the user to paste the raw
  tool_call from their LLM response. Look for missing/misnamed keys.
□ If §3.3 fails: read the actual error. Patch only what failed.
□ If the bug is "LLM didn't pass the param": offer §5 hardening
  options to the operator before applying.
```

The goal is to get to "tool is fine, LLM didn't pass the param"
without restarting anything, opening any browser, or running any live
Telegram Ads action. That diagnosis is the highest-value outcome of
this methodology because it rules out an entire class of false
positives (gateway restart, browser session, tool file edit) that
would cost time and risk violating Operating Discipline.
