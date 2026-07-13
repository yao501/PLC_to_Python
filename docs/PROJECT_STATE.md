# 项目状态快照（PROJECT_STATE）

> **用途**：跨会话记忆载体。每个 AI 会话开始时**先读本文件**（配合 `CODEX_GUIDE.md` 长期工作方针）。
> **更新纪律**：仅当阶段、版本、完成项、阻塞项或下一步发生**实质变化**时更新，不做无意义编辑；只保留"当前状态 + 决策索引 + 下一步"，不写过程叙事；超过 150 行就该精简。
> 最后更新：2026-07-13（**阶段 0.5 语义基线：已冻结通过**——冻结评审 2026-07-12 有条件通过后，权威文档写回经三轮自动"实施—审核"往返（`AI_REVIEW_HANDOFF.md::WP-20260712-001`），Codex Round 3 只读复核 **APPROVED**（2026-07-13 00:14，scope SHA-256 起止一致、无漂移），冻结条件"写回 + 一次只读复核"已全部满足。**边界不变：冻结的是工程语义基线（含项目工程约定），不是"Python 与 CODESYS PLC 语义一致"——PLC 一致性属阶段 6 对拍范围**。**当前动作：用户确认 WP CLOSED + 授权建"阶段 0.5 综合基线"Git 提交，然后进阶段 1**。冻结时须明确三句话：① 本试验证明的是"真实导出最小导入可行"、非"Python 与 PLC 语义一致"；② PLCopen XML 为阶段 5 导入器候选首选载体（显式带 executionOrderId）；③ LOAD_PREV/反馈映射已有正面证据，但 .export `IsFeedbackStart` 落点、真机黄金轨迹、REAL/整数细节仍属后续验证项）

---

## 1. 项目一句话

把 CODESYS SP16.1 软 PLC 复刻为 Python 原生软 PLC 平台（ST+CFC 双前端 → 语言无关可执行 IR → 扫描引擎），已迁移 14 业务块 + 8 原语作标准库（最近记录测试基线 690 项，本阶段未重跑，见 §2 验证证据），目标是控制+AI 同平台一体化（分进程）。

## 2. 当前位置

- **验证证据**（2026-07-12 实际运行，Fable5 写回返修轮复跑）：既有基线 `python -m unittest discover -s tests -t .` = **690/690 通过**；原型 `python -m unittest discover -s prototype_05 -t .` = **68/68 通过**（55 原型 + 5 样本一回归锁 + 8 样本二回归锁）。原型与试验零改动 `src/`。与 2026-07-09 记录一致。

- **阶段 0.5（语义基线修订）**，文档侧已完成**三轮**外部评审（ChatGPT5.5）修正；评审方判断"主体架构已站住，无需再大规模文档重构"。
- 规格版本：`IR_SPEC` **v2.2.2** / `ENGINE_SCAN_SPEC` **v2.2.2**（2026-07-12 冻结裁决写回）/ `COMPONENT_CONTRACT` **v2.1** / `TARGET_PROFILE` **v1.3** / `GOLDEN_TRACE_FORMAT` **v1.2.1**。`STAGE0_DESIGN.md` 已标历史文档，不再更新。
- **0.5 可执行验证原型已完成并经两轮定向返修**（Fable5 实施，`prototype_05/`，一次性代码）：最小指令集 + TON 经描述符 + BOOL OutputPolicy + ST/CFC 双路径同指令列表跑 24 拍 + 5 个语义敏感案例。Codex 首轮 6 条（驱动异常提交隔离、绑定 actual 类型、OutputPolicy 校验、无 LPC 基准、纯整数 DIV/MOD、文档对齐）+ 二轮 2 条（Binding 表结构校验：重复 formal/非法 actual_kind/const 值类型；安全配置 NaN/Infinity/整数范围拒绝）均修复，每条有反证测试（`prototype_05/tests/test_review_rework.py`）。
- **下一步（按序，①② 已完成）**：~~① Codex 代码复核~~✅ → ~~② CODESYS 最小导入试验~~✅（2026-07-09，`prototype_05/import_trial/FINDINGS.md`）→ ~~③a Codex 审核导入试验~~✅ → ~~③b 0.5 冻结评审~~✅（2026-07-12 **有条件通过**：`PLATFORM-OUTPUT-BASELINE-1` 通过并冻结为项目工程约定；D3 载体分支裁决）→ ~~③c 权威文档写回~~✅（同日，Fable5 实施：`ENGINE_SCAN_SPEC` v2.2.2 / `IR_SPEC` v2.2.2 / ROADMAP / RISKS / 本文件）→ ~~③d Codex 定向只读复核~~✅（经 `AI_REVIEW_HANDOFF.md::WP-20260712-001` 三轮自动往返，R3 APPROVED 2026-07-13，**冻结生效**）→ **④ 用户确认 WP CLOSED + 审查基线提交范围 → 用户授权建"阶段 0.5 综合基线"Git 提交（当前步）** → 进阶段 1。遗留小项（不阻塞，待新工作包）：`.cursor/rules/04-platform-runtime.mdc:28` 旧 D3 表述与 v2.2.2 分叉（scope 外，Codex 建议单独开包）；交接协议补幂等校验 + scope SHA-256 条款（Codex 非阻塞建议）。外部依赖（可并行）：真机黄金轨迹实采；后续导出样本仅剩显式顺序 .export（用户已裁决暂缓）、含环 .export 的 `IsFeedbackStart` 对照（可选）、多任务/GVL、自定义 FB 样本（清单见 FINDINGS.md；反馈环与 FB 实例框已由样本二采齐，不再列为依赖）。

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

## 4. 决策索引（2026-07-12 冻结评审已裁决；细节看对应文件，会话里不重新讨论；冻结待写回复核后生效）

- D1 外挂描述符（块零改动）；D2 programs 列表顺序；**D3 载体分支（2026-07-12 裁决）**：PLCopen XML 保留显式 `executionOrderId`（首选载体）、.export 自动模式序号须重建（算法未冻结，未就绪拒绝生成可执行 IR）、新建图拓扑定序；D4/D5 数值双模式 E/F1/F2；D-AI 控制与 AI 分进程。
- **OutputPolicy 物理基准与复位（2026-07-12 裁决，项目工程约定）**：边界首拍基准 = 可信反馈优先，否则 `safe_value`；`last_physical_committed` 不冒充反馈；`channel_fault` 锁存、显式复位（`ENGINE_SCAN_SPEC` v2.2.2 §4.1/§4.4）。
- IR：全类型化指令 + TypedValue 栈 + 加载期类型验证；POU 定义与实例分离（实例装载期展开，调用不建实例）。
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
- ⏸ Git 基线提交：用户已决定**0.5 原型完成 + 冻结评审通过后**建立"**阶段 0.5 综合基线**"（含文档+原型+此前迁移代码；当前约 41 处未提交变更）。**建基线前必须**：运行完整测试并记录命令/日期/结果；审查暂存范围不盲目 `git add .`；排除 `.DS_Store` 等无关文件；未实际跑测试不得声称当前全绿。此后按 diff 审核。

## 6. 新会话启动方法（给用户）

开新会话时用统一开场（与 `CODEX_GUIDE.md §5` 一致，替换任务名即可）：

> 先读 `CODEX_GUIDE.md` 和 `docs/PROJECT_STATE.md`，再只读本任务相关的权威文件。当前任务：〈任务，如"实现 0.5 可执行验证原型"〉。

- **一个会话 = 一个工作包**（如"写原型"、"写 ST 词法"、"评审反馈修订"）。工作包完成且状态有实质变化时更新本文件，再关闭会话；别在同一会话里连做多个阶段。
- 评审往返（贴外部反馈→修文档）单独开会话。
- 会话内明显变慢时，先把当前进度写进本文件再开新会话——只要状态与规格及时更新，信息损失可以很低（不是零：对话中的推理过程与未落档的备选方案会丢，重要结论务必落档）。
