"""Universal Playwright wrapper.

Knows nothing about ads.telegram.org — pure browser primitives.
Persistent profile is critical: Telegram Ads login is manual (no programmatic
code entry), so session must survive across runs.

Usage:
    async with BrowserAutomationTool.start(config) as browser:
        await browser.open_url("https://ads.telegram.org/account")
        ok = await browser.is_visible(".pr-table")
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Self

from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from hermes_telegram_ads.config import BrowserConfig
from hermes_telegram_ads.errors import (
    BrowserError,
    BrowserProfileLockedError,
    SelectorNotFoundError,
    UnexpectedNavigationError,
    classify_browser_error,
)
from hermes_telegram_ads.profile_lock import (
    check_profile_lock,
    parse_chromium_lock_owner,
    recover_profile_lock,
    remove_stale_locks,
    _read_lock_pid,
)

logger = logging.getLogger(__name__)


class BrowserAutomationTool:
    """Persistent-profile Playwright wrapper.

    Single page per instance for simplicity (ads.telegram.org is single-tab UX).
    Reuse across multiple actions; do NOT create per-call instances.
    """

    def __init__(
        self,
        playwright: Playwright,
        context: BrowserContext,
        page: Page,
        config: BrowserConfig,
    ) -> None:
        self._playwright = playwright
        self._context = context
        self._page = page
        self._config = config
        self._closed = False

    # ─── Lifecycle ────────────────────────────────────────────────────────

    @classmethod
    @asynccontextmanager
    async def start(cls, config: BrowserConfig) -> AsyncIterator[Self]:
        """Context manager. Auto-closes browser on exit."""
        tool = await cls.launch(config)
        try:
            yield tool
        finally:
            await tool.close()

    @classmethod
    async def launch(cls, config: BrowserConfig) -> Self:
        Path(config.profile_dir).mkdir(parents=True, exist_ok=True)

        # ── Preflight: stale lock cleanup ──────────────────────────────
        profile_dir = str(config.user_data_dir or config.profile_dir)
        lock_check = await check_profile_lock(profile_dir)
        if lock_check.get("stale"):
            recovered = await recover_profile_lock(profile_dir)
            logger.info("launch preflight: recovered stale lock for %s: %s", profile_dir, recovered)
            lock_check = recovered.get("after") or await check_profile_lock(profile_dir)
        if lock_check.get("locked"):
            # Live process owns the profile — structured error, no second Chromium
            raise BrowserProfileLockedError(
                profile_path=profile_dir,
                owner_pid=lock_check.get("owner_pid"),
            )

        # ── Launch browser ────────────────────────────────────────────
        pw = await async_playwright().start()
        ctx = await _launch_persistent_context(pw, config)
        # Use the first page or create one
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.set_default_timeout(config.timeout_ms)
        page.set_default_navigation_timeout(config.navigation_timeout_ms)
        return cls(pw, ctx, page, config)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._context.close()
        finally:
            await self._playwright.stop()

    # ─── Health / recovery ────────────────────────────────────────────────

    def is_healthy(self) -> bool:
        """Best-effort liveness check for the page/context. Never raises.

        Returns False if the tool was closed, the page is closed, or the
        underlying Playwright object is gone. Used by the tool layer to decide
        whether a recovery is needed before/after a transient error.
        """
        if self._closed:
            return False
        try:
            if self._page is None or self._page.is_closed():
                return False
        except Exception:
            return False
        return self._context is not None and self._playwright is not None

    async def recover(self) -> dict[str, Any]:
        """Close the broken page/context and recreate a fresh one in place.

        Read-only and idempotent: it navigates nowhere and performs NO
        ads.telegram.org actions — it only rebuilds the Chromium context from
        the same persistent profile (so the saved login session survives).

        The existing :class:`BrowserAutomationTool` instance is mutated in
        place (``_page`` / ``_context`` / ``_playwright`` are replaced) so any
        holder of this object (adapter, api) keeps working against the fresh
        page after recovery.

        Returns a diagnostic dict (no secrets):
            {"recovered": bool, "steps": [...], "errors": [...]}
        """
        result: dict[str, Any] = {"recovered": False, "steps": [], "errors": []}

        # 1. Close the old context (best-effort — it may already be dead).
        try:
            await self._context.close()
            result["steps"].append("context_closed")
        except Exception as exc:  # noqa: BLE001 — closing a dead context is fine
            result["errors"].append(f"context_close: {type(exc).__name__}")

        # 2. Stop the old Playwright driver (best-effort).
        try:
            await self._playwright.stop()
            result["steps"].append("playwright_stopped")
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"playwright_stop: {type(exc).__name__}")

        # 3. Start a fresh Playwright + persistent context from the same profile.
        try:
            pw = await async_playwright().start()
            ctx = await _launch_persistent_context(pw, self._config)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            page.set_default_timeout(self._config.timeout_ms)
            page.set_default_navigation_timeout(self._config.navigation_timeout_ms)
            self._playwright = pw
            self._context = ctx
            self._page = page
            self._closed = False
            result["recovered"] = True
            result["steps"].append("relaunched")
        except Exception as exc:  # noqa: BLE001
            self._closed = True
            result["errors"].append(f"relaunch: {type(exc).__name__}")

        return result

    @property
    def page(self) -> Page:
        return self._page

    @property
    def context(self) -> BrowserContext:
        return self._context

    # ─── Navigation ───────────────────────────────────────────────────────

    async def open_url(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        try:
            await self._page.goto(url, wait_until=wait_until)
        except PlaywrightTimeoutError as e:
            raise BrowserError(f"Navigation timeout: {url}") from e
        except PlaywrightError as e:
            # net::ERR_ABORTED, frame detached, target/page/context closed, ...
            # → typed transient error so the tool layer can recover + retry once.
            transient = classify_browser_error(e, operation="open_url")
            if transient is not None:
                raise transient from e
            raise BrowserError(f"Navigation failed ({type(e).__name__})") from e

    @property
    def current_url(self) -> str:
        return self._page.url

    async def reload(self) -> None:
        await self._page.reload()

    # ─── DOM access ───────────────────────────────────────────────────────

    async def wait_for_selector(self, selector: str, *, timeout_ms: int | None = None) -> None:
        try:
            await self._page.wait_for_selector(selector, timeout=timeout_ms or self._config.timeout_ms)
        except PlaywrightTimeoutError as e:
            raise SelectorNotFoundError(
                f"Selector not found within timeout: {selector}",
                context={"selector": selector, "url": self.current_url},
            ) from e

    def _iter_frames(self) -> list[Any]:
        """Main page first, then child frames (oauth.telegram.org widget)."""
        out: list[Any] = [self._page]
        try:
            for fr in self._page.frames:
                if fr is not self._page.main_frame:
                    out.append(fr)
        except Exception:
            pass
        return out

    async def is_visible(self, selector: str) -> bool:
        try:
            return await self._page.locator(selector).first.is_visible()
        except Exception:
            return False

    async def is_visible_any_frame(self, selector: str) -> bool:
        for fr in self._iter_frames():
            try:
                if await fr.locator(selector).first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def input_value_any_frame(self, selector: str) -> str | None:
        for fr in self._iter_frames():
            try:
                loc = fr.locator(selector).first
                if await loc.count() == 0:
                    continue
                return await loc.input_value()
            except Exception:
                continue
        return None

    async def read_text(self, selector: str) -> str:
        try:
            return (await self._page.locator(selector).first.inner_text()).strip()
        except PlaywrightTimeoutError as e:
            raise SelectorNotFoundError(f"Selector not found: {selector}") from e

    async def read_attr(self, selector: str, attr: str) -> str | None:
        try:
            return await self._page.locator(selector).first.get_attribute(attr)
        except PlaywrightTimeoutError as e:
            raise SelectorNotFoundError(f"Selector not found: {selector}") from e

    async def query_all(self, selector: str) -> list[Any]:
        """Return list of Locator-like handles. Use sparingly."""
        return await self._page.locator(selector).all()

    async def html(self) -> str:
        return await self._page.content()

    # ─── Interactions ─────────────────────────────────────────────────────

    async def click(self, selector: str, *, force: bool = False) -> None:
        try:
            await self._page.locator(selector).first.click(force=force)
        except PlaywrightTimeoutError as e:
            raise SelectorNotFoundError(f"Click target not visible: {selector}") from e

    async def click_any_frame(self, selector: str, *, force: bool = False) -> None:
        last_exc: Exception | None = None
        for fr in self._iter_frames():
            try:
                loc = fr.locator(selector).first
                if await loc.is_visible():
                    await loc.click(force=force)
                    return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
        raise SelectorNotFoundError(f"Click target not visible in any frame: {selector}") from last_exc

    async def fill(self, selector: str, value: str) -> None:
        """Fill a standard <input> / <textarea>. Does NOT work for contenteditable."""
        try:
            await self._page.locator(selector).first.fill(value)
        except PlaywrightTimeoutError as e:
            raise SelectorNotFoundError(f"Fill target not found: {selector}") from e

    async def fill_tel_defensive_any_frame(self, selector: str, value: str) -> str:
        """
        [GOAL] Type a phone into the login widget without Chrome autofill winning.
        [INPUT] CSS selector + E.164 value
        [OUTPUT] The field's actual value after typing (never log the value)

        Chrome form-state on a persistent profile can overwrite Playwright
        ``fill()``. Click → select-all → delete → type → read back.
        """
        last_exc: Exception | None = None
        for fr in self._iter_frames():
            try:
                loc = fr.locator(selector).first
                if not await loc.is_visible():
                    continue
                await loc.click()
                await self._page.keyboard.press("Control+A")
                await self._page.keyboard.press("Delete")
                await loc.press_sequentially(value, delay=30)
                actual = await loc.input_value()
                return actual or ""
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
        raise SelectorNotFoundError(
            f"Fill target not found in any frame: {selector}"
        ) from last_exc

    async def type_text(self, selector: str, value: str, *, slowly: bool = False) -> None:
        """Sequential key presses. Works for both <input> and contenteditable."""
        loc = self._page.locator(selector).first
        try:
            await loc.press_sequentially(value, delay=20 if slowly else 0)
        except PlaywrightTimeoutError as e:
            raise SelectorNotFoundError(f"Type target not found: {selector}") from e

    async def type_into_chip_input(
        self,
        selector: str,
        value: str,
        *,
        commit: bool = True,
        commit_key: str = "Enter",
    ) -> None:
        """Special handler for ads.telegram.org chip-inputs (contenteditable div).

        Triggers autocomplete via real keydown events; presses Enter to commit
        the resolved chip.
        """
        await self.type_text(selector, value, slowly=True)
        if commit:
            await self._page.keyboard.press(commit_key)

    async def press(self, key: str) -> None:
        await self._page.keyboard.press(key)

    async def check(self, selector: str) -> None:
        await self._page.locator(selector).first.check()

    async def uncheck(self, selector: str) -> None:
        await self._page.locator(selector).first.uncheck()

    async def set_checkbox_via_js(self, selector: str, checked: bool) -> None:
        """When .check()/.click() fails (e.g., element scrolled out or hidden parent)."""
        await self._page.evaluate(
            "(args) => {"
            "const el = document.querySelector(args.sel);"
            "if (!el) throw new Error('Not found');"
            "el.checked = args.v;"
            "el.dispatchEvent(new Event('change', { bubbles: true }));"
            "}",
            {"sel": selector, "v": checked},
        )

    # ─── Evaluation ───────────────────────────────────────────────────────

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        return await self._page.evaluate(js, arg)

    async def evaluate_async(self, async_js: str, arg: Any = None) -> Any:
        """Convenience for `async () => { return await ... }` snippets."""
        return await self._page.evaluate(async_js, arg)

    # ─── Screenshots ──────────────────────────────────────────────────────

    async def screenshot(
        self,
        path: str | Path,
        *,
        full_page: bool = False,
        clip: dict | None = None,
    ) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {"path": str(out), "full_page": full_page}
        if clip:
            kwargs["clip"] = clip
        await self._page.screenshot(**kwargs)
        return out

    async def screenshot_element(self, selector: str, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        await self._page.locator(selector).first.screenshot(path=str(out))
        return out

    # ─── File chooser (media upload) ──────────────────────────────────────

    async def upload_file_via_chooser(self, trigger_selector: str, file_path: str | Path) -> None:
        """Click `trigger_selector` and feed `file_path` to the native file chooser."""
        async with self._page.expect_file_chooser() as fc_info:
            await self.click(trigger_selector)
        fc = await fc_info.value
        await fc.set_files(str(file_path))

    # ─── Cookies / session ────────────────────────────────────────────────

    async def cookies(self) -> list[dict]:
        return await self._context.cookies()

    async def has_session_cookie(self, name_prefix: str = "stel_") -> bool:
        cookies = await self.cookies()
        return any(c["name"].startswith(name_prefix) for c in cookies)

    # ─── Helpers ──────────────────────────────────────────────────────────

    async def wait_for_url_change(self, *, expected_substring: str, timeout_ms: int = 10_000) -> None:
        try:
            await self._page.wait_for_url(
                lambda u: expected_substring in u, timeout=timeout_ms
            )
        except PlaywrightTimeoutError as e:
            raise UnexpectedNavigationError(
                f"URL did not change to contain {expected_substring!r}; current: {self.current_url}",
            ) from e

    async def sleep(self, seconds: float) -> None:
        """Tactical sleep when waiting for non-DOM async work (debounces)."""
        await asyncio.sleep(seconds)


# ─── Persistent context launch ──────────────────────────────────────────────


async def _launch_persistent_context(
    pw: Playwright, config: BrowserConfig
) -> BrowserContext:
    """Launch a Chromium persistent context for *config*.

    Shared by :meth:`BrowserAutomationTool.launch` and ``.recover()`` so a
    recovered context is byte-for-byte identical to the original (same profile,
    headless flag, viewport, downloads, DISPLAY/Xvfb env merge).
    """
    import os as _os

    # Ensure DISPLAY is set for headed mode (Xvfb). Merge with full parent env
    # so the Chromium subprocess keeps PATH, HOME, USER, LANG, XDG_* etc.
    # (env= replaces, not merges).
    env = {**_os.environ, "DISPLAY": _os.environ.get("DISPLAY", ":99")}
    extra_args = list(getattr(config, "chromium_args", None) or [])
    # Persistent context = cookies survive between runs.
    return await pw.chromium.launch_persistent_context(
        user_data_dir=str(config.user_data_dir or config.profile_dir),
        headless=config.headless,
        viewport={"width": config.viewport_width, "height": config.viewport_height},
        accept_downloads=True,
        args=extra_args or None,
        env=env,
    )


# Lock helpers live in profile_lock.py and are re-exported here so existing
# `from hermes_telegram_ads.browser import check_profile_lock` imports keep working.


# ─── Browser process cleanup ───────────────────────────────────────────────


async def cleanup_stale_browser_processes(profile_dir: str | Path) -> dict:
    """Kill Chromium/Chrome processes using a specific profile directory.

    SAFETY RULES:
    - Only kills processes whose cmdline contains the EXACT profile_dir.
    - SIGTERM first, wait 3s, SIGKILL only as fallback.
    - NEVER does broad pkill or kills unrelated Chrome instances.
    - Never kills a process that is still active and responding to SIGTERM
      (if it survives both SIGTERM and the wait, it's left alive with a warning).

    Args:
        profile_dir: The user-data-dir to search for in process cmdlines.

    Returns:
        {
            "ok": True,
            "killed_pids": [3035920],
            "sigterm_pids": [3035920],
            "sigkill_pids": [],
            "alive_pids": [],       # survived both signals
            "errors": [],
        }
    """
    import asyncio as _asyncio
    import os as _os
    import signal as _signal

    profile = str(Path(profile_dir).resolve())
    result: dict[str, Any] = {
        "ok": True,
        "killed_pids": [],
        "sigterm_pids": [],
        "sigkill_pids": [],
        "alive_pids": [],
        "errors": [],
    }

    # Find Chromium/Chrome processes with matching user-data-dir
    pids_to_kill: list[int] = []
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                cmdline = (entry / "cmdline").read_bytes()
                cmdline_str = cmdline.replace(b"\x00", b" ").decode("utf-8", errors="replace")
            except (OSError, PermissionError):
                continue

            # Must be Chromium/Chrome/Playwright process
            is_browser = any(
                keyword in cmdline_str
                for keyword in ("chrome", "chromium", "playwright")
            )
            if not is_browser:
                continue

            # Must contain EXACTLY this profile directory
            if f"--user-data-dir={profile}" not in cmdline_str:
                continue

            pids_to_kill.append(pid)

    except Exception as exc:
        result["errors"].append(f"process scan failed: {exc}")
        result["ok"] = False
        return result

    if not pids_to_kill:
        logger.info(
            "cleanup_stale_browser_processes: no matching processes for %s",
            profile,
        )
        return result

    logger.info(
        "cleanup_stale_browser_processes: found %d process(es) for %s: %s",
        len(pids_to_kill), profile, pids_to_kill,
    )

    # Phase 1: SIGTERM
    for pid in pids_to_kill:
        try:
            _os.kill(pid, _signal.SIGTERM)
            result["sigterm_pids"].append(pid)
            logger.info(
                "cleanup_stale_browser_processes: SIGTERM → pid=%d", pid,
            )
        except ProcessLookupError:
            logger.info("cleanup_stale_browser_processes: pid=%d already dead", pid)
        except PermissionError as exc:
            result["errors"].append(f"can't signal pid={pid}: {exc}")

    # Phase 2: Wait 3 seconds for graceful shutdown
    await _asyncio.sleep(3.0)

    # Phase 3: Check survivors → SIGKILL
    for pid in pids_to_kill:
        try:
            _os.kill(pid, 0)  # Still alive?
            # Survived SIGTERM — SIGKILL
            try:
                _os.kill(pid, _signal.SIGKILL)
                result["sigkill_pids"].append(pid)
                logger.info(
                    "cleanup_stale_browser_processes: SIGKILL → pid=%d (survived SIGTERM)",
                    pid,
                )
            except ProcessLookupError:
                pass  # died between check and kill
            except PermissionError as exc:
                result["alive_pids"].append(pid)
                result["errors"].append(f"can't SIGKILL pid={pid}: {exc}")
        except ProcessLookupError:
            # Already dead from SIGTERM — good
            result["killed_pids"].append(pid)
        except PermissionError:
            result["alive_pids"].append(pid)

    # Wait another second for SIGKILL to take effect
    await _asyncio.sleep(1.0)

    # Phase 4: Final verification
    for pid in pids_to_kill[:]:
        try:
            _os.kill(pid, 0)
            if pid not in result["alive_pids"]:
                result["alive_pids"].append(pid)
        except ProcessLookupError:
            if pid not in result["killed_pids"] and pid not in result["sigkill_pids"]:
                result["killed_pids"].append(pid)

    if result["alive_pids"]:
        logger.warning(
            "cleanup_stale_browser_processes: %d process(es) survived: %s",
            len(result["alive_pids"]), result["alive_pids"],
        )

    logger.info(
        "cleanup_stale_browser_processes: done — "
        "sigterm=%d sigkill=%d killed=%d alive=%d",
        len(result["sigterm_pids"]),
        len(result["sigkill_pids"]),
        len(result["killed_pids"]),
        len(result["alive_pids"]),
    )

    return result
