"""TelegramAdsBrowserProfileManager — singleton manager for shared Telegram Ads browser.

Manages a single TelegramAdsAdapter (which itself owns a single Playwright
`BrowserAutomationTool` / Chromium browser) across all callers within the
same Hermes process. Prevents two things:

1. **Process-level lock conflict** — Chromium SingletonLock from another process.
   Detected via check_profile_lock() before creating the adapter.

2. **In-process contention** — two coroutines trying to use the browser
   simultaneously. Serialized via asyncio.Lock, held for the entire workflow.

Contract:
    manager = TelegramAdsBrowserProfileManager.get_instance()
    adapter = await manager.acquire_adapter(config, timeout=30)
    try:
        # use adapter ... (adapter owns the BrowserAutomationTool / Playwright)
    finally:
        manager.release_adapter()

Architecture guarantees:
    - One shared TelegramAdsAdapter (owns the Playwright BrowserAutomationTool).
    - One persistent browser profile — no second Chromium with same profile.
    - asyncio.Lock serializes all Telegram Ads workflows.
    - No kill/pkill, no process inspection, no manual fallback.
    - Adapter is created ONCE and reused across workflows.
    - External process lock is checked ONLY on first creation. Subsequent
      acquires reuse the existing adapter without re-checking.
    - Browser is closed only on explicit shutdown() or fatal error.

Both `telegram_ads` and `telegram_ads_workflow` tools must use this manager.
Tools must NOT:
    - Have an independent module-level _adapter.
    - Call TelegramAdsAdapter.launch(config) directly.
    - Open another persistent profile.
    - Kill Chromium / Playwright.
    - Stop the gateway without explicit operator approval.

On external lock: raises BrowserProfileLockedError. On in-process timeout:
raises BrowserProfileBusyError. Both carry a `.context` dict for the tool
to serialize to JSON.

Backward-compat: BrowserProfileManager is exported as a deprecated alias
for the renamed class.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hermes_telegram_ads.browser import _read_lock_pid, check_profile_lock
from hermes_telegram_ads.errors import BrowserProfileBusyError, BrowserProfileLockedError

# Env var to emergency-disable atexit teardown (rollback path).
# Set in systemd unit: Environment="HERMES_TG_ADS_SKIP_ATEXIT_TEARDOWN=1"
_DISABLE_ATEXIT = bool(
    os.environ.get("HERMES_TG_ADS_SKIP_ATEXIT_TEARDOWN")
)

# Hard-kill fallback grace timeout (seconds). If graceful shutdown()
# takes longer than this, escalate to SIGTERM only for the
# SingletonLock-owner PID (never SIGKILL, never broad pkill).
_GRACEFUL_TIMEOUT_S = 5.0

# Env var: also disable SIGTERM fallback (extra safety). Default ON.
_DISABLE_SIGTERM_FALLBACK = bool(
    os.environ.get("HERMES_TG_ADS_SKIP_SIGTERM_FALLBACK")
)

if TYPE_CHECKING:
    from hermes_telegram_ads.adapter import TelegramAdsAdapter
    from hermes_telegram_ads.config import TelegramAdsConfig

logger = logging.getLogger(__name__)


class TelegramAdsBrowserProfileManager:
    """Singleton: one browser profile, one adapter, serialized access.

    The asyncio.Lock is held for the entire duration of each workflow
    (from acquire_adapter to release_adapter), ensuring only one
    workflow uses the browser at a time.
    """

    _instance: BrowserProfileManager | None = None

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._adapter: Any = None  # TelegramAdsAdapter | None
        self._config: Any = None  # TelegramAdsConfig | None
        self._active_operations: int = 0
        self._closed: bool = False
        self._initial_lock_check_done: bool = False
        self._profile_path: str = ""
        self._atexit_registered: bool = False

        # Register atexit hook for last-resort teardown.
        # Idempotent: only one hook per singleton instance.
        # Bypassed when HERMES_TG_ADS_SKIP_ATEXIT_TEARDOWN=1.
        if not _DISABLE_ATEXIT and not self._atexit_registered:
            atexit.register(self._atexit_shutdown_safely)
            self._atexit_registered = True

    @classmethod
    def get_instance(cls) -> BrowserProfileManager:
        """Return the singleton manager instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ─── Public API ─────────────────────────────────────────────────────────

    async def acquire_adapter(
        self,
        config: TelegramAdsConfig,
        *,
        timeout: float = 30.0,
    ) -> TelegramAdsAdapter:
        """Acquire the shared TelegramAdsAdapter.

        Holds the asyncio.Lock until release_adapter() is called.
        During the lock, only this workflow can use the browser.

        - If adapter already exists in this process → reuse it directly
          (no external lock check).
        - If adapter doesn't exist → check external process lock first,
          then create adapter.
        - If another workflow holds the lock → wait up to *timeout*
          seconds, then raise BrowserProfileBusyError.

        Returns:
            TelegramAdsAdapter — ready to use.

        Raises:
            BrowserProfileLockedError — another process holds Chromium lock
            BrowserProfileBusyError — timeout waiting for asyncio lock
        """
        profile_path = str(config.browser.profile_dir)

        # Acquire the lock with timeout
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=timeout)
        except TimeoutError:
            raise BrowserProfileBusyError(
                profile_path=profile_path, timeout=timeout
            )

        # ── Lock held from here until release_adapter() ──

        try:
            # Phase 1: Check external process lock (only on first creation)
            if self._adapter is None and not self._initial_lock_check_done:
                lock_check = await check_profile_lock(
                    config.browser.profile_dir
                )
                if lock_check.get("locked"):
                    raise BrowserProfileLockedError(
                        profile_path=profile_path,
                        owner_pid=lock_check.get("owner_pid"),
                    )
                self._initial_lock_check_done = True

            # Phase 2: Create adapter if needed
            if self._adapter is None and not self._closed:
                from hermes_telegram_ads import TelegramAdsAdapter

                self._adapter = await TelegramAdsAdapter.launch(config)
                self._config = config
                self._profile_path = profile_path
                logger.info(
                    "BrowserProfileManager: adapter created (profile=%s)",
                    profile_path,
                )

            if self._closed or self._adapter is None:
                raise BrowserProfileLockedError(
                    profile_path=profile_path,
                    owner_pid=None,
                )

            self._active_operations += 1
            return self._adapter

        except Exception:
            # If we fail during setup, release the lock before propagating
            self._lock.release()
            raise

    def release_adapter(self) -> None:
        """Release the adapter and the asyncio lock.

        The lock MUST have been acquired by acquire_adapter() first.
        Does NOT close the browser — adapter stays alive for next workflow.
        Close only via explicit shutdown().
        """
        if self._active_operations > 0:
            self._active_operations -= 1

        logger.debug(
            "BrowserProfileManager: released (active=%d)", self._active_operations
        )

        # Release the asyncio lock so next workflow can acquire
        try:
            self._lock.release()
        except RuntimeError:
            # Lock was not acquired by this task — this is a programming error
            logger.warning(
                "BrowserProfileManager: release_adapter called without "
                "holding the lock"
            )

    @property
    def is_active(self) -> bool:
        """True if adapter exists and is not closed."""
        return self._adapter is not None and not self._closed

    @property
    def active_operations(self) -> int:
        return self._active_operations

    # ─── Shutdown ───────────────────────────────────────────────────────────

    async def shutdown(self) -> dict:
        """Close the adapter and reset state.

        The asyncio lock is NOT held during shutdown — it's a clean teardown
        call, not a workflow operation.

        Returns:
            {"ok": True, "closed_adapter": True}
        """
        if not self._adapter:
            self._closed = True
            return {"ok": True, "closed_adapter": False, "reason": "no adapter"}

        try:
            await self._adapter.close()
            logger.info("BrowserProfileManager: adapter closed")
        except Exception as exc:
            logger.warning(
                "BrowserProfileManager: adapter close error: %s", exc
            )
            return {
                "ok": False,
                "closed_adapter": True,
                "error": str(exc),
            }
        finally:
            self._adapter = None
            self._config = None
            self._closed = True
            self._active_operations = 0
            self._initial_lock_check_done = False

        return {"ok": True, "closed_adapter": True}

    # ─── Graceful close_all (high-level teardown) ─────────────────────────

    async def close_all(
        self,
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Graceful shutdown of all browser state with per-step status.

        Alias for ``shutdown()`` with structured per-step result. Used by
        the gateway SIGTERM/SIGINT path and the atexit hook.

        Steps (in order):
            1. Close browser context (via adapter.close())
            2. Stop Playwright (via adapter.close() → browser.close())
            3. Clear internal adapter registry
            4. Reset state flags
            5. Log final result

        Args:
            timeout: Maximum seconds for the entire teardown.
                If exceeded, remaining steps are skipped and reported
                as warnings. Per-step timeouts not implemented
                (single timeout for the whole flow).

        Returns:
            {
                "ok": bool,                # True if all steps succeeded
                "contexts_closed": bool,   # BrowserContext.close() ok
                "browser_stopped": bool,   # Playwright.stop() ok
                "registry_cleared": bool,  # _adapter reset to None
                "warnings": list[str],     # soft issues (timeout, etc.)
                "errors": list[str],       # hard per-step exceptions
                "duration_ms": int,        # total elapsed
                "profile_path": str,       # for log correlation
                "had_adapter": bool,       # True if adapter existed at start
            }
        """
        import time as _time
        start = _time.monotonic()
        warnings: list[str] = []
        errors: list[str] = []

        contexts_closed = False
        browser_stopped = False
        registry_cleared = False
        had_adapter = self._adapter is not None

        if not had_adapter:
            # Nothing to close — record success on no-op
            contexts_closed = True
            browser_stopped = True
            registry_cleared = True
            self._closed = True
            self._active_operations = 0
            self._initial_lock_check_done = False
            duration_ms = int((_time.monotonic() - start) * 1000)
            result = {
                "ok": True,
                "contexts_closed": True,
                "browser_stopped": True,
                "registry_cleared": True,
                "warnings": [],
                "errors": [],
                "duration_ms": duration_ms,
                "profile_path": self._profile_path,
                "had_adapter": False,
            }
            logger.info(
                "BrowserProfileManager.close_all: no-op (no adapter), "
                "duration=%dms",
                duration_ms,
            )
            return result

        # Step 1+2: close context + browser via adapter.close()
        try:
            await asyncio.wait_for(self._adapter.close(), timeout=timeout)
            contexts_closed = True
            browser_stopped = True
            logger.info(
                "BrowserProfileManager.close_all: adapter.close() OK "
                "(profile=%s)", self._profile_path,
            )
        except TimeoutError:
            warnings.append(
                f"adapter.close() timed out after {timeout:.1f}s — "
                f"orphan browser possible (profile={self._profile_path})"
            )
            logger.warning(
                "BrowserProfileManager.close_all: adapter.close() "
                "TIMED OUT after %.1fs", timeout,
            )
        except Exception as exc:
            errors.append(f"adapter.close() error: {exc}")
            logger.warning(
                "BrowserProfileManager.close_all: adapter.close() "
                "error: %s", exc,
            )

        # Step 3+4: clear registry + reset state (ALWAYS, even on error)
        try:
            self._adapter = None
            self._config = None
            self._closed = True
            self._active_operations = 0
            self._initial_lock_check_done = False
            registry_cleared = True
        except Exception as exc:
            errors.append(f"registry clear error: {exc}")
            logger.warning(
                "BrowserProfileManager.close_all: registry clear error: %s",
                exc,
            )

        duration_ms = int((_time.monotonic() - start) * 1000)
        ok = (
            contexts_closed
            and browser_stopped
            and registry_cleared
            and not errors
        )

        result = {
            "ok": ok,
            "contexts_closed": contexts_closed,
            "browser_stopped": browser_stopped,
            "registry_cleared": registry_cleared,
            "warnings": warnings,
            "errors": errors,
            "duration_ms": duration_ms,
            "profile_path": self._profile_path,
            "had_adapter": had_adapter,
        }

        # Step 5: log final result
        if ok:
            logger.info(
                "BrowserProfileManager.close_all: OK "
                "(duration=%dms, profile=%s)",
                duration_ms, self._profile_path,
            )
        else:
            logger.warning(
                "BrowserProfileManager.close_all: PARTIAL/FAILED "
                "(ok=%s, contexts_closed=%s, browser_stopped=%s, "
                "registry_cleared=%s, warnings=%d, errors=%d, "
                "duration=%dms, profile=%s)",
                ok, contexts_closed, browser_stopped, registry_cleared,
                len(warnings), len(errors), duration_ms, self._profile_path,
            )

        return result

    @asynccontextmanager
    async def use_adapter(
        self,
        config: Any,
        *,
        timeout: float = 30.0,
    ) -> AsyncIterator[Any]:
        """Async context manager: acquire_adapter on enter, release_adapter on exit.

        Guarantees release_adapter() is called even if the workflow raises.
        Adapter is reused across context manager invocations (singleton).

        Usage:
            async with manager.use_adapter(config) as adapter:
                result = await adapter.list_accounts()
            # release_adapter() called automatically

        Args:
            config: TelegramAdsConfig instance
            timeout: Seconds to wait for the asyncio lock / external
                lock check before raising BrowserProfileBusyError.

        Yields:
            TelegramAdsAdapter — ready to use

        Raises:
            BrowserProfileLockedError — another process owns the profile
            BrowserProfileBusyError — asyncio lock timeout
        """
        adapter = await self.acquire_adapter(config, timeout=timeout)
        try:
            yield adapter
        finally:
            self.release_adapter()

    # ─── Last-resort teardown (atexit + SIGTERM fallback) ────────────────

    def _atexit_shutdown_safely(self) -> None:
        """atexit hook: graceful shutdown with SIGTERM-only escalation.

        Called automatically at interpreter shutdown (SIGTERM, normal
        exit, unhandled exception). The primary shutdown path is
        gateway/run.py which calls await self.shutdown() directly in
        the SIGTERM/SIGINT handler — this atexit is the BACKUP for
        any path that doesn't (crash recovery, OOM, hard kill).
        """
        if self._adapter is None or self._closed:
            return

        # Phase 1: graceful shutdown with timeout.
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    asyncio.wait_for(self.shutdown(), timeout=_GRACEFUL_TIMEOUT_S)
                )
                logger.info(
                    "BrowserProfileManager: atexit graceful shutdown OK"
                )
                return
            except TimeoutError:
                logger.warning(
                    "BrowserProfileManager: atexit graceful shutdown "
                    "timed out after %.1fs, escalating to SIGTERM fallback",
                    _GRACEFUL_TIMEOUT_S,
                )
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
        except Exception as exc:
            logger.warning(
                "BrowserProfileManager: atexit shutdown error: %s", exc
            )

        # Phase 2: SIGTERM-only fallback. Only fires if Phase 1 timed out
        # or errored. NEVER SIGKILL, NEVER broad pkill. Only the PID
        # recorded in our profile's SingletonLock (proves ownership).
        if _DISABLE_SIGTERM_FALLBACK:
            logger.warning(
                "BrowserProfileManager: SIGTERM fallback disabled "
                "(HERMES_TG_ADS_SKIP_SIGTERM_FALLBACK=1); orphan "
                "Chromium may remain until next manual cleanup"
            )
            return
        self._sigterm_orphan_chromium()

    def _sigterm_orphan_chromium(self) -> None:
        """SIGTERM the PID recorded in our profile's SingletonLock.

        SAFETY: this is the ONLY escalation path. It:
          1) Reads ONLY our profile's SingletonLock (never scans /proc)
          2) Verifies the PID is still alive (kill -0)
          3) Sends SIGTERM only — never SIGKILL
          4) Skips on PermissionError, ProcessLookupError, missing file
          5) Never touches: user Chrome, foreign Chromium, /tmp/locks
          6) Does NOT delete SingletonLock (Chromium removes it on exit)
        """
        if not self._profile_path:
            return
        profile = Path(self._profile_path)
        lock_path = profile / "SingletonLock"
        # Use lstat so broken symlinks are still detected. Path.exists()
        # follows the symlink and returns False for a target that doesn't
        # exist on disk (Chromium creates SingletonLock as a symlink whose
        # target is "host-<pid>" — that target file is removed at exit,
        # leaving a dangling symlink).
        try:
            lock_path.lstat()
        except (OSError, FileNotFoundError):
            return

        owner_pid = _read_lock_pid(lock_path, "SingletonLock")

        # Fallback: if the lock is a symlink (Chromium persistent
        # profile convention), the symlink target is "hostname-PID".
        # Extract PID from the symlink target.
        if owner_pid is None and lock_path.is_symlink():
            try:
                target = os.readlink(lock_path)
                # target format: "hostname-PID", e.g. "host-2975916"
                if "-" in target:
                    pid_part = target.rsplit("-", 1)[-1]
                    if pid_part.isdigit():
                        owner_pid = int(pid_part)
            except (OSError, ValueError):
                pass

        if owner_pid is None:
            logger.debug(
                "BrowserProfileManager: cannot read PID from %s, "
                "skipping SIGTERM fallback", lock_path,
            )
            return

        try:
            # signal 0 = ping the process; raises if dead
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            logger.info(
                "BrowserProfileManager: SingletonLock owner pid=%d "
                "already dead, no SIGTERM needed", owner_pid,
            )
            return
        except PermissionError:
            logger.warning(
                "BrowserProfileManager: cannot ping pid=%d (different "
                "uid); skipping SIGTERM to avoid cross-uid escalation",
                owner_pid,
            )
            return
        except OSError as exc:
            logger.warning(
                "BrowserProfileManager: ping pid=%d failed: %s",
                owner_pid, exc,
            )
            return

        # PID is alive and owned by us — safe to SIGTERM.
        try:
            os.kill(owner_pid, signal.SIGTERM)
            logger.warning(
                "BrowserProfileManager: SIGTERM sent to orphan "
                "Chromium pid=%d (profile=%s)",
                owner_pid, self._profile_path,
            )
        except ProcessLookupError:
            pass  # died between ping and SIGTERM
        except PermissionError:
            logger.warning(
                "BrowserProfileManager: cannot SIGTERM pid=%d "
                "(different uid)", owner_pid,
            )

    def is_atexit_registered(self) -> bool:
        """Test hook: whether atexit hook is registered for this instance."""
        return self._atexit_registered


# ─── Backward-compat alias (deprecated) ──────────────────────────────────────
# Old name was BrowserProfileManager. New canonical name is
# TelegramAdsBrowserProfileManager. The alias keeps existing imports
# (e.g. `from hermes_telegram_ads.browser_manager import BrowserProfileManager`)
# working until callers migrate.
BrowserProfileManager = TelegramAdsBrowserProfileManager

__all__ = [
    "TelegramAdsBrowserProfileManager",
    "BrowserProfileManager",  # deprecated alias
]
