# Active runtime vs on-disk patch verification

## Trigger

Use this reference when a code patch has been applied to the
`hermes_telegram_ads` package on disk, but a live `telegram_ads_*` tool
call still returns the **old** behavior. Common symptom: a guard that
should block a draft instead passes the draft through; a placement
matrix that should report `media_ignored_by_placement=true` instead
reports `modifiers_applied=["media_photo"]`.

The package source is editable-installed at
`~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg`
and shared across both `default` and `deepseek` profile gateways via
the venv's editable `.pth` mapping. The LLM-facing tool surface is
computed once per session/gateway-startup by importing the package and
introspecting `TELEGRAM_ADS_TOOLS`. **Once the gateway is up, the
in-process module state is frozen until the gateway restarts.** A
`patch()` to a source file on disk does NOT change what the live
gateway serves.

## Pre-mutating-action gate (mandatory for any non-read-only step)

Before issuing `telegram_ads_create_ad`, `telegram_ads_edit_ad`,
`telegram_ads_change_cpm`, `telegram_ads_start_ad`, `telegram_ads_stop_ad`,
`telegram_ads_add_to_budget`, or any other mutating tool:

1. **Verify the live tool surface has the patch.** Pick a known
   observable behavior delta from the patch (e.g. `target_type=search +
   media_path` should return `unsupported_media_for_target_type`).
   Call the relevant read-only or DRAFT-class tool with the trigger
   input. Compare the actual response shape against the post-patch
   expected shape.
2. If the response is **old behavior**: the gateway is on cached
   imports. Stop the live flow. Do not chain into mutating actions.
   Report the discrepancy and request explicit approval for a gateway
   restart before retrying.
3. If the response is **new behavior**: the patch is live. Proceed
   with the approved mutating action.

A full live test plan that depends on a guard must include this
verification as its first step, before snapshot/select/upload/validate.

## Canonical live test plan template (when patch verification is a step)

Use this structure when the operator approves a media-create test that
depends on a recently-applied guard:

```text
1. Verify the placement/media guard patch is active
   - call estimate_cpm or validate_ad with target_type=search + media_path
   - expect: structured error "unsupported_media_for_target_type"
     OR a CPM response that does NOT apply the media_photo modifier
     and reports media_ignored_by_placement=true
   - on old behavior: STOP. report "patch not live". request gateway restart.
2. Snapshot the cabinet (telegram_ads_snapshot_accounts).
3. Select ExampleBot / intended cabinet (telegram_ads_choose_account).
4. Verify media file locally (exists, format, 16:9).
5. Prepare channel-targeted draft.
6. upload_media.
7. validate_ad / checkAdPost.
8. If validation failed: stop, show errors, do not proceed.
9. If validation passed: create_ad through approval/confirmation flow.
10. Post-create verification (list_ads, get_ad, get_ad_creative,
    get_ad_budget_status) — final detail is source of truth.
```

This template was the source of `HERMES_MEDIA_CHANNEL_TEST_*`
acceptance (2026-06-06) after `HERMES_MEDIA_REVIEW_TEST_*` was
interpreted as placement mismatch, not a successful media create test.

## Restart scope discipline

A gateway restart is a **separate explicit approval** — not covered
by a "fix the bug" or "verify the patch" mandate. Per
`operate-telegram-ads` §"Gateway restart via systemd", restart one
profile at a time:

```bash
# canonical
systemctl --user restart hermes-gateway-<profile>.service
```

For this Telegram Ads package, the package is shared by both
profiles, so restarting either one re-imports the package. Prefer
restarting `default` (the operator's primary) for the verification probe;
restart `deepseek` only if DeepSeek Companion is also exercising
mutating tools.

Do **not** touch Xvfb `:99`, Playwright, Chromium, or the persistent
profile during restart-related diagnostics.

## Diagnostic recipe — distinguishing "patch not live" from "patch wrong"

If the live tool returns old behavior, run these read-only probes in
order before requesting a restart:

1. **File on disk** — `grep -rn '<expected post-patch symbol>' hermes_telegram_ads/`
   in the editable install. Empty result = patch never applied; the
   issue is local editing, not gateway caching.
2. **Editable install mapping** — `cat
   ~/.hermes/.hermes-agent/venv/lib/python3.11/site-packages/__editable__.hermes_telegram_ads-*.pth`.
   The `MAPPING` entry should point at the editable install path
   above. If it points somewhere else, `pip3 install -e <correct path> --no-deps`.
3. **Module re-import path** — `~/.hermes/.hermes-agent/venv/bin/python -c
   "import hermes_telegram_ads.media; print(hermes_telegram_ads.media.__file__)"`.
   The path returned is what the next import will see. Compare against
   the on-disk file you patched.
4. **Running gateway PID age** —
   `systemctl --user status hermes-gateway-default.service`. If the
   gateway's `Main PID` started after the patch was applied, the
   patch is already live and the old-behavior result is a real
   regression. If `Main PID` started before the patch, the gateway
   has the old import and a restart is the correct next step.

## Lesson: don't fall through to raw Playwright

The temptation when a guard "doesn't work" is to bypass it via raw
Playwright / `execute_code` / terminal subprocess. This is forbidden
per `TELEGRAM_ADS_TOOL_CONTRACT.md` §0 (Operating Discipline) and
causes the additional harm of a second Chromium that wedges the
persistent profile. The correct move on a "patch not live" finding
is **structured error + request gateway restart approval** — never
raw Playwright, never `pkill`, never second browser.

## Cross-reference

- `operate-telegram-ads` §"Gateway restart via systemd" — restart
  command, default-unit `TimeoutStopSec=30` SIGKILL caveat, supervisor
  pre-restart caveat.
- `operate-telegram-ads` §"Acceptance pass failure stop-rules" — when
  to stop the acceptance pass entirely on stale or broken state.
- `references/media-placement-compatibility.md` — the patch that this
  reference is meant to verify (placement-aware media guard).
