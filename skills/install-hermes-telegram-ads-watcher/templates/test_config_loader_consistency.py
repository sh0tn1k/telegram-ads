"""Test template: pin a 3-way config loader ladder with `not hasattr` regression guard.

Use this template when you change a config loader in hermes-agent/ (or any
project that uses a pydantic v2 BaseModel config) to fix a silent-fallback bug.
The pattern: (1) assert the broken API does NOT exist, (2) assert the new API
resolves to the right path, (3) pin the fallback path, (4) cross-entrypoint
consistency check.

This is the exact pattern used in `tests/test_telegram_ads_config_loader.py`
(2026-06-17) for the AR-ADS-WATCHER-ARCH-1 + AR-ADS-WATCHER-ARCH-2 fix.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest


# Replace these three with your project's actual values.
SHARED_CONFIG_PATH = "/home/hermes/.hermes/telegram_ads.yaml"
YAML_RESOLVED_PREFIX = "/home/hermes/.hermes/data/telegram_ads/"  # what from_yaml() should give
ENTRYPOINTS = [
    ("tools.telegram_ads_typed_tool", "_make_toolset", "_config"),
    # Smoke / watcher use exec() to avoid import-time side effects:
    # ("real_adapter_smoke", "_load_config", None),
    # ("start_ads_watcher_readonly_operational", "_load_config", None),
]
# Path of the relative-default that the bug used to fall back to. Used to
# assert the bug is gone.
DEFAULT_RELATIVE_MARKER = "browser_profiles/telegram_ads"


def _yaml_profile_path() -> str:
    """Resolve the absolute profile_dir the shared yaml currently points at."""
    from project_config_module import ProjectConfig  # adapt to your project

    cfg = ProjectConfig.from_yaml(SHARED_CONFIG_PATH)
    return str(cfg.browser.profile_dir)


# ─── 1. Bug regression guard ──────────────────────────────────────────────


def test_broken_api_does_not_exist():
    """Pin: the broken API must not be reintroduced (e.g. `from_dict` is not a
    valid pydantic method in this package)."""
    from project_config_module import ProjectConfig

    # Adapt the API name to your project's broken method.
    assert not hasattr(ProjectConfig, "from_dict"), (
        "ProjectConfig.from_dict must NOT exist — it never did; previous "
        "loader called it and silently fell back to defaults."
    )


# ─── 2. Loader resolves to yaml path, NOT the buggy default ───────────────


def test_loader_uses_yaml_not_default():
    """The loader must produce a config whose profile_dir equals the yaml
    path, not the buggy default relative path."""
    yaml_path = _yaml_profile_path()
    assert yaml_path.startswith(YAML_RESOLVED_PREFIX), (
        f"expected yaml to resolve under {YAML_RESOLVED_PREFIX!r}, got {yaml_path!r}"
    )

    for module_name, factory, config_attr in ENTRYPOINTS:
        mod = importlib.import_module(module_name)
        # Reset the singleton if there is one (most loaders cache).
        if hasattr(mod, "_toolset_singleton"):
            saved = mod._toolset_singleton
            mod._toolset_singleton = None
        else:
            saved = None
        try:
            obj = getattr(mod, factory)()
            cfg = obj._config if config_attr is None else getattr(obj, config_attr)
            got = str(cfg.browser.profile_dir)
            assert got == yaml_path, (
                f"{module_name}.{factory}() resolved to {got!r}, "
                f"expected yaml-resolved {yaml_path!r}"
            )
            assert DEFAULT_RELATIVE_MARKER not in got, (
                f"{module_name}.{factory}() still using default relative path {got!r}"
            )
        finally:
            if saved is not None:
                mod._toolset_singleton = saved


# ─── 3. Fallback path still works when yaml load fails ───────────────────


def test_fallback_to_default_when_yaml_missing(monkeypatch: pytest.MonkeyPatch):
    """When from_yaml raises, the loader must NOT crash and must produce a
    valid config (default())."""
    from project_config_module import ProjectConfig

    from project_loader_module import _load_config  # adapt

    orig = ProjectConfig.from_yaml

    def _boom(path):
        raise FileNotFoundError("synthetic: yaml missing")

    monkeypatch.setattr(ProjectConfig, "from_yaml", _boom)
    try:
        cfg = _load_config()
        # Fallback produced a valid config — exact profile_dir is irrelevant
        # for this assertion; we only require "did not crash".
        assert isinstance(cfg, ProjectConfig)
    finally:
        monkeypatch.setattr(ProjectConfig, "from_yaml", orig)


# ─── 4. Cross-entrypoint consistency ─────────────────────────────────────


def test_all_loaders_resolve_to_same_profile_path():
    """All entrypoints must resolve to the same profile_dir (yaml-resolved)."""
    yaml_path = _yaml_profile_path()

    paths = []
    for module_name, factory, _ in ENTRYPOINTS:
        mod = importlib.import_module(module_name)
        if hasattr(mod, "_toolset_singleton"):
            saved = mod._toolset_singleton
            mod._toolset_singleton = None
        else:
            saved = None
        try:
            obj = getattr(mod, factory)()
            cfg = obj._config  # adapt
            paths.append(str(cfg.browser.profile_dir))
        finally:
            if saved is not None:
                mod._toolset_singleton = saved

    assert all(p == yaml_path for p in paths), (
        f"profile_dir divergence: {paths!r} vs yaml {yaml_path!r}"
    )
