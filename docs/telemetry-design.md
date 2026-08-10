# SDK 遥测设计（fork telemetry-sink 分支）

> 本文件是**捕获层（SDK）侧权威**。消费侧契约（台账 schema、ingest 端点、
> overview 合并语义）见 arena 根仓库 `docs/design/ml-data-pipeline.md` 与
> `arena-ts/packages/command-center` 的 `lib/agent-ingest.ts`（两处变更需
> 同步）。最后更新：2026-08-10（telemetry-v3）。

## 定位

本 fork 在官方 SDK 之上加了一层**纯旁路遥测**：

- **默认 no-op**：不设置 `ARENA_HERO_TELEMETRY_ENDPOINT` 时，官方行为
  零变化（`TelemetrySink` 空实现）。
- **激活即捕获一切**：设置端点后，SDK 自动从每次状态解析中提取全部可
  得信息并批量上报——**游戏决策代码（任何 agent）不需要做任何事**。
- **绝不干扰决策循环**：fire-and-forget 后台线程批量发送，队列满丢事件、
  网络失败静默丢弃（stderr 打印 `telemetry_flush_failed` 供排障），无
  异常抛出、无阻塞。

## 通用化原则（2026-08-09 用户裁决 + 2026-08-10 落地）

第三方 agent 仓库（waaiging/arena-hero-tactic 等只读引用）**零改动**。
所有能力注入（遥测、测绘、单位构成、决策耗时）统一落在 SDK fork 层：

| 信息 | 捕获点 | 版本 |
|---|---|---|
| 玩家状态摘要（资源/人口/核心位置/单位数/敌数） | `_materialize` PlayerState 分支 | telemetry-v1 |
| 测绘（资源格/障碍格/可见单位/敌核心） | 同源 `message.objects` 提取 | mapping-v1 |
| 状态消息体积/解析耗时/上 tick 决策耗时 | 事件流旁路计时 | telemetry-v2 |
| 我方单位构成（WORKER/VANGUARD/RANGER 计数） | `units_seen` 同源统计 | **telemetry-v3** |

agent 通过 fork SDK（`pip install -e`）自动获得全部能力，无需感知。

## 激活

| 环境变量 | 作用 | 缺省 |
|---|---|---|
| `ARENA_HERO_TELEMETRY_ENDPOINT` | 上报端点（`POST {"events":[...]}`） | 空 = no-op |
| `ARENA_HERO_TENANT` | 租户标识（t1..t4），ingest 落台账键 | `unknown` |
| `ARENA_HERO_MODE` | `production` / `simulation` | `production` |

上报 payload 自动注入 `tenant` / `instance`（api_key 尾 6 位）/ `ts` /
`mode`，事件体展开在顶层。

## 事件契约

### register（client 创建）
`api_key_tail / base_url / sdk_version / pid / platform`。

### connection（WebSocket 握手）
`status: up|error`；error 时带 `error` 描述。

### tick_summary（每 tick，核心事件）

字段分层（向后兼容：**所有字段可选**，旧 ingest 忽略新字段；旧客户端
不发送新字段，新 ingest 以 null/缺省兜底）：

```text
tick, status, resources, population,
core: [x, y] | null,                    # 我方核心位置
units: int,                             # 可见 UNIT 总数（含敌方）
controlled_by_type: {TYPE: n, ...},     # telemetry-v3：我方（controlled）
                                        # UNIT 按类型计数；只列存在类型
visible_enemies: int,                   # 敌方 UNIT 数
state_bytes, parse_ms, prev_decision_ms,# telemetry-v2：体积/解析/决策耗时
resource_cells: [[x,y],...],            # mapping-v1：可见资源格
obstacle_cells: [[x,y],...],            # mapping-v1：可见障碍格
units_seen: [[id, type, controlled, x, y, hp],...],  # mapping-v1：全量可见单位
enemy_cores: [[x,y,owner],...]          # mapping-v1：敌核心
```

### disconnected（client 关闭）
无附加字段。ingest 侧把 `connection_state` 置 `down`。

## 数据链路

```text
agent 决策循环（任意实现）
   │  SDK _materialize 旁路捕获（零侵入）
   ▼
fork SDK TelemetrySink（后台线程，5s 批量 flush）
   │  POST /api/ingest/agents  {"events":[...]}
   ▼
command-center ingest（agent-ingest.ts）
   │  agents 表 upsert（含 vanguards/rangers，telemetry-v3）
   │  测绘表 upsert（resources/obstacles/units_seen/core_hunts）
   ▼
survey/<tenant>.db
   │
   ├── /api/overview  侧栏/顶栏（台账为准，JSONL 合并 t1 专属字段）
   └── /api/map       地图（survey 测绘投影）
```

## 生产接线（2026-08-10 起）

t2/t3/t4 运行 arena-hero-tactic v2（feixingwawa），v2 代码零改动：
- venv：`reference/third-party/arena-hero-tactic/.venv`（editable 装本 fork）
- 启动：`arena-ts/scripts/restart-arena-hero-tactic.ps1`（env 注入 +
  PYTHONPATH，不跑 v2 自带 deploy.py——它 pip 装官方 SDK 会覆盖 fork）
- v2 的 `load_api_key` 不读实例 cwd `.env`（只查 repo/.env +
  find_dotenv 向上）——脚本启动前把三个 env 变量注入进程环境

t1 仍走 TS 线（arena-agent），不消费 controlled_by_type（构成由
calibration/snapshot 提供）。

## 兼容性纪律

- 新字段永远可加不可改：已有字段语义冻结（units = 可见 UNIT 总数，
  population = 官方人口）。
- ingest 侧：`controlled_by_type` 缺键 = 0（SDK 只列存在类型）；
  字段整体缺失 = null（旧客户端，未知而非假 0）。
- 消费侧不得把 SDK 捕获信息与 agent 决策耦合：上报失败不影响游戏。
