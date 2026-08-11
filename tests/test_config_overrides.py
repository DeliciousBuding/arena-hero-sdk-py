"""config_overrides 模块测试(SDK fork 配置注入通道,2026-08-09)。

核心保证(验收 #1):
- 无 ARENA_CFG_* / 配置文件 → 零操作(no-op,与官方 SDK 逐字节一致)
- 环境变量 + 配置文件注入;env 优先;类型按既有值强转
- 点分键沿属性链深钻;未知键静默跳过不抛错
- 损坏配置静默降级(记 stderr、回退默认)
"""

import json
from pathlib import Path

import pytest

from arena_hero.config_overrides import (
    apply_config_overrides,
    load_config_overrides,
    overridden_decide_kwargs,
)


class _DummyStrategy:
    def __init__(self) -> None:
        self.aggro = 0.5
        self.limit = 10


class _DummyAgent:
    """模拟第三方 agent 实例(默认值 + 嵌套 strategy)。"""

    def __init__(self) -> None:
        self.worker_target = 12
        self.beacon_policy = "retreat"
        self.mode = "harvest"
        self.nested = {"inner": {"depth": 1}}
        self.waypoints: list[int] = [0, 0]
        self.strategy = _DummyStrategy()


def test_no_config_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    agent = _DummyAgent()
    applied = apply_config_overrides(instance=agent)
    assert applied == {}
    assert agent.worker_target == 12
    assert agent.beacon_policy == "retreat"
    assert agent.strategy.aggro == 0.5


def os_environ_cfg(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """清掉环境里可能残留的 ARENA_CFG_* 并返回被清键名。"""

    keys = [key for key in os_environ_keys() if key.startswith("ARENA_CFG_")]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ARENA_CONFIG_JSON", raising=False)
    return keys


def os_environ_keys() -> list[str]:
    import os

    return list(os.environ)


def test_env_int_coercion(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    monkeypatch.setenv("ARENA_CFG_WORKER_TARGET", "8")
    agent = _DummyAgent()
    applied = apply_config_overrides(instance=agent)
    assert applied == {"worker_target": 8}
    assert agent.worker_target == 8
    assert isinstance(agent.worker_target, int)


def test_env_string_stays_string(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    monkeypatch.setenv("ARENA_CFG_BEACON_POLICY", "pursue")
    agent = _DummyAgent()
    apply_config_overrides(instance=agent)
    assert agent.beacon_policy == "pursue"
    assert isinstance(agent.beacon_policy, str)


def test_env_bool_and_float(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    monkeypatch.setenv("ARENA_CFG_STRATEGY_AGGRO", "0.75")
    agent = _DummyAgent()
    apply_config_overrides(instance=agent)
    assert agent.strategy.aggro == 0.75
    assert isinstance(agent.strategy.aggro, float)


def test_dotted_key_walks_attrs(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    monkeypatch.setenv("ARENA_CFG_STRATEGY_LIMIT", "3")
    agent = _DummyAgent()
    apply_config_overrides(instance=agent)
    assert agent.strategy.limit == 3


def test_json_list_value(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    monkeypatch.setenv("ARENA_CFG_WAYPOINTS", "[1, 2, 3]")
    agent = _DummyAgent()
    agent.waypoints = [0, 0]
    apply_config_overrides(instance=agent)
    assert agent.waypoints == [1, 2, 3]


def test_dict_deep_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    monkeypatch.setenv("ARENA_CFG_NESTED", '{"inner": {"depth": 5, "extra": true}}')
    agent = _DummyAgent()
    apply_config_overrides(instance=agent)
    assert agent.nested == {"inner": {"depth": 5, "extra": True}}


def test_file_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    os_environ_cfg(monkeypatch)
    config_file = tmp_path / "arena-config.json"
    config_file.write_text(
        json.dumps({"overrides": {"mode": "control", "worker_target": 6}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARENA_CONFIG_JSON", str(config_file))
    agent = _DummyAgent()
    apply_config_overrides(instance=agent)
    assert agent.mode == "control"
    assert agent.worker_target == 6
    assert agent.beacon_policy == "retreat"  # 未覆盖键保持默认


def test_env_wins_over_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    os_environ_cfg(monkeypatch)
    config_file = tmp_path / "arena-config.json"
    config_file.write_text(json.dumps({"worker_target": 6}), encoding="utf-8")
    monkeypatch.setenv("ARENA_CONFIG_JSON", str(config_file))
    monkeypatch.setenv("ARENA_CFG_WORKER_TARGET", "9")
    agent = _DummyAgent()
    apply_config_overrides(instance=agent)
    assert agent.worker_target == 9


def test_corrupt_file_silently_degrades(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    os_environ_cfg(monkeypatch)
    config_file = tmp_path / "arena-config.json"
    config_file.write_text("not-json{{", encoding="utf-8")
    monkeypatch.setenv("ARENA_CONFIG_JSON", str(config_file))
    monkeypatch.setenv("ARENA_CFG_WORKER_TARGET", "8")
    agent = _DummyAgent()
    apply_config_overrides(instance=agent)  # 不应抛错
    assert agent.worker_target == 8  # env 通道仍生效


def test_unknown_key_silently_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    monkeypatch.setenv("ARENA_CFG_NO_SUCH_ATTR", "1")
    agent = _DummyAgent()
    apply_config_overrides(instance=agent)  # 不应抛错
    assert agent.worker_target == 12


def test_module_constant_override(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    import arena_hero.config_overrides as target_module

    monkeypatch.setenv("ARENA_CFG_CONFIG_ENV_PREFIX", "BOGUS_")
    apply_config_overrides(module=target_module)
    assert target_module.CONFIG_ENV_PREFIX == "BOGUS_"
    target_module.CONFIG_ENV_PREFIX = "ARENA_CFG_"


def test_explicit_overrides_short_circuits_env(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    monkeypatch.setenv("ARENA_CFG_WORKER_TARGET", "5")
    agent = _DummyAgent()
    applied = apply_config_overrides(instance=agent, overrides={"mode": "control"})
    assert applied == {"mode": "control"}
    assert agent.mode == "control"
    assert agent.worker_target == 12  # 显式 overrides 时不再读 env


def test_overridden_decide_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    monkeypatch.setenv("ARENA_CFG_TARGET", "40")
    monkeypatch.setenv("ARENA_CFG_MODE", "control")
    base = {"target": 30, "mode": "harvest"}
    merged = overridden_decide_kwargs(base)
    assert merged == {"target": 40, "mode": "control"}
    assert isinstance(merged["target"], int)


def test_overridden_decide_kwargs_noop_without_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    os_environ_cfg(monkeypatch)
    base = {"target": 30, "mode": "harvest"}
    assert overridden_decide_kwargs(base) == base


def test_load_returns_fresh_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    os_environ_cfg(monkeypatch)
    monkeypatch.setenv("ARENA_CFG_WORKER_TARGET", "5")
    first = load_config_overrides()
    second = load_config_overrides()
    first["worker_target"] = 99
    assert second["worker_target"] == 5
