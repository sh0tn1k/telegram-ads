"""Unit tests for the shipped Chromium profile-lock helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_telegram_ads.profile_lock import (
    check_profile_lock,
    parse_chromium_lock_owner,
    recover_profile_lock,
    remove_stale_locks,
)


@pytest.mark.asyncio
async def test_stale_file_lock_is_recovered(tmp_path: Path) -> None:
    lock = tmp_path / "SingletonLock"
    lock.write_text("999999991\n", encoding="utf-8")

    status = await check_profile_lock(tmp_path)
    assert status.get("stale") is True
    assert status.get("locked") is False

    recovered = await recover_profile_lock(tmp_path)
    assert recovered["recovered"] is True
    assert not lock.exists()
    after = recovered["after"]
    assert after.get("locked") is False
    assert after.get("stale") is not True


@pytest.mark.asyncio
async def test_live_lock_is_not_removed(tmp_path: Path) -> None:
    lock = tmp_path / "SingletonLock"
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")

    status = await check_profile_lock(tmp_path)
    assert status.get("locked") is True
    assert status.get("owner_pid") == os.getpid()

    cleanup = await remove_stale_locks(tmp_path)
    assert "SingletonLock" not in cleanup["removed"]
    assert lock.exists()


@pytest.mark.asyncio
async def test_unparseable_lock_is_not_treated_as_free(tmp_path: Path) -> None:
    lock = tmp_path / "SingletonLock"
    lock.write_bytes(b"\x00\x01not-a-pid")

    status = await check_profile_lock(tmp_path)
    assert status.get("locked") is True
    assert status.get("unparseable") is True


def test_parse_symlink_target_name(tmp_path: Path) -> None:
    lock = tmp_path / "SingletonLock"
    try:
        lock.symlink_to("host-4242")
    except OSError:
        pytest.skip("symlink creation is not permitted on this OS")
    assert parse_chromium_lock_owner(lock, "SingletonLock") == 4242


@pytest.mark.asyncio
async def test_stale_symlink_lock_is_recovered(tmp_path: Path) -> None:
    lock = tmp_path / "SingletonLock"
    try:
        lock.symlink_to("host-999999992")
    except OSError:
        pytest.skip("symlink creation is not permitted on this OS")

    status = await check_profile_lock(tmp_path)
    assert status.get("stale") is True or status.get("locked") is False
    recovered = await recover_profile_lock(tmp_path)
    assert recovered["recovered"] is True
    assert not lock.exists() and not lock.is_symlink()
