"""Media upload helpers.

POST /file/upload (multipart, cookie auth).
Hard requirement: aspect ratio 16:9. Other limits TBD.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from hermes_telegram_ads.api import TelegramAdsApi
from hermes_telegram_ads.constants import MEDIA_ASPECT_RATIO
from hermes_telegram_ads.errors import MediaUnsupportedError, MediaUploadError, MediaValidationError

MediaType = Literal["photo", "video"]
SUPPORTED_UPLOADED_MEDIA_TARGET_TYPES = {"channels"}
MEDIA_PLACEMENT_RECOVERY_HINT = 'use target_type="channels" for uploaded media'

_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}

# Video upload via /file/upload is NOT yet verified end-to-end (aspect/codec
# limits, response shape). Until confirmed live, the create/validate path refuses
# video with a structured not_implemented instead of silently attempting it.
# Flip to True once a bounded live upload_media(video) check passes.
VIDEO_UPLOAD_SUPPORTED = False


def media_type_for_path(file_path: str | Path) -> MediaType | None:
    """Classify a local media file by extension. None if unrecognised."""
    suffix = Path(file_path).suffix.lower()
    if suffix in _PHOTO_SUFFIXES:
        return "photo"
    if suffix in _VIDEO_SUFFIXES:
        return "video"
    return None


def media_hash(file_path: str | Path) -> str:
    """sha256 of the file's bytes (used in the approval fingerprint so a changed
    file forces re-approval). Returns "" if the file cannot be read."""
    p = Path(file_path)
    try:
        return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def uploaded_media_supported_for_target_type(target_type: str | None) -> bool:
    """Return True when a placement supports uploaded photo/video creatives.

    Telegram Ads placements differ: channel ads support uploaded media creatives;
    search ads render as search-result style ads; bot targeting uses logo / show
    picture workflow. Never upload media for unsupported placements because the
    server/UI can validate the file but silently create a media-less ad.
    """
    return (target_type or "").lower() in SUPPORTED_UPLOADED_MEDIA_TARGET_TYPES


def assert_uploaded_media_supported_for_target_type(
    *,
    target_type: str | None,
    media_path: str | Path | None,
) -> None:
    """Raise before upload/check/create if uploaded media is incompatible."""
    if not media_path:
        return
    normalized = (target_type or "").lower()
    if uploaded_media_supported_for_target_type(normalized):
        return
    raise MediaUnsupportedError(
        "unsupported_media_for_target_type",
        message=(
            "Uploaded photo/video creatives are supported only for target_type='channels'. "
            "Search campaigns use text/query placement only; bot targeting uses "
            "logo/show_picture workflow only."
        ),
        context={
            "reason": "unsupported_media_for_target_type",
            "target_type": normalized,
            "media_path": str(media_path),
            "supported_target_types": sorted(SUPPORTED_UPLOADED_MEDIA_TARGET_TYPES),
            "blocked_before_upload": True,
            "media_ignored_by_placement": True,
            "recovery_hint": MEDIA_PLACEMENT_RECOVERY_HINT,
        },
    )


def validate_aspect_ratio(file_path: str | Path) -> tuple[int, int]:
    """Validate 16:9 aspect locally before upload.

    For PNG/JPG uses Pillow if available. If not installed, raises with a
    clear message — installation hint included.

    For MP4 video — TBD (would need ffprobe). Currently raises ValidationError
    so caller can decide.
    """
    p = Path(file_path)
    if not p.exists():
        raise MediaValidationError(f"File not found: {file_path}")

    suffix = p.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        try:
            from PIL import Image
        except ImportError as e:
            raise MediaValidationError(
                "Pillow required for aspect-ratio validation. "
                "Install: pip install pillow",
            ) from e
        with Image.open(p) as im:
            w, h = im.size
    elif suffix in {".mp4", ".mov", ".webm"}:
        # Video aspect check requires ffprobe. Skip with note.
        raise MediaValidationError(
            f"Video aspect-ratio validation not yet implemented for {suffix}. "
            "Confirm 16:9 manually before upload."
        )
    else:
        raise MediaValidationError(f"Unsupported file type: {suffix}")

    if h == 0:
        raise MediaValidationError("Zero height image")
    ar_target_w, ar_target_h = MEDIA_ASPECT_RATIO
    expected_w = (h * ar_target_w) / ar_target_h
    # Allow ~1% tolerance for rounding
    if abs(w - expected_w) > expected_w * 0.01:
        raise MediaValidationError(
            f"Aspect ratio {w}x{h} is not 16:9 (expected width ~{expected_w:.0f} for height {h})"
        )
    return w, h


async def upload_media(api: TelegramAdsApi, file_path: str | Path) -> str:
    """Upload media file, return opaque media token.

    Steps:
        1) Local aspect-ratio pre-check (where possible).
        2) POST /file/upload via api.upload_media().
        3) Parse response → media token.
    """
    p = Path(file_path)
    # Pre-validate (best-effort)
    try:
        validate_aspect_ratio(p)
    except MediaValidationError:
        # For unsupported types (video), let server validate.
        if p.suffix.lower() in {".mp4", ".mov", ".webm"}:
            pass
        else:
            raise

    resp = await api.upload_media(str(p))
    if not isinstance(resp, dict):
        raise MediaUploadError(f"Unexpected upload response shape: {type(resp).__name__}")
    if "error" in resp:
        raise MediaUploadError(str(resp["error"]), context={"response": resp})
    # Inferred success shape — fall back to 'media' key, then any string token-looking value.
    token = resp.get("media")
    if not token:
        for k in ("file", "id", "token", "ref"):
            if isinstance(resp.get(k), str):
                token = resp[k]
                break
    if not token:
        raise MediaUploadError(
            "Upload succeeded but no media token in response",
            context={"response": resp},
        )
    return str(token)


__all__ = [
    "MEDIA_PLACEMENT_RECOVERY_HINT",
    "SUPPORTED_UPLOADED_MEDIA_TARGET_TYPES",
    "assert_uploaded_media_supported_for_target_type",
    "media_hash",
    "media_type_for_path",
    "uploaded_media_supported_for_target_type",
    "upload_media",
    "validate_aspect_ratio",
]
