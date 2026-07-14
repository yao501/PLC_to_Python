# 项目状态快照（PROJECT_STATE）

> **用途**：跨会话记忆载体。每个 AI 会话开始时**先读本文件**（配合 `CODEX_GUIDE.md` 长期工作方针）。
> **更新纪律**：仅当阶段、版本、完成项、阻塞项或下一步发生**实质变化**时更新，不做无意义编辑；只保留"当前状态 + 决策索引 + 下一步"，不写过程叙事；超过 150 行就该精简。
> 最后更新：2026-07-14（**阶段 1 前两个正式代码工作包已关闭**：`WP-20260713-002` 完成 L3 IR 内存模型与装载期静态校验；`WP-20260714-003` 完成声明制 Store、装载期实例展开、原子输入锁存、输出待提交容器与 `prev` 快照基础，Fable5 Round 1 实施后由 Codex 独立复核 `APPROVED`，用户确认 `CLOSED`。持久 Store 键 `<实例全路径>.<变量名>` 已作为**项目工程约定**写入 `IR_SPEC` v2.2.4，它不是 CODESYS / IEC 官方命名语义。最新 Python 验证记录为 Store 24/24、IR 56/56、正式 tests 770/770、0.5 原型 68/68、全仓 838/838，**只证明当前 Python 运行时内存底座与静态校验行为，不证明与目标 PLC 语义一致**。下一个聚焦工作包为显式顺序 IR 指令执行与调用帧，之后再进入五步扫描和安全服务。）

---

## 1. 项目一句话

把 CODESYS SP16.1 软 PLC 复刻为 Python 原生软 PLC 平台（ST+CFC 双前端 → 语言无关可执行 IR → 扫描引擎），已迁移 14 业务块 + 8 原语作标准库，并建立了正式 L3 IR、静态校验、Store、实例布局与过程映像基础（最近全仓测试 838 项，见 §2），目标是控制+AI 同平台一体化（分进程）。

## 2. 当前位置

- **验证证据**（2026-07-14，Codex 对 `WP-20260714-003` Round 1 独立复跑）：定向 `tests.test_runtime_store` = **24/24 通过**，`tests.test_runtime_ir` = **56/56 通过**；正式 tests = **770/770 通过**；0.5 原型 = **68/68 通过**；全仓 = **838/838 通过**。这些结果证明当前 Python 内存底座、过程映像基础、IR 模型与静态校验行为未回退，不证明与目标 PLC 语义一致。

- **阶段 0.5（语义基线修订）**，文档侧已完成**三轮**外部评审（ChatGPT5.5）修正；评审方判断"主体架构已站住，无需再大规模文档重构"。
- 规格版本：`IR_SPEC` **v2.2.4**（0.5 冻结基线 v2.2.2 + 阶段 1 `StackSlot.index` / 持久 Store 键两项工程约定写回）/ `ENGINE_SCAN_SPEC` **v2.2.2** / `COMPONENT_CONTRACT` **v2.1** / `TARGET_PROFILE` **v1.3** / `GOLDEN_TRACE_FORMAT` **v1.2.1**。`STAGE0_DESIGN.md` 已标历史文档，不再更新。
- **阶段 1 前两个正式工作包已完成**（`WP-20260713-002` / `WP-20260714-003`，均于 2026-07-14 `CLOSED`）：已有正式 IR 值对象、装载期静态校验、声明制 Store 与隔离快照、PROGRAM/用户 FB 实例布局、原子输入锁存、输出待提交容器及 80 项运行时定向测试（56 IR + 24 Store）。尚未实现显式顺序执行器、调用帧、五步扫描、OutputPolicy 或安全服务。
- **0.5 可执行验证原型已完成并经两轮定向返修**（Fable5 实施，`prototype_05/`，一次性代码）：最小指令集 + TON 经描述符 + BOOL OutputPolicy + ST/CFC 双路径同指令列表跑 24 拍 + 5 个语义敏感案例。Codex 首轮 6 条（驱动异常提交隔离、绑定 actual 类型、OutputPolicy 校验、无 LPC 基准、纯整数 DIV/MOD、文档对齐）+ 二轮 2 条（Binding 表结构校验：重复 formal/非法 actual_kind/const 值类型；安全配置 NaN/Infinity/整数范围拒绝）均修复，每条有反证测试（`prototype_05/tests/test_review_rework.py`）。
- **下一步（按序）**：① 建立阶段 1 第三个聚焦工作包：显式顺序 IR 指令执行、求值栈与调用帧；② 在执行器上组装五步扫描循环；③ 再接入 OutputPolicy 和安全服务。外部依赖继续并行：真机黄金轨迹实采；后续导出样本中的含环 `.export` `IsFeedbackStart` 对照（可选）、多任务/GVL、自定义 FB 样本（清单见 FINDINGS.md）。

## 3. 文档权威地图（谁说了算）

| 主题 | 权威文件 |
|---|---|
| 阶段/里程碑/原型范围 | `docs/PLATFORM_ROADMAP.md` |
| IR 指令集/类型规则/POU 模型/lowering | `docs/IR_SPEC.md` |
| 一拍时序/OutputPolicy/输出两层状态/能力边界 | `docs/ENGINE_SCAN_SPEC.md` |
| 块描述符/注册表/省略语义 | `docs/COMPONENT_CONTRACT.md` |
| 一致性等级 E/F1/F2、数值语义基线 | `docs/TARGET_PROFILE.md` |
| 对拍轨迹格式/采集清单 | `docs/GOLDEN_TRACE_FORMAT.md` |
| 风险/待办登记簿（唯一） | `docs/RISKS.md` |
| 编码规则 | `.cursor/rules/00a`、`04-platform-runtime.mdc` |
| AI 协作纪律（长期方针） | `CODEX_GUIDE.md`（本文件是短期快照，职责不混写） |

## 4. 决策索引（2026-07-12 冻结评审已裁决并经写回复核生效；细节看对应权威文件）

- D1 外挂描述符（块零改动）；D2 programs 列表顺序；**D3 载体分支（2026-07-12 裁决）**：PLCopen XML 保留显式 `executionOrderId`（首选载体）、.export 自动模式序号须重建（算法未冻结，未就绪拒绝生成可执行 IR）、新建图拓扑定序；D4/D5 数值双模式 E/F1/F2；D-AI 控制与 AI 分进程。
- **OutputPolicy 物理基准与复位（2026-07-12 裁决，项目工程约定）**：边界首拍基准 = 可信反馈优先，否则 `safe_value`；`last_physical_committed` 不冒充反馈；`channel_fault` 锁存、显式复位（`ENGINE_SCAN_SPEC` v2.2.2 §4.1/§4.4）。
- IR：全类型化指令 + TypedValue 栈 + 加载期类型验证；POU 定义与实例分离（实例装载期展开，调用不建实例）；PROGRAM/用户 FB 持久 Store 键 = `<实例全路径>.<变量名>`（项目工程约定，非 CODESYS/IEC 官方命名语义）。
- F1 = 边界量化（F1-expr/F1-boundary 两子行为），**不承诺** bit-exact；F2 = 位级候选须真机证明；模式禁止热切换。
- OutputPolicy 按故障原因分策略，safety/scan_fault/watchdog 强制 safe；last_effective / last_physical_committed 两层。
- 注册表键 `(block_type, variant)`；Pin 省略语义四值枚举。

## 5. 未决/阻塞项（详见 RISKS.md 三-A）

- ⬜ 用户补（余一项）：是否要 F2。~~CPU/OS、CFC 顺序模式、Patch 级别~~ 均已由导入试验两份样本补齐（`TARGET_PROFILE` v1.3，标"样本工程实测"）。
- 🟨 真机黄金轨迹实采：外部阻塞，需 SP16.1 环境（格式已就绪 v1.2.1，含 #7 整数溢出裁决用例）。
- 🟥 `int_intermediate_policy`、CFC 反馈边映射、REAL 中间精度：均为待真机裁决假设。
- ✅ 0.5 原型两轮返修完成（2026-07-05，55/55 全绿——历史子集计数，最新记录 68/68 见 §2），**Codex 代码复核已通过**（PLC 一致性未证明，属阶段 6 范围）。~~评审裁决遗留：`PLATFORM-OUTPUT-BASELINE-1`~~✅ 已于 2026-07-12 冻结评审通过并写回 `ENGINE_SCAN_SPEC` v2.2.2（属项目工程约定、非 CODESYS 官方语义，阶段 7 HAL 实现与现场验证未完成）；有符号回绕作待真机假设保留。
- ✅ 真实 CODESYS 导出最小导入试验完成（2026-07-09，`PLATFORM-IMPORT-TRIAL-1` done，见 `prototype_05/import_trial/FINDINGS.md`）。
- ⏸ `PLATFORM-CFC-AUTOORDER-1`（deferred/mitigated，2026-07-12）：D3 已裁决为载体分支并写回；.export 自动模式重建算法延后阶段 5，未就绪时导入器拒绝生成可执行 IR。
- 🟨 `PLATFORM-CFC-FEEDBACK-MAP-1`：首份真实反馈环样本已到（入环元素序号最小、回接输入读上一拍，与 `LOAD_PREV` 假设方向一致）；PLCopen 载体无显式反馈标记，`IsFeedbackStart` 落点对照（同工程 .export）为可选项，不阻塞冻结。
- ✅ Git 基线与仓库清洁：Codex 已于 2026-07-13 审查暂存范围并实际运行 68/690/758 三组测试；基线提交 `63e79fc` 已通过 PR #1 合并至 `main`（merge commit `3bff318`），基线后治理状态同步通过 PR #2 落入 `main`；此前已被跟踪的两个 `.DS_Store` 已在独立清洁任务中停止跟踪，本机文件继续由 `.gitignore` 排除。

## 6. 新会话启动方法（给用户）

开新会话时用统一开场（与 `CODEX_GUIDE.md §5` 一致，替换任务名即可）：

> 先读 `CODEX_GUIDE.md` 和 `docs/PROJECT_STATE.md`，再只读本任务相关的权威文件。当前任务：〈任务，如"实现 0.5 可执行验证原型"〉。

- **一个会话 = 一个工作包**（如"写原型"、"写 ST 词法"、"评审反馈修订"）。工作包完成且状态有实质变化时更新本文件，再关闭会话；别在同一会话里连做多个阶段。
- 评审往返（贴外部反馈→修文档）单独开会话。
- 会话内明显变慢时，先把当前进度写进本文件再开新会话——只要状态与规格及时更新，信息损失可以很低（不是零：对话中的推理过程与未落档的备选方案会丢，重要结论务必落档）。
