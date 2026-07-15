# AI 协作交接文件(AI_REVIEW_HANDOFF)

> **用途**:Claude(实施)与 Codex(审核)之间"实施—审核"往返的唯一交接载体,支撑本地事件协调器驱动的串行自动协作。
> **命名**:实施方人类可见称呼统一为 **Claude**;历史记录中的 `Fable5` / `fable5` / `FABLE_WORKING` / `Fable5 实施交接` 仅作只读兼容别名保留,新内容一律使用 Claude / `CLAUDE_WORKING` / `Claude 实施交接`。
> **职责边界**:本文件只记录工作包往返;项目状态归 `PROJECT_STATE.md`,正式风险归 `RISKS.md`,长期纪律归 `CODEX_GUIDE.md`,职责不混写。
> **事件机制**:本地协调器监听本文件的原子状态变化,完成五字段、轮次、scope 哈希和全局执行租约核验后,只唤醒当前 `handoff_to` 指向的一方。旧 Claude/Codex 30 分钟主轮询在 live 服务运行期间必须保持暂停,不得与事件协调器并行;后续低频恢复巡检只能检查服务健康并告警,不得绕过协调器直接启动 AI。

## 协议(双方必须遵守)

### 状态机(每个工作包)

```
CLAUDE_WORKING → READY_FOR_CODEX → CODEX_REVIEWING
  → CHANGES_REQUESTED(回 CLAUDE_WORKING,round+1)
  → APPROVED → CLOSED(用户确认)
  → BLOCKED(交用户仲裁)
```

（历史别名:`FABLE_WORKING` 等价于 `CLAUDE_WORKING`,仅供只读解析,新交接不再写出。）

### 状态字段映射(唯一口径;自 2026-07-14 起适用于新交接与后续轮次)

> 背景:此前"实施交接后 owner 写谁"存在双方理解分歧(Fable5 曾写 `owner: fable5` + `handoff_to: codex`,Codex 轮询要求 `owner=codex`,导致 2026-07-14 00:41–05:11 空转)。本节为唯一权威映射;**历史工作包文字(含 WP-20260713-002"验收与交接要求"中的 `owner: fable5` 表述)与本节冲突时,以本节为准,历史原文保留不改写**。

```text
CLAUDE_WORKING:     owner=claude   handoff_to=claude
READY_FOR_CODEX:    owner=codex    handoff_to=codex
CODEX_REVIEWING:    owner=codex    handoff_to=codex
CHANGES_REQUESTED:  owner=claude   handoff_to=claude
APPROVED:           owner=user     handoff_to=user
BLOCKED:            owner=user     handoff_to=user
CLOSED:             owner=user     handoff_to=user
```

> 只读兼容:历史 `FABLE_WORKING: owner=fable5, handoff_to=fable5` 与
> `CHANGES_REQUESTED: owner=fable5, handoff_to=fable5` 仍可被解析（`fable5` 规范化为 `claude`
> 后统一显示 Claude）;新交接一律写 `claude`,不得再输出 `fable5` / `FABLE_WORKING`。

字段语义:

- `owner` = 当前**拥有处理权**的一方;`handoff_to` = 当前状态**要求接力**的一方。
- 除历史轮次记录外,两者在上述所有状态中**必须一致**;状态变更时必须**原子化地同时更新** `status + owner + handoff_to`(一次写入,不允许中间态)。
- 任一字段与映射不匹配时,双方定时任务必须**幂等退出**,不得猜测或越权处理,可在自身运行报告中提示用户存在字段异常。
- Claude 完成实施/返修交接时,统一写 `status: READY_FOR_CODEX, owner: codex, handoff_to: codex`,并附完整实施交接记录与 `scope_sha256`,随后立即停止修改 scope 文件。
- Claude 仅在 `CLAUDE_WORKING(owner=claude, handoff_to=claude)` 或 `CHANGES_REQUESTED(owner=claude, handoff_to=claude)` 两种组合下接手;处理 CHANGES_REQUESTED 时按协议 round+1 且不得超过 `max_rounds`。（历史 `FABLE_WORKING` / `owner=fable5` 组合仍可只读解析。）
- Codex 仅在 `READY_FOR_CODEX(owner=codex, handoff_to=codex)` 且 `round<=max_rounds` 时接手,审核期间置 `CODEX_REVIEWING`。

### 写入权(始终只有一方可写工作文件)

- `CLAUDE_WORKING`(历史别名 `FABLE_WORKING`):Claude 可改 scope 内文件;Codex 不审核漂移中的内容。
- `READY_FOR_CODEX`:Claude 停止写入,只等待。
- `CODEX_REVIEWING`:Codex 只读检查,仅写本文件的审核区。
- `CHANGES_REQUESTED`:Codex 停止,Claude 按意见返修。
- `APPROVED`:工作包通过;Git 提交等外部操作仍须用户授权。

### 硬规则

1. `scope` 必须列出准确文件,不能只写"相关文件";审核期间 scope 内文件发生变化,审核作废退回重新交接。
2. 实施方必须报告**实际测试命令与结果**,不能只写"测试通过";未跑测试必须写明原因。
3. 审核结论必须是 `APPROVED / CHANGES_REQUESTED / BLOCKED` 三值之一,不能只写模糊评价。
4. 每个工作包最多自动往返 **3 轮**(`max_rounds`),超过转 `BLOCKED` 交用户仲裁。
5. 涉及删除、Git 提交/推送、范围扩大、规格裁决时,置 `BLOCKED` 并等用户,**不得自动执行**。
   附(用户裁决 2026-07-13):Git 提交 / GitHub 推送类任务经用户授权后由 **Codex 审核并执行**;Claude(实施方)不执行任何 Git 写操作,只提供修改清单与测试证据。
6. 双方反复同意**不能**把缺少真机证据的假设升级为已验证事实;结论仍须按"已证实事实/工程约定/待真机假设"分层。
7. 历史逐轮追加,不覆盖;新工作包新开一节。
8. 某一方超时/中断后,停在当前可恢复状态,不得猜测对方已完成。
9. 自动接力前必须同时校验 `work_package_id + status + owner + handoff_to + round`;同一工作包同一轮已处理过则幂等退出,任一字段不匹配时不得写入。
10. 实施方交接时记录 scope 文件的 `scope_sha256`;审核方在开始与结束时分别记录并比对同一 scope 的 SHA-256。任一文件漂移则本轮审核作废,转 `BLOCKED` 交用户处理。

### 记录格式

每个工作包一节,字段:`title / status / owner / round / max_rounds / scope`;实施交接区(完成内容/修改文件/明确未修改/测试命令与实际结果/已知疑问/scope_sha256/handoff_to/implementation_finished_at);审核结论区(verdict/已验证事实/项目工程约定/待真机验证假设/必须返修/非阻塞建议/审核证据/review_started_sha256/review_finished_sha256/handoff_to/reviewed_at);Round N 逐轮追加。关闭时 `status` 只写精确状态值 `CLOSED`,关闭人、时间和基线引用另列字段,避免破坏自动解析。

---

## WP-20260712-001

- title: 阶段 0.5 冻结评审裁决写回权威文档
- status: CLOSED
- closed_by: user
- closed_at: 2026-07-13
- baseline_commit: 63e79fcb14ffb53a8ad584b7cfdc23267e08874e
- baseline_branch: codex/current-baseline
- baseline_pr: https://github.com/yao501/PLC_to_Python/pull/1（已合并至 `main`，merge commit `3bff318ad59a181b45fc988665b2d2143f29ba5b`）
- owner: user
- round: 3
- max_rounds: 3
- scope:
  - docs/ENGINE_SCAN_SPEC.md
  - docs/IR_SPEC.md
  - docs/PLATFORM_ROADMAP.md
  - docs/RISKS.md
  - docs/PROJECT_STATE.md
  - CODEX_GUIDE.md
  - prototype_05/README.md

### Fable5 实施交接(Round 1)

- 完成内容:按 2026-07-12 冻结评审"有条件通过"的必要写回清单逐条落地——① `ENGINE_SCAN_SPEC` 升 v2.2.2:§4 规则 3 / §4.1(可信反馈定义+边界首拍基准选择函数+LPC 去含混)/ §4.2 / §4.4(commit_fault 与 channel_fault 区分、锁存与三条件显式复位)/ 算法步 2 / §5.1(载体三分支)/ §9(新增 5 项验收+导入用例改载体分支+修正一处与新裁决冲突的旧验收项);② `IR_SPEC` 升 v2.2.2:§4 CFCGraph 五字段(execution_order_mode / 可选 execution_order_id / order_source / 可选 feedback_marker / carrier),§6 改"按已确定执行序 lower,算法未就绪拒绝生成 IR";③ ROADMAP:§1.1 / 0.5 范围④ / 关键产出版本号 / 当前状态 / 验收记录 / 阶段 2 均改载体分支口径,黄金轨迹保持外部待办;④ RISKS:OUTPUT-BASELINE-1 转 resolved(注明阶段 7 HAL 未完成)、AUTOORDER-1 转 deferred/mitigated、EXEC-IR-1 注明"写回复核后可转 resolved";⑤ PROJECT_STATE 状态/版本/下一步/决策索引同步;⑥ CODEX_GUIDE §3 删除"一律保留原始序号"绝对表述;⑦ prototype README 55→68 口径。
- 修改文件:即 scope 全部 7 个文件。
- 明确未修改:TARGET_PROFILE.md、FINDINGS.md(按评审意见不为冻结结论改写证据正文)、全部代码与测试。
- 测试命令与实际结果:未重跑——本工作包纯文档修改、零代码变动;最近一次复跑记录为 Codex 2026-07-12(prototype_05 68/68、tests 690/690、根目录 758/758 全通过)。
- 已知疑问:① ENGINE_SCAN_SPEC 顶部 v2.2.1 历史 changelog 行仍保留"shadow→实写首拍例外用 LPC"表述,作为版本历史未删,请裁决是否需加"已被 v2.2.2 取代"标注;② §8 最小示例"顺序来自保留的执行序号"未改(示例非规范性文本)。
- handoff_to: codex
- implementation_finished_at: 2026-07-12

### Codex 审核结论(Round 1)

- verdict: CHANGES_REQUESTED
- 已验证事实:两项冻结裁决的主体已写入 `ENGINE_SCAN_SPEC` v2.2.2、`IR_SPEC` v2.2.2、ROADMAP、RISKS、PROJECT_STATE 与 CODEX_GUIDE；OutputPolicy 边界基准已改为“可信设备反馈优先，否则 `safe_value`”，`last_physical_committed` 不再冒充反馈；`channel_fault` 已写为锁存且须三条件显式复位；D3 主体已按 PLCopen XML / `.export` 自动模式 / 显式顺序载体分支。
- 项目工程约定:上述输出基准与复位制度是项目工程约定，非 CODESYS 官方语义；D3 仅冻结载体分支和“算法未就绪则拒绝生成可执行 IR”的失败策略，未冻结 `.export` 自动顺序重建算法。
- 待真机验证假设:`IsFeedbackStart` 精确落点、反馈边到 `LOAD_PREV` 的精确映射、真机黄金轨迹、REAL 中间精度、整数中间位宽/溢出、多任务/GVL/RETAIN 仍未验证；当前 Python 测试不证明与 PLC 语义一致。
- 必须返修:1) `prototype_05/README.md` 第 39–45 行仍写“暂不冻结/复位机制待决”，需改为“规格已冻结为可信反馈优先、否则 safe_value；原型仅实现无反馈时的 safe_value 分支，未实现 HAL 反馈和显式复位接口”。2) `ENGINE_SCAN_SPEC.md` 顶部 v2.2.1 历史行需明示“LPC 例外已被 v2.2.2 取代”，当前“全文唯一口径”与现行规则字面冲突；第 6 行现行摘要中“导入保留、不重新推断”需改为 v2.2.2 载体分支口径。3) `ENGINE_SCAN_SPEC.md` §5.2 和 `PLATFORM_ROADMAP.md` 阶段 0.5/阶段 2 的“拓扑推断仅用于新建 CFC”需限定为“平台通用的新建图定序”，并明确 `.export` 自动模式另走阶段 5 导入器重建算法，避免与§5.1 自相矛盾。4) `ENGINE_SCAN_SPEC.md` §8 将“顺序来自保留的执行序号”限定为“本示例假定源模型已有确定序号（例如 PLCopen XML 的 `executionOrderId`）”。5) `RISKS.md::PLATFORM-OUTPUT-POLICY-1` 仍写“待评审冻结”，需改为“规格已冻结，阶段 1 实现与测试待办”；本轮返修完成时同步将 `PLATFORM-IR-1` / `PLATFORM-EXEC-IR-1` 改为“0.5 规格冻结 resolved，正式实现仍属后续阶段”，保留 PLC 一致性未证明的边界。
- 非阻塞建议:协作机制主体可用；建议后续单独开工作包补充两条协议：自动任务必须先校验 `status + owner + handoff_to + round`以保证幂等；审核开始/结束对 scope 文件计算 SHA-256，若变化则审核作废并转 `BLOCKED`。
- 审核证据:对 scope 7 个文件进行全文/定向口径检查；对 `PLATFORM-OUTPUT-BASELINE-1`、D3、`PLATFORM-CFC-AUTOORDER-1`、原型范围和真机边界做交叉核对；审核结束时 scope SHA-256 与审核过程记录一致，未发现审核期间漂移。本工作包纯文档，本轮未重跑测试；引用的 68/690/758 为 Codex 2026-07-12 既有实际运行记录。
- handoff_to: fable5
- reviewed_at: 2026-07-12

### Fable5 实施交接（Round 2，返修）

- 完成内容：Codex Round 1 五条"必须返修"逐条落地——1) `prototype_05/README.md` 工程约定第 1 条改为"规格已冻结为可信反馈优先、否则 safe_value（v2.2.2 §4.1）；原型仅实现无反馈时的 safe_value 分支，未实现 HAL 反馈（阶段 7）"；同节第 4 条（channel_fault"复位机制待决"）一并改为"规格已冻结为锁存+三条件显式复位（§4.4），原型仅实现锁存分支"——该条虽未被点名，但与第 1 条同源于 OUTPUT-BASELINE-1 裁决，不改则同类矛盾残留。2) `ENGINE_SCAN_SPEC.md` 顶部 v2.2.1 历史行明示"shadow→实写首拍例外用 LPC 已被 v2.2.2 取代"，"全文唯一口径"降为"逻辑层口径"；第 6 行现行摘要改为 v2.2.2 载体三分支口径。3) `ENGINE_SCAN_SPEC.md` §5.2 与 `PLATFORM_ROADMAP.md` 阶段 0.5 范围④/阶段 2 范围三处，"拓扑推断仅用于新建 CFC"均限定为"平台通用的新建图定序"，并明示 `.export` 自动模式另走阶段 5 导入器重建算法、不改写载体显式顺序。4) `ENGINE_SCAN_SPEC.md` §8 改为"本示例假定源模型已有确定的执行序号（例如 PLCopen XML 的 executionOrderId）；.export 自动模式须先经阶段 5 重建算法"。5) `RISKS.md`：`PLATFORM-OUTPUT-POLICY-1` 尾部"待评审冻结"改为"规格已冻结（v2.2.2），阶段 1 实现与测试待办"；`PLATFORM-IR-1` 🟥→✅ resolved（0.5 规格冻结，正式实现属后续阶段，PLC 一致性未证明边界保留）；`PLATFORM-EXEC-IR-1` 🟨→✅ resolved（同口径，详情尾部"待复核转 resolved"同步改为已转）。
- 修改文件：`prototype_05/README.md`、`docs/ENGINE_SCAN_SPEC.md`、`docs/PLATFORM_ROADMAP.md`、`docs/RISKS.md`（均在 scope 内）。
- 明确未修改：`docs/IR_SPEC.md`、`docs/PROJECT_STATE.md`、`CODEX_GUIDE.md`（本轮五条意见均不涉及；PROJECT_STATE 当前步仍为"待 Codex 复核"，阶段未实质变化，按更新纪律不动）；全部代码与测试零改动。
- 明确未执行：非阻塞建议（幂等校验 + scope SHA-256 协议）按 Codex 意见留待单独工作包，本轮未写入协议区。
- 测试命令与实际结果：未跑——本轮仍为纯文档修改、零代码变动；最近实际运行记录仍为 Codex 2026-07-12（prototype_05 68/68、tests 690/690、根目录 758/758）。
- 自查：返修后对 scope 7 文件全局检索"暂不冻结/复位机制待决/仅用于新建/顺序来自保留的执行序号/待评审冻结/导入保留、不重新推断"，scope 内规范性文本零残留；残留命中仅为 RISKS.md 历史任务书叙事（第 8/10 行）、OUTPUT-BASELINE-1 条目内对既往裁决过程的引述、本交接文件 Round 1 历史记录——均为历史记录性质，按协议不覆盖。
- 已知疑问：① **scope 外残留**：`.cursor/rules/04-platform-runtime.mdc` 第 28 行仍为旧 D3 表述（"导入时保留原始序号、不重新推断；拓扑推断仅用于新建 CFC"），与 v2.2.2 载体分支冲突；该文件不在本工作包 scope，未动，建议下个工作包或本包 scope 扩充（需用户/Codex 裁决）后修正。② `docs/STAGE0_DESIGN.md` 第 310 行旧 D3 冻结表——该文件已标"历史文档不再更新"，未动。
- handoff_to: codex
- implementation_finished_at: 2026-07-12

### Codex 审核结论(Round 2)

- verdict: CHANGES_REQUESTED
- 已验证事实：Round 1 要求的语义口径返修已落地：`docs/ENGINE_SCAN_SPEC.md:3-6`、`95-102`、`146-154`、`176` 已改为 v2.2.2 输出基准/复位制度与 D3 载体分支口径；`prototype_05/README.md:39-47` 已把原型边界改为“可信反馈优先、否则 `safe_value`”与“`channel_fault` 锁存 + 三条件显式复位”；`docs/RISKS.md:116-121` 已把 `PLATFORM-IR-1` / `PLATFORM-EXEC-IR-1` 转为 resolved，并把 `PLATFORM-OUTPUT-POLICY-1` 改为“规格已冻结、阶段 1 实现与测试待办”。本轮未重跑测试；引用的测试数字仅来自仓库内既有记录。
- 项目工程约定：`PLATFORM-OUTPUT-BASELINE-1` 的边界首拍基准与 `channel_fault` 复位制度仍明确标注为**项目工程约定**、非 CODESYS 官方语义（`docs/ENGINE_SCAN_SPEC.md:3-4,95-102`；`docs/PROJECT_STATE.md:39`），这一分层表述正确。
- 待真机验证假设：反馈起点到 `LOAD_PREV` 的精确映射、`.export` `IsFeedbackStart` 落点、真机黄金轨迹、REAL 中间精度、整数中间位宽/溢出、HAL 可信反馈接口与现场验证仍未完成；当前 Python 测试不能表述为 PLC 一致性证明（`docs/ENGINE_SCAN_SPEC.md:149`；`docs/PROJECT_STATE.md:5,50,53`；`docs/RISKS.md:118,121,125`）。
- 必须返修：scope 内仍有**最新测试证据口径未收口**。`docs/PROJECT_STATE.md:15` 与本交接 `Round 2` 实施说明 `docs/AI_REVIEW_HANDOFF.md:87` 都把最近实际运行记录写成 `prototype_05 = 68/68`；但 `prototype_05/README.md:82-85` 仍把当前“测试结果”写成 `55/55`，`docs/PLATFORM_ROADMAP.md:128,130` 仍用 `55/55` 描述当前原型/当前状态，`docs/RISKS.md:117` 也仍用 `55/55` 描述 `PLATFORM-EXEC-IR-1` 的原型证明。请在这些 scope 文件中统一口径：要么更新为最新已记录的 `68/68`，要么明确标注 `55/55` 只是 `2026-07-05` 的历史子集证据，避免把旧计数写成当前结论。
- 非阻塞建议：scope 外的 `.cursor/rules/04-platform-runtime.mdc:28` 仍保留旧 D3 绝对表述；这与 v2.2.2 载体分支口径冲突，但本工作包无权扩 scope，建议单独开包处理。`docs/STAGE0_DESIGN.md` 属历史文档，保持不动可接受。
- 审核证据：起始与结束 SHA-256 一致，审核期间未发现 scope 漂移：`docs/ENGINE_SCAN_SPEC.md=71d4a4227a3672ba57a5a93659c6b644488b4c83d6d954203148f89f6a663a75`，`docs/IR_SPEC.md=db47d7a3eadb695359de7a92dc7f59e81a43e3675989121d070a17b8ea3c57fc`，`docs/PLATFORM_ROADMAP.md=0f12b67a477788b89c44f15c2b3a4a0057418996acb44065f5ce0e1331e33b60`，`docs/RISKS.md=98c595da3217f9478670eba057b19db8b6fdd88bbc7a9e8696a566f38c884ffa`，`docs/PROJECT_STATE.md=8d35ae18cec0f0693f6b1cf72303114ec54a3b4c1a140d33fba5a77d60bf1837`，`CODEX_GUIDE.md=026a75462aa301d28471aeaa2d999419672a219ff3941ac18fb0f851c1e02edc`，`prototype_05/README.md=3ea88e871cbe89606ddaa495b8449f38cb7b4c9cab0276e3011295a222b72664`。
- handoff_to: fable5
- reviewed_at: 2026-07-12 23:15 CST

### Fable5 实施交接（Round 3，返修）

- 完成内容：Codex Round 2 唯一"必须返修"（测试证据口径收口 55/55 vs 68/68）落地，并以本轮实际重跑测试作最新证据——1) `prototype_05/README.md`：文件清单 `tests/` 由"55 项"改为"68 项"（补列 test_import_trial 5 项 + test_import_trial_plcopen 8 项）；"测试结果"节改为"最近实际运行 2026-07-12：prototype_05 68/68、既有基线 690/690"，55/55 降级为明确标注的 2026-07-05 历史子集计数。2) `docs/PLATFORM_ROADMAP.md:128,130`：两处 55/55 均标注"2026-07-05 时点历史子集计数"，并补"含导入试验回归锁后最新记录 68/68（2026-07-12 实际运行）"。3) `docs/RISKS.md:117`（PLATFORM-EXEC-IR-1）：同口径标注。4) `docs/PROJECT_STATE.md:15`：验证证据更新为 2026-07-12 本轮实际运行（数字与 2026-07-09 记录一致）；:50 的 55/55 补"历史子集计数，最新见 §2"标注。
- 修改文件：`prototype_05/README.md`、`docs/PLATFORM_ROADMAP.md`、`docs/RISKS.md`、`docs/PROJECT_STATE.md`（均在 scope 内）。
- 明确未修改：`docs/ENGINE_SCAN_SPEC.md`、`docs/IR_SPEC.md`、`CODEX_GUIDE.md`（本轮意见不涉及）；`docs/RISKS.md:8` 历史任务书叙事行的 55/55（带日期的历史记录，Round 2 已裁定可保留）；本交接文件历史轮次记录；scope 外 `.cursor/rules/04-platform-runtime.mdc:28` 旧 D3 表述（Codex 建议单独开包，本轮未动）；全部代码与测试零改动。
- 测试命令与实际结果（2026-07-12 本轮实际运行，Fable5）：`python -m unittest discover -s prototype_05 -t .` → Ran 68 tests, OK（68/68）；`python -m unittest discover -s tests -t .` → Ran 690 tests, OK（690/690）。与 2026-07-09 Codex 记录一致；本轮文档所引测试数字均有当日实跑支撑。
- 自查：返修后全局检索 scope 7 文件中 "55/55"——残留命中仅 3 类：① 已标注"历史子集计数"的注释性表述（ROADMAP:128,130 / RISKS:117 / PROJECT_STATE:50 / prototype README 历史记录行）；② RISKS.md:8 带日期的历史任务书叙事；③ 本交接文件历史轮次。无任何位置再把 55/55 写成当前结论。
- 已知疑问：无新增。scope 外 `.cursor/rules/04-platform-runtime.mdc:28` 旧 D3 表述维持 Round 2 记录,待单独工作包。
- handoff_to: codex
- implementation_finished_at: 2026-07-12

### Codex 审核结论(Round 3)

- verdict: APPROVED
- 已验证事实:本轮 scope 内文档口径已收口,不再把 55/55 写成当前结论。`prototype_05/README.md:79-87` 现将 `tests/` 明确写为 68 项,并把 2026-07-12 最近实际运行写为 `prototype_05=68/68`、`tests=690/690`,同时把 2026-07-05 的 55/55 明确降级为历史子集计数; `docs/PLATFORM_ROADMAP.md:128-131`、`docs/RISKS.md:117`、`docs/PROJECT_STATE.md:15,20,50` 与此一致。冻结写回的核心边界也保持一致:`docs/ENGINE_SCAN_SPEC.md:3,95-108,145-154,176` 保持"可信设备反馈优先,否则 `safe_value`"与 D3 载体分支口径; `docs/IR_SPEC.md:114,215` 保持"显式序号原样保留 / .export 自动模式未就绪则拒绝生成可执行 IR"口径; `CODEX_GUIDE.md:19,61,68` 与上述分层和措辞约束一致。
- 项目工程约定:`PLATFORM-OUTPUT-BASELINE-1` 的边界首拍基准与 `channel_fault` 复位制度仍被明确标注为**项目工程约定**,不是 CODESYS 官方语义;对应写法在 `docs/ENGINE_SCAN_SPEC.md:3,95-106,120,137`、`docs/RISKS.md:121`、`docs/PROJECT_STATE.md:39`、`prototype_05/README.md:39-49` 彼此一致。
- 待真机验证假设:本轮写回后仍正确保留未验证边界,未被升级为既成事实:可信反馈 HAL 接口与现场验证待阶段 7; `.export` `IsFeedbackStart` 精确落点、反馈边到 `LOAD_PREV` 的精确映射、真机黄金轨迹、REAL 中间精度、整数中间位宽/溢出仍待后续验证。证据见 `docs/ENGINE_SCAN_SPEC.md:102,150-154`、`docs/IR_SPEC.md:215`、`docs/PLATFORM_ROADMAP.md:130`、`docs/RISKS.md:118,121,123,125`、`docs/PROJECT_STATE.md:5,52`。
- 必须返修:无。
- 非阻塞建议:scope 外 `.cursor/rules/04-platform-runtime.mdc:28` 仍保留旧 D3 绝对表述;它不阻塞本工作包批准,但建议单独开新工作包修正,避免规则层与 v2.2.2 权威文档继续分叉。
- 审核证据:审核开始与结束的 scope SHA-256 一致,本次审核有效,期间未发现 scope 漂移——`docs/ENGINE_SCAN_SPEC.md=71d4a4227a3672ba57a5a93659c6b644488b4c83d6d954203148f89f6a663a75`、`docs/IR_SPEC.md=db47d7a3eadb695359de7a92dc7f59e81a43e3675989121d070a17b8ea3c57fc`、`docs/PLATFORM_ROADMAP.md=ea35a451274d108ef120fa52a8972d96ae6bd338f427dd9981e3bb6b80cd0f6a`、`docs/RISKS.md=ee6d53634b91a987ffa472d9ec4263130b1c25c48c1383ab06017da956d80218`、`docs/PROJECT_STATE.md=70a3f9d26a6bedc3eb6be1e9238db78d4b0b9d91217cdfedd6d2324704020803`、`CODEX_GUIDE.md=026a75462aa301d28471aeaa2d999419672a219ff3941ac18fb0f851c1e02edc`、`prototype_05/README.md=1e41f23d921ebc30295a55cead705823e2f190d34f127cce52394322b094cae6`。本轮 Codex 未重跑测试;仅审核 scope 文档与交接中记录的实际测试命令/结果是否一致,并确认未把 Python 测试表述成 PLC 一致性证明。
- handoff_to: user
- reviewed_at: 2026-07-13 00:14 CST

---

## WP-20260713-002

- title: 阶段 1 正式运行时 IR 内存模型与装载期静态校验
- status: CLOSED
- closed_by: user
- closed_at: 2026-07-14
- closure_note: 用户接受 Codex Round 2 `APPROVED` 结论，并授权 Codex 完成规格写回、状态同步及 Git/GitHub 发布。
- owner: user
- handoff_to: user
- round: 2
- max_rounds: 3
- working_branch: codex/stage1-ir-model
- base_commit: e85de12bd401e105f07a62296eef0101b91b70c4
- created_by: codex
- created_at: 2026-07-13
- scope:
  - src/runtime/__init__.py
  - src/runtime/ir.py
  - src/runtime/loader.py
  - tests/test_runtime_ir.py
- scope_baseline_sha256: b4b84e690e73201c9a258ce1fac1355e2ad85833b04902ab54af21a34d28b472
- scope_baseline_manifest:
  - `ABSENT  src/runtime/__init__.py`
  - `ABSENT  src/runtime/ir.py`
  - `ABSENT  src/runtime/loader.py`
  - `ABSENT  tests/test_runtime_ir.py`

### 任务目标与权威依据

- 目标：按 `docs/IR_SPEC.md` v2.2.2 与 `docs/PLATFORM_ROADMAP.md`“阶段 1 — 执行引擎内核 MVP”建立**正式工程**的 L3 IR 内存模型与装载期静态校验；当前仅允许 Python 代码在引擎内部/测试中构造 IR，**不是最终用户编程入口**。
- 权威依据：`docs/IR_SPEC.md` §2/§3/§5/§7/§8/§9；`docs/PLATFORM_ROADMAP.md` 阶段 1；`docs/RISKS.md::PLATFORM-IR-1 / PLATFORM-EXEC-IR-1 / PLATFORM-POU-MODEL-1`。
- 参考证据：`prototype_05/ir.py`、`prototype_05/loader.py` 只可用于理解 0.5 已验证行为和已知缺口；正式代码不得从原型目录导入，也不得把原型子集直接冒充完整规格实现。

### 实施范围（Round 1）

1. `src/runtime/ir.py`
   - 定义语言无关、全类型化的正式可执行 IR 数据模型；至少覆盖 `LOAD_VAR / LOAD_CONST / LOAD_PREV / STORE_VAR / BINOP / UNOP / CONVERT / CALL_STD / CALL_FB / CALL_FUNC / CALL_FB_INSTANCE / JMP / JMP_IF_FALSE / LABEL`。
   - 建模 `Binding / ValueRef / VarDecl / InstanceDecl / IOMap / POUDefinition / ProgramInstance / FBInstance / Task` 及指令所需的签名、常量/变量/栈槽引用形态；字段语义必须与 `IR_SPEC` 一致。
   - IR 指令、绑定与引用等值对象应可稳定比较并避免可变默认值；可变集合字段必须使用安全默认工厂。`IOMap.policy` 本包只保留类型边界/占位，不实现 OutputPolicy 行为。
   - `source` 仅作前端源模型占位；本包不实现 `STBody`、`CFCGraph` 或 lowering。
2. `src/runtime/loader.py`
   - 提供清晰的公开校验入口（如 `validate_task(...)`）和专用异常类型；校验失败必须阻止进入后续执行层，本包不创建或运行引擎。
   - 校验声明与引用：IEC 类型合法、同一作用域名称唯一、POU 库键/名称一致、PROGRAM/FB/FUNCTION 结构约束、定义/实例/变量/标签/跳转目标存在、实例引用种类正确；递归实例声明须能发现循环或不可展开引用，但不得分配运行时 Store。
   - 对可执行指令做控制流感知的栈类型验证：所有可达路径不得下溢；产生/消费值的指令类型齐全；`CONVERT` 显式标明 from/to；`STORE_VAR` 类型严格匹配；比较结果为 BOOL；`JMP_IF_FALSE` 消费 BOOL；控制流汇合点的栈深与逐项类型必须一致；正常出口栈状态必须符合指令/POU 契约。
   - 校验 `CALL_FUNC / CALL_FB_INSTANCE` 的绑定齐全性、重复绑定、formal/mode/type 匹配及 actual 可写性：OUT 禁止常量，INOUT 必须是可写 StoreKey；`CALL_FUNC` 的返回类型必须与 FUNCTION 定义一致。`CALL_FB` 只验证其引用的是已声明的 library 实例，本包不接入尚未建立的 L2 描述符注册表。
   - FUNCTION 禁止 GVL/地址访问；`VAR_TEMP` 只允许 PROGRAM/FUNCTION_BLOCK；本包只执行可由当前 IR 模型静态判定的部分，无法判定的前端规则不得伪称已验证。
3. `src/runtime/__init__.py`
   - 只导出本包稳定的 IR 模型与校验入口，不导出现有原型模块，不建立最终用户 API 承诺。
4. `tests/test_runtime_ir.py`
   - 覆盖一个最小合法 Task/PROGRAM 被接受，以及上述模型和校验规则的代表性失败用例。
   - 至少覆盖：缺失类型、未知变量、STORE 类型不匹配、栈下溢、重复/缺失标签、控制流汇合栈不一致、未知实例/POU、绑定缺失/重复/模式或类型错误、OUT/INOUT 常量绑定、非法 VAR_TEMP、递归实例循环。

### 明确禁止与冻结边界

- 不实现执行器、Store/过程映像、扫描循环、数值运算、OutputPolicy、安全服务、watchdog、HAL、描述符注册表、ST/CFC 前端或 lowering。
- 不实现或猜测 `.export` 自动模式 CFC 顺序重建；不修改/解释 `executionOrderId`、`IsFeedbackStart` 或反馈边到 `LOAD_PREV` 的映射算法。
- RETAIN/PERSISTENT、GVL、多任务字段可按冻结规格**建模**，但其运行行为不在本包实现或验证范围。
- REAL/binary32、整数原生位宽/回绕/越界转换只保留类型信息，不在本包实现；不得宣称相关 PLC 语义已验证。
- 不修改 scope 外任何文件；如发现必须扩大范围、需要规格裁决或删除文件，置 `BLOCKED`、`handoff_to: user`，不得自行扩包。
- Fable5 不执行任何 Git 写操作，包括 add/branch/commit/push/merge/rebase/reset/clean 及 `.git` 内部写入；所有 Git/GitHub 操作由 Codex 在审核通过且用户授权后完成。

### 验收与交接要求

- 定向测试：`PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_ir`。
- 既有正式测试：`PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .`；不得破坏当前 690 项基线。
- 0.5 原型回归：`PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s prototype_05 -t .`；不得破坏当前 68 项基线。
- 全仓回归：`PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s . -t .`；交接时报告实际运行总数与结果，不得预写固定的新总数。
- 交接前只对 scope 四个文件按上列顺序生成清单：存在文件写 `<file_sha256>  <path>`，不存在写 `ABSENT  <path>`，行末保留换行；再对整份清单计算 SHA-256，作为本轮 `scope_sha256`。不得把本交接文件纳入 scope 哈希。
- 完成后把本工作包顶层字段改为 `status: READY_FOR_CODEX`、`owner: fable5`、`handoff_to: codex`，在下方追加“Fable5 实施交接（Round 1）”，完整记录完成内容、修改文件、明确未修改、四组实际测试命令与结果、已知疑问、`scope_sha256`、`implementation_finished_at`，然后停止写入等待 Codex 审核。

### Fable5 实施交接（Round 1）

- 完成内容：按实施范围四条逐一落地。① `src/runtime/ir.py`：14 类指令全部建模（LOAD_VAR/LOAD_CONST/LOAD_PREV/STORE_VAR/BINOP/UNOP/CONVERT/CALL_STD/CALL_FB/CALL_FUNC/CALL_FB_INSTANCE/JMP/JMP_IF_FALSE/LABEL），指令/绑定/引用均为 `frozen dataclass`（稳定等值比较、无可变默认值，内部集合用 tuple）；`Binding`（含 StoreKey/StackSlot/Const 三种 actual 形态）/`ValueRef`/`StdSig`/`VarDecl`/`InstanceDecl`/`IOMap`（policy 仅占位）/`POUDefinition`（source 占位、code 可 None）/`ProgramInstance`/`FBInstance`/`Task`；IEC 类型全集与运算/区段/枚举常量集中定义。② `src/runtime/loader.py`：`validate_task(task)` + `IRValidationError`（汇总全部错误后抛出，阻止进入执行层）；声明/引用校验（类型合法、作用域名称唯一、pou_lib 键名一致、PROGRAM/FB/FUNCTION 结构约束、ProgramInstance 目标与 store_prefix、IOMap 引用与 OUT 缺 policy、实例 kind 正确、user_fb 目标存在且为 FB）；递归实例循环 DFS 检测（不分配 Store）；控制流感知栈类型验证（worklist + 汇合点栈深/逐项类型比对、无类型指令拒绝、STORE_VAR 严格匹配、比较结果 BOOL、JMP_IF_FALSE 消费 BOOL、出口契约 PROGRAM/FB 空栈、FUNCTION 恰余返回值）；绑定校验（formal 存在/模式与 section 匹配/类型一致/无重复/INOUT 必绑、OUT 禁 Const、INOUT 必须可写 StoreKey、CALL_FUNC 返回类型核对、StoreKey 在调用方作用域解析）；FUNCTION 禁 GVL（load/store/prev/绑定实参全覆盖）、VAR_TEMP 仅 PROGRAM/FB、FUNCTION 禁声明 FB 实例。③ `src/runtime/__init__.py`：仅导出稳定模型与校验入口，不导出原型模块。④ `tests/test_runtime_ir.py`：46 项——5 个接受用例（最小 Task、控制流+CONVERT+LOAD_PREV、用户 FB/FUNCTION 调用、库块+CALL_STD、IOMap 带 policy）+ 41 个失败用例，覆盖工作包点名的全部类别。
- 修改文件：scope 四文件全部为新建（与 scope_baseline_manifest 的 ABSENT 一致）。
- 明确未修改：scope 外零文件改动；未实现执行器/Store/扫描/数值语义/OutputPolicy 行为/描述符注册表/前端/lowering/顺序重建（遵守冻结边界）；未执行任何 Git 操作。
- 测试命令与实际结果（2026-07-13 本轮实际运行，Fable5）：
  1. `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_ir` → Ran 46 tests, OK。
  2. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .` → Ran **736** tests, OK（690 既有基线 + 46 新增，零破坏）。
  3. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s prototype_05 -t .` → Ran 68 tests, OK。
  4. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s . -t .` → Ran **804** tests, OK（758 + 46）。
- 已知疑问（请审核裁决）：① OUT 绑定可写 StackSlot 按规格合法，但栈槽写回语义在执行器存在前无法静态定义，本包**保守拒绝**（有测试锁定），待执行器工作包放开——是否接受该保守策略？② "必连形参"解释为仅 VAR_IN_OUT 必绑（IN 可用默认值、OUT 可丢弃），IR_SPEC §5.2 未精确定义；③ 库块管脚键 `<inst>.<pin>` 的类型无 L2 描述符不可核对，resolve 返回"类型未知"并跳过类型比对（诚实边界，docstring 已声明）；④ 不可达代码仅做结构校验（标签/指令对象），不做栈模拟（规格表述为"所有**可达**路径"）；⑤ FUNCTION 禁止声明 FB 实例是对"FUNCTION 无实例内存"的推论性收紧；⑥ GVL 区段接受 VAR_GLOBAL 或 VAR（IR_SPEC §2 默认值为 VAR）。以上均不涉及规格修改,若审核认为需写入 IR_SPEC 则属规格裁决、转用户。
- scope_sha256: 1ea9183a4b5134f14d91b0125ae9c665621b2bd1bbd5e35b90c88cb063b988ae
- scope_manifest:
  - `d48c21a764b451669aa25b1a446e97898d35397fcd1414ec921906fe8d28b364  src/runtime/__init__.py`
  - `c75d739b3c202f1b36823062290fbca19bb4dcd7ed0e584998eff0d9079ab88f  src/runtime/ir.py`
  - `4af191a0fac6219fb38413461395264ba41cdf3bc04344883a9b71d40b706d59  src/runtime/loader.py`
  - `fd5ea94f4864c3f5d6ed965eb80c5c1e4475155a166c1ec8a5334d65dbf70012  tests/test_runtime_ir.py`
- implementation_finished_at: 2026-07-13

### Codex 审核结论（Round 1）

- verdict: CHANGES_REQUESTED
- 已验证事实:`src/runtime/ir.py` 已建立正式 IR 值对象与容器模型，`src/runtime/loader.py` 提供了 `validate_task(...)`/`IRValidationError`、声明校验、控制流感知栈类型校验以及 `CALL_FUNC`/`CALL_FB_INSTANCE`/`CALL_FB` 的基础验证入口；`src/runtime/__init__.py` 仅导出正式模型与校验入口，未复用 `prototype_05`；`tests/test_runtime_ir.py` 已覆盖最小合法 Task、类型/栈错误、控制流错误、实例引用错误、绑定错误与 POU 结构错误等主路径。本轮只读审核未重跑测试，只核对实现、测试与权威文档/工作包要求是否一致。
- 项目工程约定:本工作包仍处于阶段 1 的**正式工程内核起步**，只允许 Python 侧内部/测试构造 IR，不构成最终用户编程入口；Python 单元测试通过只证明静态模型/校验器当前行为，不证明与目标 PLC 语义一致，这一点在 `tests/test_runtime_ir.py:8-9` 的分层表述保持正确。
- 待真机验证假设:REAL/binary32 量化、整数原生位宽/回绕/越界转换、`.export` `IsFeedbackStart` 精确落点、反馈边到 `LOAD_PREV` 的精确映射、HAL 可信反馈接口与现场验证仍属后续阶段/真机验证范围；本工作包未把这些边界错误升级为既成事实。
- 必须返修:1) `StackSlot.index` 目前被建模了但**没有被校验器使用**。`src/runtime/ir.py:86-90` 明确把栈槽引用建模为 `StackSlot(index, writable)`；工作包也要求对“常量/变量/栈槽引用形态”按字段语义建模并校验（`docs/AI_REVIEW_HANDOFF.md:170-179`）。但 `src/runtime/loader.py:782-795,819-827` 在 `IN` 绑定上只按“绑定出现顺序的逆序消费栈顶”处理，完全忽略 `actual.index`，因此 `StackSlot(7)`、错序索引或重复索引都可能被当成合法 IR 接受。现有测试也只覆盖 `StackSlot(0)`（`tests/test_runtime_ir.py:398-429`），没有锁住非零/乱序索引。请按 `index` 的实际语义补校验并加反证测试，避免把字段做成无效装饰。
- 必须返修:2) “绑定齐全性”被收窄成了**只有 `VAR_IN_OUT` 必绑**，与当前工作包要求不符。工作包在 `docs/AI_REVIEW_HANDOFF.md:179` 明确要求校验 `CALL_FUNC / CALL_FB_INSTANCE` 的“绑定齐全性”；`IR_SPEC` 也把绑定表定义为随指令携带、执行器仅凭指令完成调用的正式结构（`docs/IR_SPEC.md:151-167`）。但 `src/runtime/loader.py:738-745,810-815` 只对 `VAR_IN_OUT` 做缺失检查，并在注释中直接把 `IN/OUT` 解释为“可缺省”；测试同样只覆盖缺失 `VAR_IN_OUT`（`tests/test_runtime_ir.py:359-362`），未覆盖缺失 `VAR_INPUT`/`VAR_OUTPUT`。在当前正式 IR 模型没有“默认值已解析完毕”或“显式丢弃 OUT”的独立编码前，这会让不完整调用表被误判为合法。请把实现收紧到与工作包一致，或至少把 IR 内允许的缺省/丢弃形态先显式建模后再放宽。
- 必须返修:3) `Task.cycle_ms` 仍被校验为“任意正整数”，没有落实当前冻结范围里的**单任务固定 500ms**。权威规格把当前工程写成“单任务、固定 500ms”（`docs/IR_SPEC.md:99-107`），路线图阶段 1 也沿用这一边界（`docs/PLATFORM_ROADMAP.md:27-29,136-144`）；`src/runtime/ir.py:343-345` 的 `Task` 文档字符串同样写着“单任务、固定 500ms”。但 `src/runtime/loader.py:171-174` 只拒绝非正整数，仍会接受 100ms/1000ms 等当前范围外的任务配置，等于把正式校验器放宽到了未承诺的多周期前空间。请把当前实现与冻结边界对齐，并补一条拒绝非 500ms 的测试。
- 非阻塞建议:返修时顺手把“非零/乱序 `StackSlot.index`”“缺失 `VAR_INPUT`/`VAR_OUTPUT` 绑定”“非 500ms `cycle_ms`”都写成明确失败用例；这样后续阶段继续扩引擎时，这几个边界不会再被悄悄放松。
- 审核证据:审核开始与结束的 scope SHA-256 一致，本轮审核有效、期间未发现 scope 漂移。`review_started_sha256=1ea9183a4b5134f14d91b0125ae9c665621b2bd1bbd5e35b90c88cb063b988ae`，`review_finished_sha256=1ea9183a4b5134f14d91b0125ae9c665621b2bd1bbd5e35b90c88cb063b988ae`。逐文件 SHA-256：`src/runtime/__init__.py=d48c21a764b451669aa25b1a446e97898d35397fcd1414ec921906fe8d28b364`，`src/runtime/ir.py=c75d739b3c202f1b36823062290fbca19bb4dcd7ed0e584998eff0d9079ab88f`，`src/runtime/loader.py=4af191a0fac6219fb38413461395264ba41cdf3bc04344883a9b71d40b706d59`，`tests/test_runtime_ir.py=fd5ea94f4864c3f5d6ed965eb80c5c1e4475155a166c1ec8a5334d65dbf70012`。
- handoff_to: fable5
- reviewed_at: 2026-07-14 05:45 CST

### Fable5 实施交接（Round 2，返修）

- 完成内容：Codex Round 1 三条"必须返修"逐条落地。**1) StackSlot.index 落实校验**——因 IR_SPEC §5.2 未细化 index 字段语义，按**项目工程约定**定义并实现：`index` = 距调用点栈顶的偏移（0 = 栈顶）；同一调用的全部 IN×StackSlot 索引必须为非负整数、互不重复且恰好连续覆盖 {0..k-1}；类型按 index 指向的栈位核对（绑定书写顺序不再影响语义），消费 k 个栈值。该约定写入 `src/runtime/ir.py::StackSlot` docstring 与 `src/runtime/loader.py` 模块 docstring，均明示"工程约定、规格未细化"，未改任何规格文件。`StackSlot(7)`、乱序/非连续、重复索引、负数/非整数索引、索引集合合法但栈深不足均被拒绝。**2) 绑定齐全性收紧为全部接口形参必绑**（VAR_INPUT/VAR_OUTPUT/VAR_IN_OUT 一律缺省即拒），错误消息注明理由：当前 IR 无"默认值已解析完毕"或"显式丢弃 OUT"的独立编码,缺省不可静态判定为合法,待相关形态显式建模后方可放宽（即采纳 Codex 给出的第一种方案"收紧",不自行新增 IR 编码以免扩包）。**3) Task.cycle_ms 收紧为恰等于 500**（单任务固定 500ms,IR_SPEC §3 / ROADMAP 阶段 1）,非 500、非正、非 int、bool、float 均拒绝。Codex 非阻塞建议全部采纳:三个边界均有明确失败用例锁定。
- 修改文件：`src/runtime/ir.py`（StackSlot docstring）、`src/runtime/loader.py`（`_check_task_shell`/`_check_bindings`/模块与函数 docstring）、`tests/test_runtime_ir.py`（46→56 项：新增 TestStackSlotIndex 6 项、TestTaskCycleMs 2 项、绑定缺失 VAR_INPUT/VAR_OUTPUT 各 1 项，另含 1 项"index 按偏移取值、书写顺序无关"的正向锁定）。
- 明确未修改：`src/runtime/__init__.py`（本轮意见不涉及，SHA-256 与 Round 1 一致）；scope 外零文件改动；未改任何规格文档（index 语义作为工程约定只写入代码 docstring，若 Codex 认为应写入 IR_SPEC §5.2 则属规格裁决、转用户）；未执行任何 Git 操作。
- 测试命令与实际结果（2026-07-14 本轮实际运行，Fable5）：
  1. `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_ir` → Ran 56 tests, OK。
  2. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .` → Ran 746 tests, OK（690 既有基线 + 56，零破坏）。
  3. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s prototype_05 -t .` → Ran 68 tests, OK。
  4. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s . -t .` → Ran 814 tests, OK（746 + 68）。
- 已知疑问：① index 的 {0..k-1} 连续覆盖约定是本轮为使字段可静态校验而定的最小工程约定（0=栈顶、禁跳槽、禁重复）,执行器工作包实现栈帧时若发现更合理编码可再议——届时属规格/约定变更,不由实施方单方面改动。② Round 1 疑问②（必连形参解释）已按本轮返修意见 2 收紧,其余 Round 1 疑问（OUT×StackSlot 保守拒绝、库块管脚类型边界、不可达代码、FUNCTION 禁 FB 实例、GVL 区段）维持原状,Codex Round 1 未要求变更。
- scope_sha256: 9ef480f498fbaafcb41bda022982e16eac4ab297b5536c42eef21fe535b5b655
- scope_manifest:
  - `d48c21a764b451669aa25b1a446e97898d35397fcd1414ec921906fe8d28b364  src/runtime/__init__.py`
  - `35ec127ddd8a1093710f90d173ac7b32ae3d007724838493296a4543fe92084c  src/runtime/ir.py`
  - `d8d2903d60638755390fcaf2d599111e22d0b3836005876c510b59094ebeb5bd  src/runtime/loader.py`
  - `e0773c5f1558efd3638c9111520970f8d4a6585317bb9974e270b12600807fca  tests/test_runtime_ir.py`
- handoff_to: codex
- implementation_finished_at: 2026-07-14 06:15 CST

### Codex 审核结论（Round 2）

- verdict: APPROVED
- 已验证事实:`StackSlot.index` 不再是未生效字段。`src/runtime/ir.py:87-95` 已把 `index` 明确写成“距调用点栈顶的偏移（0=栈顶）”；`src/runtime/loader.py:742-863` 已据此落实加载期校验：非负整数、重复索引、非连续 `{0..k-1}`、栈深不足、按偏移取值后的类型不匹配都会拒绝；`tests/test_runtime_ir.py:470-532` 用 6 个反证/正向用例锁定了这些行为。Round 1 指出的“字段被建模但未参与校验”问题已修复。绑定齐全性也已与工作包要求对齐：`src/runtime/loader.py:15-20,742-837` 现在把全部接口形参缺省都视为加载错误，`tests/test_runtime_ir.py:366-376` 覆盖了缺失 `VAR_INPUT`/`VAR_OUTPUT`/`VAR_IN_OUT`。`Task.cycle_ms` 也已收紧到当前冻结边界：`src/runtime/loader.py:175-181` 仅接受固定 `500ms`，`tests/test_runtime_ir.py:539-550` 覆盖了非 500 与非整数两类拒绝。`src/runtime/__init__.py:1-40` 仍只导出正式 IR 模型与校验入口，未越界复用原型模块。
- 项目工程约定:`StackSlot.index` 的 `{0..k-1}` 连续覆盖语义目前仍是**项目工程约定**，不是 `IR_SPEC` 已冻结文本；这一点已在 `src/runtime/ir.py:90-93` 与 `src/runtime/loader.py:29-34` 明示。对 `OUT -> writable StackSlot`，实现保持诚实的**保守拒绝**：规格模型允许该形态存在，但在执行器/栈写回语义未定义前，`src/runtime/loader.py:32-34,799-805` 明确不放行，`tests/test_runtime_ir.py:418-422` 也已锁定该边界；这属于当前阶段可接受的工程收紧，不应表述为 PLC 官方语义。
- 待真机验证假设:本包仍正确保留未验证边界，未被升级为既成事实：库块管脚类型需待 L2 描述符接入、`CALL_STD` 不核对 IEC 名册语义、REAL 量化/整数回绕/越界转换不在本包验证范围内；证据见 `src/runtime/loader.py:23-34`。这些 Python 测试只证明静态模型与加载期校验当前行为正确，不证明与目标 PLC 语义一致。
- 必须返修:无。
- 非阻塞建议:若后续前端/lowering/执行器都要依赖 `StackSlot.index` 的 `{0..k-1}` 约定，建议单独开工作包把该约定从代码 docstring 升格到 `docs/IR_SPEC.md` 或 `docs/RISKS.md`，避免规则只存在实现层。
- 审核证据:审核开始与结束的 scope SHA-256 一致，本轮审核有效，期间未发现 scope 漂移。`review_started_sha256=9ef480f498fbaafcb41bda022982e16eac4ab297b5536c42eef21fe535b5b655`，`review_finished_sha256=9ef480f498fbaafcb41bda022982e16eac4ab297b5536c42eef21fe535b5b655`。逐文件 SHA-256：`src/runtime/__init__.py=d48c21a764b451669aa25b1a446e97898d35397fcd1414ec921906fe8d28b364`，`src/runtime/ir.py=35ec127ddd8a1093710f90d173ac7b32ae3d007724838493296a4543fe92084c`，`src/runtime/loader.py=d8d2903d60638755390fcaf2d599111e22d0b3836005876c510b59094ebeb5bd`，`tests/test_runtime_ir.py=e0773c5f1558efd3638c9111520970f8d4a6585317bb9974e270b12600807fca`。本轮 Codex 还独立复跑了 4 组测试并与交接记录一致：`python -m unittest tests.test_runtime_ir` = 56/56、`python -m unittest discover -s tests -t .` = 746/746、`python -m unittest discover -s prototype_05 -t .` = 68/68、`python -m unittest discover -s . -t .` = 814/814（均在 `PYTHONDONTWRITEBYTECODE=1` 下实际运行）。
- handoff_to: user
- reviewed_at: 2026-07-14 06:15 CST

---

## WP-20260714-003

- title: 阶段 1 运行时 Store、实例状态与过程映像基础
- status: CLOSED
- closed_by: user
- closed_at: 2026-07-14
- closure_note: 用户接受 Codex Round 1 `APPROVED` 结论，并授权 Codex 完成持久 Store 键工程约定写回、项目状态同步及 Git/GitHub 发布收尾。
- owner: user
- handoff_to: user
- round: 1
- max_rounds: 3
- base_commit: cfafd77c70d80ab3e805284f1b8d4e6030891ce2
- created_by: user
- created_at: 2026-07-14
- scope:
  - src/runtime/store.py
  - src/runtime/process_image.py
  - src/runtime/__init__.py
  - tests/test_runtime_store.py
- scope_baseline_sha256: 67106a374db217f71d507c18c2309b049f935e0f649f9045cca5e80dab528ba9
- scope_baseline_manifest:
  - `ABSENT  src/runtime/store.py`
  - `ABSENT  src/runtime/process_image.py`
  - `d48c21a764b451669aa25b1a446e97898d35397fcd1414ec921906fe8d28b364  src/runtime/__init__.py`
  - `ABSENT  tests/test_runtime_store.py`
- 开工核验（2026-07-14，Fable5 只读）：分支 main；HEAD=cfafd77c70d80ab3e805284f1b8d4e6030891ce2 与 origin/main 一致；工作区无未提交修改；PR #4 已合并（HEAD 即其 merge commit）；WP-20260713-002 已 CLOSED。任务书全文由用户在会话中提供（目标/禁止项/测试要求以其为准，与本文件协议区并行有效）。

### Fable5 实施交接（Round 1）

- 完成内容：按任务书第四节三项逐一落地。**① Store（`src/runtime/store.py`）**：扁平变量空间、声明制（未声明键读/写抛 `UnknownStoreKeyError`，不静默创建）、每键保存声明 IEC 类型与值、重复声明抛 `DuplicateStoreKeyError`、类型非法/值与声明类型不匹配抛 `StoreTypeError`（工程结构性检查：BOOL→bool、整数族/位串/TIME→int（排除 bool）、REAL/LREAL→float、STRING→str，**不做任何转换**）；`snapshot()` 生成只读 `StoreSnapshot`（无写接口，与源 Store 完全隔离，后续写入不影响快照）；retain/persistent 仅存元数据（`retain_flags()` 查询），无任何恢复/持久化 API。**② 运行实例状态（同文件）**：`build_runtime_store(task)` 先防御性执行 `validate_task` 再建布局——GVL 裸名声明；每 `ProgramInstance` 只创建一次；user_fb 按声明路径递归展开（嵌套路径如 `PLC_PRG.O1.Sub`），每实例独立持久状态；`init_overrides` 覆盖 `initial`，指向不存在变量抛 `InstanceLayoutError`（不静默丢失），值同样过类型检查；library 实例仅登记 `(path, decl)` 于 `RuntimeLayout.library_instances`，**不猜测管脚、不分配键**（L2 边界显式保留）；FUNCTION 不建实例；VAR_TEMP/VAR_IN_OUT 不分配持久键（VAR_IN_OUT 为引用别名无自身存储）;"运行期调用不创建实例"由"布局建立后 Store 拒绝新键"从机制上保证。**持久键格式集中于单一 helper `persistent_key(path, var)` = `<实例全路径>.<变量名>`**（详见下方设计说明）。**③ 过程映像（`src/runtime/process_image.py`）**：`latch_inputs()` 两阶段——先对 IN 映射完整校验（重复通道/重复变量/非法方向/未知通道/缺失通道/目标未声明/类型不匹配均 `InputImageError`）并形成 `InputSnapshot`（只读、值副本,外部 samples 后续修改不影响）,全部通过后才一次性写 Store,**任何错误发生在第一次写入之前**（无部分更新）;`OutputPending` 最小容器（通道集与类型来自 io_map OUT + GVL 声明类型,`stage()` 仅暂存,无 commit/driver API,业务 Store 写入不自动进入容器）;`make_prev_snapshot()` 提供 prev 快照基础 API,提交时机由后续扫描引擎决定。**④ 出口（`src/runtime/__init__.py`）**追加导出上述 API。**⑤ 测试（`tests/test_runtime_store.py`,24 项）**覆盖任务书 17 条最低要求逐条对号（测试内注释标注"要求 N"）。
- 修改文件：`src/runtime/store.py`（新建）、`src/runtime/process_image.py`（新建）、`src/runtime/__init__.py`（追加导出）、`tests/test_runtime_store.py`（新建）——与基线清单一致。
- 明确未修改：`src/runtime/ir.py`、`src/runtime/loader.py`（SHA 与基线一致,可核）、全部业务块/原语、`docs/PROJECT_STATE.md`（按任务书待关闭后由 Codex 统一同步）、其余 scope 外零改动（交接文件除外）;未实现 IR 执行/求值栈/调用帧/CALL_*/数值运算/转换/量化/回绕/五步扫描/连接解算/lowering/OutputPolicy/门控/shadow/watchdog/HAL/驱动提交/RETAIN 持久化;未从 prototype_05 导入（测试 17 以子进程干净导入验证 + 源码扫描双保险）;零 Git 操作。
- API 与实例键设计说明：**(a) 持久键格式（工程约定,单一落点）**——`IR_SPEC §7` 定义了 GVL=`<var>`、实例引脚=`<instance>.<pin>`、调用帧局部=`<pou>#<frame>.<var>`,未显式裁决 PROGRAM/FB **持久**状态键。本包约定 `persistent_key(path, var)` = `<实例全路径>.<变量名>`（PROGRAM 路径=store_prefix;FB 路径=父路径+"."+实例名）,与 §3 `FBInstance.path` 及 §7 `<instance>.<pin>` 点分形态一致组合,不改动 §7 已定义的三类键;格式集中于该 helper,评审若另定格式仅改一处。判断其为"§7 的自然组合补足"而非可观察语义变更,故未置 BLOCKED——若 Codex 认为属规格裁决请改判。**(b) 进入持久 Store 的区段** = VAR_INPUT/VAR_OUTPUT/VAR（VAR_IN_OUT 别名、VAR_TEMP 每次清零,均不占持久键）。**(c) 类型检查口径**：REAL/LREAL 严格要求 float（int 初值/写入被拒）——这是 Python 侧工程收紧,保证"赋值转换必须经 CONVERT 显式化"（IR_SPEC §5.1）的边界不被 Store 层软化,不是 IEC 官方语义。
- 测试命令与实际结果（2026-07-14 本轮实际运行,Fable5,均 `PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_store -v` → Ran **24** tests, OK。
  2. `python -m unittest tests.test_runtime_ir -v` → Ran **56** tests, OK。
  3. `python -m unittest discover -s tests -t .` → Ran **770** tests, OK（746 既有 + 24 新增,零破坏）。
  4. `python -m unittest discover -s prototype_05 -t .` → Ran **68** tests, OK。
  5. `python -m unittest discover -t .` → Ran **838** tests, OK（770 + 68）。
  过程记录：测试 17 首版在本进程查 `sys.modules`,全仓 discovery 下被原型测试加载污染而误报失败;已改为子进程干净导入验证,五组随后全绿——该次失败与修正如实留档。
- 已知疑问与延后边界：① 持久键格式约定见上,请审核裁决是否需升格写入 `IR_SPEC §7`;② REAL 严格 float 检查对 lowering 期字面量（Python 中写 `0` 而非 `0.0`）不宽容,若前端工作包认为应放宽,属约定变更需评审;③ `OutputPending` 以**通道**为键（io_map OUT 的 channel）,`ENGINE_SCAN_SPEC §2` 的 `output_pending: dict` 未定键型,按 §3 第 4 步"per ch"选 channel,请审核确认;④ 库块实例的管脚键与内部状态留待 L2 描述符工作包,布局仅登记路径;⑤ RETAIN/PERSISTENT 元数据已随键保存,快照/恢复时机属阶段 8（ENGINE §6）。
- scope_sha256: 0ff96dddbd407d644989e9512d3b7e3afd5baecc178be554639fa6aeb2a0fa1c
- scope_manifest:
  - `46a7b9484f73e89413c567d60b8e85470bf2b888c2141c403316ee0ac4194a3a  src/runtime/store.py`
  - `ba64cb8a267c27dd6a162832ecbe09007f18427d450638a1d33b3143d2a86199  src/runtime/process_image.py`
  - `5804a5eaa665fd1361ab8fb17618790c4ebcddf5ded2e0f3e3695c479e1d38a9  src/runtime/__init__.py`
  - `cfa73f025e3a9f060e9962e8d1a46af617cee6f44efcf3ae252438498321a9a7  tests/test_runtime_store.py`
- implementation_finished_at: 2026-07-14

### Codex 审核结论（Round 1）

- verdict: APPROVED
- 已验证事实: `src/runtime/store.py:121-127,185-319` 已建立持久键 helper、声明制 Store、只读快照与按实例路径递归展开的运行时布局；`src/runtime/process_image.py:80-137,144-211` 把输入锁存实现为“先全量校验、后一次性写入”，并把 `output_pending` / `prev` 基础 API 与业务 Store 显式分离；`src/runtime/__init__.py:1-57` 只导出正式运行时 API，没有复用 `prototype_05`。`tests/test_runtime_store.py:93-419` 覆盖了工作包列出的 17 类底线行为，包括未知键/类型错误、实例隔离、输入原子锁存、输出待提交容器、prev 快照隔离和子进程验证“正式包不导入 prototype_05”。这些实现与 `docs/IR_SPEC.md:95-106,173,235-260`、`docs/ENGINE_SCAN_SPEC.md:28-51,158`、`docs/PLATFORM_ROADMAP.md:136-144` 当前阶段边界一致。
- 项目工程约定: 本轮实现把 PROGRAM/FB 持久键集中约定为 `<实例全路径>.<变量名>`（`src/runtime/store.py:29-36,121-127`），并把 REAL/LREAL 的 Store 层检查收紧为“只接受 float，不做隐式 int→REAL 放宽”（`src/runtime/store.py:99-114,193-217`）；这两点都已在代码中明确标成项目工程约定/工程收紧，没有包装成 IEC 或 CODESYS 官方语义。`OutputPending` 以物理 `channel` 为键（`src/runtime/process_image.py:144-190`），与 `docs/ENGINE_SCAN_SPEC.md:28,44-50` 的 `output_pending[ch]` 口径一致。
- 待真机验证假设: 本包仍正确保留后续阶段边界，未把它们升级为既成事实：REAL32 量化、整数回绕/越界、OutputPolicy/驱动提交/shadow/watchdog、RETAIN/PERSISTENT 恢复以及库块管脚/内部状态都未在本包实现，证据见 `src/runtime/store.py:15-27`、`src/runtime/process_image.py:18-20`，与 `docs/ENGINE_SCAN_SPEC.md:56-163`、`docs/IR_SPEC.md:259-260` 一致。当前 Python 测试只证明运行时内存底座行为，不证明与目标 PLC 语义一致。
- 必须返修: 无。
- 非阻塞建议: 若后续 lowering/执行器/外部工具需要直接依赖 PROGRAM/FB 持久键格式或 `OutputPending` 键型，建议单独开工作包把这些工程约定写回 `docs/IR_SPEC.md` / `docs/ENGINE_SCAN_SPEC.md`，避免约束只停留在实现层 docstring。
- 审核证据: `review_started_sha256=0ff96dddbd407d644989e9512d3b7e3afd5baecc178be554639fa6aeb2a0fa1c`，`review_finished_sha256=0ff96dddbd407d644989e9512d3b7e3afd5baecc178be554639fa6aeb2a0fa1c`。逐文件 SHA-256：`src/runtime/store.py=46a7b9484f73e89413c567d60b8e85470bf2b888c2141c403316ee0ac4194a3a`，`src/runtime/process_image.py=ba64cb8a267c27dd6a162832ecbe09007f18427d450638a1d33b3143d2a86199`，`src/runtime/__init__.py=5804a5eaa665fd1361ab8fb17618790c4ebcddf5ded2e0f3e3695c479e1d38a9`，`tests/test_runtime_store.py=cfa73f025e3a9f060e9962e8d1a46af617cee6f44efcf3ae252438498321a9a7`。本轮 Codex 独立复跑 5 组测试且与实施交接一致：`python -m unittest tests.test_runtime_store -v` = 24/24、`python -m unittest tests.test_runtime_ir -v` = 56/56、`python -m unittest discover -s tests -t .` = 770/770、`python -m unittest discover -s prototype_05 -t .` = 68/68、`python -m unittest discover -t .` = 838/838（均在 `PYTHONDONTWRITEBYTECODE=1` 下实际运行）。
- handoff_to: user
- reviewed_at: 2026-07-14 14:50 CST

---

## WP-20260714-004

- title: 阶段 1 显式顺序 IR 执行、TypedValue 求值栈与用户 POU 调用帧
- status: CLOSED
- closed_by: user
- closed_at: 2026-07-16
- closure_note: 用户确认 WP-005 已完整承接并收口本包 Round 3 的 F1 原始值结构校验阻塞项；WP-005 经三轮实施/审核最终 APPROVED，且阶段 1 执行器核心已由项目状态登记为完成。
- owner: user
- handoff_to: user
- round: 3
- max_rounds: 3
- base_commit: 52ab9cf0be57154d798027ac95d4e827dd75e7d3
- created_by: user
- created_at: 2026-07-14
- scope:
  - src/runtime/executor.py
  - src/runtime/numeric.py
  - src/runtime/__init__.py
  - tests/test_runtime_executor.py
- scope_baseline_sha256: e385009e14f64f90e1a1312069fb38533b086ca66d0e32cf31cbeed5a0896437
- scope_baseline_manifest:
  - `ABSENT  src/runtime/executor.py`
  - `ABSENT  src/runtime/numeric.py`
  - `5804a5eaa665fd1361ab8fb17618790c4ebcddf5ded2e0f3e3695c479e1d38a9  src/runtime/__init__.py`
  - `ABSENT  tests/test_runtime_executor.py`
- 开工核验（2026-07-14，Fable5 只读）：分支 main；HEAD=52ab9cf0be57154d798027ac95d4e827dd75e7d3 与 origin/main 一致；工作区无未提交修改；PR #5 已合并（HEAD 即其 merge commit）；WP-20260714-003 已 CLOSED。任务书全文由用户在会话中提供，目标/禁止项/测试要求以其为准。

### Fable5 实施交接（Round 1）

- 完成内容：**① `src/runtime/numeric.py`**——正式数值策略（零原型导入）：`NumericMode`（engineering 默认 / fidelity_f1；`int_native_width: 32|64` 可配置、64 标注为样本工程候选值；`int_intermediate_policy: native_width|declared_width`）；F1 REAL 在 §5.3 全部边界经 `on_const/on_result/on_store` 量化 binary32；`fidelity_f2` 构造即抛 `UnsupportedNumericModeError`（不静默降级）；`trunc_div`（纯整数、向零截断）/`iec_mod`（符号随被除数）/除零抛 `IECMathError`；`convert()` 组合白名单（整数族互转、整数↔REAL/LREAL 经 `src.compat.conversions`、REAL↔LREAL、同型恒等），其余抛 `UnsupportedConversionError` 不猜测；按位 NOT/AND/OR/XOR 按声明位宽解释（两模式一致，标注工程约定）。**② `src/runtime/executor.py`**——`Executor(task, layout, numeric_mode, std_functions, library_adapters)` + `execute_programs(prev_snapshot)`（单一职责，非 scan）；不可变 `TypedValue`；每入口独立栈、被调独立栈；`IRExecutionError` 带 POU/实例路径或 frame id/pc/指令/cause，异常原样上抛不转安全输出；运行期防御（下溢/类型/出口契约/未知指令/未知标签，不只信 loader）；`_Location` 抽象统一持久 Store cell / frame cell / 库块管脚 / INOUT 别名（别名=直接传调用方位置对象，真引用）；解析优先级 别名→frame/VAR_TEMP→本 POU 持久→库块管脚→GVL（FUNCTION 禁 GVL），局部与 GVL 同名不误访；PROGRAM 持久键 `<store_prefix>.<var>`；`CALL_FB_INSTANCE` 相对路径拼当前上下文全路径、只引用 layout 已展开实例（未展开明确报错）、IN 写既有持久键/OUT 执行后写回/INOUT 别名/VAR_TEMP 每次进入类型默认重建退出即弃；`CALL_FUNC` 每调独立 frame（VAR_INPUT 拷入、VAR_OUTPUT/VAR 每调按声明初值或类型默认重建）、返回值按 ret_type 压回、嵌套调用、调用方为 FUNCTION frame 时 OUT/INOUT 经调用方解析正确落 frame、try/finally 保证异常路径 frame 出栈；IN×StackSlot 先按 index 快照后一次性消费 k 项（0=栈顶,书写顺序无关）；`LoadPrev` 只读传入快照，frame/VAR_TEMP/别名/FUNCTION 内一律明确拒绝；标签表按 POU 缓存；`CallStd` 名册注入（缺实现 `MissingStdFunctionError`,返回值类型核对+量化）；`CallFb` 经注入 adapter 委托（键=实例全路径,缺失 `MissingLibraryAdapterError`,adapter 异常带 cause 包装上抛;管脚读写经 `_PinLoc` 委托 adapter,类型未知时诚实跳过核对）。**③ `__init__.py`** 追加导出。**④ 测试 40 项**覆盖任务书 46 条(多条合并于同一测试,注释标注"要求 N")。
- 修改文件：executor.py/numeric.py/test_runtime_executor.py（新建）、`__init__.py`（追加导出）。
- 明确未修改：`ir.py`/`loader.py`/`store.py`/`process_image.py`（SHA 可核）、`src/blocks`/`src/primitives`、`PROJECT_STATE.md`、scope 外零改动（交接文件除外）;未实现五步扫描/latch_inputs 调用/OutputPolicy/OutputPending 提交/驱动/scan runner/watchdog/shadow/门控/RETAIN 恢复/HAL/lowering/CFC 定序/完整 L2 注册表/完整 IEC 函数库/F2;零 Git 操作。
- Executor 公开 API：见上；数值模式构造时绑定,无热切换接口。
- frame 与变量解析设计：`_Ctx`（每 POU 体一次执行:独立栈+cells+aliases+persist 表）;frame id = `<FUNC 名>#<序号>`;`_active_frames` 仅诊断用,try/finally 出栈。
- Binding/StackSlot：严格按 IR_SPEC §5.2 + v2.2.3 index 工程约定;OUT 仅 StoreKey（OUT→StackSlot 维持 loader 保守拒绝,未修改 loader）;INOUT 仅 StoreKey→真别名,无拷入拷回。
- E/F1 数值说明：E 不量化不回绕无隐式提升;F1 为**当前候选行为**（native/declared 二策略、有符号补码回绕、越界 CONVERT 截断、REAL DIV 除零显式报错——均待真机黄金轨迹裁决,代码/测试/本记录均如此标注,不承诺 bit-exact）。
- CallStd/CallFb 延后边界：std 名册与 library adapter 均为注入点;正式 L2 BlockSchema/RuntimeAdapter 注册表（(block_type,variant) 解析、call_adapter 约定、14 块+8 原语接入）属独立工作包,届时替换注入点;测试用假 adapter,不宣称 IEC 函数库/库块接入完成。
- 实际测试命令与结果（2026-07-14,Fable5 实跑,均 `PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_executor -v` → Ran **40** tests, OK。
  2. `python -m unittest tests.test_runtime_store -v` → Ran **24** tests, OK。
  3. `python -m unittest tests.test_runtime_ir -v` → Ran **56** tests, OK。
  4. `python -m unittest discover -s tests -t .` → Ran **810** tests, OK（770 既有 + 40 新增,零破坏）。
  5. `python -m unittest discover -s prototype_05 -t .` → Ran **68** tests, OK。
  6. `python -m unittest discover -t .` → Ran **878** tests, OK（810 + 68）。
- 首次失败与修复记录（如实）：首轮 40 项中 2 失败——① `test_load_prev_reads_snapshot`:测试辅助 `_run` 的 prev_overrides 分支在快照前多写了一次当前 Store（测试侧 bug）,修为"临时写入→快照→恢复";② `test_prev_of_frame_var_rejected`:执行器 `_read_prev` 先查 prev 是否存在再查 frame 变量,导致 FUNCTION 内 LOAD_PREV 报"无 prev 快照"而非更根本的"无上一拍语义",调整检查顺序（frame/VAR_TEMP/别名/FUNCTION 先行明确拒绝）并清理死分支。重跑全绿（上列数字即修复后实跑）。
- 已知疑问与延后边界：① adapter 协议（`write_pin/read_pin/step(dt_ms)/可选 pin_type`）是本包注入边界的工程约定,L2 工作包定稿 RuntimeAdapter 后替换;② REAL/LREAL DIV 除零显式抛错（不产 ±inf）为当前候选行为,CODESYS 真机除零行为未验证;③ FUNCTION frame 的 VAR/VAR_OUTPUT 每调初值=声明 initial 优先、否则类型默认,VAR_TEMP 恒类型默认（任务书 §六.4"按类型默认值初始化"）;④ 按位 NOT/逻辑运算对整数族按声明位宽解释（两模式一致）为工程约定;⑤ E 模式越界 CONVERT 保值不回绕（IR_SPEC §8 E 不回绕;`int_overflow_convert_policy=TBD`）;⑥ FUNCTION 内 LOAD_PREV 一律拒绝（FUNCTION 无持久状态亦禁 GVL,无合法 prev 目标）。
- scope_manifest:
  - `48f6ccfc137772907dd526115d793ef25f74a63b70ccc6ea036bf926941836cc  src/runtime/executor.py`
  - `9a870a7f7fc1fa93c94f25837e5e1948681292c0da17eba0009d2a6a1636711b  src/runtime/numeric.py`
  - `7bf2db854f286d50465d25fa2ae8b4c17fea4830d99fe55f4076baaccd2fe18c  src/runtime/__init__.py`
  - `ee98c1f18d29a13db2f425f0f6d3644884bd54fa147b3da0258a89a8feea1731  tests/test_runtime_executor.py`
- scope_sha256: cfd921b5a9e6f2e27a3cb0f4eba98a0b8786265fb8196b48b8eb25190ab60719
- implementation_finished_at: 2026-07-14

### Codex 审核结论（Round 1）

- verdict: CHANGES_REQUESTED
- 已验证事实：`src/runtime/numeric.py:1-200` 已把 E/F1/F2 数值模式边界、纯整数 `DIV/MOD`、F1 `REAL` binary32 量化和不支持转换的显式拒绝集中实现；`src/runtime/executor.py:264-765` 已实现 PROGRAM 顺序执行、TypedValue 求值栈、FUNCTION/用户 FB 调用帧、`LOAD_PREV` 快照读取以及 std/library 注入边界。Codex 独立复跑 6 组测试：`PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_executor -v` = 40/40、`tests.test_runtime_store -v` = 24/24、`tests.test_runtime_ir -v` = 56/56、`discover -s tests -t .` = 810/810、`discover -s prototype_05 -t .` = 68/68、`discover -t .` = 878/878，均通过。审核开始与结束的 scope SHA-256 一致，本轮审核有效，期间未发现 scope 漂移。
- 项目工程约定：`src/runtime/numeric.py:1-35,118-177` 对 `int_native_width=64`、`int_intermediate_policy`、位运算按声明位宽解释以及 `fidelity_f2` 直接拒绝等都明确标成工程约定/候选行为，没有包装成 IEC 或 CODESYS 官方已证实语义；`src/runtime/executor.py:33-50,718-760` 对 std/library adapter 注入边界的分层表述也保持诚实。
- 待真机验证假设：F1/F2 下整数中间溢出发生点、REAL 中间精度与 REAL DIV 除零行为、library adapter 最终协议仍需 `TARGET_PROFILE.md` / 黄金轨迹 / 后续 L2 工作包裁决；当前 Python 测试只证明现实现行为，不证明与目标 PLC 语义一致。
- 必须返修：1) `src/runtime/executor.py:349-356` 只把 `NumericError` / `ValueError` / `TypeError` 等包装成 `IRExecutionError`，但 `StoreError` 族未纳入；同时 `src/runtime/executor.py:650-651` 直接信任 `CallFunc.ret_type`。结果是装载后若 IR 被污染，执行器会漏出原始 `StoreTypeError` 而不是带 POU/pc/指令上下文的 `IRExecutionError`。我用最小反证复现：先构造并验证合法任务，再把 `CallFunc('F', (), 'INT')` 篡改为 `CallFunc('F', (), 'REAL')`、后接 `StoreVar('X', 'REAL')`，`execute_programs()` 实际抛出的是 `StoreTypeError: 键 'X' 写入值 1 与声明类型 REAL 不匹配`。这与模块文档和测试文件里“运行期防御不只信 loader”的承诺不一致。请把 `StoreError` 包装进 `IRExecutionError`，并在运行期补一层 `CallFunc.ret_type`/调用返回类型一致性校验。
- 必须返修：2) `src/runtime/executor.py:581-594` 的 OUT 写回只看 `binding.type`，不校验目标位置的声明类型；`src/runtime/executor.py:163-167` 的 `_CellLoc.write()` 也完全不做类型检查。结果是调用方 frame/local 被装载后污染时，错误类型可被静默写入调用帧，运行期防御失效。我用最小反证复现：先验证一个合法任务（`Fill.O: INT -> Outer.a: INT`），再把 `Outer.locals` 中的 `a` 篡改为 `REAL`，`execute_programs()` 仍然返回 `OK`，没有任何异常。按本工作包在 `tests/test_runtime_executor.py:8-10` 申明的目标，运行期应继续拦截这类 loader 之后的非法 IR。请在 OUT/INOUT 写回路径对 `_CellLoc` / 调用方目标位置补类型校验，并新增覆盖这类 post-load 污染的反证测试。
- 非阻塞建议：把上述两条最小反证直接固化进 `tests/test_runtime_executor.py`，延续当前“装载后篡改 code/声明以验证运行期防御”的测试风格，避免后续执行器或调用帧重构把同类漏洞重新带回。
- 审核证据：`review_started_sha256=cfd921b5a9e6f2e27a3cb0f4eba98a0b8786265fb8196b48b8eb25190ab60719`，`review_finished_sha256=cfd921b5a9e6f2e27a3cb0f4eba98a0b8786265fb8196b48b8eb25190ab60719`。逐文件 SHA-256：`src/runtime/executor.py=48f6ccfc137772907dd526115d793ef25f74a63b70ccc6ea036bf926941836cc`，`src/runtime/numeric.py=9a870a7f7fc1fa93c94f25837e5e1948681292c0da17eba0009d2a6a1636711b`，`src/runtime/__init__.py=7bf2db854f286d50465d25fa2ae8b4c17fea4830d99fe55f4076baaccd2fe18c`，`tests/test_runtime_executor.py=ee98c1f18d29a13db2f425f0f6d3644884bd54fa147b3da0258a89a8feea1731`。除复跑 6 组测试外，本轮还做了两条最小手工反证：① post-load 篡改 `CallFunc.ret_type` 后执行器漏出原始 `StoreTypeError`；② post-load 篡改调用方 frame 局部声明类型后，OUT 写回被静默接受。
- handoff_to: fable5
- reviewed_at: 2026-07-14 17:56 CST

### Fable5 实施交接（Round 2，返修）

- 完成内容：Codex Round 1 两条"必须返修"逐条落地，非阻塞建议（反证固化为测试）全部采纳。**1) StoreError 包装 + CallFunc.ret_type 运行期核对**——`src/runtime/executor.py::_run` 的异常包装元组补入 `StoreError`（含 `StoreTypeError`/`UnknownStoreKeyError` 等全族）：装载后 IR 被污染时，Store 层异常一律带 POU/实例路径或 frame id/pc/指令上下文包装为 `IRExecutionError`，不再裸露；`_call_func` 在求值任何绑定/压栈之前先核对 `ins.ret_type == target.return_type`，不一致立即抛带上下文的 `IRExecutionError`（Codex 反证①的篡改现于调用点被拦截，不再走到 `StoreVar` 才漏出原始 `StoreTypeError`）。**2) OUT 写回目标类型校验 + `_CellLoc.write` 结构性检查**——`_write_out_bindings` 在写回前核对目标位置 `declared_type()` 与绑定类型，不一致抛 `IRExecutionError`（`declared=None` 仅见于 L2 未接入的库块管脚，诚实跳过核对并注明由结构性检查兜底）；`_CellLoc.write` 增加与 `Store.write` 同口径的 `check_value_type` 结构性检查，违约抛 `StoreTypeError`（经修复 1 包装为带上下文的 `IRExecutionError`）——即使声明与绑定被一致性篡改骗过声明核对，错误 Python 类型的值也无法落入 frame cell（Codex 反证②的静默写入路径已封死）。**3) 反证固化**：新增 `TestPostLoadTamperingDefenses` 3 项——篡改 `CallFunc.ret_type`、篡改调用方 frame 局部声明类型（OUT 写回拒绝）、声明+绑定一并篡改（`_CellLoc` 结构检查拦截且 cause 为 `StoreTypeError`），延续既有"装载后篡改"测试风格。
- 修改文件：`src/runtime/executor.py`（import 补 StoreError/StoreTypeError、`_CellLoc.write`、`_run` 异常元组、`_write_out_bindings`、`_call_func`）、`tests/test_runtime_executor.py`（40→43 项：import 补 StoreTypeError + 新增测试类 3 项）。
- 明确未修改：`src/runtime/numeric.py`、`src/runtime/__init__.py`（本轮意见不涉及，SHA-256 与 Round 1 一致，见 scope_manifest）；`ir.py`/`loader.py`/`store.py`/`process_image.py` 及全部 scope 外文件零改动（交接文件除外）；未执行任何 Git 操作。
- 实际测试命令与结果（2026-07-14 本轮实际运行，Fable5，均 `PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_executor` → Ran **43** tests, OK（40 + 3 新增反证）。
  2. `python -m unittest tests.test_runtime_store` → Ran **24** tests, OK。
  3. `python -m unittest tests.test_runtime_ir` → Ran **56** tests, OK。
  4. `python -m unittest discover -s tests -t .` → Ran **813** tests, OK（810 + 3，零破坏）。
  5. `python -m unittest discover -s prototype_05 -t .` → Ran **68** tests, OK。
  6. `python -m unittest discover -t .` → Ran **881** tests, OK（813 + 68）。
- 已知疑问与延后边界：① 本轮防御收口聚焦 Codex 点名的两条路径（异常包装/ret_type、OUT 写回/_CellLoc）；FUNCTION frame 的 VAR_INPUT 拷入与 frame cell 初值播种（`_call_func`/`_make_ctx` 直接构造 dict，不经 `_CellLoc.write`）在"绑定类型与 Const 值一并被结构性篡改"的极端场景下仍可能播入错误 Python 类型的初值——后续任何持久 Store 写回会被拦截，frame 内读写以类型标签为准；如 Codex 认为该播种点也须结构性校验，属同类小改，可下轮补或另列。② `_write_out_bindings` 的 `declared=None` 跳过仅适用于 `_PinLoc`（库块管脚类型待 L2 描述符），维持既有诚实边界。③ Round 1 其余已知疑问（adapter 协议、REAL DIV 除零候选行为等）维持原状，Codex Round 1 未要求变更。以上 Python 测试仅证明当前实现行为，不构成与目标 PLC 语义一致的证据。
- scope_manifest:
  - `159e04a5ef57cb9f9c4316049ef178e77fd80af700ec42627b89412f94ab1dc2  src/runtime/executor.py`
  - `9a870a7f7fc1fa93c94f25837e5e1948681292c0da17eba0009d2a6a1636711b  src/runtime/numeric.py`
  - `7bf2db854f286d50465d25fa2ae8b4c17fea4830d99fe55f4076baaccd2fe18c  src/runtime/__init__.py`
  - `edfdd96d78001d5c204d8fdcacdf160e052c07c87758dbe951e148d3958a49d7  tests/test_runtime_executor.py`
- scope_sha256: 2a3f5c59f785b89c302a882066d37e133d493b7d9e5ba8334647d21cbb6ff1b0
- handoff_to: codex
- implementation_finished_at: 2026-07-14 18:40 CST

### Codex 审核结论（Round 2）

- verdict: CHANGES_REQUESTED
- 已验证事实：Round 1 点名的两条缺陷已按提交说明落地：`src/runtime/executor.py:351-365` 现已把 `StoreError` 族统一包装为带上下文的 `IRExecutionError`，`src/runtime/executor.py:625-631` 也补上了 `CallFunc.ret_type` 与 FUNCTION 定义返回类型的一致性校验；`src/runtime/executor.py:165-173,590-612` 为 `_CellLoc.write()` 与 OUT 写回路径增加了结构性类型检查，`tests/test_runtime_executor.py:746-793` 也新增了 3 条对应反证。Codex 独立复跑 `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_executor` = 43/43、`discover -s tests -t .` = 813/813、`discover -s prototype_05 -t .` = 68/68、`discover -t .` = 881/881，均通过。
- 项目工程约定：`src/runtime/numeric.py` 中 F1 中间位宽政策、`int_native_width=64`、位运算按声明位宽解释以及 `fidelity_f2` 明确拒绝等分层表述仍然诚实，未被包装成 IEC / CODESYS 官方已证实语义；`CallStd` / `CallFb` 的注入边界保持在正式 L2 注册表工作包之外，这一职责边界未被越界扩张。
- 待真机验证假设：F1/F2 下整数中间溢出发生点、REAL 中间精度与 REAL DIV 除零行为、library adapter 最终协议仍需 `TARGET_PROFILE.md` / 黄金轨迹 / 后续 L2 工作包裁决；当前 Python 测试只证明现实现行为，不证明与目标 PLC 语义一致。
- 必须返修：1) **FUNCTION frame 播种仍绕过结构性类型检查，装载后篡改的非法值可静默进入 frame 并被当成合法类型执行。** `src/runtime/executor.py:640-656` 把 `VAR_INPUT` / `VAR_OUTPUT` / `VAR` 的初值直接写入 `callee.cells[...] = [iec_type, value]`，`src/runtime/executor.py:789-791` 的 `_initial_of(...)` 也直接返回声明初值，两条路径都没有经过 `check_value_type(...)` 或 `_CellLoc.write()`。结果是 Round 2 修复覆盖了“写回”阶段，却没覆盖“播种”阶段。我做了两条最小反证，当前代码都会**静默成功**而不是报错：① 合法装载后把 `CallFunc("AddHalf", ...)` 的绑定篡改为 `Binding("I", "IN", Const(1, "INT"), "REAL")`，其中 `FUNCTION AddHalf(I: REAL): REAL` 的函数体仅做 `I + 0.5`；`execute_programs()` 最终把 `X` 写成 `1.5`（`float`），全过程无异常。② 合法装载后把 `FUNCTION LocalHalf` 的局部声明篡改为 `VarDecl("a", "REAL", initial=1)`，函数体同样做 `a + 0.5`；`execute_programs()` 也静默得到 `X=1.5`。这违反了模块与测试文件反复声明的“运行期防御不只信 loader / 装载后篡改仍须拦截”目标。请在 FUNCTION frame 的 `VAR_INPUT` 拷入与 `VAR/VAR_OUTPUT` 初值播种路径补上结构性类型校验，并新增覆盖这两条篡改路径的反证测试。
- 非阻塞建议：返修时保持当前的“装载校验通过后篡改 IR/声明”测试风格即可，不需要引入新的测试框架；把上述两条最小反证各固化成一条测试，后续执行器/调用帧重构时更不容易把同类漏洞带回。
- 审核证据：审核开始与结束的 scope SHA-256 一致，本轮审核有效，期间未发现 scope 漂移。`review_started_sha256=2a3f5c59f785b89c302a882066d37e133d493b7d9e5ba8334647d21cbb6ff1b0`，`review_finished_sha256=2a3f5c59f785b89c302a882066d37e133d493b7d9e5ba8334647d21cbb6ff1b0`。逐文件 SHA-256：`src/runtime/executor.py=159e04a5ef57cb9f9c4316049ef178e77fd80af700ec42627b89412f94ab1dc2`，`src/runtime/numeric.py=9a870a7f7fc1fa93c94f25837e5e1948681292c0da17eba0009d2a6a1636711b`，`src/runtime/__init__.py=7bf2db854f286d50465d25fa2ae8b4c17fea4830d99fe55f4076baaccd2fe18c`，`tests/test_runtime_executor.py=edfdd96d78001d5c204d8fdcacdf160e052c07c87758dbe951e148d3958a49d7`。除复跑 4 组测试外，本轮还手工复现了两条 post-load 篡改反证：`Binding("I","IN",Const(1,"INT"),"REAL")` 进入 `FUNCTION AddHalf(I: REAL): REAL := I + 0.5` 后静默写出 `X=1.5`；`VarDecl("a","REAL", initial=1)` 进入 `FUNCTION LocalHalf(): REAL := a + 0.5` 后同样静默写出 `X=1.5`。
- handoff_to: fable5
- reviewed_at: 2026-07-14 19:16 CST

### Fable5 实施交接（Round 3，返修）

- 完成内容：Codex Round 2 唯一"必须返修"（FUNCTION frame **播种阶段**绕过结构性类型检查）落地，非阻塞建议（两条反证固化为测试）全部采纳。**修复**：`src/runtime/executor.py` 新增 `_seed_cell(...)` 播种入口——所有 FUNCTION frame cell 的建立（VAR_INPUT 拷入、VAR_OUTPUT/VAR 按 `_initial_of` 初值重建）统一经该入口做 `check_value_type` 结构性检查（与 `Store.write`/`_CellLoc.write` 同口径），违约抛带 POU/where/pc/指令上下文的 `IRExecutionError`；VAR_INPUT 路径另补"绑定 TypedValue 类型 vs 形参声明类型"一致性核对（镜像 loader 静态规则,不只信 loader）。**检查作用于 `on_store` 之前的原始值**：F1 模式下 `quantize_real32(1)` 会把 int 洗成合法 float,检查若放在数值钩子之后,播种防御在 F1 下会被掩盖——此点已写入 `_seed_cell` docstring 与调用点注释。模块 docstring"调用帧"要点同步补播种防御一句。**反证固化**：`TestPostLoadTamperingDefenses` 新增 2 项,完全按 Codex 复现构造——① 装载后把 IN 绑定篡改为 `Binding("I","IN",Const(1,"INT"),"REAL")` 进入 `AddHalf(I: REAL): REAL := I + 0.5`；② 装载后把局部声明篡改为 `VarDecl("a","REAL",initial=1)` 进入 `LocalHalf(): REAL := a + 0.5`。两者此前静默算出 X=1.5,现均在播种点抛 `IRExecutionError`（含上下文）,并断言 X 未被写出（仍为 0.0）。
- 修改文件：`src/runtime/executor.py`（模块 docstring、`_seed_cell` 新增、`_call_func` 播种块改经 `_seed_cell`）、`tests/test_runtime_executor.py`（43→45 项：新增 `_addhalf_def`/`_localhalf_def` 辅助 + 反证 2 项）。
- 明确未修改：`src/runtime/numeric.py`、`src/runtime/__init__.py`（本轮意见不涉及,SHA-256 与 Round 2 一致,见 scope_manifest）；`ir.py`/`loader.py`/`store.py`/`process_image.py` 及全部 scope 外文件零改动（交接文件除外）；未执行任何 Git 操作。
- 有意未加检查的两处播种及理由：① `_make_ctx` 的 VAR_TEMP 播种值 = `default_value(d.iec_type)`,由类型直接派生,不存在"声明与值分离"的可篡改缝隙（除非 `default_value` 自身实现错误,属另一类缺陷）；② `_call_fb_instance` 的 IN 拷入经 `Store.write`,已有结构性检查兜底（Round 2 已由 `_run` 的 `StoreError` 包装带上下文）。如 Codex 认为①也应统一过 `_seed_cell`,属同类小改可再补。
- 实际测试命令与结果（2026-07-14 本轮实际运行,Fable5,均 `PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_executor` → Ran **45** tests, OK（43 + 2 新增反证）。
  2. `python -m unittest tests.test_runtime_store` → Ran **24** tests, OK。
  3. `python -m unittest tests.test_runtime_ir` → Ran **56** tests, OK。
  4. `python -m unittest discover -s tests -t .` → Ran **815** tests, OK（813 + 2,零破坏）。
  5. `python -m unittest discover -s prototype_05 -t .` → Ran **68** tests, OK。
  6. `python -m unittest discover -t .` → Ran **883** tests, OK（815 + 68）。
- 已知疑问与延后边界：① VAR_INPUT 播种的"绑定类型 vs 形参声明类型"核对是本轮顺带补的同源防御（同一播种点、Codex 反证①的变体路径）,若认为超出返修范围可指出,回退不影响两条点名反证的拦截（结构性检查独立生效）；② Round 1/2 其余已知疑问（adapter 协议、REAL DIV 除零候选行为、FUNCTION frame 播种以外的边界）维持原状。以上 Python 测试仅证明当前实现行为,不构成与目标 PLC 语义一致的证据。
- scope_manifest:
  - `22ed199148d1ff1dfed9ea5bcd7d2c79b654aa7ab7efc7b7a981ac6463ad6bbf  src/runtime/executor.py`
  - `9a870a7f7fc1fa93c94f25837e5e1948681292c0da17eba0009d2a6a1636711b  src/runtime/numeric.py`
  - `7bf2db854f286d50465d25fa2ae8b4c17fea4830d99fe55f4076baaccd2fe18c  src/runtime/__init__.py`
  - `a8a4861ab28f3eac3c0ce497b0415f0f71902fd72f617d7c1db6b1f54a60a738  tests/test_runtime_executor.py`
- scope_sha256: e9788870660b3a3cdf886f518f5b4944c9c2ec493c2c946923eebd8e3f4c1f22
- handoff_to: codex
- implementation_finished_at: 2026-07-14 20:35 CST

### Codex 审核结论（Round 3）

- verdict: BLOCKED
- 已验证事实：`src/runtime/executor.py:617-633,665-690` 已新增 `_seed_cell(...)` 并把 FUNCTION frame 的 VAR_INPUT/VAR_OUTPUT/VAR 播种统一经结构性类型检查；`tests/test_runtime_executor.py:813-851` 也新增了两条装载后篡改反证。Codex 独立复跑 `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_executor` = 45/45、`python -m unittest discover -s tests -t .` = 815/815、`python -m unittest discover -s prototype_05 -t .` = 68/68、`python -m unittest discover -t .` = 883/883，均通过。
- 项目工程约定：`src/runtime/numeric.py:1-31,141-199` 中 F1 中间位宽政策、`int_native_width=64`、位运算按声明位宽解释以及 `fidelity_f2` 明确拒绝等分层表述仍然诚实，未被包装成 IEC / CODESYS 官方已证实语义；`CallStd` / `CallFb` 的注入边界也仍停留在正式 L2 注册表工作包之外。
- 待真机验证假设：F1/F2 下整数中间溢出发生点、REAL 中间精度与 REAL DIV 除零行为、library adapter 最终协议仍需 `TARGET_PROFILE.md` / 黄金轨迹 / 后续 L2 工作包裁决；当前 Python 测试只证明现实现行为，不证明与目标 PLC 语义一致。
- 必须返修 / 阻塞原因：1) **Round 3 声称修复的“F1 下原始值先验结构检查”仍可被 `on_const` 洗掉。** `src/runtime/executor.py:569-572` 在处理 `Const` 实参时先执行 `self.mode.on_const(...)`，`src/runtime/executor.py:681-683` 再把已经量化后的 `tv.value` 当作 `_seed_cell(..., raw=...)` 的原始值传入。结果是在 `NumericMode(mode="fidelity_f1")` 下，装载后把 `CallFunc("AddHalf", ...)` 的绑定篡改为 `Binding("I","IN",Const(1,"INT"),"REAL")` 仍会静默成功，`execute_programs()` 最终把 `X` 写成 `1.5`，没有抛出 `IRExecutionError`。这与 Fable5 在 Round 3 交接里“检查作用于 `on_store` 之前的原始值，F1 不会把非法 int 洗成合法 float”的表述不一致；现有反证测试 `tests/test_runtime_executor.py:813-833` 只在默认 engineering 模式下执行，没锁住 F1 路径。
- 必须返修 / 阻塞原因：2) **FUNCTION 返回边界与被调体内 `LoadConst` 仍缺少结构性原始值校验，F1 下同样可把非法 REAL 值洗白。** `src/runtime/executor.py:380-382` 对 `LoadConst` 直接把 `mode.on_const(ins.value, ins.type)` 压栈，没有任何 `check_value_type(...)`；`src/runtime/executor.py:710-711` 又把 callee 返回值经 `mode.on_store(...)` 后直接压回调用方栈。Codex 手工反证：合法装载后把 `FUNCTION BadReal(): REAL` 的函数体篡改为 `LoadConst(1, "REAL")`，主程序执行 `CallFunc("BadReal", (), "REAL") ; LoadConst(0.5, "REAL") ; BinOp("ADD","REAL") ; StoreVar("X","REAL")`，在 F1 模式下同样静默得到 `X=1.5`，没有任何异常。也就是说，Round 3 只封住了 frame 播种点，没有封住“被调体制造错误 TypedValue → 返回到调用方继续参与运算”这条同类通路。
- 非阻塞建议：后续返修若继续沿当前测试风格推进，至少把上述两条最小反证各固化成一条 F1 模式测试；同时审视 `LoadConst`、FUNCTION 返回压栈、`Const` 绑定求值这三处是否都需要共享同一套“原始值先验结构检查”边界，避免再出现某个边界点被 F1 数值钩子洗白。
- 审核证据：审核开始与结束的 scope SHA-256 一致，本轮审核有效，期间未发现 scope 漂移。`review_started_sha256=e9788870660b3a3cdf886f518f5b4944c9c2ec493c2c946923eebd8e3f4c1f22`，`review_finished_sha256=e9788870660b3a3cdf886f518f5b4944c9c2ec493c2c946923eebd8e3f4c1f22`。逐文件 SHA-256：`src/runtime/executor.py=22ed199148d1ff1dfed9ea5bcd7d2c79b654aa7ab7efc7b7a981ac6463ad6bbf`，`src/runtime/numeric.py=9a870a7f7fc1fa93c94f25837e5e1948681292c0da17eba0009d2a6a1636711b`，`src/runtime/__init__.py=7bf2db854f286d50465d25fa2ae8b4c17fea4830d99fe55f4076baaccd2fe18c`，`tests/test_runtime_executor.py=a8a4861ab28f3eac3c0ce497b0415f0f71902fd72f617d7c1db6b1f54a60a738`。除复跑 4 组测试外，本轮还手工复现了两条 F1 反证：① `Binding("I","IN",Const(1,"INT"),"REAL")` 进入 `AddHalf(I: REAL): REAL := I + 0.5` 后静默写出 `X=1.5`；② 把 `BadReal(): REAL` 篡改为 `LoadConst(1,"REAL")` 后，调用方继续做 `+ 0.5` 并静默写出 `X=1.5`。由于当前已到 `round=3` 且 `max_rounds=3`，协议不允许再自动回 Fable5，本轮按硬规则转 `BLOCKED` 交用户仲裁。
- handoff_to: user
- reviewed_at: 2026-07-14 20:55 CST

---

## WP-20260714-005

- title: WP-004 收口——F1 量化前原始值结构校验与 TypedValue 边界防御
- status: CLOSED
- owner: user
- handoff_to: user
- round: 3
- max_rounds: 3
- base_commit: 52ab9cf0be57154d798027ac95d4e827dd75e7d3
- depends_on: WP-20260714-004 BLOCKED artifact
- created_by: user
- created_at: 2026-07-14
- scope:
  - src/runtime/executor.py
  - tests/test_runtime_executor.py
- scope_baseline_sha256: 6e9dece6711ba7ed48443f4542cab12e574a623eae7c2d682e89c11fdf43e518
- scope_baseline_manifest:
  - `22ed199148d1ff1dfed9ea5bcd7d2c79b654aa7ab7efc7b7a981ac6463ad6bbf  src/runtime/executor.py`
  - `a8a4861ab28f3eac3c0ce497b0415f0f71902fd72f617d7c1db6b1f54a60a738  tests/test_runtime_executor.py`
- 开工核验（2026-07-14，Fable5 只读）：分支 main；HEAD=52ab9cf 与任务书一致；WP-004 顶层 BLOCKED/user/user、round 3/3 未动；两 scope 文件与聚合 SHA-256 与任务书完全一致；工作区含 WP-004 未提交成果（预期状态，未清理）。

### Fable5 实施交接（Round 1）

- 完成内容：建立共享的"原始值先验检查"入口 `Executor._checked_boundary(ctx, pc, instr, raw_value, iec_type, hook, what)`——顺序固定为**原始 Python 值结构检查（`check_value_type` 口径）→ 数值钩子 → 钩子结果复检**，E 与 F1 共享同一结构检查（非 F1 特判,差别只来自钩子）。四处边界统一接入：① `LoadConst`：原始 `ins.value` 检查先于 `on_const`；② IN×Const 绑定：新增 Const 类型标签与 Binding.type 一致性核对 + 原始 `Const.value` 先于 `on_const` 检查（不再依赖 `_seed_cell` 兜底）；③ FUNCTION 返回边界：出口栈恰一元素（既有）+ 返回标签=定义返回类型（既有）+ 返回标签=CallFunc.ret_type（新增直接核对）+ **返回值原始结构检查先于 `on_store`** + 钩子结果复检——被调体制造的"标签正确、结构错误"TypedValue 不再能被 F1 洗白后返回；④ `CallStd` 返回：原检查已在钩子前（无真实缺陷）,统一改走共享通道以消除口径分叉并补钩子结果复检。
- 根因与修复机制：WP-004 三轮的防御都布在"值已经过数值钩子之后"（播种点 raw 参数在 Const 路径拿到的已是量化后值）,F1 的 `on_const/on_store` 会把结构性错误的 `int` 洗成合法 `float`,使后续一切检查失明。修复把检查点前移到钩子之前,并明确数值钩子只许量化/回绕/显式转换、不许"修正"结构错误——钩子结果复检进一步防钩子越权。
- 修改文件：`src/runtime/executor.py`、`tests/test_runtime_executor.py`（均在 scope 内）。
- 明确未修改：`numeric.py`(9a870a7f…)/`__init__.py`(7bf2db85…)/`store.py`(46a7b948…)/`ir.py`(35ec127d…)/`loader.py`(d8d29036…)——SHA 与 WP-004 Round 3 审核记录逐一核对一致；WP-004 状态与历史零改动；`PROJECT_STATE.md`/`RISKS.md` 未动；零 Git 操作。
- TypedValue 创建入口审计（11 处）：**改走共享检查（4）**——LoadConst、IN×Const、CallFunc 返回、CallStd 返回；**已有可信结构保证（4）**——LoadVar/LoadPrev（值来自 Store/StoreSnapshot/frame cell,写入侧已有结构校验:`Store.write`、`_CellLoc.write`(Round 3 加检)、播种检查）、IN×StackSlot（栈值在其创建点已受守,标签逐一核对）、IN×StoreKey（值来自受守 Store/cell,若绑定类型被篡改,VAR_INPUT 播种的标签核对+raw 检查拦截）；**依赖运算/写入实现（3）**——BinOp/UnOp（操作数出栈已受守,结果由 `trunc_div/iec_mod`/Python 算术产生,类型封闭;比较恒 `bool()`）、Convert（`numeric.convert` 白名单+`on_store` 出口,源值出栈已受守）、FB IN 拷入/OUT 写回（值来自受守边界,`on_store` 后经 `Store.write`/`_CellLoc.write` 结构复检兜底）。结论：所有进入数值钩子的值,其原始结构要么在本入口先验检查,要么来自已受守来源——无"先量化后检查"残留。
- 新增反证测试（7,类 `TestRawValueBoundaryDefenses`,全部断言异常类型/上下文/目标未部分写入）：① F1 下篡改绑定 `Const(1,"INT")→REAL`（Codex 反证①固化,标签不一致拦截）;② 变体 `Const(1,"REAL")`（标签一致但原始值 int,原始值检查拦截——证明不依赖标签核对）;③ F1 下 FUNCTION 体篡改为 `LoadConst(1,"REAL")`（Codex 反证②固化,X 保持 0.0）;④ 同一篡改在 Engineering 下同样拒绝（非 F1 特判）;⑤ 合法 `LoadConst(1.0,"REAL")` 在 E/F1 均正常（防误伤）;⑥ `LoadConst(True,"INT")` 拒绝（bool 非整数）;⑦ 受控执行器子类使被调出口栈出现"REAL 标签、int 结构"值,返回边界在 `on_store` 前拒绝（E/F1 双模式,生产代码无后门）。另按修复语义更新既有测试 `test_tampered_input_binding_seed_rejected` 的消息断言：该篡改现在在**绑定求值边界**（早于播种兜底）被拦截,正是任务书"必须在绑定求值或更早边界被拒绝"的要求,断言随之锁定新边界（其余断言:异常类型/上下文/X 未写出,不变）。
- 反证真实失败验证（任务书 §九）：修复前行为通过一次性脚本还原（monkeypatch `_checked_boundary` 为"只跑钩子"）,两条 Codex 手工反证均复现**静默 X=1.5、无异常**（与 Round 3 审核记录一致）;恢复修复后同场景全部抛 `IRExecutionError` 且 X 保持 0.0。
- 实际测试命令与结果（2026-07-14,Fable5 实跑,均 `PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_executor -v` → Ran **52** tests, OK（45 既有 + 7 新增）。
  2. `python -m unittest tests.test_runtime_store -v` → Ran **24** tests, OK。
  3. `python -m unittest tests.test_runtime_ir -v` → Ran **56** tests, OK。
  4. `python -m unittest discover -s tests -t .` → Ran **822** tests, OK（815 + 7,零破坏）。
  5. `python -m unittest discover -s prototype_05 -t .` → Ran **68** tests, OK。
  6. `python -m unittest discover -t .` → Ran **890** tests, OK（822 + 68）。
- 首次失败与修复记录（如实）：修复落地后首跑 52 项中 1 失败——WP-004 Round 2 的 `test_tampered_input_binding_seed_rejected` 断言错误消息含"播种",而该篡改现被更早的绑定求值边界拦截（消息为"Const 类型标签…不一致"）。此为防御点**按任务书要求前移**的预期效果,非回归;已更新该测试断言锁定新边界并重跑全绿（上列数字即修复后实跑）。
- 已知疑问与延后边界：① `_seed_cell` 的 raw 检查保留（StackSlot/StoreKey 来源的播种防御仍经它兜底）,与共享通道并存——若审核认为应进一步归并可在后续包处理;② 库块管脚（`_PinLoc`）值的结构检查仍属 L2 adapter 职责（类型未知,诚实边界）;③ WP-004 既有的其余已知疑问（adapter 协议、REAL DIV 除零候选行为等）维持原状。Python 测试仅证明当前实现满足本包规则,不构成与目标 PLC 一致的证据。
- scope_manifest:
  - `509e92fe2c99648b4c98c3b8fcf2705ac6164209dc68295f2ba583da6895f8c2  src/runtime/executor.py`
  - `43e2291c2baae47e54c8639213ce3a85effe2cf20fd1b4c187ae7045febb1e6a  tests/test_runtime_executor.py`
- scope_sha256: e4eee57ec95861ef8046145f084bb06594121e392e14e220a33af0b0384fb22c
- implementation_finished_at: 2026-07-14

### Codex 审核结论（Round 1）

- verdict: CHANGES_REQUESTED
- 已验证事实：`src/runtime/executor.py:381-402` 已新增 `_checked_boundary(...)`，按“原始 Python 值结构检查 → 数值钩子 → 钩子结果复检”处理边界；`LoadConst`、IN×Const、FUNCTION 返回和 `CallStd` 四处已接入该通道。`tests/test_runtime_executor.py:899-1010` 新增 7 条反证，已覆盖 WP-004 Round 3 点名的两条 F1 漏洞、Engineering 对照、合法 REAL、BOOL→INT 和 FUNCTION 返回边界。Codex 独立复跑六组测试：执行器 52/52、Store 24/24、IR 56/56、正式 tests 822/822、原型 68/68、全仓 890/890，均通过。
- 项目工程约定：E/F1 共用严格结构类型检查、数值钩子不负责隐式修正错误 Python 类型，是本项目的运行期防御约定；F1 整数中间位宽、REAL binary32 边界量化、REAL DIV 除零和 adapter 最终协议的分层表述未被本包改写。
- 待真机验证假设：F1/F2 下整数中间溢出发生点、REAL 中间精度、REAL DIV 除零行为及 library adapter 最终协议仍需 `TARGET_PROFILE.md` / 黄金轨迹 / L2 工作包裁决；当前 Python 测试只证明现实现行为，不证明与目标 PLC 语义一致。
- 必须返修 1：**`LoadVar` 从 library adapter 管脚读取外部值时仍没有原始值结构检查，F1 依然可在 `StoreVar` 边界洗白错误类型。** `src/runtime/executor.py:307-319` 允许 `_PinLoc` 在无 `pin_type` 时返回声明类型未知的外部值；`src/runtime/executor.py:414-423` 直接将 `loc.read()` 包装成指令声明类型的 `TypedValue`，`src/runtime/executor.py:430-440` 又先执行 `on_store` 再交给位置写入。Codex 最小反证：adapter `read_pin()` 返回 Python `int 1`，IR 执行 `LoadVar("T1.Q", "REAL"); StoreVar("X", "REAL")`，F1 模式下无异常并静默写出 `X=1.0 (float)`。即使 L2 尚未提供管脚声明类型，IR 的 `LoadVar.type` 仍提供了本次运行所需的期望类型，不能因 L2 延后而跳过原始值检查。
- 必须返修 2：**IN×StoreKey 与用户 FB 输入拷入之间仍存在“错误原始值 → `on_store` 洗白 → Store 检查通过”通路。** `src/runtime/executor.py:611-613` 对 StoreKey 实参既未校验目标位置声明类型与 `Binding.type`，也未检查 `loc.read()` 的原始结构；`src/runtime/executor.py:784-790` 随后先执行 `on_store` 再写入 FB 的持久 VAR_INPUT。Codex 最小反证：先建立合法 `Sink.I: REAL <- StoreKey("X")`，再在布局建立后篡改为 `Binding("I","IN",StoreKey("N"),"REAL")`，其中 `N` 是 INT；F1 模式下无异常并静默把 `0.0 (float)` 写入 `PLC_PRG.U.I`。请在 StoreKey 绑定求值时先校验位置声明类型、Binding 类型与原始 Python 值，并让 FB IN 的 `on_store` 同样走共享检查通道。
- 必须返修 3：**FUNCTION VAR_INPUT 播种仍是先调用数值钩子，再进入 `_seed_cell` 检查 raw，与本包声明的固定顺序不一致。** `src/runtime/executor.py:720-722` 在 Python 进入 `_seed_cell(...)` 前就会先求值 `self.mode.on_store(...)`；虽然 `raw=tv.value` 可在之后拒绝错误值，但并非“检查先于钩子”。请改为先经 `_checked_boundary` 得到合法结果再播种，并审视 `StoreVar`、`_write_out_bindings`、FB IN 等余下的直接 `on_store` 调用，不要再以“来源应当受守”代替边界自身的防御。
- 必须新增测试：至少将上述两条静默洗白反证固化：① library adapter 管脚返回 `int`、IR 声明 REAL，F1 必须在它进入数值钩子前拒绝；② IN×StoreKey 装载后从 REAL 位置篡改为 INT 位置、Binding 仍声明 REAL，用户 FB VAR_INPUT 不得静默接受。两条都应断言 `IRExecutionError`、完整上下文和目标未部分写入。
- 非阻塞建议：可考虑让 `_pop(...)` 除了检查 `TypedValue.iec_type` 标签，还校验 `tv.value` 的 Python 结构类型；同时把仍直接调用 `on_store/on_result` 的边界逐项分类为“显式转换”或“必须先验原始值”，会比依赖来源推理更稳固。
- 审核证据：审核开始与结束的 scope SHA-256 一致，本轮审核有效，期间未发现 scope 漂移。`review_started_sha256=e4eee57ec95861ef8046145f084bb06594121e392e14e220a33af0b0384fb22c`，`review_finished_sha256=e4eee57ec95861ef8046145f084bb06594121e392e14e220a33af0b0384fb22c`。逐文件 SHA-256：`src/runtime/executor.py=509e92fe2c99648b4c98c3b8fcf2705ac6164209dc68295f2ba583da6895f8c2`，`tests/test_runtime_executor.py=43e2291c2baae47e54c8639213ce3a85effe2cf20fd1b4c187ae7045febb1e6a`。除独立复跑六组测试外，本轮还做了两条最小手工反证：adapter 管脚 `int 1` 被 F1 静默写为 REAL `1.0`；INT StoreKey `N=0` 被装载后篡改为 REAL FB 实参并静默写为 `0.0`。
- handoff_to: fable5
- reviewed_at: 2026-07-14 22:00 CST

### Fable5 实施交接（Round 2，返修）

- 完成内容：Codex Round 1 三条"必须返修"+"必须新增测试"逐条落地，两条非阻塞建议全部采纳。**1) LoadVar 原始值结构检查**——`_step` 的 LoadVar 分支在声明类型核对后、入栈前对 `loc.read()` 的原始值做 `check_value_type` 结构检查（对全部来源统一，不以"来源应当受守"代替边界防御）；库块管脚（`_PinLoc`，declared=None）来源的外部值由 `LoadVar.type` 提供本次运行期望类型，adapter 返回 int 而 IR 声明 REAL 现于 LoadVar 边界即拒绝，不再走到 `StoreVar.on_store` 被 F1 洗白。**2) IN×StoreKey 与 FB 拷入收口**——`_eval_in_bindings` StoreKey 分支新增两查：目标位置 `declared_type()` 与 `Binding.type` 一致（declared=None 仅库块管脚，诚实跳过）+ 原始值结构检查先于任何钩子；`_call_fb_instance` IN 拷入前新增持久键声明类型与绑定类型核对，拷入值改走共享边界通道（原始值检查 → on_store → 复检）。**3) 播种顺序与余下 on_store 收编**——FUNCTION VAR_INPUT 播种改为先经 `_checked_boundary` 得到合法结果再 `_seed_cell`（此前先求 on_store 再检 raw，顺序与声明不一致）；`StoreVar`、`_write_out_bindings`（OUT 写回）同步收编入共享通道——执行器内全部 `on_store`/`on_const`/`on_result`（CallStd）边界现统一"原始值结构检查 → 钩子 → 结果复检"；`_seed_cell` 的 `raw` 参数因不再有调用方而移除（避免无效装饰），其对最终播种值的复检保留。**非阻塞建议采纳**：`_pop` 消费点在类型标签外复检栈值 Python 结构（某创建点被绕过时消费点也不放行）；模块 docstring 新增 TypedValue 边界分类——哪些边界走共享通道、BINOP/UNOP/CONVERT 的 `on_result`/`convert` 属"运算/显式转换"类（操作数经 `_pop` 复检、结果类型封闭）。
- 修改文件：`src/runtime/executor.py`、`tests/test_runtime_executor.py`（均在 scope 内）。
- 明确未修改：`numeric.py`(9a870a7f…)/`__init__.py`(7bf2db85…)/`store.py`(46a7b948…)/`ir.py`(35ec127d…)/`loader.py`(d8d2903d…)/`process_image.py`(ba64cb8a…)——本轮实跑 sha256sum 核对与既有记录逐一一致；scope 外零改动（交接文件除外）；零 Git 操作。
- 既有测试更新说明（1 项，如实）：`test_cell_write_type_check_wraps_store_error`——OUT 写回接入共享通道后，"声明+绑定一并篡改"现于写回**原始值检查**边界被拦截（防御点按本轮意见前移），原"cause=StoreTypeError"断言不再可经执行器黑盒触达；断言改为锁定新边界（"OUT 形参"+"原始值"+上下文），并在同测试内补 `_CellLoc.write` 内层兜底的单元级直接锁定（错误结构值仍抛 StoreTypeError、目标未写入），原防御层未删除。
- 新增测试（3 项，类 `TestExternalAndStoreKeyBoundaryDefenses`，全部断言异常类型/上下文/目标未部分写入）：① adapter 管脚返回 int、IR 声明 REAL，F1 与 E 双模式均在 LoadVar 边界拒绝、X 保持 0.0（Codex 反证①固化）；② 装载后把 IN×StoreKey 从 REAL 位置 X 篡改为 INT 位置 N（绑定仍 REAL），绑定求值边界拒绝、`PLC_PRG.U.I` 保持 0.0（Codex 反证②固化）；③ 变体：绑定整体篡改为 `Const(1,"INT")×"INT"`（Const 分支自洽、能通过绑定求值），FB 拷入边界的持久键声明类型核对拦截（锁定拷入边界自身防御，双模式）。
- 反证真实失败验证：一次性脚本（不落盘）把 executor 模块内 `check_value_type` 置恒真以还原"无结构检查"旧行为——Codex 反证①复现**静默 X=1.0、无异常**（与 Round 1 审核记录一致）；恢复后同场景抛 `IRExecutionError` 且 X 保持 0.0。反证②的声明类型核对系本轮新增代码，pre-fix 缺失可由 diff 直接核对。
- 实际测试命令与结果（2026-07-14 本轮实际运行，Fable5，均 `PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_executor` → Ran **55** tests, OK（52 + 3 新增）。
  2. `python -m unittest tests.test_runtime_store` → Ran **24** tests, OK。
  3. `python -m unittest tests.test_runtime_ir` → Ran **56** tests, OK。
  4. `python -m unittest discover -s tests -t .` → Ran **825** tests, OK（822 + 3，零破坏）。
  5. `python -m unittest discover -s prototype_05 -t .` → Ran **68** tests, OK。
  6. `python -m unittest discover -t .` → Ran **893** tests, OK（825 + 68）。
- 过程记录（如实）：修复落地后首跑 55 项中 1 失败——即上述 `test_cell_write_type_check_wraps_store_error`（防御点前移的预期效果，非回归）；按上文更新断言后重跑全绿（上列数字即修复后实跑）。
- 已知疑问与延后边界：① FB 拷入的持久键声明类型核对是"让 FB IN 走共享检查通道"的自然配套（同一边界、同一反证通路），若审核认为超出返修范围可指出；② BINOP/UNOP/CONVERT 的 `on_result`/`convert` 未接入共享通道，按 Codex 非阻塞建议分类为"运算/显式转换"类（操作数已在 `_pop` 消费点复检结构、结果由类型封闭的运算产生），若审核认为这三处也须先验原始值属同类小改；③ 库块管脚写入方向（`_PinLoc.write`）的值结构检查仍属 L2 adapter 职责（写入值来自受守边界，管脚声明类型未知，维持诚实边界）；④ 其余既有已知疑问（adapter 协议、REAL DIV 除零候选行为等）维持原状。以上 Python 测试仅证明当前实现行为，不构成与目标 PLC 语义一致的证据。
- scope_manifest:
  - `3a6f097a10ff7c8530c75f6c0fe718a84e146293f089d4c530d954925eea5d67  src/runtime/executor.py`
  - `71992c79f1831d02e40721b4c97a21cf9464b9ef2ab55eb7a95b3bf644488752  tests/test_runtime_executor.py`
- scope_sha256: 4825035b6e1c387754f7d0e56dc8ab1691ea23777927b3547746340391eff3e4
- handoff_to: codex
- implementation_finished_at: 2026-07-14 22:14 CST

### Codex 审核结论（Round 2）

- verdict: CHANGES_REQUESTED
- 已验证事实：`src/runtime/executor.py:430-449` 已把 `LoadVar` 的原始值结构检查前移到入栈前；`648-663` 已为 `IN × StoreKey` 增加声明类型与原始值检查；`751-819`、`840-859`、`900-920` 已把 FUNCTION 播种/返回、FB IN 拷入、`CALL_STD` 返回等边界统一到共享检查通道。`tests/test_runtime_executor.py:1041-1115` 新增的 3 条用例也确实锁住了 adapter `int→REAL` 结构错误、StoreKey 篡改和 FB 拷入声明类型不一致三条返修路径。Codex 独立复跑 6 组测试：`tests.test_runtime_executor` = 55/55、`tests.test_runtime_store` = 24/24、`tests.test_runtime_ir` = 56/56、`discover -s tests -t .` = 825/825、`discover -s prototype_05 -t .` = 68/68、`discover -t .` = 893/893，结果与交接记录一致。
- 项目工程约定：E/F1 共用严格的原始值结构检查、数值钩子不得洗白结构错误，仍是当前项目运行期防御约定；Python 测试通过只证明当前实现行为，不证明与目标 PLC 语义一致。
- 待真机验证假设：F1/F2 下整数中间溢出发生点、REAL 中间精度、REAL DIV 除零候选行为、library adapter 最终协议与 L2 描述符接线方式仍需后续工作包与真机/黄金轨迹裁决；本轮未越权改写这些边界。
- 必须返修 1：**library adapter 的 REAL 输出仍未在 `CALL_FB` 边界按 F1 口径量化，后续 IR 运算会直接吃到未量化的 float64。** `docs/IR_SPEC.md:186-195` 明确要求 F1-expr/F2 对 REAL 施加统一边界量化，其中第 5 条写明“`CALL_FB` 输出回收按 `Pin.iec_type` 量化”。但当前实现里，`src/runtime/executor.py:430-449` 的 `LoadVar("T1.Q", "REAL")` 对 `_PinLoc.read()` 只做结构检查后直接把 `raw` 压栈，`src/runtime/executor.py:923-935` 的 `CALL_FB` 也只执行 `adapter.step(...)`，没有任何输出回收/量化步骤。Codex 最小复现：adapter `Q=3444218515.250481`，程序执行 `CallFb("T1"); LoadVar("T1.Q","REAL"); LoadConst(2579544029.4030247,"REAL"); BinOp("ADD","REAL"); StoreVar("X","REAL")`，F1 下实际写出 `X=6023762432.0`；若按规格先在库块输出边界量化，应为 `quantize_real32(quantize_real32(Q) + quantize_real32(const)) = 6023762944.0`。这不是文档表述问题，而是现实现会改变 F1 数值结果。请在库块输出进入 IR 世界前落实 REAL 边界量化，并新增对应回归测试；仅有 `tests/test_runtime_executor.py:1048-1066` 的“adapter 返回 int 结构错误”还不够覆盖这个规格要求。
- 非阻塞建议：返修时把“library pin REAL 值在 F1 下先量化后参与 `BINOP`”写成一个最小数值差异用例，直接锁住当前复现值 `6023762432.0 != 6023762944.0` 这类双重舍入差异，避免后续 L2 adapter 接入时再次回退。
- 审核证据：审核开始与结束的 scope SHA-256 一致，本轮审核有效，期间未发现 scope 漂移。`review_started_sha256=4825035b6e1c387754f7d0e56dc8ab1691ea23777927b3547746340391eff3e4`，`review_finished_sha256=4825035b6e1c387754f7d0e56dc8ab1691ea23777927b3547746340391eff3e4`。逐文件 SHA-256：`src/runtime/executor.py=3a6f097a10ff7c8530c75f6c0fe718a84e146293f089d4c530d954925eea5d67`，`tests/test_runtime_executor.py=71992c79f1831d02e40721b4c97a21cf9464b9ef2ab55eb7a95b3bf644488752`。除复跑 6 组测试外，本轮还做了 F1 手工复现：当前实现得到 `6023762432.0`，而按 `IR_SPEC §5.3` 第 5 条的边界量化口径应得到 `6023762944.0`。
- handoff_to: fable5
- reviewed_at: 2026-07-14 22:26 CST

### Fable5 实施交接（Round 3，返修）

- 完成内容：Codex Round 2 唯一"必须返修"（library adapter 的 REAL 输出未在 CALL_FB 输出回收边界按 F1 量化，IR_SPEC §5.3 边界 5）落地，非阻塞建议（最小数值差异用例锁死双重舍入）采纳。**修复**：`src/runtime/executor.py` 新增 `Executor._pin_recover_hook(value, iec_type)`——F1 下 REAL 管脚值量化到 binary32,其余透传;两处 `_PinLoc` 读取边界接入共享通道 `_checked_boundary(... hook=_pin_recover_hook ...)`（结构检查仍先于量化,量化不得洗白结构错误）:① `_step` LoadVar 分支——库块管脚回收值先量化再入栈,不再让未量化 float64 直接参与后续 BINOP（Codex 复现路径;经 INOUT 别名读到的管脚位置落入同一分支）;② `_eval_in_bindings` IN×StoreKey 分支指向管脚时同样先量化（当前下游 FUNCTION 播种/FB 拷入的 on_store 也会量化、数值上幂等,补齐是边界完备性:不依赖"下游会再量化",黑盒不可观察差异,见已知疑问②）。L2 未接入时以指令/绑定携带的 IEC 类型充当本次运行的 `Pin.iec_type`（与 Round 1 结构检查同一理由）。模块 docstring 同步补"库块管脚输出回收=§5.3 边界 5 量化边界"一节。
- 修改文件：`src/runtime/executor.py`（import 补 `quantize_real32`、模块 docstring、`_pin_recover_hook` 新增、LoadVar 分支、`_eval_in_bindings` StoreKey 分支）、`tests/test_runtime_executor.py`（55→58 项：新增 `_RealPinAdapter` 辅助 + `TestLibraryPinRealQuantization` 3 项）。
- 明确未修改：`src/runtime/numeric.py`(9a870a7f…)/`__init__.py`(7bf2db85…)/`store.py`(46a7b948…)/`ir.py`(35ec127d…)/`loader.py`(d8d2903d…)/`process_image.py`(ba64cb8a…)——本轮实跑 sha256sum 与既有记录逐一核对一致；scope 外零改动（交接文件除外）；零 Git 操作。**未动 scope 外新增文件**：工作区较上轮新增 `tools/ai_handoff/`、`tests/test_ai_handoff.py`、`docs/AI_HANDOFF_OPERATIONS.md`（AI 交接面板工具,非本包 scope,非 Fable5 本轮所写;其中 1 项测试在本环境稳定失败,见测试结果第 4/6 组说明）。
- 新增测试（3 项,类 `TestLibraryPinRealQuantization`）：① F1 下管脚 REAL 值（3444218515.250481）先量化再参与 BINOP ADD（常量 2579544029.4030247）,断言 X==6023762944.0 且 !=6023762432.0（Codex 复现值,锁死回退形态）,并与 `quantize_real32(quantize_real32(Q)+quantize_real32(C))` 公式核对;② E 模式对照:管脚 float64 原样参与运算（回收钩子 F1 专属,不误伤 E）;③ F1 下 adapter 返回 int、IR 声明 REAL 仍在回收边界拒绝且 X 未被部分写入（量化钩子不得洗白结构错误,与既有反证同口径）。
- 反证真实失败验证：一次性脚本（不落盘）monkeypatch `_pin_recover_hook` 为恒等还原修复前行为——Codex 复现场景实测 **X=6023762432.0、无异常**（与 Round 2 审核记录一致）;恢复修复后同场景 **X=6023762944.0**。数字均为本轮实跑,非预写。
- 实际测试命令与结果（2026-07-15 本轮实际运行,Fable5,均 `PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_executor` → Ran **58** tests, OK（55 + 3 新增）。
  2. `python -m unittest tests.test_runtime_store` → Ran **24** tests, OK。
  3. `python -m unittest tests.test_runtime_ir` → Ran **56** tests, OK。
  4. `python -m unittest discover -s tests -t .` → Ran **853** tests, **FAILED (failures=1)**——唯一失败为 `tests/test_ai_handoff.py::DashboardTests::test_atomic_replace_of_temporary_copy_updates_status_api`（断言 `1 not greater than 1`,文件监视版本号未在 3s 窗口内递增）。该文件是 scope 外新增的交接面板工具测试,只导入 `tools.ai_handoff.*`、与 `src.runtime` 零耦合;单独重跑 3 次均同一失败（稳定,疑为本沙盒环境文件事件/计时问题）;本包既有 825 项与新增 3 项全部通过（853 中 852 通过,唯一失败与本 scope 无关）。
  5. `python -m unittest discover -s prototype_05 -t .` → Ran **68** tests, OK。
  6. `python -m unittest discover -t .` → Ran **921** tests, **FAILED (failures=1)**——同一失败,853+68 中其余 920 项全部通过。
- 已知疑问与延后边界：① scope 外 `tests/test_ai_handoff.py` 的稳定失败需用户/Codex 在宿主环境复核（可能只在沙盒失败）,Fable5 无权修改该文件;② IN×StoreKey 指向管脚的量化补齐当前黑盒不可观察（下游 on_store 幂等）,无法用测试锁定,如 Codex 认为应移除以保持"可测试的最小修改"属同类小改;③ 整数管脚回收**不**回绕/截断——§5.3 对整数仅承诺 STORE_VAR/CONVERT 截断,管脚回收非整数截断点,本轮不发明规格外语义（`_pin_recover_hook` docstring 已注明）;④ LREAL 管脚透传（§5.3 量化仅 REAL）;⑤ 其余既有已知疑问（adapter 协议、REAL DIV 除零候选行为等）维持原状。以上 Python 测试仅证明当前实现行为,不构成与目标 PLC 语义一致的证据。
- scope_manifest:
  - `ba915afa1dd90381dc625df592284fa0fc53db2d92a88f9f5afccc8894110c39  src/runtime/executor.py`
  - `7e494fc0ec2ad9bf87713bc8267b417b79260d876bbfc19765dac60678038218  tests/test_runtime_executor.py`
- scope_sha256: 93a2d866da9c53522ca24925071b74a28a36c95cff00a9e74a10c039d304fa35
- handoff_to: codex
- implementation_finished_at: 2026-07-15 01:44 CST

### Codex 审核结论（Round 3）

- verdict: APPROVED
- 已验证事实：`src/runtime/executor.py:424-469` 已把库块管脚输出回收收口为独立的 `_pin_recover_hook(...)`，并在 `LOAD_VAR` 读取 `_PinLoc` 时先做原始值结构检查、再按 F1 对 REAL 量化后入栈；`src/runtime/executor.py:676-701` 也把 `IN × StoreKey` 指向 `_PinLoc` 的路径并入同一回收边界，不再依赖下游 `on_store` 二次量化。`tests/test_runtime_executor.py:1134-1188` 新增的 `TestLibraryPinRealQuantization` 3 条用例锁住了 Codex Round 2 点名的双重舍入差异、Engineering 对照和“量化不得洗白结构错误”三条路径。按 `docs/IR_SPEC.md:186-195` 的 F1 边界 5 口径，当前实现已把 library pin REAL 值在进入 IR 世界前量化到 binary32，未再复现 `6023762432.0` 的旧错误结果。
- 项目工程约定：L2 描述符未接入时，库块管脚回收继续以指令/绑定携带的 IEC 类型充当本次运行的 `Pin.iec_type`；`_pin_recover_hook(...)` 只对 F1 下的 REAL 做 binary32 量化，对整数/LREAL 不新增规格外回绕或截断语义（`src/runtime/executor.py:424-435`）。这仍是项目当前的工程实现口径，不是 CODESYS / IEC 官方已证实语义。
- 待真机验证假设：F1/F2 下整数中间溢出发生点、REAL 中间精度、REAL DIV 除零候选行为、library adapter 最终协议与 L2 描述符接线方式仍需 `TARGET_PROFILE.md`、黄金轨迹和后续工作包裁决；本轮 Python 测试只证明当前执行器边界防御与 F1 回收量化行为，不证明与目标 PLC 语义一致。
- 必须返修：无。
- 非阻塞建议：若后续要在 CI 或自动化任务里长期运行 `tests/test_ai_handoff.py` 这类会绑定本地 HTTP 端口的测试，建议单独标注“需宿主环境/放开本地端口绑定”，避免把沙盒权限限制误判为代码回归。
- 审核证据：审核开始与结束的 scope SHA-256 一致，本轮审核有效，期间未发现 scope 漂移。`review_started_sha256=93a2d866da9c53522ca24925071b74a28a36c95cff00a9e74a10c039d304fa35`，`review_finished_sha256=93a2d866da9c53522ca24925071b74a28a36c95cff00a9e74a10c039d304fa35`。逐文件 SHA-256：`src/runtime/executor.py=ba915afa1dd90381dc625df592284fa0fc53db2d92a88f9f5afccc8894110c39`，`tests/test_runtime_executor.py=7e494fc0ec2ad9bf87713bc8267b417b79260d876bbfc19765dac60678038218`。本轮 Codex 独立复跑：`python -m unittest tests.test_runtime_executor` = 58/58、`python -m unittest tests.test_runtime_store` = 24/24、`python -m unittest tests.test_runtime_ir` = 56/56、`python -m unittest discover -s tests -t .` = 864/864、`python -m unittest discover -s prototype_05 -t .` = 68/68、`python -m unittest discover -t .` = 932/932（后两组含 dashboard 本地端口测试，需在宿主环境下复跑；沙盒端口绑定失败已排除为环境限制，不构成 scope 回归）。
- handoff_to: user
- reviewed_at: 2026-07-15 02:25 CST
