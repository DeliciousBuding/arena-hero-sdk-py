"""探针工具（SDK fork 配置注入通道验证，2026-08-09，L-C 线）。

两个子命令：

serve   HTTP 决策服务（模拟器对手）：POST {"tick","state"} → CommandPlan
        JSON，与 opponent-bridge.py 同一协议信封。启动时按注册表构造 agent
        并调用 SDK ``apply_config_overrides``（env/文件注入，无配置=no-op）。
        ``--record-dir`` 记录每个入站状态为 JSONL（供 replay 回放）。
        ``--label`` 标识运行（默认=基线/变体），日志落 stderr。

replay  回放状态序列：同一序列跑两遍——第一遍基线（清空 ARENA_CFG_*），
        第二遍注入（--env KEY=VALUE 或 --config 文件），逐 tick 对比 plan
        差异并汇总：差异 tick 数、动作类型计数（spawn/harvest/move/shot
        /core 动作）、状态轨迹（population/resources）。附带决策计时分解
        （json 解析 / pydantic 校验 / Turn 构造 / 决策 / plan 序列化，
        avg+median ms/tick），用于"SDK 侧开销占比"测量。

用法：
  python probes/probe_tool.py serve --agent waaiging --port 8787 [--record-dir dir] [--label baseline]
  python probes/probe_tool.py replay --agent waaiging --states states.jsonl \
      --env AGGRESS_TARGET_VANGUARDS=10 --env AGGRESS_TARGET_RANGERS=12
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROBE_ROOT = Path(__file__).resolve().parent
FORK_ROOT = PROBE_ROOT.parent


def _find_coordination_root(start: Path) -> Path:
    """从探针目录向上找协调根（含 reference/official/arena-hero-python）。"""

    dir_ = start
    for _ in range(12):
        if (dir_ / "reference" / "official" / "arena-hero-python").exists():
            return dir_
        parent = dir_.parent
        if parent == dir_:
            break
        dir_ = parent
    raise FileNotFoundError("coordination root (reference/official/arena-hero-python) not found")


COORDINATION_ROOT = _find_coordination_root(FORK_ROOT)


def _resolve_reference_repo(name: str) -> Path:
    """reference/ 子仓路径（third-party/official 分组 + 旧平铺兼容）。"""

    for subdir in ("third-party", "official"):
        candidate = COORDINATION_ROOT / "reference" / subdir / name
        if candidate.exists():
            return candidate
    return COORDINATION_ROOT / "reference" / name


def _load_repos(repo: Path) -> None:
    """注入 SDK fork src > fork root > agent src > agent root（桥接同序）。"""

    resolved_repo = repo.resolve()
    resolved_sdk = FORK_ROOT.resolve()
    priority: list[Path] = []
    sdk_src = resolved_sdk / "src"
    repo_src = resolved_repo / "src"
    if sdk_src.is_dir():
        priority.append(sdk_src)
    priority.append(resolved_sdk)
    if repo_src.is_dir():
        priority.append(repo_src)
    priority.append(resolved_repo)
    for candidate in reversed(priority):
        text = str(candidate)
        if text in sys.path:
            sys.path.remove(text)
        sys.path.insert(0, text)


def _noop_submitter(plan, idempotency_key=None):  # noqa: ANN001, ARG001
    """探针不提交网络——Turn.submit() 占位。"""

    return None


class BuiltAgent:
    """按 python-agents.json 注册表构造的 agent 运行时（桥接同款接线）。"""

    def __init__(self, name: str) -> None:
        registry = json.loads(
            (COORDINATION_ROOT / "arena-ts" / "packages" / "arena-agent"
             / "scripts" / "python-agents.json").read_text(encoding="utf-8")
        )["agents"]
        if name not in registry:
            raise ValueError(f"unknown agent {name!r} (registered: {sorted(registry)})")
        config = registry[name]
        repo = _resolve_reference_repo(config["repo"])
        _load_repos(repo)
        module = __import__(config["module"])
        self.module = module
        self.instance = None
        self.memory = None

        construct = config.get("construct")
        if construct is not None:
            cls = getattr(module, construct["fn"])
            kwargs: dict[str, Any] = {}
            if "worker_target" in construct.get("kwargs", []):
                kwargs["worker_target"] = 12
            if "beacon_policy" in construct.get("kwargs", []):
                kwargs["beacon_policy"] = "retreat"
            self.instance = cls(**kwargs)

        # 外部配置注入（SDK fork 通道）：加载 agent 后、决策入口前。
        from arena_hero.config_overrides import (  # noqa: PLC0415
            apply_config_overrides,
            overridden_decide_kwargs,
        )
        applied = apply_config_overrides(module=module, instance=self.instance)
        if applied:
            print(
                f"probe: config overrides applied: {json.dumps(applied, ensure_ascii=False)}",
                file=sys.stderr,
                flush=True,
            )

        self.decide_name = config["decide"]
        base_decide_kwargs: dict[str, Any] = {}
        if "target" in config.get("decide_kwargs", []):
            base_decide_kwargs["target"] = 30
        if "mode" in config.get("decide_kwargs", []):
            base_decide_kwargs["mode"] = "control"
        self.decide_kwargs = overridden_decide_kwargs(base_decide_kwargs)

        slot = config.get("slot", "pickle")
        if slot == "json":
            # core 型 agent 自带持久化（STATE_PATH，含自动保存）——重定向到
            # 本探针专属临时文件，避免污染 agent 仓库里的状态文件，也保证
            # 基线/注入两遍从同一（空）记忆起点出发。
            import tempfile  # noqa: PLC0415

            state_path = tempfile.mktemp(prefix="arena-probe-core-", suffix=".json")
            for attr in config.get("state_paths", []):
                setattr(module, attr, str(state_path) if "TEMP" not in attr else f"{state_path}.tmp")
            self.memory = module.AgentMemory.restore(module.load_persistent_state())

    def decide(self, turn) -> None:  # noqa: ANN001
        if self.instance is not None:
            getattr(self.instance, self.decide_name)(turn)
            return
        if self.memory is not None:
            self.module.plan_turn(turn, self.memory, **self.decide_kwargs)
            return
        getattr(self.module, self.decide_name)(turn, **self.decide_kwargs)


def _sanitize_state(state: dict[str, Any]) -> dict[str, Any]:
    """探针侧适配：模拟器把对手 id（如 http://127.0.0.1:8787/decide）当作
    owner_username 传入，超 SDK 模型 24 字符上限——截断到 24（仅显示用，
    不影响任何决策字段）。"""

    for obj in state.get("objects", []):
        username = obj.get("owner_username")
        if isinstance(username, str) and len(username) > 24:
            obj["owner_username"] = username[:24]
    return state


def plan_to_plan_json(turn) -> bytes:  # noqa: ANN001
    """与 opponent-bridge.py 同款 plan 序列化（mode=json → wire 形状）。"""

    payload = json.dumps(
        turn.plan.model_dump(mode="json"),
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return payload.encode("utf-8")


# --------------------------------------------------------------------------
# serve：HTTP 决策服务
# --------------------------------------------------------------------------

class _SingleBindServer(ThreadingHTTPServer):
    """禁 SO_REUSEADDR 重复绑定（Windows 上默认会允许第二个进程绑同端口，
    静默劫持请求——探针必须独占端口，端口被占时应立即失败）。"""

    allow_reuse_address = False


def _serve(args: argparse.Namespace) -> int:
    agent = BuiltAgent(args.agent)
    record_file = None
    if args.record_dir:
        record_dir = Path(args.record_dir)
        record_dir.mkdir(parents=True, exist_ok=True)
        record_file = (record_dir / f"{args.label}.jsonl").open(
            "a", encoding="utf-8"
        )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 —— http.server 协议名。
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                message = json.loads(raw.decode("utf-8"))
                tick = int(message["tick"])
                if record_file is not None:
                    record_file.write(raw.decode("utf-8") + "\n")
                    record_file.flush()
                from arena_hero.models import PlayerState  # noqa: PLC0415
                from arena_hero.turn import Turn  # noqa: PLC0415

                state = PlayerState.model_validate(_sanitize_state(message["state"]))
                turn = Turn(tick=tick, state=state, submitter=_noop_submitter)
                agent.decide(turn)
                body = plan_to_plan_json(turn)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001 —— 失败 fail-fast 如桥。
                print(
                    f"probe serve error agent={args.agent}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()

        def log_message(self, fmt: str, *fmt_args: Any) -> None:  # noqa: ARG002
            pass  # 请求日志走 stderr 只在错误时输出，避免刷屏。

    server = _SingleBindServer(("127.0.0.1", args.port), Handler)
    print(
        f"probe serve ready: agent={args.agent} label={args.label} "
        f"port={args.port} record={'on' if record_file else 'off'}",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if record_file is not None:
            record_file.close()
    return 0


# --------------------------------------------------------------------------
# replay：基线 vs 注入 对比 + 计时分解
# --------------------------------------------------------------------------

def _load_states(states_path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 状态序列（serve --record-dir 产出，或手工合成）。

    返回 (raw_lines, parsed) —— 计时探针要按桥接真实路径解析原始行，
    不得先反序列化再重序列化（会人为放大 json 阶段）。
    """

    raw_lines: list[str] = []
    parsed_states: list[dict[str, Any]] = []
    with open(states_path, encoding="utf-8") as states_file:
        for line in states_file:
            line = line.strip()
            if not line:
                continue
            message = json.loads(line)
            if "state" in message:
                raw_lines.append(line)
                parsed_states.append(message)
    if not parsed_states:
        raise ValueError(f"no states found in {states_path}")
    return raw_lines, parsed_states


def _synthetic_states(tick_count: int = 60) -> list[dict[str, Any]]:
    """无记录时的合成状态序列：从 TS 仓库 raw-ws fixture 出发逐步扩军。

    仅供 in-process 探针（行为可区分性/计时）使用；真局证据用小场实录。
    """

    fixture = (
        COORDINATION_ROOT / "arena-ts" / "packages" / "arena-hero-ts"
        / "contracts" / "fixtures" / "raw-ws" / "state-39961.json"
    )
    base = json.loads(fixture.read_text(encoding="utf-8"))
    states: list[dict[str, Any]] = []
    for tick in range(1, tick_count + 1):
        state = json.loads(json.dumps(base))
        # TS 侧 fixture 带 Python SDK 模型尚未接受的较新字段——剔除保持
        # 与模拟器 wire 形状一致（extra_forbidden）。
        state.pop("population_tier", None)
        state.pop("upkeep_next_tick", None)
        state["population"] = min(20, 3 + tick // 3)
        state["resources"] = min(120, 5 + tick * 2)
        state["objects"] = [
            obj
            for obj in state["objects"]
            if obj["kind"] != "UNIT" or obj.get("controlled") is not True
        ]
        core = next(obj for obj in state["objects"] if obj["kind"] == "CORE")
        worker_count = min(12, 2 + tick // 5)
        vanguard_count = min(4, tick // 8)
        ranger_count = min(4, tick // 10)
        for index in range(worker_count):
            state["objects"].append({
                "kind": "UNIT",
                "id": f"00000000-0000-4000-8000-{index + 20:012d}",
                "controlled": True,
                "position": [core["position"][0] + 1 + index % 3,
                             core["position"][1] + (index // 3) % 3],
                "hp": 2,
                "unit_type": "WORKER",
                "cargo": 0,
            })
        for index in range(vanguard_count):
            state["objects"].append({
                "kind": "UNIT",
                "id": f"00000000-0000-4000-8000-{index + 60:012d}",
                "controlled": True,
                "position": [core["position"][0] - 1 - index % 3,
                             core["position"][1] + (index // 3) % 3],
                "hp": 4,
                "unit_type": "VANGUARD",
            })
        for index in range(ranger_count):
            state["objects"].append({
                "kind": "UNIT",
                "id": f"00000000-0000-4000-8000-{index + 90:012d}",
                "controlled": True,
                "position": [core["position"][0] + (index // 3) % 3,
                             core["position"][1] - 1 - index % 3],
                "hp": 2,
                "unit_type": "RANGER",
            })
        states.append({"tick": tick, "state": state})
    return states


def _plan_signature(plan_json: bytes) -> dict[str, int]:
    """plan 的动作类型计数（对比用签名）。"""

    plan = json.loads(plan_json)
    signature: dict[str, int] = {}
    for unit_action in plan.get("unit_actions", {}).values():
        if unit_action is None:
            continue
        action_type = unit_action.get("type", "?")
        signature[f"unit:{action_type}"] = signature.get(f"unit:{action_type}", 0) + 1
    core_action = plan.get("core_action")
    if core_action is not None:
        signature[f"core:{core_action.get('type', '?')}"] = (
            signature.get(f"core:{core_action.get('type', '?')}", 0) + 1
        )
    return signature


def _run_pass(
    agent_name: str,
    states: list[dict[str, Any]],
    raw_lines: list[str] | None,
    env_overrides: dict[str, str],
) -> tuple[list[bytes], dict[str, float]]:
    """完整跑一遍状态序列（fresh agent），返回 plan 列表 + 阶段计时均值。

    注意：必须先构造 BuiltAgent（它把 SDK fork src 放到 sys.path 首位），
    之后才能 import arena_hero——否则会解析到机器 site-packages 的旧 SDK。
    """

    saved: dict[str, str | None] = {}
    for key in list(os.environ):
        if key.startswith("ARENA_CFG_") or key == "ARENA_CONFIG_JSON":
            saved[key] = os.environ.pop(key)
    for key, value in env_overrides.items():
        os.environ[f"ARENA_CFG_{key}"] = value
    try:
        agent = BuiltAgent(agent_name)

        from arena_hero.models import PlayerState  # noqa: PLC0415
        from arena_hero.turn import Turn  # noqa: PLC0415

        plans: list[bytes] = []
        phase_totals: dict[str, float] = {
            "json_loads": 0.0, "pydantic": 0.0, "turn": 0.0,
            "decide": 0.0, "dump": 0.0,
        }
        for index, message in enumerate(states):
            started = time.perf_counter()
            # 与桥接一致：直接解析原始行 JSON（不重序列化）；原始行是
            # {"tick","state"} 信封——取 state 段。
            parsed = (
                json.loads(raw_lines[index])["state"]
                if raw_lines is not None
                else message["state"]
            )
            after_loads = time.perf_counter()
            state = PlayerState.model_validate(_sanitize_state(parsed))
            after_pydantic = time.perf_counter()
            turn = Turn(tick=int(message["tick"]), state=state, submitter=_noop_submitter)
            after_turn = time.perf_counter()
            agent.decide(turn)
            after_decide = time.perf_counter()
            plan_json = plan_to_plan_json(turn)
            after_dump = time.perf_counter()
            plans.append(plan_json)
            phase_totals["json_loads"] += after_loads - started
            phase_totals["pydantic"] += after_pydantic - after_loads
            phase_totals["turn"] += after_turn - after_pydantic
            phase_totals["decide"] += after_decide - after_turn
            phase_totals["dump"] += after_dump - after_decide
        return plans, phase_totals
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _replay(args: argparse.Namespace) -> int:
    if args.states:
        raw_lines, states = _load_states(Path(args.states))
    else:
        raw_lines = None
        states = _synthetic_states(args.ticks)
    env_overrides: dict[str, str] = {}
    for pair in args.env:
        key, _, value = pair.partition("=")
        env_overrides[key.strip()] = value.strip()
    if args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        config = config.get("overrides", config)
        for key, value in config.items():
            env_overrides[key] = (
                json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            )

    baseline_plans, baseline_timing = _run_pass(args.agent, states, raw_lines, {})
    injected_plans, _injected_timing = _run_pass(
        args.agent, states, raw_lines, env_overrides
    )
    injected_on = bool(env_overrides)

    diff_ticks = 0
    baseline_first_sig: dict[str, int] = {}
    injected_first_sig: dict[str, int] = {}
    for index, (baseline_plan, injected_plan) in enumerate(
        zip(baseline_plans, injected_plans, strict=True)
    ):
        if baseline_plan != injected_plan:
            diff_ticks += 1
        if index == 0:
            baseline_first_sig = _plan_signature(baseline_plan)
            injected_first_sig = _plan_signature(injected_plan)

    n = len(states)
    print(f"replay agent={args.agent} states={n} ticks "
          f"injected={'on' if injected_on else 'off'} env={env_overrides or '-'}")
    print(f"  plan 差异：{diff_ticks}/{n} ticks 的 plan 与基线不同"
          f"（{'注入生效' if diff_ticks > 0 else 'no-op 一致' if not injected_on else '注入未生效！'})")
    if diff_ticks:
        print(f"  baseline 首 tick 动作签名：{json.dumps(baseline_first_sig)}")
        print(f"  injected 首 tick 动作签名：{json.dumps(injected_first_sig)}")

    phase_ms = {name: total * 1000 / n for name, total in baseline_timing.items()}
    sdk_side = (
        phase_ms["json_loads"] + phase_ms["pydantic"]
        + phase_ms["turn"] + phase_ms["dump"]
    )
    decide_ms = phase_ms["decide"]
    total_ms = sdk_side + decide_ms
    print(f"  计时（基线，avg ms/tick，n={n}）：")
    for name, value in phase_ms.items():
        print(f"    {name:<10} {value:8.3f}")
    print(f"    {'sdk_side':<10} {sdk_side:8.3f}（{(sdk_side / total_ms * 100) if total_ms else 0:.1f}%）")
    print(f"    {'decide':<10} {decide_ms:8.3f}（{(decide_ms / total_ms * 100) if total_ms else 0:.1f}%）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SDK fork 配置注入探针")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="HTTP 决策服务（模拟器对手）")
    serve_parser.add_argument("--agent", required=True)
    serve_parser.add_argument("--port", type=int, default=8787)
    serve_parser.add_argument("--label", default="baseline")
    serve_parser.add_argument("--record-dir")
    serve_parser.set_defaults(func=_serve)

    replay_parser = subparsers.add_parser("replay", help="基线 vs 注入对比 + 计时")
    replay_parser.add_argument("--agent", required=True)
    replay_parser.add_argument("--states", default=None, help="JSONL 状态序列（缺省合成）")
    replay_parser.add_argument("--ticks", type=int, default=60, help="合成序列 tick 数")
    replay_parser.add_argument("--env", action="append", default=[], help="KEY=VALUE（注入）")
    replay_parser.add_argument("--config", default=None, help="注入配置文件路径")
    replay_parser.set_defaults(func=_replay)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
