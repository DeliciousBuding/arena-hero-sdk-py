"""External config overrides for the Arena Hero SDK (local fork: config-injection).

默认 no-op：未配置任何 ``ARENA_CFG_*`` 环境变量、且不存在
``arena-config.json`` / ``ARENA_CONFIG_JSON`` 指向的文件时，本模块读取为空、
应用为零操作——agent 行为与官方 SDK 完全一致（跑同 seed 探针对比无差异）。

外部配置 → agent 决策参数 的注入通道：
  1. 环境变量 ``ARENA_CFG_<KEY>``（键即目标字段名；值按 JSON 字面量解析，
     解析失败回退原字符串——如 ``ARENA_CFG_WORKER_TARGET=16``）；
  2. JSON 配置文件：进程工作目录 ``arena-config.json``，或
     ``ARENA_CONFIG_JSON`` 指向的路径（顶层 ``{"overrides": {...}}`` 或直接
     ``{...}`` 均可；文件内容只在 mtime 变化时重读——每 tick 决策前读缓存）。
环境变量优先于文件；同 key 后者覆盖前者。

应用点（"加载 agent 后、决策入口前"的包装，由桥接层在构造 agent 后调用
``apply_config_overrides``）：
  - 实例属性：instance 上**已存在**的属性才覆盖（不新增属性、不动类型）；
  - 模块级常量：module 上**已存在**的名字才覆盖（Python 函数按调用时
    globals 解析，运行时 ``setattr`` 即对后续决策生效）；
  - 字典值深合并（嵌套 key 逐个覆盖），其余类型整体替换。
未知 key 静默跳过并一次性 stderr 提示；任何异常回退默认并记 stderr
（静默降级）——配置通道绝不改变无配置时的行为，也绝不阻断决策。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Mapping

CONFIG_ENV_PREFIX = "ARENA_CFG_"
CONFIG_FILE_ENV = "ARENA_CONFIG_JSON"
CONFIG_FILE_DEFAULT = "arena-config.json"
# 文件内容缓存（path, mtime, size）→ parsed；mtime/size 变化才重读。
_FILE_CACHE: dict[tuple[str, float, int], dict[str, Any]] = {}
_WARNED_KEYS: set[str] = set()


def _parse_value(raw: str) -> Any:
    """把 env 值按 JSON 字面量解析；解析失败回退原字符串。"""

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _read_file_overrides() -> dict[str, Any]:
    """读配置文件（缺省 arena-config.json；ARENA_CONFIG_JSON 显式指向）。

    文件顶层可以是 ``{"overrides": {...}}`` 或直接 ``{...}``。mtime 缓存：
    对局中途改文件即可生效，无需重启进程。
    """

    path = os.environ.get(CONFIG_FILE_ENV, "").strip() or CONFIG_FILE_DEFAULT
    try:
        stat = os.stat(path)
    except OSError:
        return {}
    cache_key = (path, stat.st_mtime, stat.st_size)
    cached = _FILE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    with open(path, encoding="utf-8") as config_file:
        raw = json.load(config_file)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 配置必须是 JSON 对象")
    overrides = raw.get("overrides", raw)
    if not isinstance(overrides, dict):
        raise ValueError(f"{path}: overrides 必须是 JSON 对象")
    _FILE_CACHE.clear()
    _FILE_CACHE[cache_key] = dict(overrides)
    return _FILE_CACHE[cache_key]


def load_config_overrides() -> dict[str, Any]:
    """汇总外部覆盖：文件 + 环境变量（env 优先）。

    无任何配置 → 返回 ``{}``（no-op）。解析/读取失败 → 记 stderr 并返回
    ``{}``（静默降级，不抛错）。
    """

    merged: dict[str, Any] = {}
    file_overrides: dict[str, Any] = {}
    try:
        file_overrides = _read_file_overrides()
    except Exception as exc:  # noqa: BLE001 —— 文件损坏只丢文件通道，env 仍生效。
        print(
            f"config_overrides: config file load failed, file channel off: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
    merged.update(file_overrides)
    try:
        for key, raw in os.environ.items():
            if key.startswith(CONFIG_ENV_PREFIX):
                # env 变量名惯例大写（ARENA_CFG_WORKER_TARGET）→ 归一为
                # 小写属性名（worker_target），与 Python 属性命名对齐。
                merged[key[len(CONFIG_ENV_PREFIX):].lower()] = _parse_value(raw)
    except Exception as exc:  # noqa: BLE001 —— 配置损坏绝不影响决策。
        print(
            f"config_overrides: env load failed, env channel off: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
    return merged


def _coerce_to(existing: Any, override: Any) -> Any:
    """按既有值的类型做安全强转（尽力而为，失败保留原值）。"""

    if existing is None or override is None:
        return override
    if isinstance(existing, bool):
        if isinstance(override, str):
            lowered = override.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
        return override
    if isinstance(existing, int) and not isinstance(existing, bool):
        if isinstance(override, str):
            try:
                return int(override)
            except ValueError:
                return override
        if isinstance(override, float):
            return int(override)
        return override
    if isinstance(existing, float):
        if isinstance(override, (str, int)):
            try:
                return float(override)
            except ValueError:
                return override
        return override
    return override


def _deep_merge(target: Any, override: Any) -> Any:
    """深合并：两者都是 dict 时递归合并；其余类型整体替换。"""

    if isinstance(target, dict) and isinstance(override, dict):
        merged = dict(target)
        for key, value in override.items():
            merged[key] = (
                _deep_merge(target[key], value) if key in target else value
            )
        return merged
    return override


def _warn_unknown(key: str, source: str) -> None:
    """未知 key 一次性提示（同 key 只提示一次，防刷屏）。"""

    if key in _WARNED_KEYS:
        return
    _WARNED_KEYS.add(key)
    print(
        f"config_overrides: {key} has no matching {source} field, skipped",
        file=sys.stderr,
        flush=True,
    )


def _resolve_segment(current: object, segment: str) -> tuple[object, str] | None:
    """段名解析：先精确、再大小写不敏感（env/文件键可能与属性名大小写不同）。"""

    if hasattr(current, segment):
        return current, segment
    for name in dir(current):
        if name.lower() == segment.lower():
            return current, name
    return None


def _walk_setattr(
    root: object,
    key: str,
    value: Any,
) -> bool:
    """点分键沿属性链下钻后赋值（如 strategy.limit → root.strategy.limit）。

    中间节点与最终属性都必须是已存在的（不新增）；任一段不存在 → False
    （未知键，由调用方一次性提示）。段名匹配大小写不敏感。
    """

    segments = key.split(".")
    current: object = root
    for segment in segments[:-1]:
        resolved = _resolve_segment(current, segment)
        if resolved is None:
            return False
        current, resolved_name = resolved
        current = getattr(current, resolved_name)
    final = segments[-1]
    resolved = _resolve_segment(current, final)
    if resolved is None:
        return False
    final_object, final_name = resolved
    existing = getattr(final_object, final_name)
    setattr(final_object, final_name, _deep_merge(existing, _coerce_to(existing, value)))
    return True


def _candidate_keys(key: str) -> list[str]:
    """env 键候选解释：整键优先，再按 "_" 从右往左切段（下划线 → 点分路径）。

    如 ``strategy_aggro`` → [strategy_aggro, strategy.aggro]；整键命中
    （worker_target 这种真下划线属性）时第一候选即成功，无歧义。
    """

    candidates = [key]
    for index in range(len(key) - 1, 0, -1):
        if key[index] == "_":
            candidates.append(f"{key[:index]}.{key[index + 1:]}")
    return candidates


def apply_config_overrides(
    *,
    module: object | None = None,
    instance: object | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把覆盖应用到 agent 配置（已存在的属性/常量；未知键跳过）。

    无配置（overrides 为空/None）→ 零操作，返回 ``{}``。单条失败不影响其余
    键（逐键 try/except，出错回退默认）。点分键（如 ``strategy.limit``）
    沿属性链深钻；实例属性优先于模块级常量。
    """

    if overrides is None:
        overrides = load_config_overrides()
    if not overrides:
        return {}
    applied: dict[str, Any] = {}
    targets: tuple[object, ...] = ()
    if instance is not None:
        targets += (instance,)
    if module is not None:
        targets += (module,)
    for key, value in overrides.items():
        matched = False
        for candidate in _candidate_keys(key):
            for target in targets:
                try:
                    if _walk_setattr(target, candidate, value):
                        applied[key] = value
                        matched = True
                        break
                except Exception as exc:  # noqa: BLE001 —— 单键失败静默回退。
                    print(
                        f"config_overrides: applying {key} failed, kept default: "
                        f"{type(exc).__name__}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    break
            if matched:
                break
        if not matched:
            _warn_unknown(key, "agent")
    return applied


def overridden_decide_kwargs(
    base_kwargs: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把外部覆盖合并进每次决策的函数参数（如 core 的 target/mode）。

    ``base_kwargs`` 为桥接层按注册表声明的默认参数（如 ``{"target": 30,
    "mode": "control"}``）；覆盖键命中 base 键时才替换（并按 base 值的类型
    强转）。无配置 → 原样返回 base 的拷贝（行为逐字节一致）。
    """

    if overrides is None:
        overrides = load_config_overrides()
    merged = dict(base_kwargs)
    if not overrides:
        return merged
    for key, value in merged.items():
        for override_key, override_value in overrides.items():
            if override_key.lower() == key.lower():
                merged[key] = _coerce_to(value, override_value)
                break
    return merged
