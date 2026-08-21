"""Structural + dispatch tests for the shipped plugin registration path."""

from __future__ import annotations

from hermes_telegram_ads.hermes_tools import tool_names
from hermes_telegram_ads.plugin_runtime import (
    ADS_TOOLSET_NAME,
    RESEARCH_TOOLSET_NAME,
    WATCHER_TOOLSET_NAME,
    dispatch_registered,
    make_sync_handler,
    typed_ads_names,
    typed_research_names,
    typed_watcher_names,
)
from hermes_telegram_ads.watcher.hermes_tools import WATCHER_TOOL_NAMES


class _FakeCtx:
    def __init__(self) -> None:
        self.tools: list[tuple[str, str]] = []
        self.hooks: list[tuple[str, object]] = []
        self.prompt_sections: list[str] = []

    def register_tool(self, name, toolset, schema, handler, description="", emoji=""):
        self.tools.append((name, toolset))
        return None

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_system_prompt_section(self, section_id, text, **kwargs):
        del kwargs
        self.prompt_sections.append(section_id)

    def register_skill(self, name, path):
        self.tools.append((f"skill:{name}", path))


def test_typed_name_lists_include_verbs() -> None:
    ads = typed_ads_names()
    assert "telegram_ads_create_ad" in ads
    assert "telegram_ads_edit_ad" in ads
    assert "telegram_ads_change_cpm" in ads
    assert "telegram_ads_list_ads" in ads
    assert "telegram_ads_status" in ads
    assert "telegram_ads_login_from_env" in ads
    assert ads == tool_names()

    research = typed_research_names()
    if research:
        assert "telegram_research_search" in research
        assert "telegram_research_inspect_channel" in research
        assert "telegram_score_asset_for_project" in research
        assert "telegram_research_batch" in research
        assert "telegram_research_login_start" in research
        assert "telegram_research_login_submit_code" in research

    watcher = typed_watcher_names()
    assert watcher == list(WATCHER_TOOL_NAMES)
    assert "telegram_ads_watch_create" in watcher


def test_register_tools_uses_three_toolsets() -> None:
    from hermes_telegram_ads.plugin_runtime import register_tools

    ctx = _FakeCtx()
    counts = register_tools(ctx)
    assert counts["ads"] == len(typed_ads_names())
    assert counts["research"] == len(typed_research_names())
    assert counts["watcher"] == len(typed_watcher_names())
    ads_set = {n for n, ts in ctx.tools if ts == ADS_TOOLSET_NAME}
    research_set = {n for n, ts in ctx.tools if ts == RESEARCH_TOOLSET_NAME}
    watcher_set = {n for n, ts in ctx.tools if ts == WATCHER_TOOLSET_NAME}
    assert "telegram_ads_create_ad" in ads_set
    if typed_research_names():
        assert "telegram_research_search" in research_set
        assert "telegram-ops.channel-portfolio" in ctx.prompt_sections
    else:
        assert not research_set
        assert "telegram-ops.channel-portfolio" not in ctx.prompt_sections
    assert "telegram_ads_watch_create" in watcher_set
    assert any(name == "pre_tool_call" for name, _ in ctx.hooks)
    assert "telegram-ops.ads-operator-confirm" in ctx.prompt_sections


def test_register_ads_plugin_skips_research_and_portfolio() -> None:
    from hermes_telegram_ads.plugin_runtime import register_ads_plugin

    ctx = _FakeCtx()
    counts = register_ads_plugin(ctx)
    assert counts["ads"] == len(typed_ads_names())
    assert counts["research"] == 0
    assert counts["watcher"] == len(typed_watcher_names())
    assert counts["skills"] >= 1
    names = {n for n, _ts in ctx.tools}
    assert "telegram_ads_create_ad" in names
    assert "telegram_ads_watch_create" in names
    assert "telegram_research_search" not in names
    assert "telegram-ops.channel-portfolio" not in ctx.prompt_sections
    assert any(n.startswith("skill:") for n in names)


HERMES_RUNTIME_BAG = {
    "session_id": "20260817_111335_5a81a3fc",
    "task_id": "default",
    "user_task": "проверь кабинет",
    "tool_call_id": "tc_1",
    "turn_id": "turn_1",
    "api_request_id": "req_1",
    "enabled_tools": ["terminal"],
    "effective_task_id": "default",
    "conversation_id": "conv_1",
}


def test_strip_hermes_runtime_args_drops_injected_ids() -> None:
    from hermes_telegram_ads.plugin_runtime import strip_hermes_runtime_args

    bag = {
        "session_id": "s1",
        "task_id": "default",
        "user_task": "проверь бота",
        "tool_call_id": "tc",
        "turn_id": "t",
        "api_request_id": "r",
    }
    cleaned = strip_hermes_runtime_args("telegram_research_login_status", dict(bag))
    assert cleaned == {}

    inspect = strip_hermes_runtime_args(
        "telegram_research_inspect_profile",
        {"target": "@bot", **bag},
    )
    assert inspect == {"target": "@bot"}

    summary = strip_hermes_runtime_args(
        "telegram_research_get_task_summary",
        {**bag, "task_id": "tg_research_t1"},
    )
    assert summary == {"task_id": "tg_research_t1"}

    placeholder = strip_hermes_runtime_args(
        "telegram_research_get_task_summary",
        {"task_id": "default", "session_id": "s1"},
    )
    assert placeholder == {}


def test_watcher_call_ignores_hermes_runtime_kwargs() -> None:
    from hermes_telegram_ads.watcher.hermes_tools import (
        TelegramAdsWatcherToolset,
        kwargs_for_handler,
    )

    bound = kwargs_for_handler(
        TelegramAdsWatcherToolset.coverage,
        {
            "session_id": "s1",
            "task_id": "default",
            "user_task": "watch this",
            "tool_call_id": "tc",
        },
    )
    assert bound == {}

    bound_create = kwargs_for_handler(
        TelegramAdsWatcherToolset.create_watch,
        {
            "kind": "ad_status",
            "session_id": "s1",
            "task_id": "default",
            "user_task": "x",
            "ad_id": 12,
        },
    )
    assert bound_create == {"kind": "ad_status", "ad_id": 12}


def test_watcher_create_adopts_hermes_session_id_as_session_key() -> None:
    from hermes_telegram_ads.runtime_kwargs import (
        merge_plugin_handler_args,
        prepare_plugin_payload,
    )

    adopted = prepare_plugin_payload(
        "telegram_ads_watch_create",
        {"kind": "ad_status", "ad_id": 12, **HERMES_RUNTIME_BAG},
    )
    assert adopted["kind"] == "ad_status"
    assert adopted["ad_id"] == 12
    assert adopted["session_key"] == HERMES_RUNTIME_BAG["session_id"]
    assert "session_id" not in adopted
    assert "task_id" not in adopted
    assert "user_task" not in adopted

    explicit = prepare_plugin_payload(
        "telegram_ads_watch_create",
        {
            "kind": "ad_status",
            "session_key": "explicit-thread",
            **HERMES_RUNTIME_BAG,
        },
    )
    assert explicit["session_key"] == "explicit-thread"

    placeholder = prepare_plugin_payload(
        "telegram_ads_watch_create",
        {"kind": "ad_status", "session_id": "default", "task_id": "default"},
    )
    assert "session_key" not in placeholder

    # Hermes injects session_id as kwargs, not as a model arg.
    merged = merge_plugin_handler_args(
        "telegram_ads_watch_create",
        {"kind": "ad_status", "ad_id": 7},
        dict(HERMES_RUNTIME_BAG),
    )
    assert merged == {
        "kind": "ad_status",
        "ad_id": 7,
        "session_key": HERMES_RUNTIME_BAG["session_id"],
    }


def test_every_watcher_handler_binds_hermes_runtime_bag() -> None:
    import inspect

    from hermes_telegram_ads.runtime_kwargs import bind_handler_kwargs
    from hermes_telegram_ads.watcher.hermes_tools import (
        TelegramAdsWatcherToolset,
        watcher_tool_schemas,
    )

    toolset = TelegramAdsWatcherToolset(service=object())
    handlers = {
        "telegram_ads_watch_create": toolset.create_watch,
        "telegram_ads_watch_list": toolset.list_watches,
        "telegram_ads_watch_disable": toolset.disable_watch,
        "telegram_ads_watch_delete": toolset.delete_watch,
        "telegram_ads_watch_coverage": toolset.coverage,
        "telegram_ads_watch_events": toolset.list_events,
        "telegram_ads_watch_create_post_action": toolset.create_post_action,
    }
    assert set(handlers) == set(WATCHER_TOOL_NAMES)
    schema_by_name = {row["name"]: row["parameters"] for row in watcher_tool_schemas()}
    assert set(schema_by_name) == set(WATCHER_TOOL_NAMES)

    for name, handler in handlers.items():
        schema = schema_by_name[name]
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        minimal = {key: _dummy_schema_value(props.get(key, {})) for key in required}
        bound = bind_handler_kwargs(handler, {**HERMES_RUNTIME_BAG, **minimal})
        for key in HERMES_RUNTIME_BAG:
            assert key not in bound, f"{name} leaked {key}"
        inspect.signature(handler).bind(**bound)


def test_every_ads_handler_binds_hermes_runtime_bag() -> None:
    import inspect

    from hermes_telegram_ads.hermes_tools import TELEGRAM_ADS_TOOLS, TelegramAdsToolset
    from hermes_telegram_ads.runtime_kwargs import (
        HERMES_RUNTIME_INJECTED_KEYS,
        bind_handler_kwargs,
    )

    toolset = TelegramAdsToolset()
    for spec in TELEGRAM_ADS_TOOLS:
        handler = getattr(toolset, spec.handler)
        schema = spec.input_schema
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        minimal = {key: _dummy_schema_value(props.get(key, {})) for key in required}
        bound = bind_handler_kwargs(handler, {**HERMES_RUNTIME_BAG, **minimal})
        for key in HERMES_RUNTIME_INJECTED_KEYS:
            assert key not in bound, f"{spec.name} leaked {key}"
        inspect.signature(handler).bind(**bound)


def test_ads_call_offline_tools_ignore_runtime_bag() -> None:
    import asyncio

    from hermes_telegram_ads.hermes_tools import TelegramAdsToolset

    toolset = TelegramAdsToolset()

    async def _run() -> None:
        variants = await toolset.call(
            "telegram_ads_prepare_copy_variants",
            variants=["hello channel"],
            **HERMES_RUNTIME_BAG,
        )
        assert variants["ok"] is True
        assert variants["error"] is None

        targeting = await toolset.call(
            "telegram_ads_prepare_targeting",
            target_type="channels",
            targets=["@example_news"],
            **HERMES_RUNTIME_BAG,
        )
        assert targeting["ok"] is True
        assert targeting["error"] is None

        stub = await toolset.call("telegram_ads_set_pixel", **HERMES_RUNTIME_BAG)
        assert stub["ok"] is False
        assert stub["status"] == "forbidden"

    asyncio.run(_run())


def test_ads_call_does_not_typeerror_strict_handler() -> None:
    """A forgotten **_ must not crash the way watcher used to."""
    import asyncio
    import inspect

    from hermes_telegram_ads.hermes_tools import TelegramAdsToolset
    from hermes_telegram_ads.runtime_kwargs import bind_handler_kwargs

    async def _strict(self, ad_id: int) -> dict:
        return {"ok": True, "ad_id": ad_id}

    bound = bind_handler_kwargs(_strict, {"ad_id": 9, **HERMES_RUNTIME_BAG})
    inspect.signature(_strict).bind(object(), **bound)
    assert bound == {"ad_id": 9}

    toolset = TelegramAdsToolset()

    async def _run() -> None:
        async def _handler(ad_id: int) -> dict:
            return {"ok": True, "ad_id": ad_id}

        result = await toolset._invoke_with_recovery(
            type("S", (), {"name": "telegram_ads_get_ad", "mutating": False})(),
            _handler,
            bind_handler_kwargs(_handler, {"ad_id": 3, **HERMES_RUNTIME_BAG}),
        )
        assert result == {"ok": True, "ad_id": 3}

    asyncio.run(_run())


def test_ads_call_keeps_operator_approved_flag_on_strict_handler() -> None:
    import asyncio

    from hermes_telegram_ads.hermes_tools import TelegramAdsToolset
    from hermes_telegram_ads.operator_approval import OPERATOR_APPROVED_ARG

    toolset = TelegramAdsToolset()
    seen: list[bool] = []

    async def _handler(ad_id: int) -> dict:
        seen.append(toolset._operator_approved)
        return {"ok": True, "ad_id": ad_id}

    toolset._h_get_ad = _handler  # type: ignore[method-assign]

    async def _run() -> None:
        result = await toolset.call(
            "telegram_ads_get_ad",
            ad_id=4,
            **{OPERATOR_APPROVED_ARG: True, **HERMES_RUNTIME_BAG},
        )
        assert result == {"ok": True, "ad_id": 4}
        assert seen == [True]

    asyncio.run(_run())


def _dummy_schema_value(prop: dict) -> object:
    types = prop.get("type")
    if isinstance(types, list):
        types = next((item for item in types if item != "null"), types[0])
    if "enum" in prop:
        return prop["enum"][0]
    if types == "integer":
        return 12
    if types == "number":
        return 1.0
    if types == "boolean":
        return True
    if types == "object":
        return {"title": "x", "text": "y", "promote_url": "https://t.me/x", "cpm": 1, "target_type": "channels"}
    if types == "array":
        return ["x"]
    return "x"


def test_sync_handler_returns_json_envelope_for_unknown() -> None:
    handler = make_sync_handler("not_a_real_tool")
    raw = handler({})
    assert "UNKNOWN_TOOL" in raw or "unregistered" in raw


def test_dispatch_registered_unknown() -> None:
    result = dispatch_registered("nope_tool", {})
    assert result["ok"] is False
    assert result["error"] == "UNKNOWN_TOOL"
