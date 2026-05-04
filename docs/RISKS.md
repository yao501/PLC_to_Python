# 风险与待完善事项登记簿

本文件是项目**唯一的、正式的**待完善事项与已知风险登记簿。
每次交付后必须同步更新此文件；严禁把风险点只写在对话里或散落在 docstring。

> 最后一次更新对应任务书：`APCGCQ 命名说明小清理`（移除 GG9：`APCGCQ1` 是软 PLC 复制副产品，不构成项目命名约定；同步降级相关测试与 README 表述）
> 上一次：`APCGCQ_复核约定与Cursor执行说明.md`（小范围加固：固化 TIMEHIGH=500ms 项目修正约定 + 复核 GG1/GG2/GG5/GG6 措辞 + 补 4 条锁定测试）
> 上上次：`APCGCQ（观测器，MMYZ）业务块迁移`
> （CFC：`/Users/guangyaosun/Desktop/GCQ.docx`；ST 转换稿：`/Users/guangyaosun/Desktop/CGCQ1.txt`）

---

## 条目分类

| 分类 | 含义 |
|---|---|
| **blocker** | 不解决则上现场/接设备前**必须**处理 |
| **recommended** | 建议在对应阶段完成，有明确收益 |
| **nice-to-have** | 非关键，行有余力再做 |
| **accepted** | 已明确接受的"保留行为"，不允许静默修改 |

## 状态图例

| 状态 | 含义 |
|---|---|
| 🟥 open | 尚未处理 |
| 🟨 in-progress | 正在处理 / 部分处理 |
| 🟩 resolved | 已完成，记录归档备查 |
| 🔒 locked | 明确保留、不改，测试已锁死行为 |
| ⏸ deferred | 明确延后到某阶段 |

---

## 一、APCHXHCL 业务块相关（R 系列）

| ID | 标题 | 分类 | 状态 | 详情 / 处理方式 |
|---|---|---|---|---|
| **APCHXHCL-R1** | `A > TL` 的混合单位语义 | accepted | 🔒 locked | `TL` 在 `TOF1/TOF2.PT_ms` 中按秒使用；在 `A > TL` 中按**源块周期阈值语义**保留。原 CODESYS FB 作者已明确声明保留。`src/blocks/apchxhcl.py` docstring 锁死契约，`tests/test_blocks_apchxhcl.py::TestR1ContractLocked` 测试锁死"A 按扫描计数，不按秒"行为。如未来需要时间尺度统一，必须在上游 ST 同步修改。 |
| **APCHXHCL-R3** | 冷启动 / 刚使能即故障 | recommended | ⏸ deferred (Runtime 阶段) | `EN=FALSE` 清空缓存，`EN=True` 第一拍如果立刻故障，冻结均值会来自全零或未填满窗口。**本块层不引入 warm-up 语义**——由 Runtime 阶段 `system_ready` / `startup_inhibit_ms` / output gate 闭环。`tests/test_blocks_apchxhcl.py::TestR3ColdStartScenarios` 记录两种事实行为（刚使能立刻故障 vs 运行满一分钟后故障）。待 Runtime 门控上线后，应补集成测试验证"门控后不会输出未成熟均值"。 |
| **APCHXHCL-R4** | `REAL_TO_INT(60/TB)` 跨语言差异 | recommended | 🟩 resolved | 已收口到 `src/compat/conversions.real_to_int`（银行家舍入，与 IEC 61131-3 默认一致），业务块调用点全部接入；`src/validation.check_tb_sample_n_integer` 提供 warning；`tests/test_compat_conversions.py::TestRealToInt` + `tests/test_validation.py::TestCheckTbSampleNInteger` 锁死。**遗留浮点限制**：TB=0.3 时 `60/0.3` 在 Python 浮点下恰好等于 200.0，warning 探测不到——需要靠文档 / 使用约定规避（建议 TB 取 60 秒的精确约数：0.5 / 1.0 / 2.0 / 5.0 / 6.0 / 10.0 等）。 |
| **APCHXHCL-R5** | 有效样本判定不对称 | accepted | 🔒 locked | `FV_AVG` 只统计 `FV > 0.1`；`PV_AVG` 只统计 `PV != 0`。保留原业务习惯。`tests/test_blocks_apchxhcl.py::TestPreservedBehaviorsLocked::test_r5_*` 锁死。 |
| **APCHXHCL-R6** | `AV_TEMP` 爆值时冻结 | accepted | 🔒 locked | 超出 `(-1e10, 1e10)` 时不更新 `AV / Ok_1`。`test_r6_av_temp_freeze_when_explodes` 锁死。建议主程序监控这一场景并上报。 |
| **APCHXHCL-R7** | `PV == PV_1` 严格相等 | accepted | 🔒 locked | A 增长依赖严格 `==`，不接受 epsilon。若输入前已经做过浮点运算，可能恒为 FALSE → A 永不增 → 漏检持续不变化。`test_r7_strict_equality_pv_eq_pv1` 锁死。上游若需要去抖，应在传入 APCHXHCL 之前做量化。 |
| **APCHXHCL-R9** | 故障期间 A / TOF 继续推进 | accepted | 🔒 locked | 数组冻结，但 A 和 TOF1/TOF2 按原逻辑继续。`test_r9_tof_continues_during_fault_latching` + `test_r9_a_continues_during_fault` 锁死。 |
| **APCHXHCL-PERF-1** | 数组右移性能 | nice-to-have | 🟥 open | `SAMPLE_N=120` 时每拍两次 `O(N)` 拷贝，Python 层不是热点；高频场景可换 ring buffer。任务书明确本轮不动。 |
| **APCHXHCL-ROUND-1** | CODESYS 真实 REAL_TO_INT 与 Python 对比 | nice-to-have | 🟥 open | 当前约定"银行家舍入"与 IEC 默认一致；未来应与真实 CODESYS 运行时跑对比，对齐所有边界值（尤其 `x.5` / 负数）。如有差异，改 `src/compat/conversions.real_to_int` 一处即可。 |

（标号延续 v2 版本；v1 时期遗留的 R2 / R4(旧) / R8 等已因结构重写而消除。）

---

## 二、基础原语契约相关

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **PRIM-INT-MS** | 定时器统一 int ms 接口 | blocker | 🟩 resolved | `src/primitives/timers.py` 已全部使用 `int ms`；`step(dt_ms, IN, PT_ms) -> (Q, ET_ms)` 已稳定。 |
| **PRIM-R3-COLDSTART** | 边沿检测冷启动保护 | blocker | 🟩 resolved | IEC 语义保留，首拍 `CLK=TRUE` 会触发 Q=TRUE；冷启动保护由主程序 `system_ready` 门控（见 RUNTIME-GATE）。`tests/test_primitives.py::TestRTrigPlusSRColdStartPattern` 记录推荐闭环模式。 |
| **PRIM-PT-VALIDATE** | `PT_ms` 周期量化 warning | blocker | 🟩 resolved | `src/validation.check_pt_ms` 已落地，`tests/test_validation.py::TestCheckPTMs` 覆盖。 |
| **BLINK-B1** | `ENABLE=FALSE` 时内部相位冻结（项目工程约定） | accepted | 🔒 locked | **本项目工程约定**（非官方源码确认、非风险）：`ENABLE=FALSE` 时除 `OUT` 保持外，`_elapsed_ms` **同步冻结**，下一次 `ENABLE=TRUE` 从冻结点续跑。文档 / 代码 / 测试三处口径一致。`tests/test_primitives_blink.py::TestBlinkEnableFalseKeepsState` + `TestBlinkReenableResumesFromFrozenPhase` 锁死。 |
| **BLINK-B2** | 单拍跨多相位（已修复） | recommended | 🟩 resolved | 原实现"每拍最多翻一次"在 `dt_ms > min(TIMELOW_ms, TIMEHIGH_ms)` 时会导致波形失真。现已改为 `while` 循环逐相位消费 `_elapsed_ms`，单拍可跨任意多个相位。唯一相关护栏见 `BLINK-B4`，其职责仅限**保证状态机在退化输入下可终止**。`tests/test_primitives_blink.py::TestBlinkMultiPhaseCrossing` 锁死（含 `dt=1000 / period=200` / `dt=450 / period=200` / `dt=350 / threshold=100` 三类跨多相位用例）。|
| **BLINK-B4** | `TIMELOW_ms = 0` / `TIMEHIGH_ms = 0` 的退化行为 | accepted | 🔒 locked | 参数非负由 `RUNTIME-PARAM-VALIDATION` 上层兜底。块内部对退化情形只有一道**防死循环护栏**：`TIMELOW_ms + TIMEHIGH_ms <= 0` 时本拍不推进 `_elapsed_ms`、`OUT` 保持。**该判断仅用于防止状态机在退化输入下进入不可终止循环；不构成块内参数合法化，不替代项目级参数校验契约，也不是业务语义兜底。** 单侧为 0（另一侧 > 0）属合法退化，`while` 中 `threshold<=0` 分支让该相位"立即翻转"，不消耗 `_elapsed_ms`，在单拍内通过另一相位的正阈值完成扣减。`tests/test_primitives_blink.py::TestBlinkDegenerateZeroPeriod` 锁死。 |

---

## 三、Runtime / MainProgram 阶段延后项（重点清单）

> 本阶段按契约明确延后，不得偷偷塞进业务块内部。当前 `docs/RISKS.md` 为**唯一**的延后项集中登记处。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **RUNTIME-GATE** | 输出安全链 `system_ready AND output_enable AND safety_ok AND interlock_ok AND request` | blocker | ⏸ deferred | 契约 00a 已规定此为主程序的强制职责。所有业务块产出 `request`（逻辑请求），由主程序门控后才形成 `final_output`。 |
| **RUNTIME-STARTUP-INHIBIT** | 冷启动稳定窗口 `startup_inhibit_ms` | blocker | ⏸ deferred | 项目默认 500ms；释放还需叠加 `io_ready / bus_ready / comm_ready / safety_ok` 等条件。 |
| **RUNTIME-5-STEPS** | 主程序五步式：输入快照 → FB 推进 → request 生成 → 输出门控 → 一次性提交 | blocker | ⏸ deferred | 契约已规定，尚未写代码骨架。 |
| **RUNTIME-SAFETY-DEFAULT** | 扫描异常 / 超时 / 主循环失败时物理输出落到安全默认值 | blocker | ⏸ deferred | 需要 watchdog + fallback state 机制。 |
| **RUNTIME-SHADOW-MODE** | Shadow mode / write disable | blocker | ⏸ deferred | 首次接现场设备前必须先具备"只读取不写"的模式。 |
| **RUNTIME-HAL** | 硬件抽象层 | recommended | ⏸ deferred | 现场 I/O 对接、协议驱动、时钟源。需要与具体部署环境一起决策。 |
| **RUNTIME-WATCHDOG** | 扫描周期看门狗 | recommended | ⏸ deferred | 扫描超时时的安全响应。 |
| **RUNTIME-INTEGRATION-TESTS** | APCHXHCL 与 Runtime 门控的集成测试 | recommended | ⏸ deferred | RUNTIME-GATE + APCHXHCL-R3 联调后补集成测试，验证"门控有效期内冻结的均值不会误导下游"。 |
| **RUNTIME-PARAM-VALIDATION** | 业务参数配置装载层（统一非负校验） | blocker | ⏸ deferred | **所有业务块参数契约的统一落地位置**。待 Runtime 阶段建立配置装载入口后，集中实现对业务参数的非负 / 范围 / 类型校验。<br/><br/>**（A）当前已确认契约（必须落地）**：<br/>• `TB ≥ 0`、`TC ≥ 0`（承接 `APCHSFOP-H6`、以及 `APCHXHCL` 中同名参数）<br/>• 所有定时器 `PT_ms ≥ 0`（现有 `check_pt_ms` 逻辑归并到此）<br/>• `60 / TB` 整数性（现有 `check_tb_sample_n_integer` 归并到此）<br/><br/>**（B）未来可能的项目增强约束（非当前契约，需独立立项讨论后再决定是否采纳）**：<br/>• `TB > 0`（严格正，用于避免零除；当前 H6 契约**不包含**此条，`APCHSFOP` 的 `(TB+TC)>0.001` 门槛在 `TB=0` 且 `TC` 足够大时仍可工作）<br/>• 其他未来业务块新增的参数约束<br/><br/>建议实现为"加载期集中校验 + 启动前硬拦截"模式（与 `check_*` 的 warning 模式区分开），参数非法直接拒绝启动，而不是运行时降级。对应契约见 `00a-runtime-contract.mdc` R7 第 7 条。<br/><br/>**边界提示**：上述 (A) 与 (B) 不得混写 —— (A) 是已锁定的 H6 / 项目契约，落地 RUNTIME-PARAM-VALIDATION 即可直接实现；(B) 需要独立走"新增契约"流程，不得以 H6 收尾名义顺带植入。 |

---

## 四、测试与工程基建

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **TEST-CI** | 引入持续集成 | recommended | 🟥 open | 目前只有本地 `python -m unittest discover`，未配置 GitHub Actions 或等价 CI。 |
| **TEST-COVERAGE** | 覆盖率报告 | nice-to-have | 🟥 open | 考虑接入 `coverage.py`，对每次交付给出覆盖率阈值。 |
| **TOOL-LINT** | 统一 linter / formatter | recommended | 🟥 open | 建议 `ruff` + `black`（或只用 ruff 兼替）。 |
| **TOOL-TYPE-CHECK** | 类型检查 | nice-to-have | 🟥 open | 建议 `mypy` 或 `pyright`，当前类型注解已写得较完整。 |
| **DOC-ARCH** | 架构总览文档 | recommended | 🟥 open | `README.md` 已有概览；缺一张完整的"主程序 → 业务块 → 原语"数据流图。 |

---

## 五、APCSTATISTICS 业务块相关（S 系列）

> 本块已切换为**修正版 ST 语义**（ST 基线：`/Users/guangyaosun/Desktop/statistics.txt`；
> 任务书：`STATISTICS_修正版语义说明_与_Python改写任务书.md`）。
> 原始 `STATISTICS.txt` 的 4 处"怪癖"均由修正版 ST 从源头消除，Python 侧直接对齐修正版，
> **不再保留任何原始怪癖行为**。

| ID | 标题 | 分类 | 状态 | 详情 / 处理方式 |
|---|---|---|---|---|
| **APCSTATISTICS-S1** | `MN/MX` 声明初值 与 RESET 赋值统一 | recommended | 🟩 resolved | 修正版 ST 已把声明初值与 RESET 分支都改为 `±3.402823466E+38`。Python 侧 `__init__` 与 RESET 均使用 `REAL_MAX/REAL_MIN`。`tests/test_blocks_apcstatistics.py::TestInitialState::test_declared_init_matches_reset_values` 锁死一致性。 |
| **APCSTATISTICS-S2** | 删除 `SUM` 死变量 | recommended | 🟩 resolved | 修正版 ST 已删除。Python 实例**不应**有 `SUM` 字段。`TestRevisionDecisionsLocked::test_no_sum_field_on_instance` / `test_instance_fields_are_minimal` 锁死。 |
| **APCSTATISTICS-S3** | 删除 `COUNTER//2` 防溢出分支 | recommended | 🟩 resolved | 修正版 ST 已删除。Python 侧利用 `int` 无限精度直接累积。`TestLongRunAccumulation::test_counter_passes_two_billion_threshold_without_halving` / `test_counter_passes_ulint_wrap_threshold` 锁死"跨越 2e9 不减半"行为。 |
| **APCSTATISTICS-S4** | `AVG` 改用 Welford 增量公式 | recommended | 🟩 resolved | 修正版 ST 已改为 `AVG := AVG + (IN - AVG) / N`，与原累计算术平均**数学等价**但浮点更稳定。Python 侧实现一致。`TestWelfordFormula::test_two_samples_match_welford` / `test_matches_arithmetic_mean_for_reasonable_length` 锁死。 |
| **APCSTATISTICS-S5** | `COUNTER` 从 `DINT` 改为 `ULINT` | recommended | 🟩 resolved | 修正版 ST 已采用。Python 用 `int`，语义视为无符号累计计数。无溢出分支相关副作用。 |
| **APCSTATISTICS-S6** | `AVG` 从 `REAL` 改为 `LREAL` | recommended | 🟩 resolved | 修正版 ST 已采用。Python 用原生 `float`（IEEE 754 双精度，等同 `LREAL`）。 |
| **APCSTATISTICS-S7** | `step` 不使用 `dt_ms` | accepted | 🔒 locked | 本块无时间依赖；`step(dt_ms, ...)` 签名保留以匹配主程序统一调度。`TestStepContract::test_step_ignores_dt_ms` 锁死。 |
| **APCSTATISTICS-S8** | `RESET=True` 当拍不采样 | accepted | 🔒 locked | 任务书 §2.1 明确：RESET 分支只清空状态，不把当前 IN 纳入统计。`TestResetBranch::test_reset_does_not_sample_current_in` 锁死。 |
| **APCSTATISTICS-NaN** | 无 NaN / Inf 防御逻辑 | accepted | 🟥 open | 任务书 §6.6 明确禁止本轮加入 NaN/Inf 保护。上游若可能产生非有限值，应在 APCSTATISTICS 之前做卫生化。未来如需收口，应在此处新增独立条目并改动代码前同步更新任务书。 |

---

## 五-B、APCHSFOP 业务块相关（H 系列，任务书修订版）

> 一阶惯性滤波（IIR low-pass）。ST 源：`/Users/guangyaosun/Desktop/HSFOP.txt`。
> 公式与 APCHXHCL 内嵌滤波段完全一致。
> **本节口径按 `hsfop_risk_and_time_semantics_cursor_task.md` 任务书重写**：
> H1 / H2 / H3 / H7 是 ST 原语义（保留，非 bug）；H4 降级为说明项；
> H5 已泛化为项目级规则（见 00a 契约 R7 条）；H6 改为项目级参数契约。

| ID | 标题 | 分类 | 状态 | 详情 / 处理方式 |
|---|---|---|---|---|
| **APCHSFOP-H1** | 首拍从 `Ok_1=0` 爬升 | accepted | 🔒 locked | **ST 原行为，非缺陷。** 首拍 `AV = α·KG·IN`，**不等于** `IN`，从 0 起爬，约 `TC/TB` 拍到 63.2% 稳态。通用 `APCHSFOP` 内部**不应**引入 `INIT_OK`；若某业务上下文需要"首拍贴输入"，应由外层主程序/业务上下文门控实现（例如业务上下文用 `system_ready` 延迟放 EN）。APCHXHCL 在其内部自己用 `INIT_OK` 是**它自己**的设计，不适用于通用滤波块。`tests/test_blocks_apchsfop.py::TestFirstTick` 锁死首拍数值。 |
| **APCHSFOP-H2** | `\|AV_TEMP\| ≥ 1e10` 时冻结 `AV / Ok_1` | accepted | 🔒 locked | **ST 原语义。** 注意：这是**冻结**（保持上一合法值），**不是限幅/饱和/clamp**——不会把输出裁到 `±1e10`。实现顺序严格为：(1) 算 `AV_TEMP`；(2) 判定 `|AV_TEMP| < 1e10`；(3) 通过才一起提交 `AV = AV_TEMP`、`Ok_1 = AV`。异常值监控由主程序承担。`TestGuardAvTempExplodes` 锁死行为。 |
| **APCHSFOP-H3** | `(TB + TC) ≤ 0.001` 时整拍跳过 | accepted | 🔒 locked | **ST 原语义。** 整段逻辑不执行，`AV / Ok_1 / AV_TEMP` 保持原值。**不是告警后继续算，也不是自动替换参数**。`TestGuardTbPlusTcTooSmall` 锁死。 |
| **APCHSFOP-H4** | `AV_TEMP` 的 RETAIN 冗余 | nice-to-have | 🟩 resolved（说明项） | **降级为说明项**。真正的关键跨周期状态是 `AV` 与 `Ok_1`。`AV_TEMP` 每拍都被覆盖，ST 侧标 RETAIN 对语义无影响；Python 侧保留为实例属性仅为调试/观察，不作高优先级风险跟踪。 |
| **APCHSFOP-H5** | `TB` 与 `dt_ms` 的关系（**已泛化**） | project-rule | 🟩 resolved（已上升为契约 R7） | **原表述"`TB ≠ dt_ms/1000` 就是风险"不准确。修订口径**：`TB` 是 FB 显式输入脚，按 PLC 输入脚语义取值（有外部赋值用外部值，无赋值用声明默认 `0.5`）；`TB` **不天然等于** runtime 的 `dt_ms/1000`。真正的风险是"实现或文档把 `dt_ms` 错误替代显式输入脚 `TB`"——这条已泛化为 **00a 契约 R7 条**，作为项目级规则，不再作为本块单独风险。本次排查未发现代码层面误绑（见本文件末尾"排查结论"）。`TestTbDecoupledFromScanCycle` 新增测试锁死"`TB` 与 `cycle_ms` 不对齐时 FB 仍按 FB 语义正确工作"。 |
| **APCHSFOP-H6** | 负 `TB` / 负 `TC` → 项目级参数契约 | project-contract | ⏸ deferred（pending-runtime-validation） | **H6 收尾定论**：这**不是**业务块内部 bug，也**不是**待修复的块内逻辑缺陷。已定性为**项目级参数契约**，契约内容：`TB ≥ 0`、`TC ≥ 0`（可推广到一切业务时间参数）。此契约已写入 **`00a-runtime-contract.mdc` R7 第 7 条**。<br/><br/>**`APCHSFOP` 本体保持 ST 原语义**：只保留原有 `(TB+TC) > 0.001` 门槛，**不**在块内部新增任何"负值保护型"分支/限幅/自动修正/兜底替换。<br/><br/>**代码层校验延后到 Runtime 阶段统一落地**（见本文件 `RUNTIME-PARAM-VALIDATION` 条目），理由：当前项目没有统一的配置装载层，孤立增加一个 `check_nonneg_time` 会与现有 `check_pt_ms`/`check_tb_sample_n_integer` 风格不一致且无统一调用点，反而劣化结构。<br/><br/>**H6 不再作为 `APCHSFOP` 业务块内部待修复问题**。 |
| **APCHSFOP-H7** | 无 `EN` / `RESET` 端口 | accepted | 🔒 locked | **设计边界，非缺陷。** 符合 00a 契约 R2 条："基础/通用块接口纯净"——是否执行、是否复位由主程序/运行时统一门控。**本次任务不给 `APCHSFOP` 擅自加 EN/RESET**。 |

---

## 五-C、APCHSHLLIM 业务块相关（HL 系列）

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCHSHLLIM-HL1** | `LL > HL` 时块内静默修正为 `LL := HL` | accepted | 🔒 locked | 当 `LL > HL` 时，源码**不是交换上下限，也不是报错**，而是 `LL := HL`。因此输出区间退化为单点 `HL`；Python 必须复现该静默修正。该修正**不写回**实例状态——下一拍传入参数仍是调用方原值（与 ST `VAR_INPUT` 值传递一致）。`TestAPCHSHLLIMSilentLLFix` + `TestAPCHSHLLIMSilentLLFix::test_collapsed_to_single_point_all_three_in_branches` 锁死。 |
| **APCHSHLLIM-HL2** | 无跨周期判定状态 | accepted | 🔒 locked | 无跨周期判定状态。`self.AV` **仅**保存最近一次输出，不能参与下一拍计算。相同 `IN/HL/LL` 输入重复调用必须得到相同 `AV`。`TestAPCHSHLLIMDtMsIgnored` + `TestAPCHSHLLIMStateless` + `TestAPCHSHLLIMStateless::test_self_av_does_not_affect_next_tick` 锁死。 |
| **APCHSHLLIM-HL3** | `HL` / `LL` 不做 `ABS`、不做正负校验 | accepted | 🔒 locked | `HL` / `LL` **不做 ABS**，**不做正负校验**，**不做异常抛出**。若 `HL` / `LL` 为负数，也按源码 `LL>HL` 比较与 `IF/ELSIF` 赋值语义执行。**注意**：本块的 `HL/LL` 是**幅值参数**（区间端点），任意符号组合都合法（含合法负区间，如温度调节器输出在 `[-20.0, -10.0]`）；这与时间类参数 `TB / TC / PT_ms` 的 R7.7 非负契约**不是同一回事**，不归 `RUNTIME-PARAM-VALIDATION` 兜底。`TestAPCHSHLLIMNegativeRangeIsLegal` + `TestAPCHSHLLIMSilentLLFix::test_negative_range_with_inverted_limits` 锁死。 |

---

## 五-D、APCHSRATELIM 业务块相关（RL 系列）

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCHSRATELIM-RL1** | `HL` / `LL` 是每拍**变化量正幅值**，不是输出上下限区间 | accepted | 🔒 locked | `HL/LL` 是每拍变化量正幅值，**不是输出上下限区间**。源码通过 `IN - AV_1` 判断本拍变化量，因此 `HL/LL` 不能被理解为输出 `AV` 的上下边界。`HL=LL` 即对称速率限幅（GCQ 中的实际用法）。`TestAPCHSRATELIMGCQUsageSymmetric` + `TestAPCHSRATELIMBasicClamp` + `TestAPCHSRATELIMCrossCycleState` 锁死。 |
| **APCHSRATELIM-RL2** | 冷启动 `AV_1=0.0`，首拍可能被限速 | accepted | 🔒 locked | 源码等价冷启动 `AV_1=0.0`，首拍**可能被限速，不保证直通 `IN`**。若业务场景要求首拍直通，**必须由上层显式预置 `AV_1 := IN`，不能在块内部隐式改变源码语义**——即不允许在 `__init__` / `step` 内加"首拍自动直通"优化。`TestAPCHSRATELIMColdStart::test_first_tick_does_not_pass_large_input_directly` 锁死。 |
| **APCHSRATELIM-RL3** | 块内 `ABS()` 容错（必须保留） | accepted | 🔒 locked | `HL/LL` **必须**在块内执行 `ABS()`，**不能依赖调用方预先传入正数**。源码明确写了 `HL:=ABS(HL); LL:=ABS(LL)`，Python 实现必须保留 `abs()`。该容错只影响本拍计算，不写回输入参数。`TestAPCHSRATELIMSilentAbs` 锁死。 |
| **APCHSRATELIM-RL4** | 与 `dt_ms` 解耦：每次调用限制变化量，不按时间换算物理速率 | accepted | 🔒 locked | 源码**按每次调用限制变化量**，**不按 `dt_ms` 换算物理速率**。`dt_ms` 仅为统一 `step` 接口占位，不参与计算。虽然块名叫"速率限制"，但源码中没有时间参数；这里的速率限制本质是"每拍变化量限制"。`TestAPCHSRATELIMDtMsIgnored` + `TestAPCHSRATELIMDtMsIgnored::test_two_instances_different_dt_same_state_same_output` 锁死。 |
| **APCHSRATELIM-RL5** | 每拍重新判方向，不保存方向状态 | accepted | 🔒 locked | 每拍**重新基于 `delta = IN - AV_1` 判断方向**；**不保存方向状态**，上一拍上升/下降不影响本拍分支。源码每拍只看当前 `IN` 与上一拍 `AV_1` 的差值。等号边界严格按源码：`>` 与 `<`（不是 `>=` / `<=`），即 `delta == HL` / `delta == -LL` 时走 ELSE 分支直通 `IN`。`TestAPCHSRATELIMCrossCycleState::test_direction_change_independent_per_cycle` + `TestAPCHSRATELIMStrictBoundaryComparators` 锁死。 |

---

## 五-E、APCGCQ 组合业务块相关（GG 系列）

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCGCQ-GG1** | ST 执行顺序锁定（核心不变量） | accepted | 🔒 locked | 在采样事件那一拍（`R_TRIG.Q=True`），ST 严格按以下顺序执行：(1) `JZ_ZUP1 := JZ_ZUP`（旧）；(2) `JZ_ZUP := JZ_Z`（**上一拍** `STAT01.AVG` 的旧值，因为这一拍 `STAT01` 还没被调用）；(3) 调用 `STAT01.step(RESET=True)`；(4) `JZ_Z := STAT01.AVG`（RESET 后 = 0）。**核心要点**：采样事件那一拍，`JZ_ZUP` 必须取**旧** `JZ_Z`，然后才执行 `STAT01(IN, RESET=rtrig_q)` 并更新新的 `JZ_Z`；**不得**先调 `STAT01.step` 再赋值 `JZ_ZUP1/JZ_ZUP`。错误顺序会让 `JZ_ZUP` 被当拍 RESET 后的新统计值（=0）污染，破坏"两个完整窗口均值差"语义，导致 FOP 输入符号反向、AV 走向相反方向。`TestSTOrderingLocked` + `TestSTOrderingViaForcedDifference` + `TestSamplingSnapshotBeforeStatReset` 锁死。 |
| **APCGCQ-GG2** | 源码死区条件 `IN<INSP AND IN>INSP` 必须按源码保留 | accepted | 🔒 locked | 该条件正常**恒 False**——这是源码行为，必须按源码原样保留；按 `SEL(G, IN0, IN1)` 语义，`G=False` 时返回 `IN0 = (JTAV+DTAV)*K`，`G=True` 时返回 `IN1 = 0`，因此实际链路总是走 `(JTAV+DTAV)*K`。**严禁擅自改写为以下任何形式**：(a) `IN != INSP`；(b) `ABS(IN-INSP) > threshold`；(c) `IN <= INSP or IN >= INSP`；(d) 其他业务条件或死区判据。**也不要把它当 bug 自行"修正"**——这是 CFC 原作者刻意保留的钩子，未来若要恢复死区控制，由后续任务专门处理，本轮不动。`TestKMultiplierAndDeadbandSEL::test_in_equals_insp_still_takes_in0` + `TestSelConditionPreservedAsSourceFalseBranch` 锁死。 |
| **APCGCQ-GG3** | `BLINK01.ENABLE` 直通使能：本块不暴露 ENABLE 输入 | accepted | 🔒 locked | 原 CFC 是密码验证段才置 `ENABLE=TRUE`，本块按用户确认**暂时直通**——`BLINK01.ENABLE` 在 step 内固定 `True`，本块**不暴露** `EN` / `auth_ok` / `password` 等端口。**如需停用采样节奏，应由上层 Runtime / Controller 控制本块是否被调度**（即不调用 `step()`），而**不是**反向改写 APCGCQ 输入接口去新增 ENABLE 端口。密码验证段（CFC 顶部 `BC_MMYZ_BT (10000) → MOD → EQ → ... → BC_ERROR3`）由 Runtime 阶段单独实现，详见 `APCGCQ-GG8`。 |
| **APCGCQ-GG4** | `BLINK01.TIMEHIGH` 固定 500 ms（项目修正约定） | accepted | 🔒 locked | **项目修正约定**：`BLINK01.TIMEHIGH` 在本块按 500 ms 实现。旧 ST 转换稿中曾出现 `T#300MS`，但本项目实现**以 500 ms 为准**——不评判源 TXT 对错，只锁定项目落点。本块以模块级常量 `BLINK_TIMEHIGH_MS = 500` 固定为字面量，**不暴露为 GCQ 输入**。`TIMELOW` 仍为 `REAL_TO_TIME(TC*1000)`（`TC` 单位为秒）。**采样窗口周期**应按 `TC*1000 + 500` ms 理解（**而不是** `TC*1000 + 300`）。`TestSamplingEventSpacing::test_blink_timehigh_constant` + `TestSamplingEventSpacing::test_blink_timehigh_uses_project_500ms` 锁死。 |
| **APCGCQ-GG5** | `RLIM01.HL = LL = OUTV` 对称速率限幅；`OUTV` 不是输出上下限 | accepted | 🔒 locked | 主链路为 `(JTAV+DTAV)*K → APCHSRATELIM(IN=..., HL=OUTV, LL=OUTV) → APCHSHLLIM(IN=..., HL=OUTH, LL=OUTL) → GCAV`。**两层语义必须分清**：(1) `OUTV` 是**每拍变化量限制**，且上升 / 下降对称（按 `APCHSRATELIM-RL1`，`HL/LL` 是正幅值，不是上下区间）；(2) **最终输出幅值上下限**由 `LIM01` 使用 `OUTH/OUTL` 完成。**严禁把 `OUTV` 当作 `GCAV` 的输出上下限**，也不允许未来误改成 `LL := -OUTV`。`TestRLIMSymmetricRateLimitInChain` + `TestLIMAmplitudeLimitInChain` + `TestOutvIsRateLimitAndOuthOutlAreAmplitudeLimits` 锁死。 |
| **APCGCQ-GG6** | `FOP01.TB` 不传，沿用 APCHSFOP 声明默认值 0.5 s | accepted | 🔒 locked | ST 中 `FOP01(IN:=..., TC:=TZ*2, KG:=1)` 没传 `TB`，按 R7.7 输入脚语义，使用 `APCHSFOP` 的 ST `VAR_INPUT` 声明默认值 `0.5` 秒。本块通过模块级常量 `FOP01_DEFAULT_TB_SEC = 0.5` 显式传入。**单点真值同步约定**：若 `APCHSFOP` 默认 `TB` 调整，本常量必须同步更新——这是单点真值约定的失效场景，需要登记为长期注意项。`TestFOP01DefaultsLocked::test_fop01_default_tb_is_half_second` + `test_first_sampling_av_uses_alpha_kg_in` 锁死。 |
| **APCGCQ-GG7** | 嵌套 FB 实例命名复用 ST 实例名 | accepted | 🔒 locked | `BLINK01 / R_TRIG1 / STAT01 / FOP01 / RLIM01 / LIM01` 与 ST 源码完全一致，便于与原始 CFC/ST 图纸追踪对应关系。其中 `STAT01` 的类是 `APCSTATISTICS`（ST 中实例命名为 `STATISTICS_REAL`，按用户确认即同一类的实例化）。**不允许重命名实例属性**——任何重命名都会破坏与 CFC 的可追溯性。 |
| **APCGCQ-GG8** | 控制器验证段（`BC_ERROR3`）暂不实现 | accepted | ⏸ deferred | CFC 顶部有一段独立的"控制器验证"逻辑，最终输出诊断 `BC_ERROR3`。用户已确认：(1) 与 GCQ 主通路解耦，**不影响**任何 GCQ 内部变量；(2) `BC_ERROR3` 是上层控制器健康字，**不属于** GCQ 接口；(3) 本轮**不迁移**该段。如未来需要，作为独立模块迁移即可，不应与 GCQ 耦合。 |

> **非风险备注（不进风险登记）**：源材料 `CGCQ1.txt` 顶部出现的 `FUNCTION_BLOCK APCGCQ1` 不是业务功能块的真实命名，也不是迁移风格选择——`APCGCQ1` 是**软 PLC 复制功能块时为避免重名自动生成的名称**。本项目要实现的功能块名就是 `APCGCQ`，因此 `src/blocks/apcgcq.py` / `class APCGCQ` / `from src.blocks import APCGCQ` 是直接对应业务名的正确实现，不存在"两种候选命名"的项目级决策需要登记为风险。

---

## 六、业务块未来扩展

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **BLOCK-NEXT** | 下一批业务块迁移 | recommended | 🟨 in-progress | 用户发来 ST/CFC 源后按 `02-business-blocks` 规则接入。**当前进度**：`APCHXHCL` / `APCSTATISTICS` / `APCHSFOP` / `APCHSHLLIM` / `APCHSRATELIM` / `APCGCQ`（观测器组合块）已迁移；下一批待用户提供 ST/CFC 源。 |
| **BLOCK-TEMPLATE** | 业务块模板脚手架 | nice-to-have | 🟥 open | 将来业务块多了后，可以抽一个带 step 接口/测试骨架的模板。 |

---

## 七、排查结论存档

### 2026-04-20 HSFOP H5 泛化排查结论

**起因**：`hsfop_risk_and_time_semantics_cursor_task.md` 任务书要求把
`APCHSFOP-H5` 从"单块风险"上升为"项目级时间语义规则"，并排查既有
实现/文档中是否存在同类误解。

**排查范围**：

- `.cursor/rules/` 全部 5 份规则文件（00/00a/01/02/03）
- `src/primitives/` 全部 3 个原语文件（timers/edges/latches）
- `src/blocks/` 全部 3 个业务块文件（apchxhcl/apcstatistics/apchsfop）
- `src/compat/conversions.py` / `src/validation.py` / `src/config.py`
- `README.md` / `docs/RISKS.md`
- `cursor提示词功能/` 下 5 份提示词源文件（人读参考，非生效规则）

**代码层面**：

- 所有业务块的显式时间输入脚（`APCHXHCL.TB/TC/TL`、`APCHSFOP.TB/TC`）
  均以**关键字参数**暴露在 `step` 签名上，由调用方显式传入，**未**被 `dt_ms`
  隐式替代。
- 基础原语（`TON/TOF/TP`）的 `PT_ms` 亦是显式关键字参数，`dt_ms` 只负责
  驱动内部 `ET_ms += dt_ms`，**未**与 `PT_ms` 混用。
- **代码层面未发现 H5 同类误绑**。

**文档/注释层面**：发现 3 处"过宽表述"已修正：

| 位置 | 原表述 | 修正后 |
|---|---|---|
| `src/blocks/apchsfop.py` docstring | "调用方有义务保证 `TB ≈ dt_ms/1000`" | 显式输入脚语义；对齐是业务配置决策，不是契约强制 |
| `src/blocks/apchxhcl.py` docstring | "主程序应保证 `TB * 1000 == cycle_ms`" | 同上，改为业务配置建议，并说明 TB 可以解耦扫描周期 |
| `docs/RISKS.md` `APCHSFOP-H5` 条目 | "`TB ≠ dt_ms/1000` 时时间常数偏离" | 按任务书口径重写，并指向 00a 契约 R7 条 |

**规则层面**：新增 **00a 契约 R7 条**——《业务块显式时间输入脚 vs runtime
`dt_ms`》，明确 5 条硬约束 + Cursor 自检清单。

**提示词源文件**：未发现把"显式时间脚 = dt_ms/1000"写死的绝对化表述；
提示词 `01_1基础原语功能块迁移提示词1.md` 里的所有"时间统一 int ms"
都仅针对**基础原语的 PT_ms 接口**，不会误导到业务块的业务参数。

**结论**：

- 代码层面无需修复（原设计正确）；
- 文档/风险表层面的 3 处过宽表述已全部修正；
- 项目级规则 R7 已在 00a 契约落地，作为后续所有业务块迁移的必过检查项；
- 新增测试 `TestTbDecoupledFromScanCycle` 锁死"`TB` 与 `cycle_ms` 不对齐"
  的正确行为。

---

## 八、更新约定

每次完成交付后，**必须**：

1. 把本轮新增/变化的风险点写入对应分类；
2. 把已解决项从 🟥/🟨 推进到 🟩/🔒；
3. 在"最后一次更新对应任务书"行注明任务书文件名；
4. 相关条目必须指向具体文件 / 测试 / 任务书锚点，禁止只写"以后再说"。

如果一个延后项跨多个阶段才能关闭，建议拆成子条目分别跟踪。
