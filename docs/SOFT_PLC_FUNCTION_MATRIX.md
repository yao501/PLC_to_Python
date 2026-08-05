# Python 软 PLC 功能矩阵（SOFT_PLC_FUNCTION_MATRIX）

> **用途**：一份详细、可检索、可长期维护的功能矩阵，把“有没有代码 / 工作包是否通过 / 是否已合并 / Python 测试到哪 / 是否做过 CODESYS·HAL·现场验证”拆成互不推导的独立状态轴。它服务于用户按需选取下一工作、日常了解项目状态和 Claude/Codex 工作包规划。
> **本文件不是**新的技术规格或风险登记簿：不覆盖 `PLATFORM_ROADMAP.md`、各主题规格与 `RISKS.md`；只引用它们的结论并给一行边界。发现冲突时，本矩阵**降级标注并回查权威源**，绝不反向用矩阵改写历史或规格。
> **最后只读核验日期**：2026-08-05。**已合并主线 commit**：`aa73c56b5f07ed5bd87a6b0c4447d137f0474b04`（`main == origin/main == HEAD`）。
> **本次核验结论**：`WP-20260804-072 CLOSED` 已经 Claude 正式回审和 Codex 独立审核，收口 startup/readiness、Python 3.9 兼容与阶段 1 跨组件验收；阶段 1 / M1 Python headless MVP 功能闸门已通过。`WP-20260805-075 CLOSED` 已收口 `ENG-02/ENG-06` 的 60 分钟墙钟、轮次准入与 JSONL 失败关闭候选。上述累积改动仍是待本次 PR 合并的发布候选；Fallback Lite 与阶段 2 CFC 六文件候选保留本地、本次明确排除。Python、PLC/CODESYS、HAL、现场四级验证仍永久分离。

---

## 0. 怎么读这份矩阵（先读这一节）

### 0.1 权威优先级（冲突时从高到低）

1. **源码 / 测试与 Git 实盘**（`src/**`、`tests/**`、`git log/HEAD/工作区`）——事实的最终裁决。
2. **主题规格**：`IR_SPEC.md` / `ENGINE_SCAN_SPEC.md` / `COMPONENT_CONTRACT.md` / `TARGET_PROFILE.md` / `GOLDEN_TRACE_FORMAT.md` / `PLATFORM_ROADMAP.md`。
3. **`RISKS.md` 风险状态**（唯一正式风险登记簿）。
4. **`AI_REVIEW_HANDOFF.md` 工作包状态**（实施—审核往返记录）。
5. **本矩阵当前快照**（最低）。

任一层与更高层冲突时，本矩阵在对应单元格标注“⚠ 待回查”，并以更高层为准；不得用本矩阵把未合并候选写成已合并、把 Python 测试写成现场证明、或反写历史工作包结论。

### 0.2 六个永久分离的状态轴（互不推导）

| 轴 | 问的问题 | 取值（枚举） |
|---|---|---|
| **实现状态** | 代码写到什么程度？ | `已实现` / `部分实现` / `候选未提交` / `仅建模` / `未实现` |
| **WP 审核状态** | 承接工作包的协作状态？ | `CLOSED` / `APPROVED` / `CHANGES_REQUESTED` / `READY_FOR_CODEX` / `BLOCKED` / `无 WP`（附 WP 号） |
| **Git 状态** | 版本库里到什么程度？ | `已合并（主线 73b462b）` / `未提交候选` / `工作区未收尾` / `未涉及` |
| **Python 验证** | Python 主机测试覆盖到哪？ | `已覆盖`（附测试文件） / `部分覆盖` / `无` |
| **PLC/CODESYS 验证** | 有没有 SP16.1 导入/编译/仿真/对拍证据？ | `未验证`（当前**全部**为未验证） |
| **HAL/现场验证** | 有没有真实 HAL/物理 I/O/现场安全证据？ | `未验证`（当前**全部**为未验证） |

**四级验证永不互推**：Python 通过 ≠ PLC/CODESYS 一致；PLC 仿真 ≠ HAL/物理 I/O；HAL 台架 ≠ 现场安全发布。矩阵任何一格都不得把左边的证据搬到右边。

### 0.3 两套“状态词”不是同一维度

- **工作包状态机**（`AI_REVIEW_HANDOFF.md`）：`CLAUDE_WORKING → READY_FOR_CODEX → CODEX_REVIEWING → CHANGES_REQUESTED / APPROVED → CLOSED / BLOCKED`。描述**协作往返**。
- **风险状态**（`RISKS.md`）：`resolved / in-progress / deferred / open / locked`。描述**风险是否收敛**。
- 二者正交：一个功能点可以 `CLOSED`（协作关闭）而其风险仍 `in-progress`（现场未证明）。
- **`CLOSED` ≠ 现场可用**：`CLOSED` 只表示该工作包的产物与证据被用户确认接受，**不代表**已合并（那是 Git 事件）、更不代表 CODESYS 对拍或现场安全通过。
- **`APPROVED`、用户 `CLOSED`、Git 提交、PR 合并是四个不同事件**，分别落在 WP 状态轴和 Git 状态轴，不得相互冒充。

### 0.4 稳定 ID 与字段规则

- **ID 前缀**：`SEM`（规格/语义基线）、`PRM`（原语）、`BLK`（业务块）、`L2`、`L34`（L3/L4）、`L5`、`USR`（用户入口与后续平台）、`ENG`（工程支持）。
- ID 一经分配**永久稳定**，后续**不得为排序或美观重编号**；新增功能点分配新 ID（取该前缀下未用过的最小编号），不复用已删项编号。
- **实现状态 / WP / Git / Python / PLC / HAL** 六列即 §0.2 的六轴，取值只能用其枚举值。
- **最后核验日期 / commit**：每次实质更新在页首刷新“最后只读核验日期”和“核验主线 commit”；单行若与页首不同（例如某行引用更早快照）须在该行注明。

### 0.5 术语速查

- **headless（无界面）**：引擎只加载内存 IR、推进扫描，不带图形前端/编辑器。
- **IR（可执行中间表示）**：语言无关的类型化指令列表（Load/Store/CallFB/Convert/Jump…），ST 与 CFC 都 lower 到它；见 `IR_SPEC.md`。
- **Schema / Adapter（L2）**：`BlockSchema` 是纯数据管脚/类型元数据；`RuntimeAdapter` 是进程内把 Schema 绑到已迁移块的调用壳。
- **过程映像（process image）**：一拍开始一次性锁存的输入映像 + 一拍结束一次性提交的输出映像，隔离扫描内的读写。
- **shadow（影子/只写禁用）**：默认零配置下引擎“只算不写”，物理提交被抑制，诊断不冒充成功。
- **watchdog（看门狗）**：周期/扫描超时的安全响应；本项目当前只有**软件事件源候选**，硬件 watchdog 未做。
- **HAL（硬件抽象层）**：驱动/协议、GVL↔物理点映射、真实时钟源；未做。
- **黄金轨迹（golden trace）**：真实 CODESYS 运行导出的逐拍基准数据，用于对拍；格式就绪，真机实采为外部阻塞。

---

## 1. 大类导航

- [§3.1 SEM — 规格 / 语义基线](#31-sem--规格--语义基线)
- [§3.2 PRM — 8 个原语](#32-prm--8-个原语)
- [§3.3 BLK — 14 个业务块](#33-blk--14-个业务块)
- [§3.4 L2 — 组件模型 / 注册表 / adapter](#34-l2--组件模型--注册表--adapter)
- [§3.5 L34 — L3 程序模型（IR）/ L4 执行引擎](#35-l34--l3-程序模型ir--l4-执行引擎)
- [§3.6 L5 — 运行时安全服务](#36-l5--运行时安全服务)
- [§3.7 USR — 用户入口与后续平台](#37-usr--用户入口与后续平台)
- [§3.8 ENG — 工程支持（非软 PLC 产品功能）](#38-eng--工程支持非软-plc-产品功能)
- [§4 逐块信息质量（22 个库块）](#4-逐块信息质量22-个库块)
- [§5 当前 monitor 候选与测试证据](#5-当前-monitor-候选与测试证据)
- [§6 长期维护规则](#6-长期维护规则)

---

## 2. 一页总览

> 一眼看当前完成度。**PLC/CODESYS 与 HAL/现场两轴：全项目当前一律 `未验证`**，故总览不再逐类重复。

| 大类 | 功能点数 | 实现主体 | Git 主体 | Python 验证 | 一句话现状 |
|---|---|---|---|---|---|
| SEM 规格/语义基线 | 7 | 已冻结（0.5 基线）+ 黄金轨迹格式就绪待实采 | 已合并（主线） | 文档+原型 | 语义基线已冻结生效；真机对拍数据外部阻塞 |
| PRM 原语（8） | 8 | 已实现 + 22/22 adapter 已接入 | 已合并（主线，PR #24） | 已覆盖 | 迁移块 + L2 adapter 目录已审核关闭 |
| BLK 业务块（14） | 14 | 已实现 + 22/22 adapter 已接入 | 已合并（主线，PR #24） | 已覆盖 | 同上；APCM 整理原子性 Python 已修，CODESYS 未验 |
| L2 组件模型 | 8 | 已实现（22/22 目录级验收） | 已合并（主线，PR #24） | 已覆盖 | F2 变体一律 fail-closed；参数装载见 L5 |
| L34 IR/执行引擎 | 11 | 已实现（阶段 1 headless 核心 + 公开 API 跨组件总验收） | 既有核心已合并；WP-072 累积改动待本次 PR | 已覆盖（Python） | M1 Python 功能闸门已通过；CFC 定序属阶段 2，本次排除 |
| L5 运行时安全 | 14 | 软件安全核心、确定性 monitor 与 startup/readiness 已实现 | 既有安全核心已合并；WP-072 累积改动待本次 PR | 已覆盖（Python） | 真实调度/外部 readiness/HAL/硬件 watchdog/PLC/现场未验证 |
| USR 用户入口/后续平台 | 13 | 多数未实现 | 未涉及/试验已合并 | 无/局部 | ST/CFC 前端、导入器、HAL、AI 集成等均未做 |
| ENG 工程支持 | 6 | 已实现（含 Runbook、60 分钟墙钟、轮次/JSONL 失败关闭） | ENG-05 已合并；ENG-02/06 待本次 PR | 已覆盖 | 非软 PLC 产品功能，属协作基建 |

**最新已合并且已关闭的完整主线基线**（唯一权威主机快照）：`WP-20260730-052` 的正式 tests **1568/1568**、`prototype_05` **68/68**、全仓 **1636/1636**（经 [PR #32](https://github.com/yao501/PLC_to_Python/pull/32) 合并，merge `252842f4…`）。WP-050 的 1560/1628、WP-043/044 的 1480/1548、WP-046 的 1504/1572、WP-048 的 1537/1605 等仍是各历史检查点计数，不回写为当前主线。

**当前选择性发布候选**：阶段 1 / M1 与 WP-075 已批准工程支持已在排除 6 个 Fallback/CFC 文件后建立精确 Git 暂存候选，并从该索引导出的独立目录实测：阶段 1 定向 **71/71**、系统 Python 3.9 定向 **60/60**、AI Handoff **202/202**、正式 tests **1663/1663**、`prototype_05` **68/68**、全仓 **1731/1731**、`py_compile` 6/6 与公开导出 6/6 均通过。1698/1766 仍仅是包含本地 CFC 测试的 WP-075 历史快照，不冒充本次发布计数。

---

## 3. 详细矩阵

> 列含义见 §0.2 / §0.4。`PLC/CODESYS 验证` 与 `HAL/现场验证` 两列当前**全部**为 `未验证`，为可读性在下表中统一简写为 `未验证`。源码/权威入口为仓库相对链接。

### 3.1 SEM — 规格 / 语义基线

| ID | 大类 | 小类/功能点 | 目的与作用 | 主要源码/权威入口 | 实现状态 | WP 状态 | Git 状态 | Python 验证 | PLC/CODESYS 验证 | HAL/现场验证 | 主要风险 ID/边界 | 依赖 | 下一步 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SEM-01 | 规格/语义 | 阶段 0 设计基线（IR/扫描/组件契约概念设计） | 锁定平台地基字段与一拍时序概念，避免上层返工 | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 0 | 已实现（文档） | `无 WP`（阶段 0 里程碑） | 已合并（主线 73b462b） | 无（设计稿） | 未验证 | 未验证 | 概念设计，未证明与 PLC 一致 | — | 已被 0.5 取代为可冻结基线 |
| SEM-02 | 规格/语义 | 阶段 0.5 语义基线冻结（写回权威文档） | 把“改起来贵”的语义夯成可冻结工程基线并生效 | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 0.5 | 已实现（冻结生效） | `CLOSED`（WP-20260712-001） | 已合并（主线；PR #1 merge 3bff318 + PR #2） | 无（纯文档轮） | 未验证 | 未验证 | 项目工程约定≠CODESYS 官方语义 | SEM-01 | 维持冻结；变更须走评审 |
| SEM-03 | 规格/语义 | 目标画像 + 一致性等级 E/F1/F2 | 锁定 SP16.1/CPU·OS/任务配置与容差/位级两档一致性 | [TARGET_PROFILE.md](TARGET_PROFILE.md) | 已实现（v1.3） | `CLOSED`（随 SEM-02） | 已合并（主线 73b462b） | 无 | 未验证 | 未验证 | `PLATFORM-TARGET-PROFILE-1`（in-progress）；是否要 F2 待用户裁决 | SEM-02 | 生产环境与样本一致性复核 |
| SEM-04 | 规格/语义 | 可执行 IR 规格（类型化指令/POU 模型/lowering） | 语言无关 IR，ST/CFC 合流的地基 | [IR_SPEC.md](IR_SPEC.md) v2.2.4 | 已实现（规格冻结 v2.2.2 + 阶段 1 写回） | `CLOSED` | 已合并（主线 73b462b） | 原型双路径合流已测（见 SEM-07） | 未验证 | 未验证 | `PLATFORM-IR-1`/`PLATFORM-EXEC-IR-1`（resolved=仅 0.5 规格冻结） | SEM-02 | 阶段 6 对拍量化漂移 |
| SEM-05 | 规格/语义 | 一拍时序 / OutputPolicy 规格 | 五步扫描与分类型输出安全策略的权威定义 | [ENGINE_SCAN_SPEC.md](ENGINE_SCAN_SPEC.md) v2.2.2 | 已实现（规格冻结） | `CLOSED` | 已合并（主线 73b462b） | 见 L5 实现行 | 未验证 | 未验证 | `PLATFORM-OUTPUT-POLICY-1`/`-BASELINE-1`（工程约定） | SEM-02 | HAL 可信反馈接口（阶段 7） |
| SEM-06 | 规格/语义 | 组件描述符契约 / 一致性等级映射 | 块描述符、注册方式、省略语义规格 | [COMPONENT_CONTRACT.md](COMPONENT_CONTRACT.md) v2.1 | 已实现（规格） | `CLOSED` | 已合并（主线 73b462b） | 见 L2 实现行 | 未验证 | 未验证 | 见 L2 系列 | SEM-02 | 随 L2 演进 |
| SEM-07 | 规格/语义 | 黄金轨迹格式 + 采集清单 + 0.5 可执行原型 | 对拍数据格式就绪 + 双前端合流可执行证明 | [GOLDEN_TRACE_FORMAT.md](GOLDEN_TRACE_FORMAT.md) v1.2.1；[../prototype_05/](../prototype_05/) | 部分实现（格式+原型已成，真机实采未做） | `CLOSED`（原型两轮返修） | 已合并（主线 73b462b） | 已覆盖（`prototype_05` 68/68） | 未验证（真机实采外部阻塞） | 未验证 | `PLATFORM-GOLDEN-EARLY-1`（in-progress，外部阻塞） | SEM-04/05 | 用户提供 SP16.1 环境实采 |

### 3.2 PRM — 8 个原语

> 8 个原语均已迁移为标准库并接入 22/22 engineering adapter 目录（`WP-20260728-040 CLOSED`，[PR #24](https://github.com/yao501/PLC_to_Python/pull/24) 合并）。`fidelity_f2` 变体一律 `MissingVariantError` fail-closed。

| ID | 大类 | 小类/功能点 | 目的与作用 | 主要源码/权威入口 | 实现状态 | WP 状态 | Git 状态 | Python 验证 | PLC/CODESYS 验证 | HAL/现场验证 | 主要风险 ID/边界 | 依赖 | 下一步 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PRM-01 | 原语 | TON（通电延时定时器） | 输入维持 PT 后置位；跨拍状态 | [../src/primitives/timers.py](../src/primitives/timers.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖（`test_blocks`/`test_runtime_executor`） | 未验证 | 未验证 | `PRIM-INT-MS`/`PRIM-PT-VALIDATE`；`int ms` 接口 | L2 | — |
| PRM-02 | 原语 | TOF（断电延时定时器） | 输入撤销后维持 PT；跨拍 | [../src/primitives/timers.py](../src/primitives/timers.py) | 已实现 | `CLOSED`（WP-024/025） | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `PRIM-INT-MS` | L2 | — |
| PRM-03 | 原语 | TP（脉冲定时器） | 上升沿产生固定宽度脉冲；跨拍 | [../src/primitives/timers.py](../src/primitives/timers.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `PRIM-INT-MS` | L2 | — |
| PRM-04 | 原语 | R_TRIG（上升沿检测） | 检测输入上升沿；持上一拍 | [../src/primitives/edges.py](../src/primitives/edges.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `PRIM-R3-COLDSTART`（冷启动首拍） | L2 | — |
| PRM-05 | 原语 | F_TRIG（下降沿检测） | 检测输入下降沿；持上一拍 | [../src/primitives/edges.py](../src/primitives/edges.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `PRIM-R3-COLDSTART` | L2 | — |
| PRM-06 | 原语 | SR（置位优先锁存） | Set 优先的双稳态；跨拍 | [../src/primitives/latches.py](../src/primitives/latches.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | 锁存优先级语义锁定 | L2 | — |
| PRM-07 | 原语 | RS（复位优先锁存） | Reset 优先的双稳态；跨拍 | [../src/primitives/latches.py](../src/primitives/latches.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | 锁存优先级语义锁定 | L2 | — |
| PRM-08 | 原语 | BLINK（闪烁发生器） | 按 TIMEHIGH/TIMELOW 产生方波；跨拍 | [../src/primitives/blink.py](../src/primitives/blink.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `BLINK-B1/B2/B4`；APCM 内按 R8 量化 500ms | L2 | — |

### 3.3 BLK — 14 个业务块

> 14 个业务块均已迁移并接入 22/22 adapter 目录（[PR #24](https://github.com/yao501/PLC_to_Python/pull/24)）。仅 **APCM / APCPID / APCPIDZZD** 声明共享 `license_context`，其余 11 项不暗含授权依赖。逐块质量说明见 §4。

| ID | 大类 | 小类/功能点 | 目的与作用 | 主要源码/权威入口 | 实现状态 | WP 状态 | Git 状态 | Python 验证 | PLC/CODESYS 验证 | HAL/现场验证 | 主要风险 ID/边界 | 依赖 | 下一步 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BLK-01 | 业务块 | APCHSHLLIM（高低限幅） | 输出限幅到高低限；**无跨拍状态**（纯组合，`self.AV` 仅存最近输出、不参与下一拍；`LL>HL` 块内静默修正只影响本拍） | [../src/blocks/apchshllim.py](../src/blocks/apchshllim.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖（`test_blocks_apchshllim`） | 未验证 | 未验证 | `APCHSHLLIM-HL1..HL3` | L2 | — |
| BLK-02 | 业务块 | APCSTATISTICS（统计） | 均值/统计量；**AVG 为 LREAL**；**跨拍累积**（`MN/MX/AVG/COUNTER` running，`RESET` 清零，依调用次数非 dt_ms） | [../src/blocks/apcstatistics.py](../src/blocks/apcstatistics.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `APCSTATISTICS-S1..S8`；AVG 不得回退 REAL | L2 | — |
| BLK-03 | 业务块 | APCHSFOP（一阶惯性滤波） | 一阶低通；未传 TB 默认 0.5s；**跨拍**（持上一拍 `AV/Ok_1/AV_TEMP`） | [../src/blocks/apchsfop.py](../src/blocks/apchsfop.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `APCHSFOP-H1..H7` | L2 | — |
| BLK-04 | 业务块 | APCHSRATELIM（速率限幅） | 限制输出变化率；**跨拍**（持上一拍输出 `AV_1`） | [../src/blocks/apchsratelim.py](../src/blocks/apchsratelim.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `APCHSRATELIM-RL1..RL5` | L2 | — |
| BLK-05 | 业务块 | APCHSACCUM（累积器） | 面积/累积；**AV 为 LREAL**；**跨拍**（累积器 `AV/SS` 等状态；`IV/MS/MC` 为 init_overridable） | [../src/blocks/apchsaccum.py](../src/blocks/apchsaccum.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖（含构造值 fail-closed） | 未验证 | 未验证 | `APCHSACCUM-AC1..AC6`；AV 不得回退 REAL | L2/L5 参数装载 | — |
| BLK-06 | 业务块 | APCHXHCL（历史均值/右移） | 采样窗口均值；**跨拍**（历史窗口数组）；内嵌 TOF×2 / R_TRIG（`TOF1/TOF2/R_TRIG3`） | [../src/blocks/apchxhcl.py](../src/blocks/apchxhcl.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `APCHXHCL-R*`/`-PERF-1`/`-ROUND-1` | L2 | ring buffer 性能为 nice-to-have |
| BLK-07 | 业务块 | APCGCQ（组合业务块） | 组合控制量计算；**跨拍**；内嵌 BLINK / R_TRIG / APCSTATISTICS / APCHSFOP / APCHSRATELIM / APCHSHLLIM | [../src/blocks/apcgcq.py](../src/blocks/apcgcq.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `APCGCQ-GG1..GG8` | L2 | — |
| BLK-08 | 业务块 | APCCD（组合业务块） | 组合/CD 支路计算；**跨拍**；内嵌 BLINK / R_TRIG×2 / TON / APCSTATISTICS / APCHSFOP | [../src/blocks/apccd.py](../src/blocks/apccd.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖（原子提交反证） | 未验证 | 未验证 | `APCCD-CD1..CD7` | L2 | — |
| BLK-09 | 业务块 | APCPIDZZD（PID 自整定） | 自整定 PT1/TI1；**跨拍多实例**；内嵌 TON×2 / R_TRIG×2 / APCHSACCUM / APCHSHLLIM，构造注入 `license_context` | [../src/blocks/apcpidzzd.py](../src/blocks/apcpidzzd.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `APCPIDZZD-*`（RESET/CLAMP locked）；共享 `license_context` | L2 | — |
| BLK-10 | 业务块 | APCPID（变比例变积分 PID） | PID 调节；内嵌 PIDZZD1；跨拍 | [../src/blocks/apcpid.py](../src/blocks/apcpid.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `APCPID-*`；共享 `license_context`（内嵌与顶层同一 ctx） | L2 | — |
| BLK-11 | 业务块 | APCSPFINDER（设定值自动寻找） | 分析用 SP 寻找；跨拍 | [../src/blocks/apcspfinder.py](../src/blocks/apcspfinder.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `APCSPFINDER-*` | L2 | — |
| BLK-12 | 业务块 | APCRSFNAUTOPARA（RSFN 自动参数推荐） | 参数推荐；**跨拍**（复用真实 `APCSPFINDER` 实例 + 自身状态） | [../src/blocks/apcrsfnautopara.py](../src/blocks/apcrsfnautopara.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `APCRSFNAUTOPARA-*` | L2 | — |
| BLK-13 | 业务块 | APCMAUTOPARA（APCM 自动参数推荐） | 推荐分析；**跨拍**（复用真实 `APCSPFINDER` 实例 + 自身状态） | [../src/blocks/apcmautopara.py](../src/blocks/apcmautopara.py) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | `APCMAUTOPARA-*`（CYCLE/SPFINDER locked） | L2 | — |
| BLK-14 | 业务块 | APCM（智能综合控制模块） | 综合 APC 控制；内嵌多块；跨拍 | [../src/blocks/apcm.py](../src/blocks/apcm.py) | 已实现（ZLEN/R_TRIG02 原子整理已修） | `CLOSED`（含 2026-07-22 授权维护） | 已合并（主线；APCM 整理 [PR #19](https://github.com/yao501/PLC_to_Python/pull/19)） | 已覆盖（APCM 63/63 等） | 未验证（SP16.1 编译/仿真/趋势对拍未做） | 未验证 | `APCM-*`；共享 `license_context`；`APCM-ZLOUT-1` 现场回路未对拍 | L2 | CODESYS 导入编译+趋势对拍 |

### 3.4 L2 — 组件模型 / 注册表 / adapter

| ID | 大类 | 小类/功能点 | 目的与作用 | 主要源码/权威入口 | 实现状态 | WP 状态 | Git 状态 | Python 验证 | PLC/CODESYS 验证 | HAL/现场验证 | 主要风险 ID/边界 | 依赖 | 下一步 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| L2-01 | L2 组件 | Pin / BlockSchema（纯数据管脚元数据） | 管脚/类型/省略语义的纯数据描述 | [../src/runtime/descriptors/model.py](../src/runtime/descriptors/model.py)（`Pin`/`BlockSchema` 定义）；[../src/runtime/loader.py](../src/runtime/loader.py)（装载期消费）；[COMPONENT_CONTRACT.md](COMPONENT_CONTRACT.md) | 已实现 | `CLOSED`（WP-016..022） | 已合并（主线，PR #24/#22） | 已覆盖（`test_runtime_descriptors`） | 未验证 | 未验证 | `PLATFORM-L2-REGISTRY-1` | SEM-06 | — |
| L2-02 | L2 组件 | RuntimeAdapter（进程内调用壳） | 把 Schema 绑定到迁移块 step | [../src/runtime/descriptors/model.py](../src/runtime/descriptors/model.py)（`RuntimeAdapter` 定义）；[../src/runtime/loader.py](../src/runtime/loader.py)、[../src/runtime/executor.py](../src/runtime/executor.py)（调用消费） | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖 | 未验证 | 未验证 | 不为迁就引擎改写 `src/blocks` | L2-01 | — |
| L2-03 | L2 组件 | Registry（`(block_type, variant)` 唯一注册表） | 库块注册与解析入口 | [../src/runtime/descriptors/registry.py](../src/runtime/descriptors/registry.py)（`Registry` 类）；[../src/runtime/descriptors/representative.py](../src/runtime/descriptors/representative.py)（`build_default_registry` 默认 22 键构造）；[../src/runtime/__init__.py](../src/runtime/__init__.py)（re-export） | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖（实盘 22 键） | 未验证 | 未验证 | `PLATFORM-L2-REGISTRY-1` | L2-01 | — |
| L2-04 | L2 组件 | 22/22 engineering adapter 目录 | 8 原语 + 14 业务块完整目录级验收 | [../src/runtime/descriptors/representative.py](../src/runtime/descriptors/representative.py)（`build_default_registry` 注册 22 项）；[../tests/test_runtime_descriptors.py](../tests/test_runtime_descriptors.py)（目录级验收）；[../tests/test_runtime_stage1_acceptance.py](../tests/test_runtime_stage1_acceptance.py)（WP-062 跨组件候选） | 已实现（22/22 目录级验收） | `CLOSED`（WP-20260728-040）；`WP-062` 仅新增待回审候选证据 | 产品已合并（主线，PR #24）；WP-062 测试未提交 | 已覆盖；WP-062 候选独立复核精确 22 键 | 未验证 | 未验证 | `PLATFORM-L2-REGISTRY-1`/`RUNTIME-STAGE1-PY-ACCEPTANCE`；Python 证据不外推 | L2-01..03 | 待独立 Reviewer 与未来 Claude 回审 |
| L2-05 | L2 组件 | OmitPolicy 四态（required/use_default/keep_previous/none_means_no_write） | 管脚省略语义四值枚举 | [../src/runtime/descriptors/model.py](../src/runtime/descriptors/model.py)（`OmitPolicy` 枚举定义）；[../src/runtime/executor.py](../src/runtime/executor.py)（`_LibraryRuntime.step` 运行时消费 required/use_default/keep_previous/none_means_no_write 输入边界；loader 仅做静态 schema/引脚校验，不读取 `omit_policy`） | 已实现 | `CLOSED`（WP-020/021/022） | 已合并（主线，PR #24） | 已覆盖（每态反证） | 未验证 | 未验证 | required 缺失 fail-closed 不半写 | L2-01 | — |
| L2-06 | L2 组件 | F2 缺失变体失败关闭 | `fidelity_f2` 一律 `MissingVariantError` | [../src/runtime/descriptors/registry.py](../src/runtime/descriptors/registry.py)（`MissingVariantError`/`resolve`）；[../src/runtime/loader.py](../src/runtime/loader.py)（装载期消费）；[../tests/test_runtime_stage1_acceptance.py](../tests/test_runtime_stage1_acceptance.py)（WP-062 候选） | 已实现 | `CLOSED`；WP-062 只增候选验收证据 | 产品已合并；WP-062 测试未提交 | 已覆盖（全 22 项 fail-closed）；WP-062 重新经默认 Registry 公开 API 验收 | 未验证 | 未验证 | F2 与“块零改动”互斥；`RUNTIME-STAGE1-PY-ACCEPTANCE` 不使其成为 PLC 证据 | L2-04 | 用户裁决是否上 F2；候选待回审 |
| L2-07 | L2 组件 | LicenseContext 共享（仅 3 块） | 授权上下文注入，仅 APCM/APCPID/APCPIDZZD | [../src/globals/](../src/globals/) | 已实现 | `CLOSED` | 已合并（主线，PR #24） | 已覆盖（依赖图精确锁定） | 未验证 | 未验证 | `LIC-*`；其余 19 项不暗含依赖 | L2-04 | 阶段二授权按商业需要 |
| L2-08 | L2 组件 | serializer / HMI 边界 | Schema 可 JSON 序列化 / HMI 可写标记边界 | [../src/runtime/descriptors/model.py](../src/runtime/descriptors/model.py)（`BlockSchema.to_json` 序列化 / `hmi_writable` 边界） | 部分实现（序列化就绪；HMI 运行期写未做） | `CLOSED`（序列化部分） | 已合并（主线，PR #24） | 已覆盖（Schema JSON 序列化） | 未验证 | 未验证 | `hmi_writable` 运行期写属后续 | L2-04 | HMI 在线写立项 |

### 3.5 L34 — L3 程序模型（IR）/ L4 执行引擎

| ID | 大类 | 小类/功能点 | 目的与作用 | 主要源码/权威入口 | 实现状态 | WP 状态 | Git 状态 | Python 验证 | PLC/CODESYS 验证 | HAL/现场验证 | 主要风险 ID/边界 | 依赖 | 下一步 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| L34-01 | L3 IR | IR 值 / 类型化指令集 | 全类型化指令 + TypedValue 栈 | [../src/runtime/ir.py](../src/runtime/ir.py) | 已实现 | `CLOSED`（WP-002） | 已合并（主线 73b462b） | 已覆盖（`test_runtime_ir`） | 未验证 | 未验证 | `PLATFORM-EXEC-IR-1`（0.5 冻结） | SEM-04 | — |
| L34-02 | L3 IR | POU / 实例模型（定义与实例分离） | `POUDefinition/ProgramInstance/FBInstance` | [../src/runtime/ir.py](../src/runtime/ir.py) | 已实现 | `CLOSED` | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | `PLATFORM-POU-MODEL-1`（in-progress） | L34-01 | 随语言前端推进 |
| L34-03 | L3 IR | Loader 装载期静态校验 | 加载期类型/结构错误聚合 fail-closed | [../src/runtime/loader.py](../src/runtime/loader.py) | 已实现 | `CLOSED`（WP-002） | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | 递归实例声明循环 fail-closed | L34-01 | — |
| L34-04 | L4 引擎 | Store / 隔离快照 / 批量原子提交 | 声明制 Store、隔离快照、原子提交 | [../src/runtime/store.py](../src/runtime/store.py) | 已实现 | `CLOSED`（WP-003） | 已合并（主线 73b462b） | 已覆盖（`test_runtime_store`） | 未验证 | 未验证 | `PLATFORM-EXEC-STORE-ATOMICITY-1`（局部 in-progress） | L34-01 | — |
| L34-05 | L4 引擎 | 实例布局（装载期展开） | PROGRAM/用户 FB 实例按路径展开 | [../src/runtime/store.py](../src/runtime/store.py) | 已实现 | `CLOSED` | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | 持久 Store 键为工程约定 | L34-02 | — |
| L34-06 | L4 引擎 | 过程映像（输入锁存/输出映像） | 一拍输入原子锁存、输出待提交容器 | [../src/runtime/process_image.py](../src/runtime/process_image.py) | 已实现 | `CLOSED` | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | — | L34-04 | — |
| L34-07 | L4 引擎 | Executor 显式顺序执行 | 按显式顺序逐条执行指令 + 求值栈 | [../src/runtime/executor.py](../src/runtime/executor.py) | 已实现 | `CLOSED`（WP-004/005） | 已合并（主线 73b462b） | 已覆盖（`test_runtime_executor`） | 未验证 | 未验证 | `StackSlot.index` 工程约定 | L34-01 | — |
| L34-08 | L4 引擎 | FUNCTION / 用户 FB / VAR_IN_OUT | 调用帧、`ValueRef` 别名引用语义 | [../src/runtime/executor.py](../src/runtime/executor.py) | 已实现 | `CLOSED` | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | VAR_IN_OUT 写透调用方 | L34-07 | — |
| L34-09 | L4 引擎 | E/F1 数值边界 | engineering/F1 量化边界与失败关闭 | [../src/runtime/numeric.py](../src/runtime/numeric.py) | 已实现 | `CLOSED` | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | `PLATFORM-REAL-FIDELITY-1`/`-INT-WIDTH-1`（in-progress） | SEM-03 | 阶段 6 量化漂移裁决 |
| L34-10 | L4 引擎 | 五步 ScanEngine（确定性单拍） | 输入锁存→执行→策略→提交的可重复单拍编排 | [../src/runtime/engine.py](../src/runtime/engine.py) | 已实现 | `CLOSED`（WP-006） | 已合并（主线 73b462b） | 已覆盖（`test_runtime_engine`） | 未验证 | 未验证 | `RUNTIME-5-STEPS`（resolved=Python 核心）；CFC 图定序编译器属阶段 2 未做 | L34-07 | 阶段 2 CFC 定序编译器 |
| L34-11 | L4 引擎 | 单任务运行栈纵向装配（阶段 1 E2E） | 把 `build_runtime`→安全状态→OutputPolicy→CommitSupervisor→默认 shadow CommitPort→ScanEngine→OuterScanRunner→软件 monitor→startup readiness 连成同一对象图 | [../src/runtime/task_runtime.py](../src/runtime/task_runtime.py)；[../tests/test_runtime_stage1_acceptance.py](../tests/test_runtime_stage1_acceptance.py) | 已实现（Python 内部/测试入口，非最终 ST/CFC 用户入口） | `CLOSED`（WP-050；WP-060/061/062/065/071 由 WP-072 正式收口） | 既有装配已合并；WP-072 累积改动待本次 PR | 阶段 1 公开 API 定向 71/71；Claude/Codex 独立审核通过 | 未验证 | 未验证 | `RUNTIME-TASK-ASSEMBLY`/`RUNTIME-STAGE1-PY-ACCEPTANCE`；不推导 PLC/HAL/现场验证 | L34-10, L5-04, L5-08, L5-10, L5-12 | 阶段 2 CFC 候选另行回审 |

### 3.6 L5 — 运行时安全服务

| ID | 大类 | 小类/功能点 | 目的与作用 | 主要源码/权威入口 | 实现状态 | WP 状态 | Git 状态 | Python 验证 | PLC/CODESYS 验证 | HAL/现场验证 | 主要风险 ID/边界 | 依赖 | 下一步 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| L5-01 | L5 安全 | SafetySnapshot（原子安全状态） | 一拍原子消费 ready/safety/interlock 等信号 | [../src/runtime/output_policy.py](../src/runtime/output_policy.py) | 已实现 | `CLOSED`（WP-007） | 已合并（主线 73b462b） | 已覆盖（`test_runtime_output_policy`） | 未验证 | 未验证 | `RUNTIME-GATE`（in-progress，信号源现场未做） | L34-10 | — |
| L5-02 | L5 安全 | OutputPolicy（分类型/分原因策略） | 按通道类型+故障原因生成 final，安全优先 | [../src/runtime/output_policy.py](../src/runtime/output_policy.py) | 已实现 | `CLOSED`（WP-007） | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | `PLATFORM-OUTPUT-POLICY-1`（工程约定） | L5-01 | HAL 可信反馈 |
| L5-03 | L5 安全 | SafeImageTicket（两阶段安全映像事务） | staging + 提交后确认的一次性两阶段事务 | [../src/runtime/scan_runner.py](../src/runtime/scan_runner.py) | 已实现 | `CLOSED`（WP-008） | 已合并（主线 73b462b） | 已覆盖（`test_runtime_scan_runner`） | 未验证 | 未验证 | 结构化失败信号 | L5-02 | — |
| L5-04 | L5 安全 | OuterScanRunner（故障安全外层运行器） | 扫描异常/watchdog 事件绕过损坏 request、单次安全提交 | [../src/runtime/scan_runner.py](../src/runtime/scan_runner.py) | 已实现 | `CLOSED`（WP-008） | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | `RUNTIME-SAFETY-DEFAULT`（in-progress） | L5-03 | 真实事件源接入 |
| L5-05 | L5 安全 | CommitSupervisor（提交监督器） | 驱动确认回执、逐通道 commit_fault/channel_fault | [../src/runtime/commit_supervisor.py](../src/runtime/commit_supervisor.py) | 已实现 | `CLOSED`（WP-009/010） | 已合并（主线 73b462b） | 已覆盖（`test_runtime_commit_supervisor`） | 未验证 | 未验证 | 安全值重试、三条件显式复位 | L5-04 | — |
| L5-06 | L5 安全 | commit_fault / channel_fault 锁存与复位 | 瞬时故障与锁存故障区分、三条件复位 | [../src/runtime/commit_supervisor.py](../src/runtime/commit_supervisor.py) | 已实现 | `CLOSED` | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | `PLATFORM-OUTPUT-BASELINE-1`（工程约定） | L5-05 | HAL 现场复位接口 |
| L5-07 | L5 安全 | last_physical_committed / 驱动回执类型信任 | LPC 两层状态 + 不可信回执子类失败关闭 | [../src/runtime/commit_supervisor.py](../src/runtime/commit_supervisor.py) | 已实现 | `CLOSED`（WP-011） | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | `PLATFORM-DRIVER-RECEIPT-TYPE-1`（resolved=Python 契约） | L5-05 | 真实驱动/HAL |
| L5-08 | L5 安全 | 默认 shadow / write-disable 栈 | 零配置只算不写；诊断不冒充成功 | [../src/runtime/scan_runner.py](../src/runtime/scan_runner.py) | 已实现 | `CLOSED`（WP-012..015） | 已合并（主线 73b462b） | 已覆盖（`test_runtime_shadow_mode`） | 未验证 | 未验证 | `RUNTIME-SHADOW-MODE`（in-progress；不防语言级反射） | L5-04 | — |
| L5-09 | L5 安全 | shadow→实写边界 | 切实写先全通道 safe_value 重建，首拍限速 | [../src/runtime/scan_runner.py](../src/runtime/scan_runner.py) | 已实现 | `CLOSED`（WP-015） | 已合并（主线 73b462b） | 已覆盖 | 未验证 | 未验证 | 预存 fault 不因切换清除 | L5-08 | — |
| L5-10 | L5 安全 | 启动装配 / 参数校验（build_runtime） | 启动期 IR/L2/构造/Store/时间/inhibit 校验一次性失败关闭 | [../src/runtime/parameters.py](../src/runtime/parameters.py) | 部分实现（静态启动装配子范围） | `CLOSED`（WP-20260729-042） | 已合并（主线，[PR #26](https://github.com/yao501/PLC_to_Python/pull/26) merge 495ebb1） | 已覆盖（`test_runtime_parameters`） | 未验证 | 未验证 | `RUNTIME-PARAM-VALIDATION`（**in-progress**，动态值/外部配置/持久化未做） | L34-03 | 外部配置源/优先级立项 |
| L5-11 | L5 安全 | startup inhibit（确定性 readiness） | `startup_inhibit_ms` exact-int 校验、显式稳定窗口、readiness 释放与 scan/watchdog 锁存优先 | [../src/runtime/startup.py](../src/runtime/startup.py)；[../src/runtime/task_runtime.py](../src/runtime/task_runtime.py)；[../tests/test_runtime_stage1_acceptance.py](../tests/test_runtime_stage1_acceptance.py) | 已实现（确定性显式调用） | `CLOSED`（WP-060/061/062/065/071 由 WP-072 `APPROVED/CLOSED` 收口） | 待本次 PR | exact-bool、整数纳秒窗口、TOCTOU、双域失败原子性、故障锁存优先级与 Python 3.9 兼容均已覆盖 | 未验证 | 未验证 | `RUNTIME-STARTUP-INHIBIT`/`RUNTIME-STAGE1-PY-ACCEPTANCE`（Python 子范围收口；外部信号/实时/HAL/现场仍 open） | L5-10, L34-11 | 真实信号与调度另立项 |
| L5-12 | L5 安全 | 软件 monitor（周期/超时/watchdog 事件源） | 可注入整数纳秒时钟、无后台线程的一次性超时事件源 | [../src/runtime/monitor.py](../src/runtime/monitor.py)；[../tests/test_runtime_stage1_acceptance.py](../tests/test_runtime_stage1_acceptance.py) | 已实现并审核关闭（Python 确定性事件源） | `CLOSED`（WP-048；WP-062 跨组件证据由 WP-072 收口） | 产品已合并；新验收证据待本次 PR | 一次性派发、事件拍不推进、安全图像采用与不重放已覆盖 | 未验证 | 未验证 | `RUNTIME-WATCHDOG`/`RUNTIME-SAFETY-DEFAULT`；仅软件事件源 | L5-04 | 真实调度/硬件 watchdog 另立项 |
| L5-13 | L5 安全 | 实时扫描循环 | 真实周期线程、墙钟抖动、连续 deadline miss 升级 | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 7 | 未实现 | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | `RUNTIME-WATCHDOG`；进程卡死/OS 崩溃兜底属外部 | L5-12 | 阶段 7 |
| L5-14 | L5 安全 | 硬件 watchdog | 真实硬件看门狗与外部安全回路 | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 7 | 未实现 | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | `RUNTIME-HAL`（deferred）；接现场必需 | L5-13 | 阶段 7 |

### 3.7 USR — 用户入口与后续平台

| ID | 大类 | 小类/功能点 | 目的与作用 | 主要源码/权威入口 | 实现状态 | WP 状态 | Git 状态 | Python 验证 | PLC/CODESYS 验证 | HAL/现场验证 | 主要风险 ID/边界 | 依赖 | 下一步 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| USR-01 | 用户入口 | CFC 定序（数据流定序编译器） | 把 CFC 图 lower 成执行序（阶段 2） | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 2 | 未实现 | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | `PLATFORM-CFC-AUTOORDER-1`（deferred/mitigated） | L34-10 | 阶段 2 |
| USR-02 | 用户入口 | ST 前端（词法/语法/lowering） | ST 源模型 → IR（阶段 3） | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 3 | 未实现 | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | `PLATFORM-ST-CONFORMANCE-1`（open） | SEM-04 | 阶段 3 |
| USR-03 | 用户入口 | CFC 模型 + 图形编辑器 | 用户建图（阶段 4） | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 4 | 未实现 | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | — | USR-01 | 阶段 4 |
| USR-04 | 用户入口 | 生产级 CODESYS 工程导入 | SP16.1 导出 → 可执行 IR（阶段 5） | [../prototype_05/import_trial/FINDINGS.md](../prototype_05/import_trial/FINDINGS.md) | 部分实现（**仅最小导入可行性试验**） | `无 WP`（试验为 PLATFORM-IMPORT-TRIAL-1 done） | 已合并（主线，试验解析器） | 部分覆盖（`test_import_trial` 回归锁） | 未验证 | 未验证 | `PLATFORM-IMPORT-TRIAL-1`（done）；`-CFC-AUTOORDER-1`/`-FEEDBACK-MAP-1` | USR-01 | 阶段 5 重建算法 |
| USR-05 | 用户入口 | 多任务 / GVL / 工程装配 | 多任务、全局变量、工程级装配 | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) | 未实现（仅 LicenseContext 雏形） | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | GVL 容器可由 LicenseContext 泛化 | L34-02 | — |
| USR-06 | 用户入口 | 黄金轨迹对拍验证 | 真机逐拍对拍（阶段 6，命门） | [GOLDEN_TRACE_FORMAT.md](GOLDEN_TRACE_FORMAT.md) | 未实现（格式就绪，真机实采外部阻塞） | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | `PLATFORM-GOLDEN-EARLY-1`（in-progress） | SEM-07 | 用户提供 SP16.1 实采 |
| USR-07 | 后续平台 | HAL / 协议 / I/O 映射 | 驱动、GVL↔物理点、真实时钟（阶段 7） | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 7 | 未实现 | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | `RUNTIME-HAL`（deferred，接现场必需） | L5-14 | 阶段 7 |
| USR-08 | 后续平台 | 现场 shadow / 受控写 | 现场安全放开写的受控流程 | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 7 | 未实现（Python shadow 核心已关闭，现场未验） | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | `RUNTIME-SHADOW-MODE`（in-progress） | L5-09/USR-07 | 现场验证 |
| USR-09 | 后续平台 | RETAIN / PERSISTENT 持久化 | 断电/重启保持型变量恢复（阶段 8） | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 8 | 仅建模（IR 属性预留，快照/恢复未做） | `无 WP` | 未涉及（Schema serializer 边界已合并） | 无 | 未验证 | 未验证 | `PLATFORM-RETAIN-1`（deferred，阶段 8） | L34-04 | 阶段 8 |
| USR-10 | 后续平台 | 在线监控 / 趋势 / 调试 | HMI 在线监视与趋势 | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) | 未实现 | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | `hmi_writable` 运行期写属后续 | L2-08 | — |
| USR-11 | 后续平台 | AI worker / IPC / AI-FB | 控制与 AI 分进程 + 共享内存/IPC（阶段 9，核心价值） | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) 阶段 9 | 未实现 | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | `PLATFORM-AI-DETERMINISM-1`（open） | USR-07 | 阶段 9 |
| USR-12 | 后续平台 | 部署 / 升级 / 回滚 | 现场部署与版本升级/回滚 | [PLATFORM_ROADMAP.md](PLATFORM_ROADMAP.md) | 未实现 | `无 WP` | 未涉及 | 无 | 未验证 | 未验证 | 现场发布须多级验证 | USR-07 | — |
| USR-13 | 后续平台 | 授权阶段二（按商业需要） | 阶段一“一机一码”之后的商业授权 | [../src/licensing/](../src/licensing/) | 仅建模（阶段一已实现，阶段二未做） | `无 WP` | 已合并（阶段一） | 部分覆盖（`LIC-*`） | 未验证 | 未验证 | `LIC-PHASE2-*`（open） | L2-07 | 阶段 10 |

### 3.8 ENG — 工程支持（非软 PLC 产品功能）

> 以下为**协作与工程基建**，明确**不是软 PLC 产品功能**；它们保障实施—审核可追溯，不进入产品能力度量。

| ID | 大类 | 小类/功能点 | 目的与作用 | 主要源码/权威入口 | 实现状态 | WP 状态 | Git 状态 | Python 验证 | PLC/CODESYS 验证 | HAL/现场验证 | 主要风险 ID/边界 | 依赖 | 下一步 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ENG-01 | 工程支持 | v2 三阶段协作（自审/交接/独立审核） | 实施方不审核自己的交接；九项交接门禁 | [AI_REVIEW_HANDOFF.md](AI_REVIEW_HANDOFF.md) 协议区 | 已实现 | `无 WP`（机制本身） | 已合并（主线 73b462b） | 部分覆盖（`test_ai_handoff`） | 未验证 | 未验证 | 非产品功能；受限环境端口权限假失败见 §5 | — | — |
| ENG-02 | 工程支持 | 工作包状态机 + 协调器 | 事件协调器串行唤醒；5 轮上限；exact-int/source-target 轮次准入与 JSONL 失败关闭 | [AI_REVIEW_HANDOFF.md](AI_REVIEW_HANDOFF.md)；[../tools/ai_handoff/parser.py](../tools/ai_handoff/parser.py)；[../tools/ai_handoff/scheduler.py](../tools/ai_handoff/scheduler.py) | 已实现 | `CLOSED`（WP-063/064 由 WP-073→074→075 正式收口） | 协议基线已合并；本轮代码待本次 PR | `test_ai_handoff` 202/202；物理 LF、Unicode bridge、空/非对象 JSON 与失败日志已覆盖 | 未验证 | 未验证 | 非产品功能；租约/轮次不扩大 scope/外部授权 | ENG-01 | 证据新鲜度/authority/pre-Popen 等仍是后续独立项 |
| ENG-03 | 工程支持 | Git / GitHub 收尾（Codex 执行） | 提交/推送由 Codex 审核并执行；Claude 不写 Git | [CODEX_GUIDE.md](../CODEX_GUIDE.md) §6 | 已实现（分工纪律） | `无 WP` | 已合并（主线 73b462b） | 无（流程约定） | 未验证 | 未验证 | Git 列只在实际操作成功后更新 | ENG-01 | — |
| ENG-04 | 工程支持 | 测试快照纪律 | 历史测试数字原样保留，不回写冒充当前 | [PROJECT_STATE.md](PROJECT_STATE.md) | 已实现（纪律） | `无 WP` | 已合并（主线 73b462b） | 无 | 未验证 | 未验证 | 环境差异不等于功能矛盾 | — | — |
| ENG-05 | 工程支持 | Claude 实施 Runbook 与启动器强制阅读 | 集中允许命令、历史易错项、v2 模板与停笔清单，并让首轮/返修 prompt 在写入前强制提示完整阅读 | [CLAUDE_IMPLEMENTATION_RUNBOOK.md](CLAUDE_IMPLEMENTATION_RUNBOOK.md)；[../tools/ai_handoff/scheduler.py](../tools/ai_handoff/scheduler.py) | 已实现 | `CLOSED`（WP-20260730-052 Round 1；收口 WP-20260730-051 Round 3 中断检查点） | 已合并（主线，PR #32，merge `252842f4…`） | 已覆盖（`ClaudeNamingTests` 27/27、`test_ai_handoff` 155/155；宿主正式 1568/1568、全仓 1636/1636） | 未验证 | 未验证 | **非产品功能**；Runbook 不证明模型正确，v2 自审与独立审核仍强制 | ENG-01/02 | 产品主线进入阶段 2 CFC 数据流定序编译器 |
| ENG-06 | 工程支持 | 双 AI 执行入口默认墙钟 | Codex/Claude adapter 默认主执行墙钟对称设为 60 分钟，显式 override 逐值透传 | [../tools/ai_handoff/scheduler.py](../tools/ai_handoff/scheduler.py)；[AI_HANDOFF_OPERATIONS.md](AI_HANDOFF_OPERATIONS.md) | 已实现 | `CLOSED`（WP-063 经 WP-073→074→075 收口） | 待本次 PR | 默认 3600、321/654 override、15 秒探针、dry/live 与短超时回归已覆盖 | 未验证 | 未验证 | `--max-turns=80`、`max_rounds=5`、`timeout_seconds=3600` 与账户额度独立 | ENG-01/02/05 | 外部服务可用性仍不由墙钟保证 |

---

## 4. 逐块信息质量（22 个库块）

> 补充 §3.2 / §3.3 的横切事实（每块共同点，避免逐行重复）：

- **业务目的 / 输出作用**：见 §3.2（8 原语）与 §3.3（14 业务块）各行“目的与作用”。
- **是否跨拍 / 关键依赖组合**（逐块，见 §3.2/§3.3 各行“目的与作用”）：8 原语**全部跨拍**（持有实例状态）；14 业务块中 **APCHSHLLIM 是唯一无跨周期判定状态的纯组合限幅块**（`APCHSHLLIM-HL2` 锁定；`APCHSHLLIM_SCHEMA.state_vars` 实盘为空；`self.AV` 仅存最近输出、不参与下一拍），其余 **13 块均跨拍**（持有实例状态：APCSTATISTICS running `MN/MX/AVG/COUNTER`、APCHSFOP `AV/Ok_1/AV_TEMP`、APCHSRATELIM `AV/AV_1`、APCHSACCUM 累积状态、APCHXHCL 历史窗口数组等）。关键组合：APCHXHCL 内嵌 TOF×2/R_TRIG；APCGCQ 内嵌 BLINK/R_TRIG/APCSTATISTICS/APCHSFOP/APCHSRATELIM/APCHSHLLIM；APCCD 内嵌 BLINK/R_TRIG×2/TON/APCSTATISTICS/APCHSFOP；APCPIDZZD 内嵌 TON×2/R_TRIG×2/APCHSACCUM/APCHSHLLIM 并构造注入 `license_context`；APCM 内嵌 BLINK/FOP/LIM/PID/RSF/CD 等多块并有整理组合关系；APCMAUTOPARA/APCRSFNAUTOPARA 复用真实 `APCSPFINDER` 实例；APCPID 内嵌 `PIDZZD1`。
- **adapter 目录状态**：22/22 engineering adapter 已 `CLOSED` 并经 [PR #24](https://github.com/yao501/PLC_to_Python/pull/24) 合并（`WP-20260728-040`，merge `8351fdf`）。
- **F2 不存在**：所有 22 项 `fidelity_f2` 变体一律 `MissingVariantError` fail-closed（`L2-06`）。
- **授权依赖**：**仅 APCM / APCPID / APCPIDZZD** 声明共享 `license_context`（`L2-07`）；其余 **19 项**不得虚构授权依赖。
- **各块真实风险/锁定边界**：见 §3 各行“主要风险 ID/边界”，展开引用 `RISKS.md` 对应系列（`APCM-*` / `APCPID-*` / `APCPIDZZD-*` / `APCHSFOP-H*` / `APCHSHLLIM-HL*` / `APCHSRATELIM-RL*` / `APCGCQ-GG*` / `APCCD-CD*` / `APCHSACCUM-AC*` / `APCSTATISTICS-S*` / `APCHXHCL-R*` / `APCSPFINDER-*` / `APCRSFNAUTOPARA-*` / `APCMAUTOPARA-*` / `PRIM-*` / `BLINK-B*`）。
- **验证边界（关键）**：Python 测试 / adapter 通过**只**证明 Python 主机行为，**不得**写成 CODESYS 或现场证明。
- **数值类型锁定**：`APCSTATISTICS.AVG` 为 **LREAL**；`APCHSACCUM.AV` 为 **LREAL**——**不得**回退为 REAL。
- **APCM 整理原子性**：APCM 的 `ZLEN` / `R_TRIG02` 原子整理已在 ST / Python 侧修复（[PR #19](https://github.com/yao501/PLC_to_Python/pull/19)），但 **CODESYS SP16.1 编译、仿真、趋势对拍、真机验证仍未完成**（`APCM-ZLOUT-1` 现场回路未对拍）。

---

## 5. monitor 历史检查点与当前测试证据

> **阅读规则**：第 1～5 项保留各工作包当时的历史检查点；第 6 项是 WP-048 收口口径，第 7 项是当前口径。工作包关闭、Git 合并、PLC 验证和现场验证是互不推导的状态轴。

1. **WP-20260729-043 候选**：产生 `src/runtime/monitor.py`、`src/runtime/__init__.py` 导出、`tests/test_runtime_monitor.py` 39 项定向测试和 `docs/RISKS.md` 叠加，均为**工作区候选**——**未提交、未合并、未审核通过**。该轮实施交接因结构化测试字段写成 `OK，Ran N`（而非 `Ran N tests, OK`）被项目解析器判定 `v2-invalid / handoff_gate_ok=false`。
2. **WP-20260729-044 状态**：已恢复合法 v2 测试证据（`Ran N tests, OK` 格式），但 **Codex Round 1 verdict = `CHANGES_REQUESTED`**（当前 `owner=claude / handoff_to=claude / round=1`）。两个**必须返修**项：
   - **一次性事件二次派发**：同一 active sequence 派发后可再次 `poll/dispatch`，得到两个 `sequence=1`、身份不同的事件，callback 被调用两次——可能造成同一超时周期**二次安全提交**（需把“本周期已锁存/派发”作为独立于 pending 槽的持久终态）。
   - **int 子类信任边界**：配置值与时钟返回值以 `isinstance(int)` 接受可重载运算的 `int` 子类，可绕过正值/单调性闸门并伪造事件（需改用 exact-int 边界，拒绝 `type(value) is not int`）。
3. **两套测试计数不可混用**：
   - **Claude 候选环境**（WP-043/044 实施轮）：`Ran 39 / 157 / 240 / 192 / 147 / 1480 / 68 / 1548 tests, OK`——**只能**作为该轮实施证据。
   - **Codex 受限审核环境**：本机随机端口被禁，`tests.test_ai_handoff` 147 项中同一组 **9 项**报 `PermissionError: Operation not permitted`；因此正式 `discover` 为 1480 中 9 errors（1471 通过）、全仓 `discover` 为 1548 中 9 errors（1539 通过）。
   - **不得**把 `1548` 写成最新已批准主线基线。
4. **该时点已合并且已关闭的完整主机基线**：为 **`WP-20260729-042`** 的正式 tests **1441/1441**、`prototype_05` **68/68**、全仓 **1509/1509**（经 [PR #26](https://github.com/yao501/PLC_to_Python/pull/26) 合并）。
5. **WP-20260729-046 返修候选（已创建、已实施至 Round 3=max_rounds，用户授权恢复最后一轮，待独立审核）**：本包**仅返修**契约缺陷，不新增功能、不改公开导出。**Round 1**（返修 WP-044 Codex 两项阻塞项）：① 同一 active sequence 事件二次生成 / callback 二次调用——已加**独立于 pending 槽**的 sequence 终态 `_latched_seq`，事件消费后同一序号不再生成 / 不再触发 callback，且不永久抑制后续合法周期；② `int` 子类信任边界——配置值与每次时钟返回值改为 **exact-int**（`type(value) is not int` 即失败关闭），先于任何数值比较 / 换算。**Round 2**（返修 Codex 本包 Round 1 阻塞项）：③ exact-int 拒绝路径 repr 反噬——原拒绝分支以 `%r` 格式化被拒的 `int` 子类，可重载 `__repr__` 在信任边界诊断阶段抛异常逃逸（`RuntimeError` 取代稳定的 `MonitorConfigError`/`MonitorClockError`）；已改为经 `_safe_type_name()` 只报告**可信类型名**、绝不 `repr` 不可信值（`bool` 分支不可继承故保留）。**Round 3**（返修 Codex 本包 Round 2 阻塞项）：④ 可信类型名经 metaclass 反噬——Round 2 的 `_safe_type_name()` 直接返回 `type(value).__name__`，自定义 metaclass 可重载类型对象 `__getattribute__`（或把 `__name__` 定义成数据描述符）令其返回 `__str__` 抛异常的恶意对象，诊断 `%s` 再次执行攻击者代码逃逸；已改为经 `type.__getattribute__` **绕过自定义 metaclass** 取候选类名、**仅当** `type(name) is str`（exact）才用、否则回退固定占位符 `"<unavailable>"`，`type(value)` 用单参内建 `type()`。`tests/test_runtime_monitor.py` 由 39 增至 **63**（Round 1 +11、Round 2 +6、Round 3 +7）。**Round 3 Claude 亲自宿主复跑真实计数（本机允许绑定本地端口，`test_ai_handoff` 全绿）**：`test_runtime_monitor` **63**、monitor+scan_runner+output_policy **181**、shadow+engine+scan_runner+output_policy+commit_supervisor **240**、parameters+executor **192**、`test_ai_handoff` **147**、`discover tests` **1504**、`prototype_05` **68**、`discover .` 全仓 **1572**，导入冒烟 `runtime-monitor-import-ok SoftwareCycleMonitor`，均 OK。此为**工作区候选**——**未提交、未合并、未经 Codex `APPROVED`、未经用户 `CLOSED`**；不得据本包升级实现/审核/Git/PLC/HAL 状态，`1572` 亦不得写成已批准主线基线。
6. **WP-048 收口口径（WP-20260729-048 / PR #28）**：WP-047 Round 3 暴露的 pending 公开事件别名读取已在 WP-048 收口；Claude Round 2 v2 自审/交接合法，Codex Round 2 独立审核 `APPROVED`，用户于 2026-07-29 确认 `CLOSED`。WP-043～048 累积 monitor、矩阵与 5 轮协议说明已通过 [PR #28](https://github.com/yao501/PLC_to_Python/pull/28) 合并（merge `c5031fff…`），最终宿主证据 monitor **96/96**、monitor+runner+policy **214/214**、安全运行时组 **240/240**、parameters+executor **192/192**、`test_ai_handoff` **147/147**、正式 tests **1537/1537**、`prototype_05` **68/68**、全仓 **1605/1605** 现为主线快照，导入冒烟通过。PLC/CODESYS、真实调度、HAL/物理 I/O、硬件 watchdog 与现场安全仍为未验证。
7. **最新主线口径（WP-20260730-050 / PR #30）**：在上述 monitor 主线基础上，阶段 1 单任务运行栈装配与手搭 TON E2E 已由 Claude v2 自审、Codex Round 3 独立审核 `APPROVED`、用户授权关闭，并经 [PR #30](https://github.com/yao501/PLC_to_Python/pull/30) 合并（merge `73b462b5…`）。最新完整宿主证据为正式 **1560/1560**、`prototype_05` **68/68**、全仓 **1628/1628**；L34-11 的 Git 状态现为已合并。真实调度、多任务、startup inhibit 计时/释放、CFC 定序、HAL/I/O、硬件 watchdog、CODESYS 与现场安全仍未验证。
8. **当前最新主线口径（WP-20260730-052 / PR #32）**：Claude 实施 Runbook、首轮/返修统一 prompt、`--max-turns 80` 与契约测试已由 Claude v2 自审、Codex 独立审核 `APPROVED`、用户关闭，并经 [PR #32](https://github.com/yao501/PLC_to_Python/pull/32) 合并（merge `252842f4…`）。最新完整宿主证据为正式 **1568/1568**、`prototype_05` **68/68**、全仓 **1636/1636**。该项是工程协作基建，不新增软 PLC 产品能力，不改变 PLC/CODESYS、HAL/现场验证状态。

---

## 6. 长期维护规则

> 本矩阵的更新流程（`CODEX_GUIDE.md` 有对应的长期强制规则条目）：

1. **创建工作包**时声明 `function_matrix_ids`（至少一个现有 ID；新增功能先按 §0.4 分配稳定 ID），并说明预期改变哪些状态轴。
2. **Claude 交接**时逐项列出实际影响的矩阵 ID；**只有实际修改状态时才更新矩阵**，不为每轮测试重复制造无意义行。
3. **Codex 审核**必须核对这些 ID 的实现/WP/Python/PLC/HAL 状态是否与证据一致；`CHANGES_REQUESTED` 不得提前写成完成。
4. **`APPROVED`、用户 `CLOSED`、Git 提交、PR 合并是不同事件**；**Git 列只有在实际 Git/GitHub 操作成功后**才能更新为已提交/已合并。
5. **Git/GitHub 收尾后的行政同步**负责写入真实 commit/PR 和主线测试快照；历史工作包测试数字**原样保留**。
6. **Python、PLC/CODESYS、HAL、现场四级验证永不互相推导**。
7. **`RISKS.md` 仍是唯一风险登记簿**；矩阵只引用风险 ID 和一行边界，不复制大段风险详情、不自行把风险标 `resolved`。
8. **功能状态发生实质变化时**同步矩阵；只改说明文字或历史叙事时**不强制更新**。
9. **每次新会话开工**除 `PROJECT_STATE.md` 外，按任务读取矩阵中涉及的 ID，不要求无关任务全文读取本矩阵。
