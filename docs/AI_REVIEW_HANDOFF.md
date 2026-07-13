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
- baseline_pr: https://github.com/yao501/PLC_to_Python/pull/1（draft/open，尚未合并）
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
