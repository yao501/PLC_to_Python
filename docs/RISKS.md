# 风险与待完善事项登记簿

本文件是项目**唯一的、正式的**待完善事项与已知风险登记簿。
每次交付后必须同步更新此文件；严禁把风险点只写在对话里或散落在 docstring。

> 最后一次更新对应任务书：`STATISTICS_修正版语义说明_与_Python改写任务书`（ST 基线：`/Users/guangyaosun/Desktop/statistics.txt`）

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

## 六、业务块未来扩展

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **BLOCK-NEXT** | 下一批业务块迁移 | recommended | 🟥 open | 用户发来 ST/CFC 源后按 `02-business-blocks` 规则接入。 |
| **BLOCK-TEMPLATE** | 业务块模板脚手架 | nice-to-have | 🟥 open | 将来业务块多了后，可以抽一个带 step 接口/测试骨架的模板。 |

---

## 七、更新约定

每次完成交付后，**必须**：

1. 把本轮新增/变化的风险点写入对应分类；
2. 把已解决项从 🟥/🟨 推进到 🟩/🔒；
3. 在"最后一次更新对应任务书"行注明任务书文件名；
4. 相关条目必须指向具体文件 / 测试 / 任务书锚点，禁止只写"以后再说"。

如果一个延后项跨多个阶段才能关闭，建议拆成子条目分别跟踪。
