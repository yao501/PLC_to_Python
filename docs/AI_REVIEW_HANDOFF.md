# AI 协作交接文件(AI_REVIEW_HANDOFF)

> **用途**:Fable5(实施)与 Codex(审核)之间"实施—审核"往返的唯一交接载体,支撑双侧定时轮询的自动协作。
> **职责边界**:本文件只记录工作包往返;项目状态归 `PROJECT_STATE.md`,正式风险归 `RISKS.md`,长期纪律归 `CODEX_GUIDE.md`,职责不混写。
> **轮询机制**:Fable5 侧每 30 分钟定时检查一次本文件(Cowork 计划任务);Codex 侧检查任务由用户在 ChatGPT 配置。发现 `handoff_to` 指向自己且状态匹配时接力。

## 协议(双方必须遵守)

### 状态机(每个工作包)

```
FABLE_WORKING → READY_FOR_CODEX → CODEX_REVIEWING
  → CHANGES_REQUESTED(回 FABLE_WORKING,round+1)
  → APPROVED → CLOSED(用户确认)
  → BLOCKED(交用户仲裁)
```

### 状态字段映射(唯一口径;自 2026-07-14 起适用于新交接与后续轮次)

> 背景:此前"实施交接后 owner 写谁"存在双方理解分歧(Fable5 曾写 `owner: fable5` + `handoff_to: codex`,Codex 轮询要求 `owner=codex`,导致 2026-07-14 00:41–05:11 空转)。本节为唯一权威映射;**历史工作包文字(含 WP-20260713-002"验收与交接要求"中的 `owner: fable5` 表述)与本节冲突时,以本节为准,历史原文保留不改写**。

```text
FABLE_WORKING:      owner=fable5   handoff_to=fable5
READY_FOR_CODEX:    owner=codex    handoff_to=codex
CODEX_REVIEWING:    owner=codex    handoff_to=codex
CHANGES_REQUESTED:  owner=fable5   handoff_to=fable5
APPROVED:           owner=user     handoff_to=user
BLOCKED:            owner=user     handoff_to=user
CLOSED:             owner=user     handoff_to=user
```

字段语义:

- `owner` = 当前**拥有处理权**的一方;`handoff_to` = 当前状态**要求接力**的一方。
- 除历史轮次记录外,两者在上述所有状态中**必须一致**;状态变更时必须**原子化地同时更新** `status + owner + handoff_to`(一次写入,不允许中间态)。
- 任一字段与映射不匹配时,双方定时任务必须**幂等退出**,不得猜测或越权处理,可在自身运行报告中提示用户存在字段异常。
- Fable5 完成实施/返修交接时,统一写 `status: READY_FOR_CODEX, owner: codex, handoff_to: codex`,并附完整实施交接记录与 `scope_sha256`,随后立即停止修改 scope 文件。
- Fable5 仅在 `FABLE_WORKING(owner=fable5, handoff_to=fable5)` 或 `CHANGES_REQUESTED(owner=fable5, handoff_to=fable5)` 两种组合下接手;处理 CHANGES_REQUESTED 时按协议 round+1 且不得超过 `max_rounds`。
- Codex 仅在 `READY_FOR_CODEX(owner=codex, handoff_to=codex)` 且 `round<=max_rounds` 时接手,审核期间置 `CODEX_REVIEWING`。

### 写入权(始终只有一方可写工作文件)

- `FABLE_WORKING`:Fable5 可改 scope 内文件;Codex 不审核漂移中的内容。
- `READY_FOR_CODEX`:Fable5 停止写入,只等待。
- `CODEX_REVIEWING`:Codex 只读检查,仅写本文件的审核区。
- `CHANGES_REQUESTED`:Codex 停止,Fable5 按意见返修。
- `APPROVED`:工作包通过;Git 提交等外部操作仍须用户授权。

### 硬规则

1. `scope` 必须列出准确文件,不能只写"相关文件";审核期间 scope 内文件发生变化,审核作废退回重新交接。
2. 实施方必须报告**实际测试命令与结果**,不能只写"测试通过";未跑测试必须写明原因。
3. 审核结论必须是 `APPROVED / CHANGES_REQUESTED / BLOCKED` 三值之一,不能只写模糊评价。
4. 每个工作包最多自动往返 **3 轮**(`max_rounds`),超过转 `BLOCKED` 交用户仲裁。
5. 涉及删除、Git 提交/推送、范围扩大、规格裁决时,置 `BLOCKED` 并等用户,**不得自动执行**。
   附(用户裁决 2026-07-13):Git 提交 / GitHub 推送类任务经用户授权后由 **Codex 审核并执行**;Fable5 不执行任何 Git 写操作,只提供修改清单与测试证据。
6. 双方反复同意**不能**把缺少真机证据的假设升级为已验证事实;结论仍须按"已证实事实/工程约定/待真机假设"分层。
7. 历史逐轮追加,不覆盖;新工作包新开一节。
8. 某一方超时/中断后,停在当前可恢复状态,不得猜测对方已完成。
9. 自动轮询接力前必须同时校验 `work_package_id + status + owner + handoff_to + round`;同一工作包同一轮已处理过则幂等退出,任一字段不匹配时不得写入。
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
