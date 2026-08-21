"""Writable tmp dir for watcher/lock tests (system pytest tmp can be denied)."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

_DEFAULT = Path(__file__).resolve().parent / ".tmp"


@pytest.fixture
def tmp_path() -> Path:
    root = Path(os.environ.get("HERMES_TEST_TMP", _DEFAULT))
    root.mkdir(parents=True, exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
