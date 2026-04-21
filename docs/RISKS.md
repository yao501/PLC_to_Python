# 风险与待完善事项登记簿

本文件是项目**唯一的、正式的**待完善事项与已知风险登记簿。
每次交付后必须同步更新此文件；严禁把风险点只写在对话里或散落在 docstring。

> 最后一次更新对应任务书：`H6 收尾补充任务书（负 TB / 负 TC）`
> （`APCHSFOP` H6 收尾：从 in-progress 推到 deferred，定性为项目级参数契约）

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

## 六、业务块未来扩展

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **BLOCK-NEXT** | 下一批业务块迁移 | recommended | 🟥 open | 用户发来 ST/CFC 源后按 `02-business-blocks` 规则接入。 |
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
