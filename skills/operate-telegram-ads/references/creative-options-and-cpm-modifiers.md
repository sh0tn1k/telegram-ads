# Telegram Ads creative options & CPM modifier audit

Session pattern verified 2026-06-05 against installed package
`hermes_telegram_ads` @ `fix/browser-recovery` (commit `aed0ea818`,
version `0.1.0`).

Read-only audit: no live Telegram Ads calls, no media uploaded, no
mutating action executed, no raw Playwright/Chromium/Xvfb.

## Capability matrix (4 creative options)

| Capability | UI supported | Tool / schema field | Payload support | Validate support | Create/Edit support | CPM modifier handled | Status |
|---|---|---|---|---|---|---|---|
| 1. Show bot/channel picture (`picture=1`) | yes | `show_picture: bool` in `CreateAdDraft` (default `True`) | yes — `build_create_ad_payload` emits `picture: 1/0` | yes (passed through `checkAdPost`) | create: yes; edit: NOT carried (immutable) | **no** | partial — exposed in create, missing in edit, no modifier math |
| 2. Upload photo (PNG/JPG/WEBP, 16:9) | yes (`/file/upload` → opaque token) | `CreateAdDraft.media_path: str \| None` (local file); `EditAdDraft.media_token: str \| None` | create: **BROKEN** — 3 callsites of `build_create_ad_payload(self.api, draft)` in `adapter.py` ignore `media_token=` kwarg; edit: yes (`build_edit_ad_payload` emits `media:`) | **BROKEN** — `_build_check_payload` → `build_check_ad_post_payload(self.api, draft)` called without `media_token=`; `checkAdPost` always sees `media: ""` | create: token not submitted; edit: token submitted | **no** | partial — schema accepts media_path, edit wires it, create does not |
| 3. Upload video (MP4/MOV/WEBM) | yes (server-side 16:9 enforcement) | same as photo (single `media_path` / `media_token` field) | same as photo | yes (server-side; package pre-check raises `MediaValidationError("not yet implemented for .mp4")`) | same as photo | **no** | partial — same gap as photo |
| 4. Custom emoji in ad text (Premium emoji status) | yes | none — `text: str` is a free-form string | n/a — emoji rides inside `text` | partial — `validate_ad_text` checks length / no-line-breaks / no-bullets / no-masked-profanity / no-excess-caps. No emoji-specific check. | yes — flows through `text` | **no** | partial — works as a string, no first-class schema, no modifier |

## Tool inventory (LLM-facing, 58 total)

- `telegram_ads_upload_media` — present, `SafetyClass.DRAFT`, returns `{media_token, token_length, file_path}`. Validates 16:9 locally for images; raises `MediaValidationError` for video (intentional — server-side check).
- `telegram_ads_remove_media` — **NOT IMPLEMENTED**. No tool, no adapter method, no `safety.py` entry.
- `telegram_ads_validate_ad` with media — DRAFT, but routes through `checkAdPost` with empty `media` because `_build_check_payload` does not pass `media_token`.
- `telegram_ads_preview_ad` with media — DRAFT, calls `validate_ad` (same gap) plus browser screenshot. Screenshot works; `checkAdPost` preview does not include media.
- `telegram_ads_create_ad` with media token — **BROKEN**. `CreateAdDraft.media_path` is accepted, but `adapter.create_ad` does not call `upload_media`, and `build_create_ad_payload` always emits `media: ""`.
- `telegram_ads_edit_ad` with media token — works. `EditAdDraft.media_token` is wired through `build_edit_ad_payload` → `media: draft.media_token or ""`. Edit path is the only one that submits a media field.

## Critical bugs

1. **Create path silently drops media.** The LLM-facing schema says
   `media_path` is supported. The user passes it. The server receives
   `media=""`. No error, no warning. The agent believes the ad is
   media-bearing; it isn't. **Triple-check at approval time** that any
   `telegram_ads_create_ad` confirmation that includes a `media_path`
   field has been confirmed via a live `telegram_ads_get_ad` /
   `telegram_ads_get_ad_creative` call after submission.

2. **`checkAdPost` cannot preview media.** `telegram_ads_validate_ad`
   and `telegram_ads_preview_ad` both go through the broken path.
   Media-bearing drafts cannot be validated before submission. The
   server-side `media` field is always empty.

3. **No `remove_media` tool.** Telegram's live form clears media by
   `edit_ad` with `media_token=""`. The package has no
   `telegram_ads_remove_media` or `telegram_ads_remove_picture` tool.
   Agents that want to clear media must do it through
   `telegram_ads_edit_ad` and explain the intent in the human
   approval ask.

4. **No CPM modifier table.** The package does NOT auto-adjust CPM
   based on:
   - show bot picture (+30% per Telegram UI convention)
   - custom emoji (+50%)
   - photo (+50%)
   - video (+80%)

   The `cpm` value sent in the payload is always the raw `draft.cpm`.
   The values cited above are from the live UI and are **subject to
   change** — verify with the live form before quoting them in
   client-facing communication.

5. **No first-class emoji handling.** `text` is a free-form string.
   Custom emoji status rides as raw characters (Telegram renders
   them). There is no `text_entities` field, no Premium emoji
   validation, no `emoji_status` field. The +50% modifier is not
   surfaced in approval asks.

## Patch recipe (when authorized)

### 1. Wire `media_path` → `media_token` in create path

`hermes_telegram_ads/hermes_tools.py` — `_h_create_ad`:

```python
raw = await adapter.create_ad(
    d, confirmation_id=confirmation_id, media_path=d.media_path
)
```

`hermes_telegram_ads/adapter.py` — `create_ad`:

```python
async def create_ad(
    self,
    draft: CreateAdDraft,
    *,
    confirmation_id: str,
    media_path: str | Path | None = None,
) -> dict[str, Any]:
    await self.ensure_logged_in()
    self.safety.raise_if_policy_violations(draft.text, draft.promote_url)
    params = self._confirmation_params_create(draft)
    self.safety.gate(METHOD_CREATE_AD, params, confirmation_id=confirmation_id)

    if not self.browser.current_url.endswith("/account/ad/new"):
        await self.browser.open_url(BASE_URL + URL_AD_NEW)
        await self.api.bootstrap()

    media_token: str | None = None
    if media_path is not None:
        media_token = await self.upload_media(media_path)

    payload = await build_create_ad_payload(
        self.api, draft, media_token=media_token
    )
    result = await self.api.create_ad(payload)
    self.audit.log(
        METHOD_CREATE_AD,
        risk_level=RiskLevel.CONFIRM_REQUIRED.value,
        confirmation_id=confirmation_id,
        extra={
            "title": draft.title,
            "cpm": draft.cpm,
            "budget": draft.budget,
            "media_uploaded": bool(media_token),
        },
    )
    return result
```

### 2. Wire `media_path` into `checkAdPost`

`adapter.py` — `validate_ad`:

```python
async def validate_ad(
    self,
    draft: CreateAdDraft,
    *,
    media_path: str | Path | None = None,
) -> dict[str, Any]:
    await self.ensure_logged_in()
    if not self.browser.current_url.endswith("/account/ad/new"):
        await self.browser.open_url(BASE_URL + URL_AD_NEW)
        await self.api.bootstrap()
    self.safety.raise_if_policy_violations(draft.text, draft.promote_url)

    media_token: str | None = None
    if media_path is not None:
        media_token = await self.upload_media(media_path)

    payload = await build_check_ad_post_payload(
        self.api, draft, media_token=media_token
    )
    result = await self.api.check_ad_post(payload)
    self.audit.log(
        "validate_ad",
        extra={
            "valid": not result.get("error"),
            "field": result.get("field"),
            "error": result.get("error"),
            "media_uploaded": bool(media_token),
        },
    )
    return result
```

### 3. Add `telegram_ads_remove_media` and `telegram_ads_remove_picture` (informational)

Both as `SafetyClass.SAFE_READ` wrappers that explain the only path
to clear media is `edit_ad` with `media_token=""`:

```python
async def _h_remove_media(
    self, ad_id: int, **_: Any
) -> dict[str, Any]:
    return tool_forbidden(
        "telegram_ads_remove_media",
        "remove_media",
        message=(
            "Telegram Ads does not expose a 'remove media' API. To clear a "
            "media token, edit_ad with media_token=''."
        ),
    )
```

### 4. CPM modifier table (declarative, opt-in)

New module `hermes_telegram_ads/cpm_modifiers.py`:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class CpmModifier:
    name: str
    factor: float
    source: str
    notes: str

MODIFIERS: dict[str, CpmModifier] = {
    "show_picture": CpmModifier(
        name="Show bot/channel picture",
        factor=1.30,
        source="Telegram Ads UI",
        notes="UI shows +30%. Subject to change; re-verify before quoting.",
    ),
    "premium_emoji": CpmModifier(
        name="Custom/premium emoji in text",
        factor=1.50,
        source="Telegram Ads UI",
        notes="UI shows +50%. Subject to change; re-verify before quoting.",
    ),
    "media_photo": CpmModifier(
        name="Photo media attached",
        factor=1.50,
        source="Telegram Ads UI",
        notes="UI shows +50%. Subject to change; re-verify before quoting.",
    ),
    "media_video": CpmModifier(
        name="Video media attached",
        factor=1.80,
        source="Telegram Ads UI",
        notes="UI shows +80%. Subject to change; re-verify before quoting.",
    ),
}

def compute_effective_cpm(
    base_cpm: float,
    *,
    show_picture: bool = False,
    has_premium_emoji: bool = False,
    media_kind: str | None = None,  # "photo" | "video" | None
) -> tuple[float, list[str]]:
    factors = []
    if show_picture:
        factors.append(MODIFIERS["show_picture"])
    if has_premium_emoji:
        factors.append(MODIFIERS["premium_emoji"])
    if media_kind == "photo":
        factors.append(MODIFIERS["media_photo"])
    if media_kind == "video":
        factors.append(MODIFIERS["media_video"])
    effective = base_cpm
    applied = []
    for m in factors:
        effective *= m.factor
        applied.append(f"{m.name} (+{(m.factor-1)*100:.0f}%)")
    return round(effective, 4), applied
```

Wire into `_h_create_ad` and `_h_edit_ad` `human_summary` so the
modifier is visible in the approval ask. **The actual CPM submitted
is still `draft.cpm`** — the modifier is informational unless the
package owner decides to compute and submit `effective_cpm` instead.

### 5. Tests to add (sandbox-safe, no live browser)

- `test_show_picture_passed_in_create_payload` — `FakeAdapter` captures the `media_path` passed to `create_ad` and asserts it equals the draft's `media_path`.
- `test_validate_ad_passes_media_token_to_check_payload` — `FakeAdapter.check_ad_post` is called with `media=token` not `media=""`.
- `test_remove_media_returns_forbidden_envelope` — `telegram_ads_remove_media` returns the structured "edit_ad with media_token='' is the only path" envelope.
- `test_cpm_modifier_applied_for_show_picture` — `compute_effective_cpm(10.0, show_picture=True) == (13.0, ["Show bot/channel picture (+30%)"])`.
- `test_cpm_modifier_stacks_photo_and_picture` — `compute_effective_cpm(10.0, show_picture=True, media_kind="photo") == (19.5, [..., ...])` (10 × 1.3 × 1.5 = 19.5).

## Acceptance-pass impact (read-only verification)

The creative-options gap does NOT break the standard read-only
acceptance pass (items 1–10 in `references/acceptance-readonly-protocol.md`).
What it changes:

- Item 9 (`telegram_ads_validate_ad` for safer recreate draft) must
  be aware that `checkAdPost` cannot preview media. If a draft
  includes `media_path`, the validation result's `preview` will not
  contain the media. Surface this in the verdict and recommend
  `telegram_ads_preview_ad` for the screenshot path (which works).
- Item 6 (`telegram_ads_get_ad_stats`) reads the live state, so
  the silent-drop bug does not affect the acceptance pass.

## TL;DR

- Schema accepts more than the package delivers for media.
- Edit is the only path that actually submits a media token.
- The `cpm` modifier math is not in the package — UI values
  (+30% / +50% / +50% / +80%) are observation, not implementation.
- A `telegram_ads_remove_media` is missing; route through `edit_ad`.
- All patches above are **opt-in** and require explicit per-step
  approval — none should be applied without it.
