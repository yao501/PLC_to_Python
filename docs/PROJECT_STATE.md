# 项目状态快照（PROJECT_STATE）

> **用途**：跨会话记忆载体。每个 AI 会话开始时**先读本文件**（配合 `CODEX_GUIDE.md` 长期工作方针）。
> **更新纪律**：仅当阶段、版本、完成项、阻塞项或下一步发生**实质变化**时更新，不做无意义编辑；只保留"当前状态 + 决策索引 + 下一步"，不写过程叙事；超过 150 行就该精简。
> 最后更新：2026-07-24（`WP-20260723-016`～`WP-20260724-021` 的中断与阻塞历史均已诚实保留；最终承接包 `WP-20260724-022` Round 1 已获 Codex `APPROVED`、必须返修与非阻塞建议均为无，并由用户确认 `CLOSED`。L2 描述符/adapter/Registry 核心、Loader/Store/Executor 纵向接入、TON/APCHSHLLIM/APCM 三个代表性 adapter 与 OmitPolicy 完整成功边界现已收口；风险登记已同步。WP-022 单文件审核起止 SHA-256 均为 `90524dc414e2ed07d7a1ee19ec50d4ae103a29e98b7d86afad15e8641af883aa`，`git diff --check` 通过。用户已授权独立 Git/GitHub 收尾；开工基线 `main=origin/main=909fb709`。）
> **临时高优先级修复更新（2026-07-22）**：现场趋势反证发现 APCM 原 ST 与 Python 转写的 `ZLEN/R_TRIG02` 整理不是原子动作，可能在清零 RSF 后同拍命中 AO4，并让 PID 从独立旧 `TP` 重建。用户授权 Codex 直接维护：桌面 `APCM20260722.txt` 与 `src/blocks/apcm.py` 已同步改为冻结 `ZL_PID_BASE`、完整复位/重新武装 RSF、事件拍冻结预限幅/CD 支路、PID 只从冻结值重建，`R_TRIG02` 每拍推进；整理专用位置式基准不单独限幅，避免 PID+RSF 与反向 CD 抵消时破坏组合总量。Claude 独立对抗审核结论为 `APPROVED_WITH_CONDITIONS`、P0/P1 均无；其 P2 测试保护与行为落档建议经 Codex 复核成立，已新增 5 个测试并扩展 ZLEN 重开反证，未再改变 ST/Python 控制语义。原 ST 备份 SHA-256=`80933acb254add72a9702e27b8d3f6af610f0436c7a75e61f143551d40c59e46`，修复版 SHA-256=`b29dfcf9eb5de3c79ac5c7f4ef2a92d7f7ec9f7511140d8490f68f41c99bd128`，CRLF 保持。APCM 63/63、相关链 206/206、正式 tests 1182/1182、全仓 1250/1250 通过；开工基线 `main=origin/main=2bab893`（PR #18 merge）。仓库变更的独立 Git/GitHub 收尾载体为分支 `codex/apcm-atomic-cleanup`、功能提交 `42c7a17` 与 [PR #19](https://github.com/yao501/PLC_to_Python/pull/19)。**尚未在 CODESYS SP16.1 中导入编译、仿真或真机验证；外部 ZLOUT 消费回路、增量输出解释与 ZLEN 现场启用条件仍须对拍，不得仅凭 Python 测试直接部署现场。**协调器与旧轮询保持停止/暂停。
> **主线更新（2026-07-23）**：`WP-20260723-015` Round 3 已获 Codex `APPROVED` 并由用户确认 `CLOSED`，收口了零配置默认 write-disable shadow 栈、普通对象图写门/底层提交能力旁路、shadow 故障/watchdog 逻辑采用、shadow→实写全通道 `safe_value` 边界重建及对应反证。最终独立验证为定向 240/240、既有运行时 166/166、正式 tests 1222/1222、`prototype_05` 68/68、全仓 1290/1290，`git diff --check` 通过；八文件审核哈希始终为 `f96d2a053bb4c7596ec33dd5c53368e14c962e27a25fbb3207a5a42caea991bf`。**Shadow 的 Python 核心契约已审核关闭，但 `RUNTIME-SHADOW-MODE` 风险仍为 in-progress：真实 HAL/可信反馈、实时 monitor、硬件 watchdog、真实驱动、PLC/CODESYS 对拍和现场安全证明均未完成，不构成现场发布授权。**协调器与旧轮询保持停止/暂停。
> **当前工作包（2026-07-24）**：`WP-20260724-022` 已由用户确认 `CLOSED`，当前无活动实施/审核包。协调器 stopped，旧 Claude/Codex 30 分钟主轮询继续暂停；Git/GitHub 收尾正在由 Codex 按用户授权执行。完整 14+8 目录、其余 19 个 adapter、F2、参数装载、monitor、HAL、真实 I/O 和现场证明仍明确排除。

---

## 1. 项目一句话

把 CODESYS SP16.1 软 PLC 复刻为 Python 原生软 PLC 平台（ST+CFC 双前端 → 语言无关可执行 IR → 扫描引擎），已迁移 14 业务块 + 8 原语作标准库，并建立了正式 L3 IR、静态校验、Store、实例布局、过程映像基础、显式顺序执行器核心、生产 OutputPolicy、外层故障安全扫描运行器、提交监督器、已审核关闭的 Python shadow mode 核心，以及已审核收口的 L2 registry/代表性 adapter 纵向链（最新全仓发现集 1349 项，见 §2），目标是控制+AI 同平台一体化（分进程）。

## 2. 当前位置

- **L2 registry 核心与代表性 adapter 纵向链已审核收口**（2026-07-24，最终承接包 WP-022 `APPROVED`、用户已确认 `CLOSED`）：已建立纯数据 `BlockSchema`、进程内 `RuntimeAdapter`、`(block_type, variant)` 唯一注册表、Loader 解析、Store pin 过程映像、Executor 库块调用，以及 TON/APCHSHLLIM/APCM 三个代表性 adapter。OmitPolicy 已锁定 `required` / `use_default` / `keep_previous` / `none_means_no_write`，其中 `_stepped` 仅在 adapter、全部 `VAR_IN_OUT` 与声明输出完整成功回收后推进，失败路径清空本拍 `_driven`。Claude 环境最终为新增反证 **1/1**、既有反证 **6/6**、L2/IR/Store/Executor **197/197**、安全运行时相关 **240/240**、正式 tests **1281/1281**、原型 **68/68**、全仓 **1349/1349**；Codex 审核沙箱正式/全仓各有同 9 项 scope 外面板用例因禁止绑定端口报环境错误，排除该模块后分别 **1137/1137**、**1205/1205** 通过。不同计数是环境与测试集合快照差异，不是功能矛盾。完整 14+8 描述符目录、其余 19 个 adapter、F2、真实 HAL/monitor、PLC/CODESYS 对拍与现场证明仍未完成；上述 Python 证据不构成现场发布授权。
- **最新 v2 自审与独立审核验证**（2026-07-23，WP-015 Round 3 `APPROVED`、用户已确认 `CLOSED`）：Claude 最终交接为定向 shadow/engine/runner/policy/supervisor **240/240 通过**、既有运行时 **166/166 通过**、正式 tests **1222/1222 通过**、0.5 原型 **68/68 通过**、全仓 **1290/1290 通过**。Codex 独立复核普通对象图底层提交能力不可达、零配置两拍 shadow、直接 `port.commit` 抑制及 shadow→实写首拍从 `safe_value=0` 限速到 5；开始/结束 scope 哈希均为 `f96d2a05…991bf`，`git diff --check` 通过。审核沙箱中正式/全仓各有同 9 个既有 HTTP 端口用例因禁止绑定而不能运行，其余 1213/1213、1281/1281 通过；随后在允许本地端口绑定的环境完整复跑 1222/1222、1290/1290 通过。WP-011 的 172/166/1172/68/1240、WP-008 的 144/274/1108/68/1176 等均为不同时点证据；增长来自后续 APCM 与 shadow 回归，属正常测试增长，不是矛盾。以上只证明当前 Python 实现和协作工具行为，不是目标 PLC/CODESYS、真实 HAL/驱动或真机安全一致性的证据。
- **WP-006 审核证据快照**（2026-07-16，Codex 对 `WP-20260716-006` Round 1 独立复跑）：定向 `tests.test_runtime_engine` = **28/28 通过**、`tests.test_runtime_executor` = **58/58 通过**、`tests.test_runtime_store` = **24/24 通过**、`tests.test_runtime_ir` = **56/56 通过**；当时正式 tests = **937/937 通过**、0.5 原型 = **68/68 通过**、全仓 = **1005/1005 通过**。当时之后的计数增长来自后续 WP-007/WP-008 运行时用例与协作基础设施回归；其中协作基础设施曾单独新增 1 项监听器就绪回归和 2 项项目内心跳回归。不同数字是不同时点的真实测试快照，历史证据保留原始计数，不回写冒充当时结果。以上 Python 测试均不证明与目标 PLC 语义一致。

- **阶段 0.5（语义基线修订）**，文档侧已完成**三轮**外部评审（ChatGPT5.5）修正；评审方判断"主体架构已站住，无需再大规模文档重构"。
- 规格版本：`IR_SPEC` **v2.2.4**（0.5 冻结基线 v2.2.2 + 阶段 1 `StackSlot.index` / 持久 Store 键两项工程约定写回）/ `ENGINE_SCAN_SPEC` **v2.2.2** / `COMPONENT_CONTRACT` **v2.1** / `TARGET_PROFILE` **v1.3** / `GOLDEN_TRACE_FORMAT` **v1.2.1**。`STAGE0_DESIGN.md` 已标历史文档，不再更新。
- **阶段 1 显式顺序执行器与五步扫描骨架已完成**：`WP-20260714-004` 三轮实现并在达到自动轮次上限后曾转 `BLOCKED`，其剩余问题由窄范围 `WP-20260714-005` 完整收口；WP-004/005/006 均已由用户确认 `CLOSED`。现已有正式 IR 值对象、装载期静态校验、声明制 Store 与隔离快照、PROGRAM/用户 FB 实例布局、原子输入锁存、输出待提交容器、显式顺序指令执行、TypedValue 求值栈、FUNCTION/用户 FB 调用帧、E/F1 数值边界以及可重复调用的确定性单拍扫描编排器。
- **生产 OutputPolicy 与安全状态快照已关闭**：`WP-20260716-007` 已把 `ENGINE_SCAN_SPEC §4` 的分原因策略、强制安全优先级、冷启动 `hold→safe_value`、正常路径限速、`last_effective` 状态与 IEC 非有限/越界值失败关闭落实为可直接注入 `ScanEngine` 的策略端口；Round 3 Codex 结论为 `APPROVED`、用户于 2026-07-20 确认 `CLOSED`。本包只消费原子安全状态，不生成 watchdog/scan-fault，也不实现 shadow、真实提交或 HAL。
- **外层安全扫描运行器已审核关闭**：`WP-20260720-008` Round 3 已获 Codex `APPROVED` 并由用户确认 `CLOSED`。`OuterScanRunner` 在正常路径继续复用同一 `ScanEngine` 与提交端口；提交前扫描异常或显式 watchdog 事件会绕过损坏 request，生成全通道安全映像并单次提交。`SafeImageTicket` 把 staging 与提交后策略历史确认拆成一次性两阶段事务，锁存/staging/commit/confirm 的失败均以保留原始与 fallback 异常的结构化信号上报。真实周期计时、后台线程、硬件 watchdog、shadow、真实 HAL、`last_physical_committed` 与提交故障锁存仍不在本包，须由后续工作包和真机验证承接。
- **提交监督恢复包已审核关闭**：`WP-20260721-009` Round 1～3 建立并逐步收敛驱动确认回执、`last_physical_committed`、逐通道 `commit_fault` / `channel_fault`、安全值重试、三条件显式复位、复位并发失败关闭与不可污染诊断快照；其 Round 4 中断历史保持 `BLOCKED`。后继 `WP-20260722-010` 已在 Round 2 收口普通异常 `__repr__` 与 fallback 类型名二次字符串化失败，Codex verdict=`APPROVED`、必须返修=无；未改变 `ENGINE_SCAN_SPEC v2.2.2 §4.1/§4.4` 语义，已由用户确认 `CLOSED`。
- **驱动回执类型信任边界已加固关闭**：`WP-20260722-011` 已将 WP-010 独立审核中识别、当时明确 scope 外的恶意整数子类风险独立收口。不可信驱动回执子类不再能在监督器置故障前触发重载运算；故障通道形成 `PartialCommitError`、计数与锁存升级，健康通道仍独立成功，Store 对业务内部 IEC 值的现有工程映射未改。Codex Round 1 `APPROVED`，已由用户确认 `CLOSED`。
- **Python shadow mode 核心已审核关闭**：`WP-20260722-012`、`WP-20260723-013` 与 `WP-20260723-014` 的中断历史据实保持 `BLOCKED`；其检查点由 `WP-20260723-015` 接续并在 Round 3 完整收口。零配置 `CommitPort(real_committer)` + `OuterScanRunner(...)` 默认形成可运行 write-disable 栈；物理写须显式 `legacy_unshadowed=True` 或经唯一受支持切换；gate/port/runner 的普通可达属性图不能取得可旁路的底层提交能力；shadow 正常、scan-fault、watchdog 均零物理写且诊断不冒充成功；shadow→实写先全通道边界重建，首拍从 `safe_value` 限速。用户已确认 WP-015 `CLOSED`。真实 HAL/可信反馈、实时 monitor、硬件 watchdog、趋势对拍、PLC/CODESYS 和现场安全证明仍排除并保持后续独立工作包。
- **0.5 可执行验证原型已完成并经两轮定向返修**（Fable5 实施，`prototype_05/`，一次性代码）：最小指令集 + TON 经描述符 + BOOL OutputPolicy + ST/CFC 双路径同指令列表跑 24 拍 + 5 个语义敏感案例。Codex 首轮 6 条（驱动异常提交隔离、绑定 actual 类型、OutputPolicy 校验、无 LPC 基准、纯整数 DIV/MOD、文档对齐）+ 二轮 2 条（Binding 表结构校验：重复 formal/非法 actual_kind/const 值类型；安全配置 NaN/Infinity/整数范围拒绝）均修复，每条有反证测试（`prototype_05/tests/test_review_rework.py`）。
- **下一步（边界分离）**：① 在本次 Git/GitHub 收尾后，单独起草完整 14+8 描述符目录及其余 19 个 adapter 工作包，不混入 F2、HAL 或 monitor；② APCM 现场发布前，在隔离的 CODESYS SP16.1 工程中完成导入/编译、整理事件仿真对拍和受控现场趋势确认；③ 真实 monitor 与 HAL 继续分别立项，现场安全证明保持独立闸门。

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
