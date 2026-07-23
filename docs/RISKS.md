# 风险与待完善事项登记簿

本文件是项目**唯一的、正式的**待完善事项与已知风险登记簿。
每次交付后必须同步更新此文件；严禁把风险点只写在对话里或散落在 docstring。

> 最新更新对应任务书：`WP-20260722-011 不可信驱动回执精确标量类型门禁`（2026-07-22）。Codex 原反证曾证实自定义 `int` 子类可在 `_evaluate()` IEC 值域比较中漏出普通 `RuntimeError`并绕过故障记账。现已在不可信回执进入任何值域/有限性/相等运算前建立 exact `bool/int/float/str` 类型门禁，且在两文件 scope 内以恶意 `int/float/str` 子类、多通道隔离与精确锁存升级反证收口。Claude 五组测试 172/166/1172/68/1240 全绿；Codex 独立复核 `APPROVED`、必须返修=无，scope 聚合 SHA-256 始终为 `e2bfd12ea91f7bc3ce807fc3e0ce8d151789e6d210cf9b33c810ec852e7fb3ea`，`git diff --check` 通过。风险 `PLATFORM-DRIVER-RECEIPT-TYPE-1` 转已解决；真实 HAL/驱动、shadow、硬件 watchdog 与现场安全证明仍是独立未验证边界。
>
> 上一次更新对应任务书：`PLATFORM-IMPORT-TRIAL-1 最小导入可行性试验`（2026-07-09，用户提供 SP16.1 真实导出 → 解析识别全部成功（POU/ST 源码/CFC 图/连线/反馈标记字段/顺序模式/任务配置/目标设备），`IMPORT-TRIAL-1` 转 ✅；**实测发现**：自动数据流模式导出不含每元素执行序号 → 新增 **PLATFORM-CFC-AUTOORDER-1**（D3 需评审修订）；`TARGET_PROFILE` 升 v1.3 补齐 CPU/OS（Win x64）、CFC 顺序模式、任务配置三项 ⬜（标"样本工程实测"）；试验代码与回归锁在 `prototype_05/import_trial/` + `tests/test_import_trial.py`（原型测试 60/60、既有基线 690/690，2026-07-09 实际运行）；未改 `src/`，未执行 Git 操作。**追加（同日样本二）**：用户提供 **PLCopen XML** 导出（反馈环 + TON 实例框，采集清单②③ ✅）→ `parse_plcopen.py` 识别成功 + 8 项回归锁；实测：PLCopen XML **显式存储 executionOrderId**（`CFC-AUTOORDER-1` 获缓解，阶段 5 候选首选载体）、**无**显式反馈标记字段（`CFC-FEEDBACK-MAP-1` 转 🟨）、**Patch 级别 = SP16 Patch 1**（`TARGET_PROFILE` v1.3 补齐）。**仍未完成**：0.5 冻结评审、.export 反馈标记落点对照（可选）、真机实采）。
>
> 上一次更新对应任务书：`阶段0.5 可执行验证原型 + 两轮定向返修`（Fable5 实施、Codex 只读审核：新增一次性原型 `prototype_05/`（最小指令集 / TON 经描述符 / OutputPolicy / ST+CFC 双路径合流跑 24 拍 / 5 个语义敏感案例）；Codex 首轮 6 条意见（驱动异常提交隔离、绑定 actual 类型闭环、OutputPolicy 校验、冷 shadow→实写无 LPC 基准、纯整数 DIV/MOD、治理文档对齐）与二轮 2 条遗留缺口（Binding 表结构：重复 formal/非法 actual_kind/const 值类型；安全配置 NaN/Infinity/整数范围）全部修复并有反证测试；测试证据 55/55 原型 + 690/690 既有基线（2026-07-05 实际运行）；`EXEC-IR-1` 更新为"原型已证明、待评审冻结"，新增 **PLATFORM-OUTPUT-BASELINE-1**（含审核方建议裁决语义）与 **PLATFORM-IMPORT-TRIAL-1**（外部依赖）；未改 `src/`，未执行任何 Git 操作。**仍未完成**：Codex 三轮复核、CODESYS 最小导入试验、冻结评审、真机实采、目标画像待定项）。
>
> 更早更新对应任务书：`阶段0.5 语义基线修订（设计物落地）`（据第二轮评审"只立项未执行"的指正，实际产出阶段 0.5 设计物：新增 `docs/TARGET_PROFILE.md`（目标画像+一致性等级 E/F1/F2）、`docs/GOLDEN_TRACE_FORMAT.md`（轨迹格式+采集计划+外部阻塞记录）；`IR_SPEC` 升 v2（源模型/可执行 IR 指令集分离 + POU 模型 + fidelity/engineering 双模式类型系统，消除"默认64位/阶段6裁决"与决策表的矛盾）；`ENGINE_SCAN_SPEC` 升 v2（`OutputPolicy` 取代 BOOL 门控、CFC 改"导入保留不重新推断"）；`COMPONENT_CONTRACT` 升 v2（描述符补 版本/状态/初始化/持久性/输入省略/序列化 字段）；**修正 `.cursor/rules/04-platform-runtime.mdc`**（不再写"IR 已冻结/旧 D3D4D5/AI 同进程"，改新决策——此为评审指出的最危险矛盾）；风险表 `EXEC-IR-1/TARGET-PROFILE-1/OUTPUT-POLICY-1/POU-MODEL-1` 转 🟨、`GOLDEN-EARLY-1` 转 🟨（格式就绪、实采待外部）；未改 `src/`、未加测试，未执行任何 Git 操作。**仍未完成**：评审冻结、真机实采、用户确认目标画像待定项）。
>
> 更早更新对应任务书：`阶段0 外部评审纳入 + 阶段0.5 语义基线修订立项`（据 ChatGPT5.5 对阶段0设计的评审：阶段0 由"已冻结"退回"概念设计完成"，新增**阶段 0.5 语义基线修订**为进阶段1的工程冻结闸门；路线图 `PLATFORM_ROADMAP.md` 增阶段 0.5；`STAGE0_DESIGN.md` 修订 D3/D4/D5、新增 §11、退回验收状态；三份规格 `IR_SPEC/ENGINE_SCAN_SPEC/COMPONENT_CONTRACT` 标注"待阶段0.5冻结"；风险表新增 **PLATFORM-EXEC-IR-1 / TARGET-PROFILE-1 / OUTPUT-POLICY-1 / POU-MODEL-1 / GOLDEN-EARLY-1**，修订 **PLATFORM-IR-1 / REAL-FIDELITY-1 / AI-DETERMINISM-1**；未改动 `src/`、未加测试，未执行任何 Git 操作）。
>
> 上一次更新对应任务书：`平台演进路线图 + 阶段0设计稿 + 平台级风险登记`（新增 `docs/PLATFORM_ROADMAP.md`（项目定位从"功能块迁移"转向"Python 原生软 PLC 平台"，支持 ST+CFC、与 AI/Python 一体化运行）与 `docs/STAGE0_DESIGN.md`（IR / 引擎一拍时序 / 组件契约 / 类型系统 / 反馈环设计稿，含决策点 D1~D5）；新增风险表 **三-A 平台演进 PLATFORM-IR-1 / RETAIN-1 / REAL-FIDELITY-1 / ST-CONFORMANCE-1 / RT-JITTER-1 / AI-DETERMINISM-1**；本次仅新增文档与风险登记，未改动 `src/`、未加测试，未执行任何 Git 操作）。
>
> 最新更新对应临时高优先级任务：`APCM ZLEN 限位整理原子化修复`（同步修复现场 ST 与 `src/blocks/apcm.py`；Claude 独立审核 `APPROVED_WITH_CONDITIONS`、P0/P1 无，P2 测试/落档建议已由 Codex 收口；APCM **63 用例**、正式 tests **1182/1182**、全仓 **1250/1250** 通过）。尚未完成 CODESYS SP16.1 导入/编译/仿真或真机验证，禁止把 Python 结果表述为现场安全证明；未执行任何 Git 操作。

> 上一次更新对应任务书：`APCMAUTOPARA（APCM 自动参数推荐）迁移`（新增 `src/blocks/apcmautopara.py` + 导出 + `tests/test_blocks_apcmautopara.py` 60 用例；新增风险表 **五-M APCMAUTOPARA-CYCLE-1/RESET-1/SPFINDER-1/DATAREASON-1/MANRESP-1/HISTORY-1**；窗口统计+PID/RSF/观测器/重叠控制四组单窗口推荐+历史三阶段融合，复用 `APCSPFINDER` 真实子实例，纯状态/数值块、无授权门控、不接 `LicenseContext`、时间严格来自输入 `CYCLE`；**关键与 APCRSFNAUTOPARA 差异**：顶层 `IF EN`（非 `EN AND NOT RESET`），`EN=True&RESET=True` 时先复位后本拍仍采集；`DATA_REASON=2` 死分支按源保留、不实时写；`MAN_RESP_ACTIVE` 可跨窗口保留；推进 BLOCK-NEXT；未执行任何 Git 操作）。
>
> 上一次更新对应任务书：`APCRSFNAUTOPARA（RSFN 自动参数推荐）迁移`（新增 `src/blocks/apcrsfnautopara.py` + 导出 + `tests/test_blocks_apcrsfnautopara.py` 64 用例；新增风险表 **五-L APCRSFNAUTOPARA-CYCLE-1/RANGE-1/RESET-1/RUNNING-1/SPFINDER-1/FUSION-1/CALC-1/DATAREASON-1/START-1**；窗口统计+历史三阶段融合，复用 `APCSPFINDER` 真实子实例，纯状态/数值块、无授权门控、不接 `LicenseContext`、时间严格来自输入 `CYCLE`；**仅采纳 ChatGPT5.5 修复版的 Bug1**：`RESET-1`（RESET 现清 `WIN_SP/PV/AV_SUM`，真实数据正确性缺陷）更新为 **fixed**；`DATAREASON-1`（`DATA_REASON=2` 死分支）的 Bug2 实时补丁**经双方复核撤回、不同步**（会破坏"最近完成窗口快照"语义），仍 **accepted/locked** 按源保留；正式基线为"只修 Bug1"。当前窗口先入库后同拍融合、弱推荐→`RSF_REASON=5`、`START-1` 冷启动零基准等仍按源码保留；推进 BLOCK-NEXT；未执行任何 Git 操作）。
>
> 上一次更新对应任务书：`APCSPFINDER（分析用设定值自动寻找）迁移`（新增 `src/blocks/apcspfinder.py` + 导出 + `tests/test_blocks_apcspfinder.py` 46 用例；新增风险表 **五-K APCSPFINDER-CYCLE-1/EN-1/HOLD-1/RESET-1/EDGE-1/ANALYSIS-1**；纯状态/数值块、无 FB 依赖、无授权门控、不接 `LicenseContext`、`del dt_ms` 时间严格来自输入 `CYCLE`；EN 仅控制自动稳定段寻找、RESET 不提前返回、不稳定/SAMPLE_OK=False 保留历史自动 SP；推进 BLOCK-NEXT；未执行任何 Git 操作）。
>
> 上一次更新对应任务书：`APCPID（变比例变积分 PID 调节器）迁移`（新增 `src/blocks/apcpid.py` + 导出 + `tests/test_blocks_apcpid.py` 56 用例；新增风险表 **五-J APCPID-CYCLE-1/ORDER-1/RM-1/INPUT-1/OUTRL-1/ZZD-1/GATE-1/DEADVAR-1/PARAM-1**；嵌套复用既有 `APCPIDZZD` 真实实例并共享同一 `LicenseContext`；外层 APCPID + 内层 APCPIDZZD 双层授权一拍调用 2 次不去重；PID 公式使用内部 `CYCLE`、`dt_ms` 仅驱动授权/嵌套块；`RM/SP/KD/TD` 本拍局部改写不持久化；推进 BLOCK-NEXT；未执行任何 Git 操作）。
>
> 上一次更新对应任务书：`BD_MMYZ 授权模块阶段 1 迁移`（新增 `src/licensing/`（`dword`/`hashcore`/`providers`/`xtxx`/`bd_zcm`/`bd_mmyz`/`bd_mmyz_st`/`issuer`）+ `src/globals/license_context.py`；ST 源 `BD_MMYZ.zip`：`XTXX`/`BD_ZCM`/`BD_MMYZ`/`BD_MMYZ_ST`；平台适配机器标识替代 `SysTargetGetSerialNumber`；哈希核心单实现 + DWORD 32 位回绕；周期复验时间用可注入 Provider 禁系统时钟；LicenseContext 每实例隔离 + 密码实时读取；新增风险条目 **五-H LIC-PLATFORM-1/2、LIC-CLOCK-1、LIC-HASH-1、LIC-CTX-1、LIC-SEC-LEGACY-1、LIC-PHASE2-1（阶段 2 待办）**；新增 6 个测试文件 61 用例；全量 300→361；未执行任何 Git 操作）。**随后据 ChatGPT5.5 复盘做 1 处严格 ST 语义修正**：`XTXX` 空序列号场景 `Serial_result` 改回透传底层（读取成功但空 → `Serial_result=0`、`SerialOK=False`），不再被 XTXX 内部判定覆盖；更新 `LIC-PLATFORM-1` 三场景说明 + 对应测试；全量 361→362。
>
> 上一次更新对应任务书：`APCHSACCUM（离散积算）业务基础块迁移`（新增 `src/blocks/apchsaccum.py` + 导出 + `tests/test_blocks_apchsaccum.py` 24 用例；新增风险表 **五-G AC1~AC6** 系列：离散积算非 dt 积分 + ST 顺序锁定、MS 单次回绕不取模 + 负值修正延后、`MS` 源字面量 `E+38` 与注释 `E308` 冲突忠实使用待确认、`bPositiveAccum` 声明未用保留属性、RETAIN 映射 + 冷启动 `AV=0`、参数校验/输出门控延后；ST 源 `/Users/guangyaosun/Desktop/APCHSACCUM.txt`；全量测试 276→300；推进 BLOCK-NEXT；未执行任何 Git 操作）。
>
> 上一次更新对应任务书：`APCCD（重叠控制）业务块迁移` + `BLINK.TIMEHIGH 量化复盘`（用户确认原任务周期=500ms：APCCD `BLINK_TIMEHIGH_MS` 300→500，与 GCQ 统一；CD2/GG4 理由由"笔误"升级为"任务边界采样→亚周期脉宽量化到 cycle_ms"；**新增契约 R8「同任务 BLINK+R_TRIG 周期采样脉宽量化」+ 风险表 `SAMPLING-PATTERN` 条目**；并据 ChatGPT5.5 复盘收紧 R8：限定"同任务"适用范围 + 等价条件化、区分内部 `TIMEHIGH` 量化 vs 业务驱动 `TIMELOW`（拒绝块内 ceil 静默变换）、`BLINK-B2` 措辞降级为"连续时间/仿真型余数保留实现，非无条件 PLC 等价"、补 `TC=1.1` 抖动锁定测试；并据 ChatGPT5.5 再轮复盘把 `TIMELOW` 整数倍由"强制"软化为"工程建议"（非整数倍属合法配置）、明确 **warning 尚未实现（当前仅契约/风险表/测试记录，待接入 `RUNTIME-PARAM-VALIDATION`）**、给"长期平均周期准确"加适用边界（固定 `dt_ms=500`+整数毫秒+余数保留，保持源参数均值，非真实 CODESYS 结论）、`{3,4}` 标注为"本实现行为/未真机验证"）（ST 源：`/Users/guangyaosun/Desktop/APCCD.txt`；新增 CD1~CD7 条目 + 推进 BLOCK-NEXT）
> 上一次：`APCGCQ 命名说明小清理`（移除 GG9：`APCGCQ1` 是软 PLC 复制副产品，不构成项目命名约定；同步降级相关测试与 README 表述）
> 上上次：`APCGCQ_复核约定与Cursor执行说明.md`（小范围加固：固化 TIMEHIGH=500ms 项目修正约定 + 复核 GG1/GG2/GG5/GG6 措辞 + 补 4 条锁定测试）
> （APCGCQ CFC：`/Users/guangyaosun/Desktop/GCQ.docx`；ST 转换稿：`/Users/guangyaosun/Desktop/CGCQ1.txt`）

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
| **BLINK-B2** | 单拍跨多相位（余数保留实现；非无条件 PLC 等价） | recommended | 🟩 resolved | 原实现"每拍最多翻一次"在 `dt_ms > min(TIMELOW_ms, TIMEHIGH_ms)` 时会导致波形失真。现已改为 `while` 循环逐相位消费 `_elapsed_ms`，单拍可跨任意多个相位。**定性（措辞收紧）**：这是一个**"连续时间 / 仿真型"余数保留实现**——对细 `dt_ms`（`dt_ms ≪ 相位`）准确、长期平均周期无漂移；但它**不是无条件等价于真实 PLC 扫描语义**：在粗扫描（`dt_ms` 接近/超过某相位，尤其**亚周期脉宽** `TIMEHIGH < cycle_ms`）下，会出现"同拍 True→False 吞脉冲""非整数倍相位逐次抖动"等现象，此时必须配合 **R8（采样脉宽量化到 `cycle_ms` 整数倍）** 才能与"任务边界采样"一致。即：余数保留是数学上正确的连续时间实现，PLC 离散扫描场景下的正确落地由 R8 约束承担。唯一相关护栏见 `BLINK-B4`，其职责仅限**保证状态机在退化输入下可终止**。`tests/test_primitives_blink.py::TestBlinkMultiPhaseCrossing` 锁死（含 `dt=1000 / period=200` / `dt=450 / period=200` / `dt=350 / threshold=100` 三类跨多相位用例）；采样脉宽量化见 `SAMPLING-PATTERN` / R8。|
| **BLINK-B4** | `TIMELOW_ms = 0` / `TIMEHIGH_ms = 0` 的退化行为 | accepted | 🔒 locked | 参数非负由 `RUNTIME-PARAM-VALIDATION` 上层兜底。块内部对退化情形只有一道**防死循环护栏**：`TIMELOW_ms + TIMEHIGH_ms <= 0` 时本拍不推进 `_elapsed_ms`、`OUT` 保持。**该判断仅用于防止状态机在退化输入下进入不可终止循环；不构成块内参数合法化，不替代项目级参数校验契约，也不是业务语义兜底。** 单侧为 0（另一侧 > 0）属合法退化，`while` 中 `threshold<=0` 分支让该相位"立即翻转"，不消耗 `_elapsed_ms`，在单拍内通过另一相位的正阈值完成扣减。`tests/test_primitives_blink.py::TestBlinkDegenerateZeroPeriod` 锁死。 |
| **SAMPLING-PATTERN** | 同任务 BLINK + R_TRIG 周期采样的脉宽量化约定（已升级为契约 R8） | project-rule | 🟩 resolved（已上升为契约 R8） | **适用范围严格限定**：仅"同一固定 `cycle_ms` 任务内、`BLINK.OUT` 只经同任务 `R_TRIG` 在任务边界用作采样节拍"的业务/组合块（`APCGCQ` / `APCCD` 及后续同类块）。核心：`R_TRIG` 只在任务边界（每 `cycle_ms`）观察 `BLINK.OUT`，**亚周期脉宽不可分辨**——在该场景可观察层面，`TIMEHIGH ≤ cycle_ms` 都被采成"1 拍宽"脉冲（`cycle_ms=500` 时 300 与 500 **有条件等价**）。**该等价在以下情况不成立**：① OUT 被更快任务读取；② OUT 直接用于业务判断/物理输出；③ 同实例跨任务消费；④ 非固定周期仿真模式。<br/><br/>**`TIMEHIGH`（端口内部脉宽常量）**：强制量化到 `cycle_ms` 整数倍；本项目 `BLINK` 为余数保留实现（`BLINK-B2`），取小于一拍的源值（300<500）会同拍吞脉冲 → 事件丢失 + 抖动（仿真：理论 ~23 事件只剩 14，间隔 3/5 抖动）。源值小于一拍时量化到 `cycle_ms` 是"量化复现"而非"笔误修正"，**严禁草率定性为源码笔误**。<br/><br/>**`TIMELOW`（= 业务输入 `TC*1000`）**：**保源参数，不得在块内静默 ceil/round 变换**（按 R7 业务输入不被 `cycle_ms` 改写）。整数倍是**工程建议**（获得稳定拍间隔），**非整数倍属合法配置**（原 PLC 可能确有 `TC=1.1/1.25`），允许使用。**warning 现状（不过度承诺）**：尚未实现配置校验函数，**当前仅在契约 R8 / 本风险表 / 测试中记录**"拍间隔可能抖动"，待接入 `RUNTIME-PARAM-VALIDATION`（Runtime 阶段）再统一提供 warning；**不阻断运行、不改写参数**。当前 Python 余数保留 BLINK 在非整数倍下表现为"逐次抖动 + 长期平均准确"（仿真：`TC=1.1` → 间隔 `{3,4}`），**不是** ceil 模型。**"长期平均准确"适用边界**：仅在"固定 `dt_ms=500` + 整数毫秒参数 + 余数保留实现"下、含义为"长期平均周期保持源参数对应均值（如 1600ms）"；此 `{3,4}` 是**当前实现行为**，与真实 CODESYS 是否一致**未在真机验证**（需在线观察 10~20 周期裁决），不得表述为"与真实 PLC 一致"。<br/><br/>量化只针对采样脉宽配置取值，不外推到业务时间公式参数（`TB/TC/TL` 进公式部分仍服从 R7）。承接案例：`APCGCQ-GG4`、`APCCD-CD2`。完整规则见 `.cursor/rules/00a-runtime-contract.mdc::R8`。`tests/test_blocks_apccd.py::TestBlinkSamplingCadence`（含 `TC=1.1` 抖动锁定）+ `tests/test_blocks_apcgcq.py::TestSamplingEventSpacing` 锁死。 |

---

## 三、Runtime / MainProgram 阶段延后项（重点清单）

> 本阶段按契约明确延后，不得偷偷塞进业务块内部。当前 `docs/RISKS.md` 为**唯一**的延后项集中登记处。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **RUNTIME-GATE** | 输出安全链 `system_ready AND output_enable AND safety_ok AND interlock_ok AND request` | blocker | ⏸ deferred | 契约 00a 已规定此为主程序的强制职责。所有业务块产出 `request`（逻辑请求），由主程序门控后才形成 `final_output`。 |
| **RUNTIME-STARTUP-INHIBIT** | 冷启动稳定窗口 `startup_inhibit_ms` | blocker | ⏸ deferred | 项目默认 500ms；释放还需叠加 `io_ready / bus_ready / comm_ready / safety_ok` 等条件。 |
| **RUNTIME-5-STEPS** | 主程序五步式：输入快照 → FB 推进 → request 生成 → 输出门控 → 一次性提交 | blocker | ⏸ deferred | 契约已规定，尚未写代码骨架。 |
| **RUNTIME-SAFETY-DEFAULT** | 扫描异常 / 超时 / 主循环失败时物理输出落到安全默认值 | blocker | ⏸ deferred | 需要 watchdog + fallback state 机制。 |
| **RUNTIME-SHADOW-MODE** | Shadow mode / write disable | blocker | 🟨 in-progress（WP-20260723-015 Round 3 已获 Codex `APPROVED`、用户确认 `CLOSED`：Python 核心已审核关闭，**未现场验证、不得发布**） | Python 已实现零配置默认 write-disable 的 `WriteGate` / `CommitPort` / `OuterScanRunner` 栈；普通可达属性图不能取得可旁路的底层 `CommitSupervisor` 或物理驱动，历史无门实写必须显式 `legacy_unshadowed=True`。shadow 正常、scan-fault、watchdog 均零物理提交且诊断不冒充成功；shadow→实写先原子挂起全通道边界重建，首拍从 `safe_value` 限速，预存 `commit_fault` / `channel_fault` 不因切换清除。39 条 shadow 回归覆盖零配置多拍、属性/底层端口旁路反证、并发/递归失败关闭和 `safe_value` 首拍。诚实边界：不防御 `object.__setattr__`、槽描述符、`__closure__`、`gc` 等 Python 语言级反射；真实 HAL/可信反馈、实时 monitor/周期线程/抖动统计、硬件 watchdog、真实驱动/协议 I/O、自动放开写、趋势对拍、PLC/CODESYS 与现场安全证明均未完成，因此风险保持 in-progress。 |
| **RUNTIME-HAL** | 硬件抽象层 | recommended | ⏸ deferred | 现场 I/O 对接、协议驱动、时钟源。需要与具体部署环境一起决策。 |
| **RUNTIME-WATCHDOG** | 扫描周期看门狗 | recommended | ⏸ deferred | 扫描超时时的安全响应。 |
| **RUNTIME-INTEGRATION-TESTS** | APCHXHCL 与 Runtime 门控的集成测试 | recommended | ⏸ deferred | RUNTIME-GATE + APCHXHCL-R3 联调后补集成测试，验证"门控有效期内冻结的均值不会误导下游"。 |
| **RUNTIME-PARAM-VALIDATION** | 业务参数配置装载层（统一非负校验） | blocker | ⏸ deferred | **所有业务块参数契约的统一落地位置**。待 Runtime 阶段建立配置装载入口后，集中实现对业务参数的非负 / 范围 / 类型校验。<br/><br/>**（A）当前已确认契约（必须落地）**：<br/>• `TB ≥ 0`、`TC ≥ 0`（承接 `APCHSFOP-H6`、以及 `APCHXHCL` 中同名参数）<br/>• 所有定时器 `PT_ms ≥ 0`（现有 `check_pt_ms` 逻辑归并到此）<br/>• `60 / TB` 整数性（现有 `check_tb_sample_n_integer` 归并到此）<br/><br/>**（B）未来可能的项目增强约束（非当前契约，需独立立项讨论后再决定是否采纳）**：<br/>• `TB > 0`（严格正，用于避免零除；当前 H6 契约**不包含**此条，`APCHSFOP` 的 `(TB+TC)>0.001` 门槛在 `TB=0` 且 `TC` 足够大时仍可工作）<br/>• 其他未来业务块新增的参数约束<br/><br/>建议实现为"加载期集中校验 + 启动前硬拦截"模式（与 `check_*` 的 warning 模式区分开），参数非法直接拒绝启动，而不是运行时降级。对应契约见 `00a-runtime-contract.mdc` R7 第 7 条。<br/><br/>**边界提示**：上述 (A) 与 (B) 不得混写 —— (A) 是已锁定的 H6 / 项目契约，落地 RUNTIME-PARAM-VALIDATION 即可直接实现；(B) 需要独立走"新增契约"流程，不得以 H6 收尾名义顺带植入。 |

---

## 三-A、平台演进相关（Platform 系列）

> 项目定位升级：从"把 CODESYS 功能块逐个迁移成 Python"，扩展为"做一个 **Python 原生软 PLC 平台**（支持 ST + CFC，已迁移功能块作为标准库），使控制逻辑能与 AI/Python 程序在同平台一体化运行（控制与重 AI 推理**分进程**隔离，见 D-AI）"。
> 总纲见 `docs/PLATFORM_ROADMAP.md`；地基设计见 `docs/STAGE0_DESIGN.md`。
> 本系列登记平台化新引入的、迁移阶段未覆盖的风险与决策。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **PLATFORM-IR-1** | 程序模型（IR）稳定性 | blocker | ✅ resolved（2026-07-12：**0.5 规格冻结**，`IR_SPEC` v2.2.2；正式实现仍属后续阶段） | IR 是整个平台的地基，ST/CFC 前端、执行引擎、导入器、编辑器全部依赖它。**评审修订**：阶段 0 只完成概念设计，**工程冻结改到阶段 0.5**；冻结前已分离"源模型 vs 可执行 IR"（见 `PLATFORM-EXEC-IR-1`）、补全 POU 模型（见 `PLATFORM-POU-MODEL-1`）、定 REAL/整数双模式（见 `PLATFORM-REAL-FIDELITY-1`）。**边界**：冻结的是规格与 0.5 原型证据，正式引擎/前端实现属阶段 1+；**与 PLC 语义一致性未证明**（阶段 6 对拍范围）。后续规格变更必须走评审并评估对上层影响。 |
| **PLATFORM-EXEC-IR-1** | 缺语言无关的可执行 IR | blocker | ✅ resolved（2026-07-12：**0.5 规格冻结**，`IR_SPEC` v2.2.2；2026-07-14 阶段 1 工程约定写回至 v2.2.3；PLC 一致性未证明） | v1 只把 ST AST 与 CFC 图统一到 `execute(ctx)` 接口，未做语言无关可执行 IR。**已修订**：`IR_SPEC` v2.2.1 §5 定义**全类型化**指令集（TypedValue 栈、指令带 IEC 类型、`LOAD_PREV` 入表、`CALL_FUNC`/`CALL_FB_INSTANCE` 拆分并**携带 `Binding` 绑定表**、加载期类型验证 pass），§3 **POU 定义与运行实例分离**（`POUDefinition`/`ProgramInstance`/`FBInstance`，实例装载期展开），§6 给 ST 与 CFC 的 lowering，源模型与可执行 IR 已分离。**0.5 原型已证明**（2026-07-05，`prototype_05/`）：ST 与 CFC 最小程序 lower 出结构相等的指令列表并跑通 24 拍逐值一致，全类型化验证 pass、5 个语义敏感案例、绑定/常量/安全配置的加载期校验闭环均有测试（经两轮定向返修——2026-07-05 时点 55/55 全绿为历史子集计数，含导入试验回归锁后最新记录 68/68（2026-07-12 实际运行），Codex 代码复核已通过）。**阶段 1 正式实现闭环（2026-07-14）**：`WP-20260713-002` 建立正式 IR 内存模型与装载期静态校验，Codex Round 2 `APPROVED`；实现反馈将 `StackSlot.index` 的 `{0..k-1}` 栈顶偏移规则写入 `IR_SPEC` v2.2.3。该规则是**项目工程约定**，不是 PLC 官方语义；执行器若需改变必须重新评审。以上 Python 证据仍**不证明与目标 PLC 一致**，PLC 一致性属阶段 6 对拍范围。 |
| **PLATFORM-CFC-FEEDBACK-MAP-1** | CFC 反馈起点 → `LOAD_PREV` 映射未经真机样本验证 | blocker | 🟨 in-progress（首份反馈环样本已到） | CFC 反馈起点是**元素级**标记并决定反馈环最低执行序号；"起点元素的哪些入边 lower 成 `LOAD_PREV`"是设计假设（`IR_SPEC §6`）。**样本二证据（2026-07-09，PLCopen XML）**：真实反馈环（ADD.In2 回接自身输出）以纯拓扑存在，入环元素执行序号**全图最小**（ADD=1）、被回接的输入读上一拍值——与假设方向一致；但 PLCopen XML **无显式反馈标记字段**，`IsFeedbackStart`（.export 专有）的精确落点仍未对照。**处理**：可选采同工程 .export 对照 `IsFeedbackStart` 落点（不阻塞 0.5 冻结）；阶段 5 实现导入器时以"序号+拓扑推断"与该标记互验后冻结映射；含多环/跨环样本届时补。 |
| **PLATFORM-TARGET-PROFILE-1** | 缺目标运行环境画像与一致性等级 | blocker | 🟨 in-progress | **已产出** `docs/TARGET_PROFILE.md`：锁定 CODESYS SP16.1，数值语义按 IEC+IEEE754 定，三档一致性等级 **E（容差）/F1（边界量化）/F2（位级保真候选，须真机证明）**（v1.1 修正：F1 不承诺与 CODESYS 边界 bit-exact——双重舍入，见 `TARGET_PROFILE §4.1`），并点明 F2 与"块零改动"互斥。**已补齐（2026-07-09，导入试验两份样本实测，`TARGET_PROFILE` v1.3）**：CPU/OS = Win x64（Control Win V3 x64）、Patch = SP16 Patch 1、CFC 顺序模式 = 自动数据流、任务 = Cyclic 500ms/priority1/watchdog 关——均标"样本工程实测"，生产工程若不同须另确认。**余待**：是否要 F2（用户决策）；真机对拍前按 v1.3 §2 复核生产环境与样本一致。 |
| **PLATFORM-OUTPUT-POLICY-1** | 输出安全链只适用 BOOL | blocker | 🟨 in-progress | v1 的 `final = req AND system_ready AND ...` 处理不了模拟量（如 APCM `AV`）与"安全默认值非 0"。**已修订**：`ENGINE_SCAN_SPEC` v2.2 §4 定义 `OutputPolicy` 取代布尔门控：故障策略**按原因分别定义** + 安全优先级（safety/scan_fault/watchdog 强制 safe 不可配 hold）；输出状态分 **`last_effective`（逻辑生效）/`last_physical_committed`（物理提交）** 两层（shadow 下限速/保持可连续模拟）；驱动写失败为**固定行为**（告警+持续写安全值+`commit_fault_retry_n` 拍后升级，提交层处理、不进策略层故障集合）；§4.5 显式声明**软件能力边界**（进程卡死/OS 崩溃场景兜底属外部硬件 watchdog/安全回路，阶段 7 前必须就位）。**规格已冻结**（2026-07-12 冻结评审，`ENGINE_SCAN_SPEC` v2.2.2，边界基准与复位制度见 `PLATFORM-OUTPUT-BASELINE-1`）；**阶段 1 实现与测试待办**：模拟量三路径 + 分原因策略 + 异常提交 + 两层状态 + 提交失败测试。 |
| **PLATFORM-OUTPUT-BASELINE-1** | 冷启动/无物理历史的限速基准与 channel_fault 复位语义未冻结 | recommended | ✅ resolved（2026-07-12 冻结裁决已写回 `ENGINE_SCAN_SPEC` v2.2.2 §4.1/§4.4；**阶段 7 HAL 实现与现场验证仍未完成**） | `ENGINE_SCAN_SPEC §4` 只规定了故障恢复与 shadow→实写**有** `last_physical_committed` 时的限速基准，未规定：① 冷启动正常路径首拍基准；② 冷启动直接 shadow 后切实写且**无** LPC 时的基准；③ `channel_fault` 升级后的复位机制。**0.5 原型暂定**：①② 基准 = `safe_value`（保守，避免无物理基准跳变，有测试锁定）；③ 锁存不自动清除。Codex 审核裁决"暂不冻结"，并指出候选语义还包括"必须读取设备反馈"。**冻结裁决（2026-07-12 冻结评审通过，已写回 `ENGINE_SCAN_SPEC` v2.2.2 §4.1/§4.4）**：需重建物理基准的边界首拍——有可信设备反馈优先以反馈为基准，否则 `safe_value`；`last_physical_committed` 只是驱动确认写出的最后命令值，不冒充反馈、不作对齐基准；`channel_fault` 锁存，安全值写成功只清瞬时 `commit_fault` 不自动清锁存，解除须"故障原因消失 + 安全值写成功 + 显式确认/复位"三条件。此为**项目工程约定，不是 CODESYS 官方语义**，可信反馈接口与现场验证依赖阶段 7 HAL，届时未过验证不得宣称已闭环。 |
| **PLATFORM-DRIVER-RECEIPT-TYPE-1** | 驱动确认值子类可绕过提交失败记账 | blocker | ✅ resolved（2026-07-22，WP-20260722-011 Codex Round 1 `APPROVED`） | **原问题**：驱动返回重载比较运算的 `int` 子类时，`_iec_value_error()` 值域比较可漏出普通 `RuntimeError`；已尝试通道没有 `PartialCommitError`、`commit_fault`、失败计数或回执证据。**已解决**：`CommitSupervisor` 在任何不可信值的 IEC 值域、浮点有限性或严格相等运算前，先要求命令与回执均为 exact `bool/int/float/str` 且 exact 类型相同；子类/非支持类型/异型值逐通道失败关闭。落盘 5 条对抗性用例与 Codex 自有不落盘反证均证实恶意 `int/float/str` dunder 未被调用，故障记账、旧 LPC、健康通道隔离和第 N 次锁存升级均成立；公共 Store/IEC 类型规则未改。**仍然边界**：此仅证明当前 Python 契约，不证明真实 HAL/驱动、PLC/CODESYS、硬件 watchdog 或现场安全回路一致。 |
| **PLATFORM-IMPORT-TRIAL-1** | 真实 CODESYS 导出最小导入可行性试验 | blocker | ✅ done（2026-07-09） | 用户提供 SP16.1 导出 `prototype_05/import_trial/sample/test.export`（CFC_TEST/ST_TEST/PLC_PRG/MainTask）。试验解析器 `parse_export.py` **全部识别成功**：POU 树与语言分类、ST 接口+实现逐行还原（TextLines 数组顺序=文档顺序，Id 非行号）、CFC 源框/调用框/汇框/9 条连线/SEL 三脚接线、元素级 `IsFeedbackStart` 字段、`UseExplicitExecutionOrder` 顺序模式、任务配置（Cyclic 500ms priority1 watchdog 关）、目标设备（Control Win V3 x64）。5 项回归锁 `tests/test_import_trial.py`。**详情与后续样本清单**：`prototype_05/import_trial/FINDINGS.md`。衍生新风险 `PLATFORM-CFC-AUTOORDER-1`（见下）。**样本二（同日，PLCopen XML `test_fb_feedback.xml`）**：反馈环 + TON 实例框识别成功（`parse_plcopen.py` + 8 项回归锁）——反馈环样本已补，但 .export 的 `IsFeedbackStart` 精确落点仍缺**可选**对照（`PLATFORM-CFC-FEEDBACK-MAP-1` 转 🟨）。 |
| **PLATFORM-CFC-AUTOORDER-1** | 自动数据流模式下导出不含每元素执行序号 | blocker | ⏸ deferred/mitigated（2026-07-12：载体差异已冻结为 D3 载体分支，写回 `ENGINE_SCAN_SPEC` v2.2.2 §5.1 / `IR_SPEC` v2.2.2 §4/§6；.export 重建算法延后阶段 5，未就绪时导入器拒绝生成可执行 IR） | **导入试验实测发现（影响 D3）**：`UseExplicitExecutionOrder=False`（自动数据流）时，`.export` 只存拓扑与连线，编辑器显示的执行序号 0..N 是**派生值**，未存储。故 D3"导入保留原始序号"只对**显式顺序模式**直接成立；自动模式导入器必须**重建**顺序。试验用"拓扑排序+同层按元素 Id 升序"重建，与样本编辑器显示序号**逐一吻合**，但仅单样本、无环图，不得当作已验证算法。**缓解（样本二实测，2026-07-09）**：**PLCopen XML 载体每元素显式存储 `executionOrderId`**——该载体上 D3"导入保留序号"直接成立，阶段 5 导入器**候选首选 PLCopen XML**，.export 自动模式的派生问题降级为备选载体问题。**处理**：① 显式顺序模式 .export 样本——用户裁决**暂缓**；② 若坚持 .export 载体才需分支/多页样本对拍重建算法（阶段 5）；③ ~~冻结评审时修订 D3 表述~~✅ 已裁决为载体分支并写回（2026-07-12；"拓扑排序+同层按 Id"明确**不冻结**为算法）。 |
| **PLATFORM-POU-MODEL-1** | POU 模型不足，无法"像 CODESYS 一样新建 POU" | recommended | 🟨 in-progress | v1 只有顶层 `Program` + 库 FB 实例。**已修订（v2.2.1）**：`IR_SPEC` §3 **定义与运行实例分离**（`POUDefinition`/`ProgramInstance`/`FBInstance`，实例装载期按路径展开、调用不创建）、VAR_TEMP（仅 PROGRAM/FB）、`VAR_IN_OUT` 经 `ValueRef` 引用、FUNCTION 调用帧、调用点 `Binding` 绑定表（模型先定）。实现随语言前端阶段推进。 |
| **PLATFORM-GOLDEN-EARLY-1** | 真 PLC 对拍安排太晚 | blocker | 🟨 in-progress（格式就绪，实采待外部） | 黄金轨迹提前。**已产出** `docs/GOLDEN_TRACE_FORMAT.md` v1.2.1：JSONL 轨迹格式（bits 位模式/NaN-Inf 约定/哈希规范）、**7 项**最小采集清单（TON/边沿/反馈环/REAL 递推/冷热启动/APCM/**整数中间溢出差分**）、采集方法、回放脚手架规格。**外部阻塞**：第一批真机实采需访问 CODESYS SP16.1 运行环境，本仓库内无法完成，依赖用户提供（见该文件 §5）。**690 测试只证明 Python 不回退、不证明与 PLC 一致** 的判断保留。 |
| **PLATFORM-RETAIN-1** | RETAIN / PERSISTENT 重启恢复 | blocker | ⏸ deferred（阶段 8） | 迁移阶段一律按"`RETAIN` 仅是重启恢复概念、本轮不落盘"。但要"完美复刻 PLC + 接现场设备"，断电/重启后保持型变量的恢复是真实功能。**处理**：IR 中以变量属性（`InstanceDecl.retain` / `VarDecl.retain/persistent`）建模（阶段 0 已预留），快照/恢复机制在阶段 8 落地；需明确"何时快照"与扫描时序的一致性，及冷启动 vs 热启动初始化语义。接现场前必须就位，可与阶段 7 并行。 |
| **PLATFORM-REAL-FIDELITY-1** | REAL 32 位 vs Python 64 位 float 保真 | blocker | 🟨 in-progress | 现有 14 块全用 Python `float`(64) = LREAL，CODESYS `REAL` 是 32 位单精度。**已定模式（D5，写入 `TARGET_PROFILE §4` + `IR_SPEC §8`）**：`engineering`(E，float64+容差，默认，零改动) / `fidelity` 分 **F1**（边界量化，细分 F1-expr/F1-boundary 两种子行为，见 `TARGET_PROFILE §4.2`；零改动；**不承诺与 CODESYS bit-exact**）与 **F2**（块级 float32 全程，**位级保真候选、须真机证明**，**与零改动互斥、按需立项**）。"块零改动"与"位级保真"不可兼得已显式入档。模式为装载期配置，**禁止运行中热切换**。**待**：阶段 6 黄金轨迹量化 E 模式漂移、裁决是否上 F1/F2。 |
| **PLATFORM-INT-WIDTH-1** | 整数运算中间位宽/回绕发生点目标相关 | blocker | 🟥 open | CODESYS 整数临时结果**按目标设备原生位宽**（x86/ARM32 ≥32 位、x64 =64 位）计算，既非无限精度也不必然按声明类型逐步截断；有符号溢出/越界转换行为亦依赖编译目标。**处理（v2.2.1）**：IR 参数化为 `int_native_width: 32|64` + `int_intermediate_policy: native_width(默认假设)|declared_width` + `int_overflow_convert_policy: TBD`（`IR_SPEC §5.4`）；黄金轨迹 #7 用**表达式变体差分法**裁决（不得把中间值赋给变量，`GOLDEN_TRACE_FORMAT §3`）；裁决前 fidelity 整数中间溢出结果视为待验证。 |
| **PLATFORM-ST-CONFORMANCE-1** | ST 子集语义一致性 | recommended | 🟥 open | ST 前端需覆盖一个与 CODESYS 行为一致的 ST 子集；语义边界（`SEL` 极性、整数/实数提升、`T#` 时间字面量、表达式求值顺序、`MIN/MAX/LIMIT` 等标准函数）易与真 PLC 偏差。**处理**：阶段 3 实现 ST 前端时建一致性测试套件，直接复用迁移阶段沉淀的 `src/compat` 与对 `SEL` 等的既有理解；理想验收是"用 ST 重写某已迁移块的源 ST，结果与该块 Python 实现一致"。对应路线图阶段 3。 |
| **PLATFORM-RT-JITTER-1** | 实时扫描抖动 / 超时 | blocker | ⏸ deferred（阶段 7） | Python 非硬实时，固定 500ms 主循环受 GC、OS 调度、AI 推理占用影响，可能抖动或超时。接现场驱动物理 I/O 时，扫描节拍不稳会直接影响控制品质与安全。**处理**：阶段 7 落实定时驱动 + 超时检测 + watchdog 升级响应 + 扫描异常落安全默认值（承接 `RUNTIME-WATCHDOG` / `RUNTIME-SAFETY-DEFAULT`）；与 `PLATFORM-AI-DETERMINISM-1` 联动（重 AI 必须离扫描线程）。接现场前必须解决。 |
| **PLATFORM-AI-DETERMINISM-1** | AI 时延/故障破坏控制循环 | recommended | 🟥 open | 平台核心价值是"控制 + AI 一体化"，但重 AI 推理若与控制循环**同 OS 进程同线程**，其时延会拖垮 500ms 周期，且 AI 的 **OOM / 崩溃 / GIL 占用**会带倒控制循环。**评审修订**：定位改为"**同平台同生态、但分进程**"——控制运行时与 AI worker **分进程 + 共享内存/IPC**，用户体验仍是一套平台。**处理**：阶段 9 采用"推理离扫描进程 + 结果锁存进扫描 + 经安全门控采纳"，复用 **APCM/APARA 人工确认应用**范式（建议 vs 命令分离）；阶段 9 预研验证此隔离边界。 |

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
| **APCGCQ-GG4** | `BLINK01.TIMEHIGH` 端口量化到任务周期 500 ms（源写 300，等价复现） | accepted | 🔒 locked | **理由已升级（替换原"笔误/不评判源 TXT"的草率说法）**：`BLINK01.TIMEHIGH` 在本块按 500 ms 实现，源/转换稿中的 `T#300MS` **不是笔误**，而是因任务边界采样被量化——与 `APCCD-CD2` 同一原理：① `R_TRIG1` 只在任务边界（每 500ms）观察 `BLINK01.OUT`，亚周期脉宽（300ms < 500ms）不可分辨，在"同任务、OUT 仅取边沿"场景的可观察层面 300 与 500 **有条件等价**（等价前提见 R8 第 1 条）；② 本项目 `BLINK` 为余数保留实现（`BLINK-B2`），取 300（< dt=500）会吞脉冲/抖动，故端口量化到 `cycle_ms = 500` 才忠实复现真实 PLC 的整齐采样节拍。本块以模块级常量 `BLINK_TIMEHIGH_MS = 500` 固定为字面量，**不暴露为 GCQ 输入**。`TIMELOW` 仍为 `REAL_TO_TIME(TC*1000)`（`TC` 单位为秒）。**采样窗口周期** = `TC*1000 + 500` ms。`TestSamplingEventSpacing::test_blink_timehigh_constant` + `TestSamplingEventSpacing::test_blink_timehigh_uses_project_500ms` 锁死。 |
| **APCGCQ-GG5** | `RLIM01.HL = LL = OUTV` 对称速率限幅；`OUTV` 不是输出上下限 | accepted | 🔒 locked | 主链路为 `(JTAV+DTAV)*K → APCHSRATELIM(IN=..., HL=OUTV, LL=OUTV) → APCHSHLLIM(IN=..., HL=OUTH, LL=OUTL) → GCAV`。**两层语义必须分清**：(1) `OUTV` 是**每拍变化量限制**，且上升 / 下降对称（按 `APCHSRATELIM-RL1`，`HL/LL` 是正幅值，不是上下区间）；(2) **最终输出幅值上下限**由 `LIM01` 使用 `OUTH/OUTL` 完成。**严禁把 `OUTV` 当作 `GCAV` 的输出上下限**，也不允许未来误改成 `LL := -OUTV`。`TestRLIMSymmetricRateLimitInChain` + `TestLIMAmplitudeLimitInChain` + `TestOutvIsRateLimitAndOuthOutlAreAmplitudeLimits` 锁死。 |
| **APCGCQ-GG6** | `FOP01.TB` 不传，沿用 APCHSFOP 声明默认值 0.5 s | accepted | 🔒 locked | ST 中 `FOP01(IN:=..., TC:=TZ*2, KG:=1)` 没传 `TB`，按 R7.7 输入脚语义，使用 `APCHSFOP` 的 ST `VAR_INPUT` 声明默认值 `0.5` 秒。本块通过模块级常量 `FOP01_DEFAULT_TB_SEC = 0.5` 显式传入。**单点真值同步约定**：若 `APCHSFOP` 默认 `TB` 调整，本常量必须同步更新——这是单点真值约定的失效场景，需要登记为长期注意项。`TestFOP01DefaultsLocked::test_fop01_default_tb_is_half_second` + `test_first_sampling_av_uses_alpha_kg_in` 锁死。 |
| **APCGCQ-GG7** | 嵌套 FB 实例命名复用 ST 实例名 | accepted | 🔒 locked | `BLINK01 / R_TRIG1 / STAT01 / FOP01 / RLIM01 / LIM01` 与 ST 源码完全一致，便于与原始 CFC/ST 图纸追踪对应关系。其中 `STAT01` 的类是 `APCSTATISTICS`（ST 中实例命名为 `STATISTICS_REAL`，按用户确认即同一类的实例化）。**不允许重命名实例属性**——任何重命名都会破坏与 CFC 的可追溯性。 |
| **APCGCQ-GG8** | 控制器验证段（`BC_ERROR3`）暂不实现 | accepted | ⏸ deferred | CFC 顶部有一段独立的"控制器验证"逻辑，最终输出诊断 `BC_ERROR3`。用户已确认：(1) 与 GCQ 主通路解耦，**不影响**任何 GCQ 内部变量；(2) `BC_ERROR3` 是上层控制器健康字，**不属于** GCQ 接口；(3) 本轮**不迁移**该段。如未来需要，作为独立模块迁移即可，不应与 GCQ 耦合。 |

> **非风险备注（不进风险登记）**：源材料 `CGCQ1.txt` 顶部出现的 `FUNCTION_BLOCK APCGCQ1` 不是业务功能块的真实命名，也不是迁移风格选择——`APCGCQ1` 是**软 PLC 复制功能块时为避免重名自动生成的名称**。本项目要实现的功能块名就是 `APCGCQ`，因此 `src/blocks/apcgcq.py` / `class APCGCQ` / `from src.blocks import APCGCQ` 是直接对应业务名的正确实现，不存在"两种候选命名"的项目级决策需要登记为风险。

---

## 五-F、APCCD 组合业务块相关（CD 系列）

> 重叠控制组合块。ST 源：`/Users/guangyaosun/Desktop/APCCD.txt`。
> 结构与 `APCGCQ` 同构（BLINK+R_TRIG+STAT+FOP 采样链），但下游是"延时进入 +
> 保持输出 + 退出回补 ZLOUT + 跟踪切除"逻辑。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCCD-CD1** | ST 执行顺序锁定（核心不变量） | accepted | 🔒 locked | 采样事件那一拍（`R_TRIG1.Q=True`）必须严格按 ST 顺序：(1) `JZ_ZUP3 := 旧 JZ_ZUP2`；(2) `JZ_ZUP2 := 旧 JZ_Z1`（上一拍 `STAT1.AVG`）；(3) 之后才 `STAT1.step(RESET=True)`；(4) `JZ_Z1 := STAT1.AVG`（RESET 后 = 0）。**窗口快照必须发生在 `STAT1.RESET` 之前**——否则 `JZ_ZUP2` 会被当拍 RESET 后的 0 污染，破坏"相邻完整窗口均值差"语义、令 `FOP1` 输入符号反向。与 `APCGCQ-GG1` 同构。`tests/test_blocks_apccd.py::TestSTOrderingLocked`（含两窗口异值反证）锁死。 |
| **APCCD-CD2** | `BLINK1.TIMEHIGH` 端口量化到任务周期 500ms（源写 300，等价复现） | accepted | 🔒 locked | **已定论（用户确认原任务周期 = 500ms）**：源 ST 写 `TIMEHIGH:=T#300MS`，但端口以模块级常量 `BLINK_TIMEHIGH_MS = 500` 实现，采样窗口周期 = `TC*1000 + 500` ms，与 `APCGCQ` 口径统一。<br/><br/>**理由（"忠实复现"而非"修正"）**：① `R_TRIG1` 只能在任务边界（每 500ms）观察 `BLINK1.OUT`，**任何 ≤ 500ms 的高电平宽度都不可分辨**；在"同任务、OUT 仅经 R_TRIG 取边沿"这一场景的可观察层面，`TIMEHIGH=300` 与 `500` **等价**（高电平占 1 个周期，周期 = `TC*1000 + 500`）。原作者写 300 只是给了个"小于一拍的短脉冲"，被任务边界量化成一拍。**该等价是有条件的**（OUT 被更快任务读取 / 直接用于业务或物理输出 / 跨任务消费 / 仿真细 dt 时不再成立，见 R8 第 1 条）。② 本项目 `BLINK` 为**余数保留**实现（`BLINK-B2`），仅当 `TIMEHIGH_ms >= dt_ms` 时才与"任务边界采样"一致；取源值 300（< dt=500）会在同一拍内 True→False 吞掉高脉冲 → 采样事件丢失 + 节拍抖动（仿真：60 拍中理论 ~23 事件只剩 14，间隔在 3/5 拍抖动），与真实 PLC 不符。③ 故端口把内部脉宽常量量化到 `cycle_ms = 500` 得到整齐采样节拍（周期 1500ms = 15 拍）。**另注**：`TIMELOW = TC*1000` **建议**配置为 500ms 整数倍以获得稳定拍间隔，但**非整数倍属合法配置**（原 PLC 可能确有 `TC=1.1/1.25`），允许使用。warning 尚未实现，**当前仅在契约 R8 / 本表 / 测试中记录**"拍间隔可能抖动"，待接入 `RUNTIME-PARAM-VALIDATION` 再统一提供；**不阻断运行、不在块内对 `TC*1000` 做 ceil/round 静默变换**（R8 第 3 条）。当前实现在 `TC=1.1` 下间隔 `{3,4}` 拍是**本端口行为**（长期平均准确仅在固定 `dt_ms=500`+整数毫秒+余数保留下成立），与真实 CODESYS 是否一致未在真机验证。<br/><br/>`tests/test_blocks_apccd.py::TestBlinkSamplingCadence`（相邻采样间隔恒 15 拍，反证源值 300 的抖动）锁死。与 `APCGCQ-GG4` 同一原理。 |
| **APCCD-CD3** | `FOP1.TB = 0.5s`（ST 未连 TB，沿用 APCHSFOP 声明默认值） | accepted | 🔒 locked | ST 中 `FOP1(IN:=..., TC:=TZ*2, KG:=1)` 未连接 `TB`，按 R7.7 输入脚语义使用 `APCHSFOP` 的 VAR_INPUT 声明默认值 `0.5` 秒。本块以模块级常量 `FOP1_DEFAULT_TB_SEC = 0.5` 显式传入。**单点真值同步约定**：若 `APCHSFOP` 默认 `TB` 调整，本常量必须同步更新。`TB` 与 `dt_ms` 解耦（`dt_ms` 不替代 `TB`）。`tests/test_blocks_apccd.py::TestFOPAndTimeParams`（dt=1000 仍 TB=0.5；`FOP1.TC=TZ*2`）锁死。 |
| **APCCD-CD4** | `ZLOUT` 的 `VAR_IN_OUT` Python 适配 | accepted | 🔒 locked | ST 中 `ZLOUT` 是 `VAR_IN_OUT`（读-改-写引用管脚）。Python 改为"**入参 + 返回值**"模式：每拍以 `step(ZLOUT=...)` 入参为当拍真值来源（块内**不**缓存为唯一真值），仅在 `R_TRIG2.Q` 回补拍对入参做加法，并通过 `out["ZLOUT"]` 返回。**调用方必须把 `out["ZLOUT"]` 回灌到下一拍**，否则回补结果丢失。`tests/test_blocks_apccd.py::TestZLOUTVarInOut` 锁死（入参直通 / 不用旧缓存 / 回灌模式）。 |
| **APCCD-CD5** | `TS` 进入时的回补顺序 | accepted | 🔒 locked | `IF TS: AV_TEMP:=0` 必须在 `R_TRIG2` 回补之后执行。`TS` 进入拍若已有非零 `AV_TEMP`，`R_TRIG2.CLK=(...|OR TS) AND AV_TEMP<>0` 为真 → 先经 `R_TRIG2` 向 `ZLOUT` 回补一次，再被 `IF TS` 清零。**不得**把 `if TS: AV_TEMP=0` 移到 `R_TRIG2` 之前（会吞掉回补）。`R_TRIG2` 保证退出/跟踪条件持续为真时只回补一次。`tests/test_blocks_apccd.py::TestTrackingCutoff` 锁死（进入拍一次回补后清零 / 持续不重复 / 冷启动不变 ZLOUT）。 |
| **APCCD-CD6** | `CD_BH==0` 时 `FLG` 保持旧值 | accepted | 🔒 locked | `FLG:=SEL(CD_BH>0, SEL(CD_BH<0, FLG, -1), 1)`：`CD_BH>0 → 1`，`CD_BH<0 → -1`，`CD_BH==0 → 保持旧 FLG`。**严禁**写成 `sign(CD_BH)`（会把 0 写成 0）。注意该零值分支仅在 `TON1.Q AND CD_GD<=0` 时可达（正常 `CD_GD>0` 进不去），测试用 `CD_GD=0` 显式打到。`FLG` 冷启动 `0.0`（对应 ST 未显式赋初值的默认零值）。`tests/test_blocks_apccd.py::TestTONAndAVTempFLG::test_flg_kept_when_cdbh_zero` 锁死。 |
| **APCCD-CD7** | 不承担最终物理输出安全门控 | accepted | ⏸ deferred (Runtime 阶段) | `APCCD` 只产出逻辑结果（`AV / CD_BH / ZLOUT`），**不暴露** `EN / RESET / system_ready / output_enable / safety_ok / interlock_ok`，块内也不做物理输出安全门控。最终输出安全链由后续 Runtime / MainProgram 闭环（见 `RUNTIME-GATE` / `RUNTIME-STARTUP-INHIBIT`）。另：块内 `MIN(MAX(x,CDL),CDH)` 钳幅严格按源码字面顺序执行（含 `CDL>CDH` 反向时恒落 `CDH`），`TC/TZ/TL` 等参数非负 / 范围校验由 `RUNTIME-PARAM-VALIDATION` 兜底，本块不内嵌参数合法化。`tests/test_blocks_apccd.py::TestTONAndAVTempFLG::test_clamp_literal_order_min_max` 锁死钳幅字面顺序。 |

---

## 五-G、APCHSACCUM 业务基础块相关（AC 系列）

> 离散积算 / 单次回绕块。ST 源：`/Users/guangyaosun/Desktop/APCHSACCUM.txt`。
> 无 FB 依赖；携带 RETAIN 跨周期状态（`AV/SS/LR/preRS/bPositiveAccum`）。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCHSACCUM-AC1** | 离散积算 + ST 执行顺序锁定（核心不变量） | accepted | 🔒 locked | 每拍执行一次 `AV := AV + MC*I1`，是**离散累加**而非按 `dt` 的连续时间面积积分——`dt_ms` 仅为统一调度接口保留，`step` 内 `del dt_ms`，**不得**乘进累积公式。三步顺序不可调整：① 先处理上一拍遗留 `AV>=MS OR AV<0`（置 `AV:=IV`）；② 本拍一次积算 / **单次回绕**；③ 最后查 `RS` 上升沿。`tests/test_blocks_apchsaccum.py::TestNormalAccumulation` / `TestMSWraparound` / `TestRSRisingEdgeReset` 锁死。 |
| **APCHSACCUM-AC2** | MS 单次回绕 + 负值修正延后（不取模/不循环） | accepted | 🔒 locked | `IF AV+MC*I1<MS` 直累并 `SS:=False`，否则 `AV:=AV+MC*I1-MS` 且 `SS:=True`——**单拍只减一次 `MS`**。单拍输入跨越多个 `MS` 时会留下 `AV>=MS`，到**下一拍开头**才因第①步置 `IV`；负 `I1` 令 `AV<0` 时当拍不修正，同样**下一拍开头**才置 `IV`。**严禁**用 `AV % MS` / while 循环 / 数学优化替代——那会改变相等边界、单次回绕、负值延后修正等源 ST 行为。`tests/test_blocks_apchsaccum.py::TestMSWraparound::test_single_tick_crossing_multiple_ms_subtracts_only_once`、`TestNegativeAndIVRecovery` 锁死。 |
| **APCHSACCUM-AC3** | `MS` 源字面量 `E+38` 与注释 `E308` 冲突（忠实使用字面量，待确认） | accepted | 🟡 monitor | 源 ST 第 15 行**可执行字面量**为 `MS:=1.797693134862E+38`，但同行行尾注释写 `1.79769313486232E308`（≈ LREAL 上限）。本块按**可执行字面量** `MS=1.797693134862e38` 实现，**不**改成 `e308` / `float("inf")` / Python 最大浮点。两者差异巨大（`E+38` 仅约 REAL 上限量级，`E308` 才是 LREAL 量级），疑为源工程注释/字面量不一致。**未取得原 PLC 在线行为或原始工程变量定义前不做修正**；如后续确认应为 `E308`，仅改默认常量、不动逻辑。`tests/test_blocks_apchsaccum.py::TestUnusedVarAndLiteralLock`（锁 `e38`、反证非 `e308`）。 |
| **APCHSACCUM-AC4** | `bPositiveAccum` 源声明未使用（保留属性不加语义） | accepted | 🔒 locked | 源 ST `VAR RETAIN` 声明 `bPositiveAccum:BOOL`（注释意为"TRUE 时不累积负输入"），但 body **从未引用**。本块保留同名实例属性（冷启动 `False`）以忠实复现，但**不**实现"只积正值"逻辑——置 `bPositiveAccum=True` 后负 `I1` 仍正常参与积算。若未来需要该语义须凭源工程确认后另行任务，不在本块擅自添加。`tests/test_blocks_apchsaccum.py::TestUnusedVarAndLiteralLock::test_bpositive_accum_does_not_block_negative_input` 锁死。 |
| **APCHSACCUM-AC5** | RETAIN 映射 + 冷启动 `AV=0.0`（不实现跨进程持久化） | accepted | 🔒 locked | ST 中 `AV/SS`（VAR_OUTPUT RETAIN）与 `LR/preRS/bPositiveAccum`（VAR RETAIN）在 Python 中映射为**实例属性跨 `step` 保持**，满足"跨扫描周期 RETAIN"语义；**不**实现跨 Python 进程重启的文件持久化（与项目其它块一致）。冷启动 `AV` 固定 `0.0`（源 `AV:LREAL` 无显式初值），即使 `IV` 被配置为非零，初始 `AV` 也**不**设为 `IV`——`IV` 只在"旧 `AV>=MS`""旧 `AV<0`""`RS` 上升沿复位"三处生效。`tests/test_blocks_apchsaccum.py::TestExportAndColdStart` 锁死。 |
| **APCHSACCUM-AC6** | 不承担参数校验与物理输出安全门控 | accepted | ⏸ deferred (Runtime 阶段) | 本块只产出逻辑结果（`AV/SS`，及内部 `LR`），**不暴露** `EN/ENO/RESET/system_ready` 等接口，块内**不做**参数校验 / 浮点保护 / 范围修正 / 异常拦截。`IV/MS/MC` 的合法性（如 `MS>0`）由配置装载层 `RUNTIME-PARAM-VALIDATION` 兜底；最终物理输出安全链由 Runtime/MainProgram 闭环（`RUNTIME-GATE` / `RUNTIME-STARTUP-INHIBIT`）。 |

---

## 五-H、授权模块相关（LIC 系列，阶段 1：一机一码）

> 授权基础设施。ST 源：`BD_MMYZ.zip`（`XTXX` / `BD_ZCM` / `BD_MMYZ` /
> `BD_MMYZ_ST`）。Python 落地于 `src/licensing/` 与 `src/globals/`。
> **阶段定位**：本轮只保留"一机一码 + 关键功能块门控"的原有业务结构，
> 注册码/密码算法是可逆可复现的确定性算法，**不构成强密码学保护**。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **LIC-PLATFORM-1** | Python 平台机器标识替代 `SysTargetGetSerialNumber` | accepted | 🟡 monitor | CODESYS 源 `XTXX` 用 `SysTargetGetSerialNumber` 取序列号。Python 目标是 PC/服务器，改用可注入 `SerialTextProvider`：Windows `MachineGuid` / Linux `/etc/machine-id` / macOS `IOPlatformUUID`，规范化为 `PYPLC\|<OS>\|<原始ID>`（ASCII、非空、Latin-1 可编码、≤255 字节）。**这是部署平台适配，不是逐字节复刻**，因此 **不保证** 与旧 SoftPLC 的 `SerialText` 或旧注册码兼容。`BD_ZCM` 回归向量是 **Python 迁移回归向量，非 CODESYS 黄金样本**。`PLCid/CPUtype/Version` 不实现（保留 0 + `RESULT_NOT_IMPLEMENTED`，不伪造）。<br/><br/>**`Serial_result` 严格保留源 ST 语义**：它只反映 Provider 的“底层读取结果码”，**不**被 XTXX 内部的可用性判定覆盖。三种场景：① 底层读取失败 → `Serial_result!=0`、`SerialOK=False`；② **底层读取成功但序列号为空 → `Serial_result=0`、`SerialOK=False`**（对齐源 ST：空串只让 `LEN>0` 不成立，不改 `Serial_result`）；③ 成功且有效 → `Serial_result=0`、`SerialOK=True`。非 Latin-1 / 超 255 字节是 Python 平台适配层的不可用判定，仅置 `SerialOK=False`，同样不改写 `Serial_result`。下游 `BD_ZCM/BD_MMYZ` 一律依据 `SerialOK`（空/失败都导向 `ERROR=1000`/`9000`），与 `Serial_result` 是否为零无关。`tests/test_licensing_xtxx.py::TestXTXXFailureModes` 锁死三场景。 |
| **LIC-PLATFORM-2** | 机器标识可能变化/重复 + 禁止降级后备 | accepted | 🟡 monitor | `MachineGuid` / `machine-id` / `IOPlatformUUID` 在重装系统、克隆虚拟机、镜像复制、硬件更换时可能变化或重复。阶段 1 **接受**该限制。读取失败时**严禁**偷偷降级到 MAC / `uuid.getnode()` / 主机名 / 随机值 / 本地新建 ID——必须如实失败，最终导向 `BD_ZCM.ERROR=1000` / `BD_MMYZ()=9000` / `KZQBDYZMK.OK%10000` 反映失败。`tests/test_licensing_xtxx.py` 锁失败链路。 |
| **LIC-CLOCK-1** | 周期复验时间来自注入 Provider，禁用系统时钟 | accepted | 🔒 locked | `BD_MMYZ_ST` 的 `totalSeconds=(GetDateTime_ms/1000) MOD 65536` 来自可注入 `DateTimeProvider`，**不得**用 `time.time()` / `time.monotonic()` / `datetime.now()`。`step(dt_ms)` 保留 `dt_ms` 仅为统一接口，**不**用它自行累计/推进时间；同一扫描周期内多次调用必须看到**同一**当前时间（未来 Runtime 每周期推进一次统一时间）。`tests/test_licensing_bd_mmyz_st.py::TestNoAutoAdvanceTime` 锁死。 |
| **LIC-HASH-1** | 哈希核心单实现 + DWORD 32 位回绕语义 | accepted | 🔒 locked | `BD_ZCM` 与 `BD_MMYZ` 的四路哈希（初值 `0x811C9DC5/0xA5A5A5A5/0x5A5A5A5A/0x9E3779B1`）**只保留一份**纯函数 `hashcore.compute_registration_codes`，避免"展示注册码"与"校验注册码"漂移。每个 `DWORD` 乘法/加法中间步骤按 `& 0xFFFFFFFF` 回绕；`SerialText` 按 **Latin-1 字节、索引 1-based** 参与（对应 ST `BYTE_TO_DWORD(SerialText[i])` + `FOR i:=1 TO nLen`）。`CheckMM1~4` 因输入 ZCM∈[0,9999]、乘积 <2^32 不会回绕，故不套多余 mask（遵循"只在需要处回绕"）。二者**外层执行顺序/错误码各自保留**。 |
| **LIC-CTX-1** | LicenseContext 每实例隔离 + 密码实时读取 | accepted | 🔒 locked | 授权全局变量（`BD_MM1~4`、`KZQBDYZMK`、`BD_ERROR1~9`）封装在 `LicenseContext` 每实例容器，**不建模块级单例**，避免多 Runtime / 测试相互污染。`KZQBDYZMK` 每次验证通过闭包**实时读取**本实例当前 `BD_MM1~4`（不在构造时拷贝缓存），用户更新密码下一拍即生效。本轮**不**实现 `APCPIDZZD`/`APCPID` 的错误累加，仅提供 `BD_ERROR1~9`（默认 0.0）与门控接口。`set_passwords` 经 `to_dword` 拒绝 bool 偷渡。 |
| **LIC-SEC-LEGACY-1** | 阶段 1 非强密码学保护（语义留痕） | accepted | ⏸ deferred (阶段 2) | 阶段 1 的注册码与密码算法是**可逆/可复现的确定性算法**，用于一机一码与基础复制阻断，**不构成强密码学保护**（无签名、无密钥、无证书）。本轮**不**实现联网验证 / License 文件持久化 / 第三方加密库。**原 SoftPLC 为何"实际可用"（关键背景）**：CODESYS 中这些功能块被**编译为受密码保护的库文件**后供主程序调用，主程序方看不到库内源码（打开库需密码），因此 `BD_MMYZ / CheckMM` 算法**藏在不可见的库里**——保护来自"**库文件不可见 + 生态交付边界**"，**而非算法本身**。Python 迁移默认交付 `.py` 源码，"库不可见"这一前提**消失**，算法随源码暴露，故必须在阶段 2 同时补回"签名授权"与"库化交付"两层。详见下方 `LIC-PHASE2-*` 阶段 2 待办。 |
| **LIC-PHASE2-1** | 阶段 2：离线签名授权（待办） | recommended | ⏸ deferred (阶段 2：授权安全增强) | 阶段 2 必须包含：① 离线**私钥**签发 License 文件；② Python 客户端只持**公钥**验证许可证签名；③ 许可证绑定规范化机器标识、产品版本、功能模块、到期时间等字段；④ 私钥**不得**进入客户部署程序；⑤ 设计许可证迁移/换机/失效/重签发流程；⑥ 保留 `KZQBDYZMK.OK/ERR/YZTG` 门控接口，让 `APCPIDZZD`/`APCPID` 无需感知安全升级；⑦ 评估虚拟机克隆、机器标识变化、离线攻击、代码篡改等威胁模型。本轮**不**实现其中任何一项。**算法本质提醒**：阶段 1"生成密码"与"验证密码"用同一套**公开确定性算法**，验证=本机重算 `CheckMM` 比对，故发码公式随验证逻辑一起进入客户端——一机一码当前只是**功能绑定**而非**安全授权**；任何持有部署代码者均可自行发码。这是体制问题，无法靠"把发码工具挪到工程目录之外"解决，必须靠阶段 2 非对称签名根治。 |
| **LIC-PHASE2-2** | 阶段 2：License 结构与签名范围（待办） | recommended | ⏸ deferred (阶段 2) | 建议算法 **Ed25519**（私钥签发 / 公钥验证）。License 字段至少：`schema_version / license_id / product_id / machine_id_hash / enabled_modules / issued_at / expires_at（可选）/ issuer_key_id / signature`。**关键约束**：① `signature` 必须覆盖**除自身外全部字段的规范化（canonical）字节序列**，防改字段 / 改序列化形式绕过，不能只签 `machine_id_hash`；② 写 `machine_id_hash`（规范化机器标识的哈希）而非完整机器码，客户端重算本机哈希再比对——但 `machine_id_hash` **本身不提供安全性**（机器码客户端可重算），安全完全来自签名；③ `issuer_key_id` 支撑公钥轮换，客户端需维护公钥信任列表。 |
| **LIC-PHASE2-3** | 阶段 2：离线威胁模型与残余风险（待办） | recommended | ⏸ deferred (阶段 2) | 落地前须明确：① **离线到期不可信**——本项目规则禁止基础块读系统时钟、`BD_MMYZ_ST` 用可注入时间，客户端无可信时间源，改系统时间即可绕过 `expires_at`，需可信时间方案或显式接受该限制；② **私钥托管**——私钥丢失则无法再签发、泄露则全线崩，须离线备份 + 受控保管 + 轮换预案；③ **离线吊销难**——撤销已发许可证需吊销名单分发机制，纯离线难以实现，须先有预期；④ **代码篡改绕过**——签名只防伪造，不防攻击者直接 patch 掉客户端验证分支，纯软件无绝对防御，强绑定需硬件（加密狗/TPM）或联网激活。 |
| **LIC-TOOL-OFFLINE-1** | 厂商侧发码工具独立存放（已落地） | accepted | 🔒 locked | 厂商侧"注册码 → 密码"发码工具**刻意不放进本工程**，作为单文件、零依赖（仅标准库）的独立脚本维护于工程目录之外，避免随转写代码分发到客户/部署侧。**定位**：阶段 1 旧 `BD_MMYZ` 的兼容/过渡发码器；真正面向客户发布时应逐步切换到阶段 2 签名 License（`LIC-PHASE2-*`），不再依赖 `MM1~MM4` 短数字。注意此举只是"不奉送现成批量发码器"，**不**改变 `LIC-PHASE2-1` 指出的算法本质泄漏问题。 |
| **LIC-PHASE2-4** | 阶段 2：库化交付层（接回 CODESYS 库保护，待办） | recommended | ⏸ deferred (阶段 2) | 与"签名 License"**正交**的第二层：补回 Python 默认裸 `.py` 交付所丢失的"库不可见"边界（对应 CODESYS 编译库）。要点：① 关键模块（授权层 + 真正关键算法块）用 **Nuitka / Cython 编译为原生扩展**（`.so/.pyd`），**不裸交付** `.py` 源码；② 发布物做**完整性校验 + 版本控制**；③ **明确认知边界**——Cython/Nuitka/混淆/PyInstaller **只能抬高逆向与篡改门槛，不构成 CODESYS 库密码那样的封闭边界，更不能替代签名 License**；**PyInstaller 单独使用不是源码保护方案**（仅打包，`.pyc` 可解包反编译）；④ 工程成本：原生编译需按平台分别构建工具链、调试/打包复杂度上升，须按"发布工程"单独排期。建议结构：`plc_runtime/`（公开 API，仅 `licensing_public_api.py`）+ `plc_protected/`（`license_gate / signed_license_verify / protected_pid_blocks`，编译封装），主程序只调 `KZQBDYZMK.step(dt_ms)` 与读 `OK/ERR/YZTG/ERR_N`，不感知内部细节。 |
| **LIC-PHASE2-5** | 阶段 2：安全根定位 + 禁降级 + 加固层（待办） | recommended | ⏸ deferred (阶段 2) | **安全根定位（必须钉死）**：唯一**安全根**是"私钥签发 → 公钥验证、签名不可伪造"（数学保证）；**编译封装 / 混淆 / 用 License 密钥解密关键算法块** 全部只是**加固层（提高逆向与 patch 成本），不是安全根**——因为 Python 客户端必须自己解密并运行，解密后内容理论上可从内存/二进制/运行逻辑提取，或直接 patch 校验。故"License 作运行必要材料"只能定位为加固，**不得**当成秘密本身。① **`fail-closed`**：V2 验证失败即关断关键功能，不得回退任何旧路径。② **授权校验放入受保护模块内部**，不是仅留一个外层 `if KZQBDYZMK.OK`（外层布尔门在二进制层易被 patch）。③ **加固手段（仅抬成本）**：关键算法可依赖"验证 License 的模块权限/参数/派生运行材料"来提高直接 patch 的成本。绝对强绑定仍需硬件（加密狗/TPM）或联网激活。 |
| **LIC-PHASE2-6** | 阶段 2：V1/V2 兼容按"发布物版本"隔离（待办） | recommended | ⏸ deferred (阶段 2) | **兼容策略必须按发布物版本隔离，严禁在同一生产包里同时兼容两套授权**（否则等于给生产包留可用降级入口，签名 License 形同虚设）：① **旧项目 / 已部署客户**——继续 V1 `MM1~MM4` 作为**遗留兼容**方案（独立旧发布物）；② **新版本生产发布**——**仅** V2 签名 `license.key`，**不接受** `MM1~MM4`；③ **开发 / 迁移环境**——可有显式测试开关，但**绝不进入生产构建**。这样既不会突然让旧客户失效，也不会在新版本里留下可利用的降级入口。 |

---

## 五-I、APCPIDZZD（PID 自整定）相关（PIDZZD 系列）

> PID 自整定功能块。ST 源：`/Users/guangyaosun/Desktop/APCPIDZZD.txt`。
> 复用 `TON`/`R_TRIG`/`APCHSACCUM`/`APCHSHLLIM`/`LicenseContext.KZQBDYZMK`；
> 携带跨扫描状态；通过构造函数注入 `LicenseContext`。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCPIDZZD-CYCLE-1** | 业务语义绑定 500ms 固定任务周期 | accepted | 🔒 locked | 源 ST 将 `SQSJ/JSSJ/JSSJ2` 以**固定 `+0.5` 秒**推进（不是 `dt_ms/1000`），故该块业务语义绑定原 SoftPLC 的 500ms 固定任务周期。Python 严格保留三处 `+= 0.5` 字面量；`dt_ms` 仅驱动 `TON1/TON2`（`PT=5000ms`）。**只在 `dt_ms=500` 下验证语义**；非 500 不拒绝、不静默缩放为比例积分，仅登记此风险。`tests/test_blocks_apcpidzzd.py::TestScanTime` 锁死（含 `test_not_dt_over_1000` 反证非 `dt/1000`）。 |
| **APCPIDZZD-COMMENT-1** | `PT1K/TI1K<=0` 注释与实际代码冲突（按代码实现） | accepted | 🔒 locked | 源注释（第 15~16 行）称 `PT1K/TI1K<=0` 时"比例/积分自整定不起作用并将 `PT1/TI1` 清零"。但**实际 ST 仅在周期顶部**（第 57~62 行）`IF PT1K<=0 THEN PT1:=0;`、`IF TI1K<=0 THEN TI1:=0;` 清零一次；同一扫描周期内**后续收敛识别分支仍可能用 `(1+PT1K*系数)` 重新赋值** `PT1/TI1`（如负 `PT1K` 会得到负增量）。Python 按**实际执行路径**实现，**不**按注释"整周期禁用"。`tests/test_blocks_apcpidzzd.py::TestCoefficientCommentConflict::test_negative_coefficient_still_adjusts_in_identify` 锁死（`PT1K=-0.5` 顶部清零后识别分支仍得 `PT1=-5`）。 |
| **APCPIDZZD-COMMENT-2** | `JSSJF` 注释写"正积算"实为负积算路径（按代码实现） | accepted | 🔒 locked | 源 ST 第 41 行 `JSSJF` 中文注释写为"正积算时间均值"，但**实际代码**（第 247~249 行）仅在 `IF FJSBZ`（负积算结束）路径中更新和使用它：`HLLIM1(IN:=JSSJ,HL:=JSSJF*1.5,LL:=JSSJF*0.5); JSSJF:=JSSJF*0.95+0.05*HLLIM1.AV;`。Python 按**实际负积算路径**实现（`_update_mean_f`）；正积算均值是 `JSSJZ`（`_update_mean_z`）。 |
| **APCPIDZZD-ACCUM-1** | TON1 延时窗口内 HSACCUM1 持续累加（源级语义推导，待实机对照） | accepted | 🟡 monitor | 源 ST 在 `TON1.Q=False` 的整个 5 秒延时窗口内每拍走 ELSE 分支调用 `HSACCUM1(RS:=TRUE)`；而已锁定的 `APCHSACCUM` 语义里每次调用都**先执行 `AV:=AV+MC*I1`** 再判断 `RS` 上升沿，且 CODESYS 中 `I1` 输入脚**保持上次赋值**（上次有效积算的 `ABS(PV-SP)`）。据此**推导**：延时窗口内 `AV` 会按保留的 `I1` **持续增长**（仅首拍 `RS` 上升沿复位到 `IV`，其后 `RS` 持续为真不再复位）。**口径**：本结论是"依据当前 ST 调用路径 + CODESYS 功能块输入保持语义 + 已锁定 `APCHSACCUM` 源逻辑"的**源级语义推导**，**尚未以真实 SoftPLC 500ms 跟踪数据做黄金对照**；非缺陷判定。Python 通过记忆 `_hsaccum_last_I1` 并在复位调用回传，忠实复现该推导（冷启动 `_hsaccum_last_I1=0.0`，对齐 REAL 默认零值；不修改 `APCHSACCUM` 本体）。`tests/test_blocks_apcpidzzd.py::TestEndShiftReset::test_history_shift_order`（面积行 `[12,23,25]`）与 `TestAccumAndDirection::test_reset_call_retains_last_i1`（`LR=复位前AV+保留I1`）锁死该推导下的可观测结果。 |
| **APCPIDZZD-IF-1** | 两个独立 `IF` 不得合并为 `elif` | accepted | 🔒 locked | `IF PV>SP`/`IF PV<SP`（方向标志）与 `IF ZJSBZ`/`IF FJSBZ`（结束积算）均为**独立 `IF`**，源 ST 不写成互斥 `elif`。`PV==SP` 时两个方向 IF 都不触发（标志保持）；冷启动 `ZJSBZ=FJSBZ=True` 时结束拍两个分支按序各自执行（是否真正写历史仍由 `JSSJ>5` 决定）。`tests/test_blocks_apcpidzzd.py::TestEndShiftReset::test_cold_start_both_flags_execute` 锁死。 |
| **APCPIDZZD-GATE-1** | 授权门控严格保序 + 失败只累加 BD_ERROR5 | accepted | 🔒 locked | 每拍先调用一次 `KZQBDYZMK.step`，再读 `OK % 10000`；通过才执行全部自整定逻辑（提前 `return` 实现"失败分支"）。授权失败时**只**累加 `license_context.BD_ERROR5`（REAL，`>999999999→100000000.0`），**不**重置/推进/限幅任何 `PT1/TI1`/定时器/积算/边沿状态。`BD_ERROR5` 取自注入的 `LicenseContext`，**不**建模块级单例。后续 `APCPID` 迁移时内外层各自调用授权块的源行为须保留，本轮不去重。`tests/test_blocks_apcpidzzd.py::TestAuthGate` 锁死（含每拍仅一次验证调用）。 |
| **APCPIDZZD-RESET-1** | 非自动状态（RM≠1）精确复位边界 | accepted | 🔒 locked | `RM≠1` 时源 ST **只**清零 `JS_Z`/`JS_F` 全数组、`ZJSBZ/FJSBZ:=FALSE`、`JSSJ/JSSJ2:=0`；**不**复位 `PT1/TI1/SQSJ/ZDPC/JSSJZ/JSSJF` 与 `TON1/TON2/HSACCUM1/R_TRIG1/R_TRIG2/HLLIM1` 实例状态。这些保留是源 ST 真实行为，不得"看起来更合理"地一并复位。`tests/test_blocks_apcpidzzd.py::TestNonAutoReset::test_non_auto_resets_and_preserves` 锁死。 |
| **APCPIDZZD-CLAMP-1** | 输出限幅固定在自动状态主逻辑末尾 | accepted | 🔒 locked | `PT1:=MIN(MAX(PT1,-0.7*PT),PT*3)`、`TI1:=MIN(MAX(TI1,-0.7*TI),TI*3)` 仅在 `RM=1` 分支末尾执行一次（在 `IF TON1.Q ... ELSE ... END_IF` 之后）。不得提前到中间分支、不得用绝对值、不得额外纠正 `PT/TI` 非法输入。`tests/test_blocks_apcpidzzd.py::TestOutputClamp` 锁死（上限 `3*PT`/`3*TI`、下限 `-0.7*PT`/`-0.7*TI`）。 |
| **APCPIDZZD-PARAM-1** | 不承担参数校验与物理输出安全门控 | accepted | ⏸ deferred (Runtime 阶段) | 本块只产出逻辑结果（`PT1/TI1` 与内部状态），**不**做入参校验 / 异常保护 / 容错 / 默认授权通过后门；`PT/TI/PVMU/PVMD/MU/MD` 等参数合法性由配置装载层 `RUNTIME-PARAM-VALIDATION` 兜底，最终物理输出安全链由 Runtime/MainProgram 闭环。`APCPID` 与 Runtime 调度本轮不实现。 |

---

## 五-J、APCPID（变比例变积分 PID 调节器）相关（APCPID 系列）

> 变比例变积分 PID 调节器。ST 源：`/Users/guangyaosun/Desktop/APCPID.txt`。
> 复用 `APCPIDZZD`（嵌套自整定）/ `LicenseContext.KZQBDYZMK`（授权门控）；
> 携带跨扫描状态；通过构造函数注入 `LicenseContext`，`PIDZZD1` 与本块共享同一
> `LicenseContext`。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCPID-CYCLE-1** | 使用内部 `CYCLE` 参与 PID 公式，`dt_ms` 仅用于授权/嵌套块 | accepted | 🔒 locked | APCPID 的 PID 公式（`B1/C1..C4/DU_TEMP`）使用源 ST 内部 `CYCLE` 变量（默认 0.5 秒，绑定 500ms 任务），而 `dt_ms` **仅**用于外层授权 `KZQBDYZMK.step(dt_ms)` 与嵌套 `PIDZZD1.step(dt_ms, ...)`。**不得**用 `dt_ms/1000` 自动替换 `CYCLE`，**不得**因 `dt_ms != 500` 缩放公式，也不拒绝非 500ms 输入。默认语义在 `dt_ms=500 / CYCLE=0.5` 下验证。`tests/test_blocks_apcpid.py::TestCycleAndParamCorrection::test_pid_uses_cycle_not_dt_over_1000`（dt=1000 仍 `B1=1.0`，反证非 `dt/1000`）+ `test_cycle_le_zero_set_half_and_persists` 锁死。 |
| **APCPID-ORDER-1** | `TIi` 的 `SVH` 判断使用上一拍遗留 `EK` | accepted | 🔒 locked | 源 ST 顶部 `IF ABS(EK)>=SVH_SJ THEN TIi:=TI+ABS(SP-PV)*KI+TI1` 发生在本拍 `EK:=0.9*EK_LAST+0.1*(PV+IC-SP)` 重算之前，使用的是**上一拍**遗留的实例状态 `EK`（`EK` 是 ST `VAR`，跨拍保留）。Python 严格按实际 ST 顺序：`self.EK` 为持久化实例属性，顶部 `TIi` 判断读旧值，随后才重算 `self.EK`。`tests/test_blocks_apcpid.py::TestOldEKOrder::test_tii_uses_previous_ek_then_ek_recomputed` 锁死（预置旧 EK=100 走 SVH 分支得 `TIi=40`，同拍 EK 重算为 1.0）。 |
| **APCPID-RM-1** | RM 注释称非法值保持前状态，实际非 0/3/4 进入自动 | accepted | 🔒 locked | 源 ST RM 注释（第 16 行）称"填入其它非法值，一律保持前运行方式"，但**实际代码**仅对 `RM=3/4`（跟踪）、`RM=0`（手动）做显式分支，其余值（含 `RM=1`、`RM=2`、任意非法值）一律进入 `ELSE` 自动 PID 路径。Python 按**实际路径**实现，**不**按注释"保持前状态"。`tests/test_blocks_apcpid.py::TestAutoCorePID::test_rm1_rm2_illegal_all_take_auto`（RM=1/2/99 同走自动得 `AV=5`）+ `TestCommentConflicts::test_illegal_rm_enters_auto_not_hold_previous` 锁死。 |
| **APCPID-INPUT-1** | `RM/SP/KD/TD` 是 VAR_INPUT，本拍局部改写不持久化 | accepted | 🔒 locked | 源 ST 在本拍内改写 `VAR_INPUT` 的 `RM`（ATE 段）、`SP`（`TM` 跟踪段）、`KD`（`KD<=0→0.001`）、`TD`（`TD<0→0`）。Python 将其实现为**仅影响当前 `step()` 余下路径及传给 `PIDZZD1` 的参数**的局部变量（`rm/sp/kd/td`），**不**把该改写伪造为调用方下一拍输入。与之相对，`CYCLE/MU/TIi/PTt` 等 ST `VAR` 的改写**必须**持久化为实例属性。`tests/test_blocks_apcpid.py::TestCommentConflicts::test_kd_td_local_not_persisted_across_scans`（第一拍 KD=0 局部 0.001 得 `B1=1000`，第二拍 KD=1 得 `B1=1`）+ `TestCycleAndParamCorrection::test_kd_le_zero_local_only_affects_this_scan`（`not hasattr(p,'KD')`）锁死；持久化侧 `test_mu_minus_md_zero_corrected_and_persists` 锁死。 |
| **APCPID-OUTRL-1** | `OutRL` 实为自动末尾输出提交阈值，非常规限速器 | accepted | 🔒 locked | 源 ST `OutRL` 注释为"调节器输出变化率下限"，但**实际行为**是：仅在**自动模式末尾**以 `IF ABS(AV-AV_TEMP)>ABS(OutRL) THEN AV:=AV_TEMP;` 决定是否把本拍计算的 `AV_TEMP` 提交给 `AV`（严格 `>`，且对 `OutRL` 取 `ABS`）。`OutRL=0` 时只要 `AV≠AV_TEMP` 即提交；该逻辑**只**在自动分支，不在手动/跟踪分支。Python 按实际行为实现，**不**按名称重构为常规限速器。`tests/test_blocks_apcpid.py::TestOutRL`（差值 `>ABS(OutRL)` 才更新 / 等于不更新 / 负 `OutRL` 仍用 `ABS`）+ `TestCommentConflicts::test_outrl_is_av_commit_threshold_not_rate_limiter` 锁死。 |
| **APCPID-ZZD-1** | `PIDZZD1` 在 PID 历史更新之后调用，`PT1/TI1` 下一拍才生效 | accepted | 🔒 locked | 源 ST 在历史状态更新（`DU_1/UK_1/EK_1/EK_2/PV_LAST/DEK_1/DEK_2`）**之后**才调用嵌套 `PIDZZD1(...)`，随后 `PT1:=PIDZZD1.PT1; TI1:=PIDZZD1.TI1;`。因此本拍 PID 主计算（`PX`/`TIi`）用的是**上一拍**遗留的 `PT1/TI1`，新值仅供**下一拍**使用。传入 `PIDZZD1` 的 `RM/SP` 是本拍经 ATE/TM 处理后的局部值。Python 严格保证：先更新历史 → 调 `PIDZZD1.step(dt_ms, ...)` → 回写 `PT1/TI1`。`tests/test_blocks_apcpid.py::TestPidzzdOrderAndDelay`（旧 `PT1/TI1` 进本拍 `PX=12`/`TIi=23`、历史先于 PIDZZD1 更新、回写一致）锁死。 |
| **APCPID-GATE-1** | 双层授权严格保序，授权通过一拍调用 2 次（不去重） | accepted | 🔒 locked | 每拍先调用一次**外层** `KZQBDYZMK.step(dt_ms)` 再读 `OK%10000`；通过才执行主 PID 逻辑，逻辑末尾调用一次 `PIDZZD1.step`，而 `APCPIDZZD` 内部**又**调用一次 `KZQBDYZMK.step`——故授权通过的完整扫描共发生 **2 次** 授权调用（外层 1 + 内层 1），**不得**去重/缓存/优化为"一拍只授权一次"；两次共享注入式同一时间（不自行推进时间）。授权失败时只累加注入式 `BD_ERROR1`（REAL，`>999999999→100000000.0`）并立即 `return`，**不**调用 `PIDZZD1`、**不**推进或重置任何 PID 状态。沿用 `APCPIDZZD-GATE-1` 的无单例/无默认通过后门约定，`LicenseContext` 构造注入。`tests/test_blocks_apcpid.py::TestAuthGate`（成功 2 次 / 失败 1 次、失败保持全状态且跳过 PIDZZD1、`BD_ERROR1` 累加与回绕、下一拍恢复）锁死。 |
| **APCPID-DEADVAR-1** | `nowRM/deadenter/C2/C3/C4` 源声明/计算但不参与活动输出，保留不删 | accepted | 🔒 locked | `nowRM`（目前运行方式）与 `deadenter`（首次进入死区标志）在源 ST `body` 中**从未被引用**；`C2/C3/C4` 在自动非死区分支**被计算**但活动 `DU_TEMP` 公式（第 272 行）**未使用**（旧差分公式第 270 行已注释）。Python 忠实保留：`nowRM/deadenter` 为默认零实例属性不启用，`C2/C3/C4` 每拍照常计算更新。**不得**因"未使用"删除或优化。`tests/test_blocks_apcpid.py::TestInitialState::test_uninitialized_states_zero` + `TestAutoCorePID::test_intermediate_coefficients`（锁 `C2=10.125`/`C3=-15`/`C4=5`）锁死。 |
| **APCPID-PARAM-1** | 不承担参数校验与物理输出安全门控 | accepted | ⏸ deferred (Runtime 阶段) | 本块只产出逻辑结果（`AV` 与内部状态），**不**做入参校验 / 异常保护 / 容错 / 默认授权通过后门；`PT/TI/OutT/OutB/OutRH/OutRL/PVMU/PVMD/MU/MD` 等参数合法性由配置装载层 `RUNTIME-PARAM-VALIDATION` 兜底，最终物理输出安全链由 Runtime/MainProgram 闭环（`RUNTIME-GATE` / `RUNTIME-STARTUP-INHIBIT`）。MainProgram 与 Runtime 调度本轮不实现。 |

---

## 五-K、APCSPFINDER（分析用设定值自动寻找）相关（APCSPFINDER 系列）

> 分析用设定值（SP）自动寻找功能块。ST 源：`/Users/guangyaosun/Desktop/APCSPFINDER.txt`。
> **纯状态/数值逻辑块**：无 FB 依赖、无授权门控、不接 `LicenseContext`、不读系统时钟；
> 携带跨扫描状态。仅供自动参数推荐算法内部选 `SP_USE`，不写现场控制 SP。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCSPFINDER-CYCLE-1** | 稳定段时间来自输入 `CYCLE`，非 `dt_ms` | accepted | 🔒 locked | 稳定段累计严格用 `CYCLE_S=MAX(CYCLE,0.001)`、`STABLE_T:=STABLE_T+CYCLE_S`（源 ST 第 72/138 行），`dt_ms` 仅为统一 `step` 接口保留（`del dt_ms`），**不**参与累计、**不**用 `dt_ms/1000` 替换 `CYCLE`、**不**因 `dt_ms != 500` 缩放。`CYCLE` 是本拍 `VAR_INPUT`，**不**像 APCPID 的内部 `VAR CYCLE` 那样持久化改写。`CYCLE<=0 → CYCLE_S=0.001`。`tests/test_blocks_apcspfinder.py::TestCycleVsDtMs`（同 CYCLE 不同 dt_ms → `STABLE_T` 相同；不同 CYCLE 同 dt_ms → `STABLE_T` 按 CYCLE 缩放）+ `TestThresholds::test_cycle_zero_or_negative` 锁死。 |
| **APCSPFINDER-EN-1** | EN 只控制自动稳定段寻找，其余逻辑不受 EN 影响 | accepted | 🔒 locked | 源 ST 中 `EN`（与 `NOT RESET` 一起）仅包裹"自动 SP 稳定段寻找"段（第 118~170 行，含 `PV_1/AV_1` 更新）。基础阈值计算（72~98，无条件）、`RESET` 处理（101~115）、`SP_TAG_BAD` 计算（173~176）、最终 `SP_USE` 四级优先级（179~207）均**不**受 `EN=False` 影响，**不得**因 `EN=False` 提前 `return`。故 `EN=False` 时：阈值仍更新、稳定段不推进、`PV_1/AV_1` **不**更新、`SP_TAG_BAD` 仍重算、`SP_USE` 仍重选（且可使用历史 `SP_AUTO`）。`tests/test_blocks_apcspfinder.py::TestENFalse` 锁死。 |
| **APCSPFINDER-HOLD-1** | 已确认的自动 SP 在不稳定/SAMPLE_OK=False 时保留 | accepted | 🔒 locked | 不稳定段（`D_PV>PV_TH` 或 `D_AV>AV_TH`）仅清当前稳定段统计（`STABLE_ACTIVE/T/N/PV_SUM`，`PV_MAX/MIN` 回到当前 PV），**不**清 `SP_AUTO/SP_AUTO_OK/SP_AUTO_CONF`（资格 IF 因 `STABLE_N>0` 不成立而跳过更新，旧值留存）。`SAMPLE_OK=False`（且 `SP_AUTO_EN=True`）时两分支都不进入，稳定段与 `SP_STABLE_T_OUT/PV_RANGE` 全部冻结，但 `PV_1/AV_1` 仍在 EN 块末尾更新。仅 `RESET` 或 `SP_AUTO_EN=False` 改变可用性（后者只清 `SP_AUTO_OK/CONF`，保留 `SP_AUTO` 与稳定段）。`tests/test_blocks_apcspfinder.py::TestHistoryHold` 锁死。 |
| **APCSPFINDER-RESET-1** | RESET 不提前返回，当拍仍执行最终 SP 选择 | accepted | 🔒 locked | `RESET=True` 清自动寻找内部状态（`INIT_DONE/STABLE_*/SP_AUTO/SP_AUTO_OK/SP_AUTO_CONF/SP_TAG_BAD/SP_STABLE_T_OUT/SP_STABLE_PV_RANGE`，`PV_MAX/MIN:=PV`），但**不**重置 `PV_1/AV_1/D_PV/D_AV/PV_TH/AV_TH` 等未在源 RESET 段出现的变量；`RESET` 不依赖 `EN`。`RESET` 当拍因 `EN AND NOT RESET` 为假**不**进入稳定段，但其后 `SP_TAG_BAD` 与最终 `SP_USE` 优先级**仍执行**（人工/现场 SP 仍可被选；无则 `SP_USE=PV` 且 `SP_VALID=False`）。**不得** `RESET` 后提前 `return`。`tests/test_blocks_apcspfinder.py::TestResetTick` 锁死。 |
| **APCSPFINDER-EDGE-1** | EN/SP_AUTO_EN/历史 SP_AUTO_OK/允许替换 四者交叉的反直觉边缘路径 | accepted | 🔒 locked | **窄边缘组合**：历史 `SP_AUTO_OK=True` + `EN=False` + `SP_AUTO_EN=False` + `SP_TAG_EN=True` + `SP_AUTO_REPLACE_BAD_TAG=True` + 现场 `SP_TAG` 与历史 `SP_AUTO` 偏差 `>SP_BAD_TH` 时：① `EN=False` 使整段自动块（含 `ELSIF NOT SP_AUTO_EN` 的 `SP_AUTO_OK:=FALSE`，源第 163-165 行）**不执行**，历史 `SP_AUTO_OK` 保留为 True；② `SP_TAG_BAD` 据历史 `SP_AUTO` 重算为 True（第 174-176 行）；③ 现场分支因 `NOT(SP_AUTO_REPLACE_BAD_TAG AND SP_TAG_BAD AND SP_AUTO_OK)=NOT(True)=False` 被排除（第 184 行）；④ 自动分支又被 `SP_AUTO_EN=False` 拦下（第 193 行）；⑤ 落 `ELSE` → `SP_USE=PV / SP_VALID=False / SP_SOURCE=0 / SP_REASON=4`（第 202-207 行）。此路径与"现场 SP 优先于自动 SP"的注释直觉略有出入，**疑似源 ST 设计缺口**（自动寻找已禁用却仍因历史自动 SP 否决有效现场 SP），但也可能是原设计者意图"`EN/SP_AUTO_EN` 关闭时整块不输出可信 SP"。当前迁移**按源 ST 原样保留，不自行修复**；若未来业务确认"自动寻找关闭时现场 SP 仍应正常使用"，再单开源业务修正任务（最小修正通常是给现场替换条件追加 `SP_AUTO_EN`）。`tests/test_blocks_apcspfinder.py::TestENFalse::test_en_false_auto_disabled_history_ok_replace_falls_to_pv` 回归锁定。 |
| **APCSPFINDER-ANALYSIS-1** | SP_USE 仅为分析推荐输入，不写现场控制 SP | accepted | 🔒 locked | 本块输出 `SP_USE` 是自动参数推荐算法的分析设定值，**不**参与现场实际控制 SP 写入，**不**直接修改 `APCPID/APCPIDZZD` 或任何现场 SP；优先级 `SP_MAN(源1/因1) > SP_TAG(源2/因2，可疑仍用→因5) > SP_AUTO(源3/因3，替代可疑现场→因6)`，无有效 SP 时 `SP_USE=PV / SP_VALID=False / 源0 / 因4`（`SP_USE=PV` 仅为临时占位避免下游虚假大误差，不加额外限幅）。`SP_SOURCE/SP_REASON` 编码不得改动。本块不接 `LicenseContext`、不新增授权门控、不引入 TON/R_TRIG/累计器。`tests/test_blocks_apcspfinder.py::TestFinalPriority`（6 条优先级/编码）+ `TestInitialState::test_no_context_or_auth_attrs` 锁死。 |

---

## 五-L、APCRSFNAUTOPARA（RSFN 自动参数推荐）相关（APCRSFNAUTOPARA 系列）

> RSFN 自动参数推荐功能块。ST 源：`/Users/guangyaosun/Desktop/APCRSFNAUTOPARA.txt`。
> 窗口统计 + 历史窗口三阶段相似融合，只推荐 `*_REC` 参数，**不**写现场控制 SP 或
> `APCRSFN` 实际参数；**复用**已迁移 `APCSPFINDER` 真实子实例（`self.SPF1`），
> **不**接 `LicenseContext`、**不**新增授权门控、**不**读系统时钟、**不**引入 TON/
> R_TRIG/累计器。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCRSFNAUTOPARA-CYCLE-1** | 窗口/面积/统计时间来自输入 `CYCLE`，非 `dt_ms` | accepted | 🔒 locked | 时间严格用 `CYCLE_S=MAX(CYCLE,0.001)` 推进（`WIN_ELAPSED/面积/区间停留/SP 寻找` 均由其驱动）；`dt_ms` 仅为统一 `step` 接口保留并转发给 `SPF1`（其内部 `del dt_ms` 忽略），**不**参与累计、**不**用 `dt_ms/1000` 替换、**不**因 `dt_ms != 500` 缩放。`CYCLE<=0 → CYCLE_S=0.001`。`tests/test_blocks_apcrsfnautopara.py::TestCycleDtRange`（`test_cycle_zero_or_negative` + `test_same_cycle_diff_dt_ms_identical`）锁死。 |
| **APCRSFNAUTOPARA-RANGE-1** | 量程无效时内部临时 100，但 RANGE_OK 仍 False | accepted | 🔒 locked | `OUT_RANGE:=OUT_RANGE_USE`（来自 `PHY_RANGE_EN ? |PHY_MU-PHY_MD| : |MU-MD|`），`RANGE_OK:=OUT_RANGE>0` 在 `IF OUT_RANGE<=0 → OUT_RANGE:=100` **之前**取值；故量程无效时内部计算用临时 100 继续，但 `RANGE_OK=False`，并使 `WINDOW_VALID=False`、`DATA_REASON=3`。100 是源码内部临时量程，不得替换。`TestCycleDtRange::test_invalid_range_temp_100_but_range_ok_false` + `TestDataReason::test_reason_3_range_invalid` 锁死。 |
| **APCRSFNAUTOPARA-RESET-1** | RESET 漏清 WIN_SP/PV/AV_SUM（修复版已修） | fixed | 🔒 locked | **原始基线缺陷**：RESET 注释为"复位全部统计"，但 RESET 块**未**清零 `WIN_SP_SUM/WIN_PV_SUM/WIN_AV_SUM`，中途 RESET（未完成窗口）后下一窗口均值 `WIN_*_AVG` 会带入残留累计（数据正确性 bug）。**修复版基线**（`APCRSFNAUTOPARA_fixed.txt`，RESET 块新增三行清零）已修复，Python 同步：RESET 当拍这三项一并归零，下一窗口均值不再被污染。RESET 块仍只清 `H_VALID/H_WEIGHT` 两个历史数组（其余 `H_*` 靠 `H_VALID=False` 屏蔽，此为有意设计，非缺陷）。`TestEdgeSemantics::test_reset_clears_sp_pv_av_sum` + `test_reset_no_residual_in_next_window_avg` 锁死。 |
| **APCRSFNAUTOPARA-RUNNING-1** | RUNNING 直接镜像 EN | accepted | 🔒 locked | `RUNNING:=EN`（401，在初始化块之后）。故 `EN=True` 且 `RESET=True` 时 `RUNNING=True`，但数据采集分支 `IF EN AND NOT RESET` 不执行（不采样）。`RUNNING` 不得改为"本拍是否实际采样"。`TestInitColdStartReset::test_en_true_reset_true_running_but_no_sample` 锁死。冷启动首拍 `EN=True/RESET=False` 会先进初始化块（含一次 `SPF1` reset 调用）再进采集块（第二次 `SPF1` 调用），同一拍对 `SPF1` 调用两次，不得提前 return。 |
| **APCRSFNAUTOPARA-SPFINDER-1** | 仅复用 APCSPFINDER 取分析 SP，不写现场 SP | accepted | 🔒 locked | 通过 `self.SPF1`（真实跨扫描 `APCSPFINDER` 实例）获取 `SP_USE` 等并镜像为本块输出；`SP_USE` 仅供自动参数推荐分析，**不**写入现场控制 SP 或 `APCRSFN` 实际设定值。内部偏差用 `SP_WORK:=SP_USE`，`NOT SP_VALID → SP_WORK:=PV`（但 `SP_USE` 仍保留 `SPF1` 真实输出）。`SPF1` 调用固定 `PVMU=MAX(E4_IN*2,1)/PVMD=0/OUTT=OUT_RANGE/OUTB=0`，不得改为实际过程 PV 量程。`TestSpfinderIntegration` 锁死。 |
| **APCRSFNAUTOPARA-FUSION-1** | 当前有效窗口先入历史、后同拍参与三阶段融合；弱推荐 → RSF_REASON=5 | accepted | 🔒 locked | 有效窗口先写 `H_*`（`HISTORY_COUNT/H_IDX` 更新）再进入 `FOR MATCH_STAGE 1..3` 相似融合，故当前窗口同拍即可自匹配；每阶段仅在 `NOT FINAL_STRONG` 时执行且开头清零 `SIMILAR_COUNT/FUSE_SUM_W/全部 *_REC`（不叠加前阶段）。`FINAL_STRONG=(SIMILAR_COUNT>=FUSE_MIN_N) AND (FUSE_SUM_W>=FUSE_MIN_WEIGHT)`，`FINAL_WEAK=(FUSE_SUM_W>0) AND NOT FINAL_STRONG`，`FINAL_VALID=FINAL_STRONG`，`RSF_OK=FINAL_VALID AND WINDOW_VALID`；`NOT FINAL_VALID → RSF_REASON:=5`（覆盖单窗口的慢/振荡/噪声原因码）。`FUSE_SUM_W=0` 时 `*_REC` 回退到 `W_*`。`TestFusion` 锁死。 |
| **APCRSFNAUTOPARA-CALC-1** | CALC_NOW 仅上升沿可提前结算；CALC_OLD 每拍末尾更新 | accepted | 🔒 locked | `CALC_R:=CALC_NOW AND NOT CALC_OLD`（内部边沿，非 R_TRIG）。窗口结束 `WIN_ELAPSED>=MAX(WIN_T,MIN_WIN_T) OR (CALC_R AND WIN_ELAPSED>=MIN_WIN_T)`；持续 True 不重复结算。`CALC_OLD:=CALC_NOW` 在每拍末尾（909）无条件更新，`EN=False/RESET=True` 亦然——故 `EN=False` 期间高电平不会在重新使能时被误判为新上升沿。末尾 `ERR_1/PV_1/AV_1/TP_1/RSF_LEVEL_1/RSF_LOCK_LEVEL_1` 同样无条件更新（`ERR_1` 用本拍 `ERR`，而 `ERR` 仅在采集块重算、EN=False 时保留旧值）。`TestCalcEdge` + `TestEdgeSemantics::test_en_false_updates_tail_state` 锁死。 |
| **APCRSFNAUTOPARA-DATAREASON-1** | DATA_REASON=2 结算点不可达（死分支，按源保留，不同步 Bug2 实时补丁） | accepted | 🔒 locked | 两个窗口结算条件都要求 `WIN_ELAPSED>=MIN_WIN_T`（`MAX(WIN_T,MIN_WIN_T)>=MIN_WIN_T`，及显式 `CALC_R AND WIN_ELAPSED>=MIN_WIN_T`），故进入结算 `DATA_REASON` 判断时 `WIN_ELAPSED>=MIN_WIN_T` 恒成立，`ELSIF WIN_ELAPSED<MIN_WIN_T → 2` 分支**永不命中**（即使 `WIN_T<MIN_WIN_T`）。这是源代码遗留的诊断/可观测性死分支，**不**影响推荐参数计算。**Bug2 实时补丁已撤回不同步**：ChatGPT5.5 修复版曾在采集块内新增 `IF WIN_ELAPSED<MIN_WIN_T THEN DATA_REASON:=2` 让其实时可见，但 `DATA_REASON` 与 `WINDOW_T/ERR_*/PV_DELTA/AV_DELTA` 同属"最近完成窗口快照"语义，实时写入会把新窗口的 `DATA_REASON=2` 与上一完成窗口的 `WINDOW_T` 等错位输出，破坏接口一致性。经复核（双方一致）该补丁不应作为新基线，正式基线为"只修 Bug1"。死分支按源码原样保留、**不**删除；若将来画面需"当前窗口还差多少时间"，应单独新增 `CURRENT_WINDOW_T/CURRENT_DATA_REASON` 实时输出，不复用本快照组。`TestDataReason::test_reason_2_unreachable_at_settle` + `test_reason_2_not_written_during_accumulation` 锁死（其余 3/6/4/5/1 均有专项可达测试）。 |
| **APCRSFNAUTOPARA-START-1** | 冷启动首拍变化量相对零基准 | accepted | 🔒 locked | 初始化块（`RESET OR NOT INIT_DONE`，332-399）**未**初始化 `PV_1/AV_1/TP_1/ERR_1/RSF_LEVEL_1/RSF_LOCK_LEVEL_1`，它们冷启动默认 0；而冷启动首拍 `EN=True/RESET=False` 同一拍即进入采集分支，故第一笔样本 `D_PV=ABS(PV-0)`、`D_AV=ABS(AV-0)`、`ABS(TP-TP_1)=ABS(TP-0)`。若 `PV/AV/TP` 初值非零，首拍会额外抬高 `WIN_NOISE_SUM` 或产生首拍手动/RSF 事件（长窗口下被稀释；短窗口或 `CALC_R` 提前结算时可能影响 `NOISE_EST`/`WINDOW_VALID`/事件计数）。Python 按源码原样保留——**不**在初始化块额外重置这些"上一拍"变量（额外初始化会改变源 ST 首拍语义）；末尾（909-915）仍无条件把它们更新为当前输入。`TestEdgeSemantics::test_cold_start_first_sample_uses_zero_baseline` + `test_init_block_does_not_reset_prev_baselines` 锁死。 |

---

## 五-M、APCMAUTOPARA（APCM 自动参数推荐）相关（APCMAUTOPARA 系列）

> APCM 自动参数推荐功能块。ST 源：`/Users/guangyaosun/Desktop/APCMAUTOPARA.txt`。
> 窗口统计 + PID/RSF/观测器/重叠控制四组单窗口推荐 + 历史三阶段相似融合，
> 只输出 `*_REC` 推荐值与状态，**不**写 APCM 实际控制参数或现场 SP；**复用**
> 已迁移 `APCSPFINDER` 真实子实例（`self.SPF1`），**不**接 `LicenseContext`、
> **不**新增授权门控、**不**读系统时钟。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCMAUTOPARA-CYCLE-1** | 业务累计时间来自 `CYCLE_S`，非 `dt_ms` | accepted | 🔒 locked | 窗口/面积/半波/噪声/手动合并/响应观察等一律由 `CYCLE_S=MAX(CYCLE,0.001)` 驱动；`dt_ms` 仅为统一 `step` 接口保留并转发给 `SPF1`（其内部 `del dt_ms` 忽略），**不**参与本模块任何累计/缩放。`CYCLE<=0 → CYCLE_S=0.001`。`tests/test_blocks_apcmautopara.py::TestCycleDtRange` 锁死。 |
| **APCMAUTOPARA-RESET-1** | `EN=True&RESET=True` 时先复位后本拍仍进入 `IF EN` 采集 | accepted | 🔒 locked | 源 ST 顶层顺序：`IF RESET OR NOT INIT_DONE` 初始化 → `RUNNING:=EN` → `IF EN` 采集。**不得**改写为 `IF EN AND NOT RESET`（与 `APCRSFNAUTOPARA` 不同）。故 `EN=True&RESET=True`：`RUNNING=True`，本拍 `WIN_ELAPSED+=CYCLE_S` 且可重新累计采样；RESET 块清零 `WIN_SP/PV/AV_SUM` 等窗口累计及 `MAN_RESP_ACTIVE`（仅复位块清，窗口结算不清）。默认回退块（483-519）在复位块**之前**执行，条件 `(HISTORY_COUNT=0) OR RESET OR NOT INIT_DONE`，故 RESET 当拍 `*_REC` 回退到当前输入参数。冷启动/复位且 `EN=True` 时 `SPF1` 当拍被调用两次（复位初始化 `EN=False,RESET=True` + 正常路径 `EN=True,RESET=当前输入`）。`TestResetEn` 锁死与 `APCRSFNAUTOPARA` 的差异。 |
| **APCMAUTOPARA-SPFINDER-1** | 仅复用 APCSPFINDER 提供分析 SP | accepted | 🔒 locked | 通过 `self.SPF1`（真实跨扫描 `APCSPFINDER` 实例）获取 `SP_USE` 等并镜像为本块输出；`SP_USE` 仅供推荐分析，**不**写入 APCM 现场控制 SP。`SPF1` 调用固定 `OUTT=MU/OUTB=MD`（物理量程，非 `OUTT/OUTB` 限制量程）；内部偏差用 `SP_WORK:=SP_USE`，`NOT SP_VALID → SP_WORK:=PV`。`TestSpFinderIntegration` 锁死。 |
| **APCMAUTOPARA-DATAREASON-1** | DATA_REASON 为最近完成窗口快照；2 为结算点死分支 | accepted | 🔒 locked | `DATA_REASON/WINDOW_T/ERR_*/PV_DELTA/AV_DELTA` 同属"最近完成窗口快照"，仅在窗口结算时更新。**不得**在累计阶段实时写 `DATA_REASON=2`（会破坏快照一致性，与 APCRSFNAUTOPARA Bug2 补丁同理已撤回）。结算块内 `ELSIF WIN_ELAPSED<MIN_WIN_T → 2` 在当前结算条件（恒 `WIN_ELAPSED>=MIN_WIN_T`）下不可达，为源遗留分支，原样保留。`TestWindowSnapshot::test_data_reason_2_not_written_during_accumulation` + `test_data_reason_2_unreachable_at_settle` 锁死。 |
| **APCMAUTOPARA-MANRESP-1** | 手动响应观察可跨窗口保留 | accepted | 🔒 locked | `MAN_RESP_ACTIVE/MAN_RESP_CT` 在窗口结算时**不**清除（ST 1538-1585 只清 `MAN_EVENT_CNT/MAN_GAIN_*` 等窗口统计）；一次手动动作的响应观察可在后续窗口继续并在该窗口计入 `MAN_EVENT_CNT/WIN_EVENT_N`。RESET 块会清 `MAN_RESP_ACTIVE`（ST 622），与窗口结算语义不同。`TestManRespCrossWindow` 锁死。 |
| **APCMAUTOPARA-HISTORY-1** | 历史缩小时只清指定摘要项；H_PT~H_TZ_CD 由 H_VALID 屏蔽 | accepted | 🔒 locked | `INIT_DONE=True` 且 `H_N<>H_N_OLD` 时才处理 `HISTORY_N` 变化；缩小时 `H_VALID[H_N+1..24]:=False` 并清零 `H_EVENT_N/H_SP_AVG/H_PV_AVG/H_AV_AVG/H_ERR_ABS_AVG/H_NOISE_EST/H_GAIN`，**不**逐项清 `H_PT~H_TZ_CD` 推荐缓存；增大时不伪造历史。有效窗口先写 `H_IDX` 再同拍三阶段融合；每阶段 `NOT FINAL_STRONG` 才执行且清零重算。`TestHistoryFusion` 锁死。 |

---

## 五-N、APCM（APC 智能综合控制模块）相关（APCM 系列）

> APC 智能综合控制模块。ST 源：`/Users/guangyaosun/Desktop/APCM.txt`。
> 综合 PID、RSF、观测器、重叠控制、PIDZZD 自整定与 APCMAUTOPARA 推荐应用。
> Python 以长期存在的 `APCM(license_context)` 对象模拟 CODESYS 普通 `VAR` 区 FB
> 实例；本轮不实现断电/重启后的 RETAIN/PERSISTENT 文件持久化。

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **APCM-INSTANCE-STATE-1** | 跨扫描状态来自 FB 实例生命周期，不依赖全局 RETAIN | accepted | 🔒 locked | CODESYS 中普通 `VAR APCM_1 : APCM;` 的成员变量、嵌套 FB、触发器、定时器和可调参数均跨扫描保留；`RETAIN/PERSISTENT RETAIN` 只涉及重启恢复，不等于“下一扫描周期是否保持”。Python 侧用同一个长期存在的 `APCM` 对象模拟，**不**实现状态文件/序列化/断电恢复。`tests/test_blocks_apcm.py::TestInitialAndInstanceState` 锁定 `step()` 不接 `PT/TI/...` 默认参数、不覆盖既有 `self.*`。 |
| **APCM-PERSISTENCE-1** | 过程量每拍传入，配置长期 `self.*` 保持 | accepted | 🔒 locked | `SP/PV/OC/TS/TP` 是本拍过程输入管脚，`step()` 开始、授权判断之前写入 `self.*`；`RM/OUTT/OUTB/SADD/SSUB/ZLEN/ZSYK` 仅在显式传入非 `None` 时覆盖实例字段。`PT/KP/TI/KI/TD/KD/DI/SVH/SVL/RSF/GC/CD/APARA_*` 等配置均为长期实例字段；上位/HMI 直接写 `self.*`，`APARA` 应用成功也直接写 `self.*`，下一扫描周期持续生效。`step()` 禁止用默认实参每拍覆盖配置参数。 |
| **APCM-AUTH-1** | 授权失败只累加 BD_ERROR6，不推进内部状态 | accepted | 🔒 locked | `APCM.step()` 先写入本拍明确传入的过程量和可选覆盖项，再调用共享 `LicenseContext.KZQBDYZMK.step(dt_ms)`；若 `OK%10000!=0`，仅 `BD_ERROR6+=1` 且 `>999999999→100000000.0` 后返回，不推进触发器/定时器/RSF/GC/CD/PID/PIDZZD/APARA，也不清 APARA 命令位或 `MM`。授权成功一拍有外层 APCM + 内层 PIDZZD 共 2 次真实授权调用；失败只有外层 1 次。`TestAuthGate` 锁死，包括 `R_TRIG/TON/BLINK/PIDZZD/APARA` 子状态冻结。 |
| **APCM-ZLOUT-1** | ZLOUT 限位整理必须原子化且无扰重建 PID | accepted | 🔒 locked | 2026-07-22 现场趋势反证发现旧 ST/Python 的整理并非原子动作：`R_TRIG02.Q` 清零 RSF 输出后仍同拍执行活动 RSF，保留的 `CT_*` 可立即命中 AO4；PID 同时从独立旧 `TP` 重建，破坏组合输出连续性。修复基线改为：每拍调用 `R_TRIG02(CLK:=ZLEN AND 限位条件)`；上升沿先冻结 `ZL_PID_BASE=AV_P_TEMP+AV_R` 并一次累加到 `ZLOUT`；整理拍完整复位 RSF 档位/累计/闭锁/边沿、禁止活动 RSF，并冻结预限幅/CD 支路一拍，避免 CD 同拍退出回补被 PID 重建覆盖；PID 只从 `ZL_PID_BASE` 重建，普通 RM=3/4 跟踪才读取并限幅 `TP`。整理专用位置式基准不得单独限幅，因为 PID+RSF 可能与反向 `AV_C` 抵消后总输出恰在边界，先截断 PID 会制造明显反向跳变。`ZLEN=False` 必须推进触发器到 False，禁止 Q 悬挂；在限位持续时重新开启会形成一次确定的新事件。若整理前 `AV_P_TEMP` 未到限位、冻结基准合并 RSF 后才到限位，次拍既有 `R_TRIG03` 会产生一次上升沿：`OutM=0` 为位置式一拍保持并重同步 `UK`，`OutM=1` 为一拍零增量命令，随后恢复正常分支；这不是物理位置归零，但外部增量执行语义仍须真机确认。Python `zlout_ref` 仍须绑定外部 `RealRef`；外部程序如何消费/回灌 `ZLOUT`、是否 RETAIN，以及 `ZLEN` 在 `RM<>1`、`TL=0`、模式切换时的现场启用条件不由本 FB 自证，必须在 CODESYS 工程与现场闭环中裁决。`TestZLOUT` 现锁定上下限、四档、正反方向、OutM=0/1、`TP!=ZL_PID_BASE`、OC/AV_GC 单次扣除、PCMMS=1/2、下一拍完整防抖、ZLEN 关闭后仍限位重开、CD/RSFEN/TS/ATE/RM/SADD/SSUB 同拍、OutRL/RTH 边界及 PID+RSF/反向 CD 抵消。 |
| **APCM-MM-1** | MM 是一次性手动输出命令，手动 PID 路径末尾清零 | accepted | 🔒 locked | 源手动 PID 分支末尾 `MM:=0` 是对实例字段的实际写回。Python 在 `RM=0` 或 `R_TRIG03/R_TRIG9/F_TRIG1/F_TRIG2/R_TRIG05/R_TRIG06` 任一触发的强制手动路径末尾执行 `self.MM=0`，避免 HMI 单次写入 `MM=1` 后跨扫描重复快增/快减。`TestMMOneShot` 锁死。 |
| **APCM-CYCLE-1** | 固定 500ms 离散计数不按 dt_ms 缩放 | accepted | 🔒 locked | 源中 `NUM/SUMMAX/SUMMIN` 固定 `+0.5`，RSF `CT_1~4/CT_1_1~4_1/CT_TL/CT_RSF_OUT/CT_RSF_LOCK` 固定 `+1` 并使用 `2*TL/2*TL1~4/2*TL_OUT/2*RSF_LOCK_T` 比较。这些逻辑绑定原 500ms 任务语义，Python 不用 `dt_ms/1000` 替代；非 500ms 输入不拒绝也不比例缩放。`TestObserverRSFCDPID` 锁定 TS 下 RSF 清零、2/3/4 档触发、快退（`|EK_R|<E1_FAST_OUT`）、慢退（`CT_RSF_OUT` 达 `2*TL_OUT`）、反向退出闭锁、超时解锁、升档解锁与整理回收路径。 |
| **APCM-R8-1** | BLINK1/2 高电平按同任务采样量化为 500ms | accepted | 🔒 locked | APCM 内 `BLINK1.OUT→R_TRIG01`、`BLINK2.OUT→R_TRIG04` 均属同任务边界采样；源 `TIMEHIGH=T#300MS` 在 Python 侧按项目 R8 约定使用有效 `TIMEHIGH=500ms`。`TIMELOW` 仍保持 `TC*1000` / `TC_CD*1000`，不得扫描周期量化、ceil 或改写。测试通过包装 `BLINK1.step` 与 `BLINK2.step` 锁定有效 `TIMEHIGH=500ms` 且 `TIMELOW=TC*1000/TC_CD*1000`。 |
| **APCM-FOP-TB-1** | FOP1/FOP2 未传 TB，使用 APCHSFOP 默认 0.5s | accepted | 🔒 locked | APCM 源 `FOP1(IN:=..., TC:=TZ, KG:=1)`、`FOP2(IN:=..., TC:=TZ_CD, KG:=1)` 未连接 `TB`，Python 明确传 `TB=0.5`，不得使用 `dt_ms/1000`。 |
| **APCM-GC-1** | PCMMS/GCEN 的 AV_GC 按源 SEL 实际表达式 | accepted | 🔒 locked | `AV_GC` 以源实际 `SEL` 为准：`PCMMS==1` 时取 `AV_J_GC+AV_D_GC`；否则仅在 `GCEN and PCMMS in (0,2) and not TS and RM==1` 时取 `LIM1.AV`，否则为 0。不得按注释中的串/并联直觉重构。`TestObserverRSFCDPID::test_av_gc_matches_source_pcmms_gate` 锁死。 |
| **APCM-RM-1** | RM 非法值按实际分支进入自动路径 | accepted | 🔒 locked | 源注释称非法 `RM` 保持前运行方式，但 APCM 内嵌 PID 的实际分支未实现该限制；Python 按代码：`RM=3/4/R_TRIG02.Q` 跟踪，`RM=0` 或强制条件手动，其余值走自动路径。`TestEmbeddedPIDBranches` 锁定 RM=1 自动（避开 `F_TRIG1/F_TRIG2` 冷启动误边沿）与 RM=3 跟踪增量/位置分支。 |
| **APCM-COMBINATION-1** | APCM 内嵌 PID/GC/CD/RSF 按源顺序实现，不替换为旧小块 | accepted | 🔒 locked | APCM 内部 PID 必须按源 ST 内嵌公式实现，**不得**调用 `APCPID` 替代；GC/CD/RSF 也按 APCM 源内联顺序迁移（例如 CD 钳幅顺序、`TL` 用于 CD_TON、整理前 `AV_TEMP` 使用上一拍值等），只复用基础/辅助 FB：`APCHSFOP/APCSTATISTICS/APCHSHLLIM/APCPIDZZD/APCMAUTOPARA`。`FOP1/FOP2` 未连 `TB`，显式使用源默认 `0.5s`；BLINK 高电平按项目约定 500ms。`TestInitialAndInstanceState` 与 `TestObserverRSFCDPID` 锁定 FOP 默认、RSF/GC/CD/内嵌 PID 深层路径，含 CD `TL=0` 与 `TL>0` 两类进入/恢复时序及 `abs(CD_BH)<CD_GD` 严格边界反证。 |
| **APCM-PIDZZD-APARA-1** | PIDZZD 与 APARA 调用位于总 AV 之后；APARA 应用写 self | accepted | 🔒 locked | 总输出 `AV` 计算并限幅后才调用真实 `PIDZZD1.step(...)`，传入 `SP` 而非 `SP_V`，`PT1/TI1` 回写后影响下一扫描；随后调用真实 `APARA1.step(...)` 并镜像输出。APARA 应用按钮为上升沿；`FINAL_STRONG` 要求对应组 OK，`FINAL_WEAK` 允许人工确认后应用；成功路径清对应命令位，授权失败不清。`TestPIDZZDAndAPARA` 锁死，包括完整 `step()` 顺序、RESET/CALC 脉冲、PID 写回下一拍生效、RSF/CD/GC 组应用与持续高不重复、CD 成功应用不改变本拍 AV、PID/CD 失败原因、APARA 全镜像字段经 `step()` 抽样、HMI 覆盖 `APARA_APPLIED_PT` 下一拍生效。 |

---

## 六、业务块未来扩展

| ID | 标题 | 分类 | 状态 | 详情 |
|---|---|---|---|---|
| **BLOCK-NEXT** | 下一批业务块迁移 | recommended | 🟨 in-progress | 用户发来 ST/CFC 源后按 `02-business-blocks` 规则接入。**当前进度**：`APCHXHCL` / `APCSTATISTICS` / `APCHSFOP` / `APCHSHLLIM` / `APCHSRATELIM` / `APCGCQ`（观测器组合块）/ `APCCD`（重叠控制组合块）/ `APCHSACCUM`（离散积算块）/ `APCPIDZZD`（PID 自整定块）/ `APCPID`（变比例变积分 PID 调节器，嵌套复用 `APCPIDZZD`）/ `APCSPFINDER`（分析用设定值自动寻找，纯状态块）/ `APCRSFNAUTOPARA`（RSFN 自动参数推荐，窗口统计+历史融合，复用 `APCSPFINDER`）/ `APCMAUTOPARA`（APCM 自动参数推荐，四组推荐+历史融合，复用 `APCSPFINDER`）/ `APCM`（APC 智能综合控制模块，组合 PID/RSF/GC/CD/PIDZZD/APARA）已迁移；下一批待用户提供 ST/CFC 源。 |
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
