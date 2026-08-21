"""Append-only JSONL audit log for all tool actions.

Sensitive data handling:
    - account_token is hashed (SHA-256, truncated) if config.audit.hash_account_tokens=True
    - Confirmation_id is logged in full so support can correlate
    - Raw API responses are NOT logged (only status_code + error-field if any)
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from hermes_telegram_ads.config import AuditConfig


class AuditLogger:
    def __init__(self, config: AuditConfig) -> None:
        self._config = config
        if self._config.enabled:
            self._config.path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        action: str,
        *,
        risk_level: str = "safe_read",
        result_status: str = "ok",
        account_token: str | None = None,
        confirmation_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not self._config.enabled:
            return

        entry: dict[str, Any] = {
            "time": datetime.now(tz=UTC).isoformat(),
            "tool": "telegram_ads",
            "action": action,
            "risk_level": risk_level,
            "result_status": result_status,
        }
        if account_token is not None:
            entry["account_token_hash"] = self._mask(account_token)
        if confirmation_id is not None:
            entry["confirmation_id"] = confirmation_id
        if extra:
            # Avoid leaking large blobs.
            entry.update({k: v for k, v in extra.items() if k not in {"raw_response", "html"}})

        try:
            with self._config.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError:
            # Audit log failures must not crash the tool.
            pass

    def _mask(self, token: str) -> str:
        if not self._config.hash_account_tokens:
            return token
        h = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"sha256:{h[:16]}"
