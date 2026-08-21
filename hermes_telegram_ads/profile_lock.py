"""Chromium persistent-profile lock helpers.

[GOAL] Detect a live owner vs a stale SingletonLock and recover the latter.
[INPUT] Absolute profile directory (the same one ads tools and the watcher use).
[OUTPUT] Structured lock status / cleanup result. Never kills a live PID.

Linux Chromium writes SingletonLock as a dangling symlink ``<hostname>-<pid>``.
Treating that as unreadable used to skip cleanup and then launch a second
Chromium, which wedges on "Opening in existing browser session".
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK_FILES = frozenset({"SingletonLock", "SingletonSocket", "SingletonCookie"})
_LOCK_TARGET_PID = re.compile(r"(?:^|-)(\d+)$")


def parse_chromium_lock_owner(lock_path: Path, filename: str | None = None) -> int | None:
    """Extract the Chromium owner PID from a lock path."""
    name = filename or lock_path.name
    try:
        if lock_path.is_symlink():
            target = os.readlink(lock_path)
            leaf = Path(target).name
            match = _LOCK_TARGET_PID.search(leaf)
            if match:
                return int(match.group(1))
    except OSError:
        pass

    try:
        content = lock_path.read_bytes()
        first_line = content.split(b"\n")[0].strip()
        if first_line.isdigit():
            return int(first_line)
        decoded = first_line.decode("ascii", errors="ignore").strip()
        match = _LOCK_TARGET_PID.search(decoded)
        if match:
            return int(match.group(1))
    except (OSError, ValueError):
        pass

    if name == "SingletonSocket":
        try:
            content = lock_path.read_bytes()
            pid_str = content[:16].split(b"\x00")[0].decode("ascii", errors="ignore").strip()
            if pid_str.isdigit():
                return int(pid_str)
        except (OSError, ValueError):
            pass

    return None


def _read_lock_pid(lock_path: Path, filename: str) -> int | None:
    return parse_chromium_lock_owner(lock_path, filename)


def _pid_alive(pid: int) -> bool | None:
    """True if pid is alive, False if dead, None if we cannot tell."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:  # noqa: BLE001
            return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError as exc:
        if getattr(exc, "errno", None) in {3, 22}:
            return False
        return None


async def remove_stale_locks(profile_dir: str | Path) -> dict:
    """Remove stale Chromium lock files whose owning PID is dead."""
    profile = Path(profile_dir)
    result: dict[str, Any] = {
        "ok": True,
        "removed": [],
        "skipped": [],
        "audit_event": "stale_lock_removed",
        "dead_pids": [],
    }

    if not profile.is_dir():
        result["audit_event"] = None
        return result

    for filename in sorted(_LOCK_FILES):
        lock_path = profile / filename
        if not lock_path.exists() and not lock_path.is_symlink():
            continue

        owner_pid = _read_lock_pid(lock_path, filename)
        if owner_pid is None:
            result["skipped"].append(filename)
            result["unparseable"] = True
            logger.info(
                "remove_stale_locks: SKIP %s (pid unparseable, left untouched)",
                lock_path,
            )
            continue

        alive = _pid_alive(owner_pid)
        if alive is True:
            logger.info("remove_stale_locks: KEEP %s (pid=%d alive)", lock_path, owner_pid)
            result["skipped"].append(filename)
            continue
        if alive is None:
            result["skipped"].append(filename)
            logger.info(
                "remove_stale_locks: SKIP %s (pid=%d, can't verify status)",
                lock_path,
                owner_pid,
            )
            continue

        try:
            lock_path.unlink()
            result["removed"].append(filename)
            result["dead_pids"].append(owner_pid)
            logger.info(
                "remove_stale_locks: REMOVED %s (pid=%d dead, stale lock cleaned)",
                lock_path,
                owner_pid,
            )
        except OSError as exc:
            result["skipped"].append(filename)
            logger.warning("remove_stale_locks: FAILED to remove %s: %s", lock_path, exc)

    if not result["removed"] and not result["dead_pids"]:
        result["audit_event"] = None

    return result


async def check_profile_lock(profile_dir: str | Path) -> dict:
    """Check if a Chromium profile dir is locked by a live process."""
    profile = Path(profile_dir)
    if not profile.is_dir():
        return {"locked": False}

    saw_unparseable = False
    saw_stale = False
    for filename in _LOCK_FILES:
        lock_path = profile / filename
        if not lock_path.exists() and not lock_path.is_symlink():
            continue

        owner_pid = _read_lock_pid(lock_path, filename)
        if owner_pid is None:
            saw_unparseable = True
            continue
        alive = _pid_alive(owner_pid)
        if alive is True:
            return {"locked": True, "owner_pid": owner_pid}
        if alive is None:
            return {"locked": True, "owner_pid": owner_pid}
        saw_stale = True

    if saw_unparseable:
        return {"locked": True, "owner_pid": None, "unparseable": True}
    if saw_stale:
        return {"locked": False, "stale": True}
    return {"locked": False}


async def recover_profile_lock(profile_dir: str | Path) -> dict:
    """If the lock is stale, remove it. Live locks are left in place.

    [OUTPUT] Combined check + cleanup result used by launch preflight and tests.
    """
    status = await check_profile_lock(profile_dir)
    if status.get("stale"):
        cleanup = await remove_stale_locks(profile_dir)
        after = await check_profile_lock(profile_dir)
        return {"check": status, "cleanup": cleanup, "after": after, "recovered": not after.get("locked")}
    return {"check": status, "cleanup": None, "after": status, "recovered": False}


__all__ = [
    "check_profile_lock",
    "parse_chromium_lock_owner",
    "recover_profile_lock",
    "remove_stale_locks",
    "_read_lock_pid",
]
