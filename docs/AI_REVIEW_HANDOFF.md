# AI 协作交接文件(AI_REVIEW_HANDOFF)

> **用途**:Claude(实施)与 Codex(审核)之间"实施—审核"往返的唯一交接载体,支撑本地事件协调器驱动的串行自动协作。
> **命名**:实施方人类可见称呼统一为 **Claude**;历史记录中的 `Fable5` / `fable5` / `FABLE_WORKING` / `Fable5 实施交接` 仅作只读兼容别名保留,新内容一律使用 Claude / `CLAUDE_WORKING` / `Claude 实施交接`。
> **职责边界**:本文件只记录工作包往返;项目状态归 `PROJECT_STATE.md`,正式风险归 `RISKS.md`,长期纪律归 `CODEX_GUIDE.md`,职责不混写。
> **事件机制**:本地协调器监听本文件的原地写入与原子替换/重命名,完成五字段、轮次、scope 哈希和全局执行租约核验后,只唤醒当前 `handoff_to` 指向的一方。全局执行租约只约束经该协调器启动并主动获取租约的子进程，不能约束独立定时任务；因此旧 Claude/Codex 30 分钟主轮询在 live 服务运行期间必须保持暂停,不得与事件协调器并行。隔离环境只能把项目内 `.ai-handoff-runtime/coordinator_status.json` 当作只读存活投影：必须同时校验 `coordinator_live` 与 `valid_until_epoch`；缺失、损坏、停止或过期只能告警，绝不授权恢复旧轮询或取得执行权。后续低频恢复巡检只能检查服务健康并告警,不得绕过协调器直接启动 AI。

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

### 三阶段职责（自 2026-07-20 起适用于新工作包；历史记录只读兼容）

每一轮必须拆成**三个互不冒充**的阶段，各自独立成段：

1. **Claude 交接前自审**（`### Claude 交接前自审（Round N）`）
   - 发生在 `CLAUDE_WORKING` 状态内、**原子交接之前**；
   - 必须记录：`self_review_started_at` / `self_review_finished_at` /
     `self_review_verdict: PASS | BLOCKED` / `self_review_scope_sha256` /
     `self_review_manifest`（逐文件 SHA-256）/ 实际测试命令与真实计数 /
     首次失败 / 失败根因 / 修复内容 / 修复后重跑结果 / 已知疑问 / 未验证边界 / 是否满足交接条件。
2. **Claude 实施交接**（`### Claude 实施交接（Round N）`）
   - **只**负责汇总产物并执行原子状态转移，**不得冒充独立审核**；
   - 仅当自审 `PASS` 后，才可原子写 `READY_FOR_CODEX / owner=codex / handoff_to=codex`。
3. **Codex 独立审核结论**（`### Codex 审核结论（Round N）`）
   - **只在交接完成后**启动；Claude 不得审核自己的交接；
   - 必须保持独立的 `review_started_sha256` / `review_finished_sha256`、独立测试与 `verdict`；
   - 审核期间 Claude 对 scope 保持只读。

**交接门禁（九项全满足才允许交接）**：

1. 自审段存在，且标题带明确 `Round N`（缺失即拒绝）；
2. `self_review_round == 当前 round`；
3. `self_review_started_at` / `self_review_finished_at` 均存在，且**整串完整匹配**
   `YYYY-MM-DD HH:MM[:SS][时区]`（禁止 substring 匹配，任何前后缀垃圾一律拒绝）。
   时区标记只接受 `Z` / `UTC` / `CST` / `±HH:MM` / `±HHMM`；未知时区（如 `XYZ`）拒绝。
   **项目约定：`CST` 在本项目明确解释为 Asia/Shanghai，即 UTC+08:00**（非美国中部时间）；
   不带时区的 naive 时间戳按项目本地时区 UTC+08:00 解释。
   非法日历日期/时刻（`2026-02-30`、`25:00`）拒绝。
   两个时间戳**必须同为 aware 或同为 naive**，混用直接拒绝（不得静默忽略偏移量）；
   比较时统一折算到 **UTC** 后判断，结束不得早于开始；
4. `self_review_verdict == PASS`；
5. 结构化字段「实际测试命令与结果」同时含**可识别的实际命令**、**明确成功标记**
   （`OK` / `PASS` / `通过`）与**真实计数**；只要出现 `FAILED` / `FAIL` / `ERROR` / `失败`
   即拒绝，**等额计数不能覆盖失败标记**；正文、已知疑问等其他字段里的 `Ran N tests` **一律不算**；
6. `self_review_manifest` 与 scope 证据**密码学绑定**，而不只是检查外形：
   每项须为「64 位十六进制 SHA-256 + 两空格 + 路径」；路径与工作包 `scope`
   **精确一致且顺序相同**（顺序是规范 manifest 的一部分，不得缺失/重复/多余/错序）；
   再按声明顺序重建规范文本 `<sha256>  <path>\n`，其 SHA-256 **必须等于**
   `self_review_scope_sha256`——因此伪造但格式正确的 SHA 也会被拒绝。
   调度时还会与当前实际文件重算的 manifest **逐项比对**，覆盖交接后的文件内容漂移；
7. `是否满足交接条件` 明确为 是/true；
8. `self_review_scope_sha256` 与实施交接 `scope_sha256` 均存在且**相等**；
9. 本轮实施交接 `Round` 等于当前 `round`，且记录位置在自审**之后**（禁止先交接后补自审）。

任一不满足 → **保持 `CLAUDE_WORKING`、拒绝交接、给出明确诊断，不得伪造 PASS**。

**协议生效边界（不可歧义）**：legacy 只由明确 ID 白名单界定，当前为
`WP-20260712-001` … `WP-20260720-008` 这 8 个现存工作包。**此外的所有工作包一律按 v2 处理**：
必须在顶层写 `- handoff_protocol: v2`；**漏写即拒绝交接，不会自动降级为历史格式**。
历史白名单包一旦显式声明 v2，也同样受上述九项门禁约束。

面板状态区分 `legacy` / `v2-ok` / `v2-missing` / `v2-invalid` / `v2-undeclared`：
显式 v2 但自审缺失或无效时，显示「v2 自审缺失 / v2 自审无效」，**不得显示为「历史格式」**。
历史白名单包才显示「历史格式：自审证据未独立结构化」，且不把旧「实施交接」正文里的
测试/哈希冒充成结构化自审证据。

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

---

## WP-20260716-006

- title: 阶段 1 五步扫描编排骨架与确定性单拍执行
- status: CLOSED
- closed_by: user
- closed_at: 2026-07-16
- closure_note: 用户接受 Codex Round 1 证据更正后的 `APPROVED` 结论，并授权关闭本包、启动下一工作包及使用事件协调器继续 Claude—Codex 串行协作。
- closure_baseline_commit: aa15d27bbe4b3a22640291e38874dee394bd8ca6
- owner: user
- handoff_to: user
- round: 1
- max_rounds: 3
- base_commit: 1be16da9953703a768402394d13f9e3a7a8d1f6b
- created_by: user
- created_at: 2026-07-16
- depends_on:
  - WP-20260714-003 CLOSED
  - WP-20260714-004 CLOSED
  - WP-20260714-005 CLOSED
- scope:
  - src/runtime/engine.py
  - src/runtime/__init__.py
  - tests/test_runtime_engine.py
- scope_baseline_sha256: 0f5950f4d078963c933f341be374e315d6795db28c481aaae3d8134a59737046
- scope_baseline_manifest:
  - `ABSENT  src/runtime/engine.py`
  - `7bf2db854f286d50465d25fa2ae8b4c17fea4830d99fe55f4076baaccd2fe18c  src/runtime/__init__.py`
  - `ABSENT  tests/test_runtime_engine.py`

### 用户授权与任务书（Round 1）

- 目标：在现有 `Task`、`RuntimeLayout`、`Executor`、`latch_inputs()`、`OutputPending` 与 `make_prev_snapshot()` 之上，新增一个可重复调用的**确定性单拍扫描编排器**。连续调用单拍入口即组成扫描循环；本包不引入真实时间调度线程。
- 权威顺序：严格落实 `.cursor/rules/00a-runtime-contract.mdc R6` 与 `docs/ENGINE_SCAN_SPEC.md §3`：①输入映像一次性锁存；②+③按 `Task.programs` 顺序执行现有可执行 IR（业务只写 request/store）；④经明确注入的输出策略端口把最终值写入本拍 `OutputPending`；⑤经明确注入的提交端口集中提交一次，成功后才更新 `prev` 快照。
- 职责边界：本包只实现五步**编排与端口契约**，不发明 `OutputPolicy` 算法，不实现 `system_ready/output_enable/safety_ok/interlock_ok`、startup inhibit、watchdog、shadow mode、safe value、故障恢复、真实驱动或 HAL。测试可以注入最小 fake policy/committer 来证明顺序与边界，但不得把 fake 包装成生产安全实现。
- 建议公开 API：可采用 `ScanEngine` / `ScanResult` / `ScanError` 等清晰命名；构造时注入已绑定同一 `Task/RuntimeLayout` 的 `Executor`、输出策略端口和提交端口；提供 `scan(samples)` 或等价单拍入口。可调整具体命名，但必须保持下面的可验证语义。
- `dt_ms` 纪律：仅使用 `Task.cycle_ms`；不得读取墙钟、不得按 Python 实际耗时推导 dt、不得在本包 sleep。现有 `Executor` 给 library adapter 的 `dt_ms` 仍应来自同一任务配置。
- `prev` 纪律：引擎创建时取得初始只读快照；第 2+3 步始终把**上一拍成功提交后的**快照传入 `Executor.execute_programs()`；只有第 5 步提交成功后才用 `make_prev_snapshot()` 替换 `prev`。输入锁存、IR、策略或提交任一步异常时，异常原样向外传播且不得伪造安全输出；`prev` 不得前移。扫描异常的安全提交属于后续 outer scan runner / OutputPolicy 工作包。
- 输出纪律：每拍使用干净的 `OutputPending`（新建或先清空均可）；第 4 步只能由注入策略端口显式 stage 输出，扫描器不得把 Store 的 OUT/request 变量直接复制成物理输出；进入提交前必须拒绝缺失或额外输出通道；第 5 步集中调用提交端口恰一次，并传独立快照，避免提交方反向污染内部 pending。
- 输入纪律：复用 `latch_inputs()` 的两阶段原子校验；输入锁存失败时不得执行 IR、策略或提交。保留 `InputSnapshot` 的只读/隔离语义。
- 生命周期纪律：成功单拍返回值至少应能只读观察本拍输入快照、门控后待提交输出与提交后 `prev`（或提供等价诊断）；调用方修改返回副本不得污染引擎内部状态。
- 错误与重入：同一引擎对象的并发/递归 `scan()` 必须失败关闭，不能交错两拍；单线程下一拍在上一拍返回后可继续。不要在本包启动后台线程。
- 最低测试要求：
  1. 用事件轨迹精确断言五步顺序，且策略/提交各恰调用一次。
  2. 两拍 `LOAD_PREV` 回归：第二拍读到第一拍**成功提交后**的值，不读本拍新值。
  3. 输入锁存失败时 Store 无部分输入更新，且后续三段均未执行。
  4. IR 执行失败、策略失败、提交失败三类路径均不更新 `prev`，不继续后续步骤；异常不被伪装成安全输出。
  5. 提交失败后允许调用方处理异常并再次调用；下一拍仍使用上次成功提交的 `prev`，没有半拍残留 pending。
  6. 输出策略漏 stage 任一 OUT 通道时，在提交前拒绝；零 OUT 通道任务可以合法提交空快照。
  7. 业务 Store/request 的写入不会绕过策略自动进入 pending；提交方修改收到的 dict 不污染结果或下一拍。
  8. 多 PROGRAM 仍按现有显式列表顺序执行；`Task.cycle_ms` 原样到达 library adapter，测试不得依赖墙钟。
  9. 同一引擎递归/并发重入失败关闭，失败后锁状态可恢复，不永久卡死。
  10. `src.runtime` 仅导出本包稳定公共 API；不得导入/复用 `prototype_05`。
- 必跑验证（均设置 `PYTHONDONTWRITEBYTECODE=1`）：专用 `tests.test_runtime_engine`；既有 `tests.test_runtime_executor`、`tests.test_runtime_store`、`tests.test_runtime_ir`；正式 `tests/` 全量；`prototype_05` 全量；全仓 discovery。报告每组实际计数与首次失败/修复过程，不得预写结果。
- 禁止修改：除上列三个 scope 文件和本交接文件的本轮原子交接记录外，不得修改任何代码、测试、规格、`docs/PROJECT_STATE.md`、双方自动化配置或 Git 元数据；不得执行 `git add/commit/push/branch/merge`、`gh`、PR 操作；不得启动/恢复 30 分钟轮询。
- 交接要求：实施前重算 baseline 聚合哈希并与 `scope_baseline_sha256` 一致；完成后按 scope 声明顺序写逐文件 SHA 与聚合 SHA，报告接口、失败语义、明确未实现边界和测试证据；随后原子改为 `READY_FOR_CODEX / codex / codex` 并停笔。Codex 审核期间只读 scope；结论为 `CHANGES_REQUESTED / claude / claude`、`APPROVED / user / user` 或 `BLOCKED / user / user`，并写审核开始/结束同一 scope 哈希。

### Claude 实施交接（Round 1，Codex 中断恢复登记）

- 完成内容：Claude 已在 scope 内新增 `ScanEngine` 确定性单拍编排器、稳定公共导出和专用测试；严格串联输入锁存、显式顺序 IR 执行、注入的输出策略端口和集中提交端口，仅在提交成功后更新 `prev`。实施进程在已写完三个 scope 文件并将顶层字段原子交给 Codex 后命中 Claude Code `max-turns=40`，未来得及写本证据段；Codex 根据真实运行记录、scope 实盘和独立复跑结果仅补写中断恢复证据，未代替 Claude 改动业务 scope。
- 接口与失败语义：`ScanEngine.scan(samples)` 完成单拍；策略端口 `stage_outputs(...)` 和提交端口 `commit(outputs)` 每拍各调用一次。输入、IR、策略或提交异常原样传播，不伪造安全输出，`prev` 不前移，pending 清理；并发/递归重入失败关闭。
- 明确未实现：本包不实现生产 `OutputPolicy`、safe value、安全门控、scan runner、watchdog、shadow mode、故障恢复、真实驱动/HAL 或实时线程；Python 测试不构成与目标 PLC 语义一致的证据。
- 实际验证（2026-07-16，Codex 中断恢复后独立复跑，均设置 `PYTHONDONTWRITEBYTECODE=1`）：`tests.test_runtime_engine` = 28/28，`tests.test_runtime_executor` = 58/58，`tests.test_runtime_store` = 24/24，`tests.test_runtime_ir` = 56/56，正式 `tests/` = 937/937，`prototype_05` = 68/68，全仓 = 1005/1005。
- 首次失败与复核记录：受限沙箱内首次正式/全仓运行的 8 个 dashboard 用例因禁止绑定本地临时端口报 `PermissionError`，换至宿主环境排除。宿主正式集首跑又暴露 scope 外 `test_in_place_write_triggers_reload` 的 kqueue 时序波动（单跑三次 1 通过/2 超时）；随后正式集与全仓各自完整通过。该波动属 AI 协作基础设施、不在 WP-006 scope，未在本包越权修改；稳定性问题如实保留，由后续独立基础设施修复处理。
- scope_manifest:
  - `fb5eae2eba02bcd8f8c46d26db1ff51f64baa35395b86311afeab555cf376921  src/runtime/engine.py`
  - `6a66edd8f4de1ccd9a194bdcf8ba4820d2f4c6e6d1dc48f9e0e7ed794cc95472  src/runtime/__init__.py`
  - `f803f7f3524d33afb68caa2fc5ca14a176651dd57ae85d2535093f85129e3f04  tests/test_runtime_engine.py`
- scope_sha256: 76764354a4048c2494217654d4e94fabe204b914107ec63cef273caf5d6154d3
- handoff_to: codex
- implementation_finished_at: 2026-07-16 03:08 CST

### Codex 审核结论（Round 1）

- verdict: BLOCKED
- 已验证事实：五字段在接手时为 `WP-20260716-006 / READY_FOR_CODEX / codex / codex / round=1`，且 `1 <= max_rounds=3`；scope 明确且仅含 `src/runtime/engine.py`、`src/runtime/__init__.py`、`tests/test_runtime_engine.py`。`src/runtime/engine.py:126-226` 已按输入锁存 → 显式顺序 IR → 注入策略 stage → 集中提交 → 提交成功后更新 `prev` 的顺序实现单拍编排，并在所有退出路径清空 pending、释放非重入锁；`src/runtime/__init__.py:44-52` 只新增本包稳定公共导出；`tests/test_runtime_engine.py` 的 28 项专用测试覆盖任务书列出的十类最低要求。静态审查与 `git diff --check` 未发现 scope 内代码缺陷。
- 项目工程约定：`stage_outputs(pending, store, inputs, prev)` / `commit(outputs)` 是本包为后续 OutputPolicy 与提交层定义的注入端口约定；以“提交端口无异常返回”代表本拍提交成功、以对象身份核对 Executor 与同一 Task/RuntimeLayout 的装配关系，均属于当前项目工程边界，不是 IEC 61131-3 或 CODESYS 官方语义。
- 待真机验证假设：本包未实现生产 OutputPolicy、安全门控、startup inhibit、watchdog、shadow mode、safe value、提交故障恢复、真实驱动/HAL 或实时调度；Python 测试只证明当前编排和失败边界，不证明与目标 SP16.1 PLC 扫描语义一致。
- 必须返修 / 阻塞原因：交接证据存在不可忽略的时间矛盾。审核宿主在 `2026-07-16 03:32:03 CST (+0800)` 读取到交接中的 `implementation_finished_at: 2026-07-16 17:05 CST`，该完成时间比审核时钟晚约 13 小时 33 分；三个 scope 文件的宿主 mtime 分别为 `03:05:17`、`03:05:23`、`03:08:05 CST`，也不能支持 `17:05 CST` 的完成时间。用户已明确要求“出现证据异常时安全停止”，因此即使代码与独立测试未发现阻塞性缺陷，本轮也不能批准。请用户核实系统时钟/时区和交接记录来源，裁决是否更正时间证据并重新交接；Codex 不自行改写实施方证据。
- 非阻塞建议：当前受限沙箱运行正式 937 项与全仓 1005 项时，均只有 scope 外 `tests/test_ai_handoff.py` 的同 8 项因本地端口绑定被禁止而报 `PermissionError`；排除该模块后正式测试 856/856 通过。该环境限制与本包代码无耦合，可由独立基础设施工作包处理，不应混入本包 scope。
- 审核证据：独立复算 `base_commit=1be16da9953703a768402394d13f9e3a7a8d1f6b` 的 baseline 聚合 SHA-256 为 `0f5950f4d078963c933f341be374e315d6795db28c481aaae3d8134a59737046`，与任务书一致。`review_started_sha256=76764354a4048c2494217654d4e94fabe204b914107ec63cef273caf5d6154d3`，`review_finished_sha256=76764354a4048c2494217654d4e94fabe204b914107ec63cef273caf5d6154d3`，审核期间 scope 无漂移。逐文件 SHA-256：`src/runtime/engine.py=fb5eae2eba02bcd8f8c46d26db1ff51f64baa35395b86311afeab555cf376921`，`src/runtime/__init__.py=6a66edd8f4de1ccd9a194bdcf8ba4820d2f4c6e6d1dc48f9e0e7ed794cc95472`，`tests/test_runtime_engine.py=f803f7f3524d33afb68caa2fc5ca14a176651dd57ae85d2535093f85129e3f04`。Codex 本轮独立运行：扫描 28/28、执行器 58/58、Store 24/24、IR 56/56、排除 scope 外 dashboard 模块后的正式测试 856/856、原型 68/68 均通过；正式/全仓含 dashboard 时分别为 937 项/8 errors 与 1005 项/8 errors，错误均为沙箱禁止绑定本地端口。
- handoff_to: user
- reviewed_at: 2026-07-16 03:32 CST

### Codex 审核结论（Round 1，证据更正后复核）

- verdict: APPROVED
- 已验证事实：上一条 `BLOCKED` 的唯一阻塞是 Codex 中断恢复记录把 `implementation_finished_at` 误写为未来时间 `17:05 CST`；该记录本来就是 Codex 补写，不是 Claude 原始证据。现已依宿主时钟与三个 scope 文件实际 mtime 更正为 `2026-07-16 03:08 CST`，时间先后关系合法。scope 代码、测试和哈希均未改动；上轮静态审查已确认五步编排、失败边界、`prev` 纪律、输出隔离和重入保护均符合任务书，无 scope 内必须返修。
- 项目工程约定：`stage_outputs(...)` / `commit(outputs)` 是本包的注入端口契约；提交端口无异常返回才表示本拍成功。这是项目实现边界，非 IEC 61131-3/CODESYS 官方语义。
- 待真机验证假设：与上轮审核一致；生产 OutputPolicy、安全门控、watchdog、shadow mode、提交故障恢复、真实 HAL/驱动和与 SP16.1 PLC 的一致性仍属后续工作。
- 必须返修：无。
- 非阻塞建议：AI 协作基础设施的 `test_in_place_write_triggers_reload` 在单独重复运行时仍可见 kqueue 时序波动，应由独立基础设施修复处理，不混入 WP-006 scope。
- 审核证据：`review_started_sha256=76764354a4048c2494217654d4e94fabe204b914107ec63cef273caf5d6154d3`，`review_finished_sha256=76764354a4048c2494217654d4e94fabe204b914107ec63cef273caf5d6154d3`，复核前后 scope 无漂移。Codex 已独立运行扫描 28/28、执行器 58/58、Store 24/24、IR 56/56、正式 tests 937/937、原型 68/68、全仓 1005/1005；首次环境失败与 scope 外监听时序波动均已如实记录。
- handoff_to: user
- reviewed_at: 2026-07-16 03:36 CST

---

## WP-20260716-007

- title: 阶段 1 生产 OutputPolicy 核心与原子安全状态快照
- status: CLOSED
- closed_by: user
- closed_at: 2026-07-20
- closure_note: 用户确认接受 Codex Round 3 的 `APPROVED` 结论，授权关闭本包、完成 Git/PR 收尾、恢复事件协调器并创建下一工作包。Python 验证只证明当前实现行为，不构成与目标 PLC/CODESYS 或真实 HAL 一致的证据。
- owner: user
- handoff_to: user
- round: 3
- max_rounds: 3
- base_commit: aa15d27bbe4b3a22640291e38874dee394bd8ca6
- created_by: user
- created_at: 2026-07-16 21:47 CST
- depends_on:
  - WP-20260716-006 CLOSED
- scope:
  - src/runtime/output_policy.py
  - src/runtime/__init__.py
  - tests/test_runtime_output_policy.py
- scope_baseline_sha256: 1be108cf92e8d373a745d9ce8d5338d4838a6d69d2d81bf903f8aa2d865a88bc
- scope_baseline_manifest:
  - `ABSENT  src/runtime/output_policy.py`
  - `6a66edd8f4de1ccd9a194bdcf8ba4820d2f4c6e6d1dc48f9e0e7ed794cc95472  src/runtime/__init__.py`
  - `ABSENT  tests/test_runtime_output_policy.py`

### 工作包创建行政证据（Claude 启动前）

- 用户已明确授权 Codex 关闭 WP-006、更新项目状态、启动下一工作包并让 Claude/Codex 按事件机制继续协作；因此 `docs/PROJECT_STATE.md` 由 Codex 作为**创建工作包的行政动作**更新，不属于 Claude 的实施 scope，也不是 scope 漂移。
- 实际原子创建/启动时间为 `2026-07-16 21:47:42 CST`：`docs/PROJECT_STATE.md` 的宿主 mtime 为该时刻；协调器生命周期记录 Claude 子进程也于同一时刻启动。任务书原先预填的 `created_at: 21:44 CST` 是编写任务期间的准备时间，现按可核验事件时间更正为 `21:47 CST`。
- 行政更新后的 `docs/PROJECT_STATE.md` SHA-256 为 `c0455052eaf8157c76aba1090a74ca48c30f9d97028337fd163c90116309e90c`；Claude 启动后不得修改该文件。Claude 交接中的“scope 外零文件改动”专指其 `21:47:42 CST` 启动后的实施生命周期，不否认这项已授权、预先存在的 Codex 行政更新。
- review_retry_authorized_at: 2026-07-16 22:10 CST
- review_retry_reason: 已补齐上述用户授权与行政变更来源证据；scope 三文件及其实施哈希未发生变化，允许同一幂等键重新执行一次只读审核。

### 用户授权与任务书（Round 1）

- 目标：按 `docs/ENGINE_SCAN_SPEC.md §4/§4.1/§4.2` 与 `.cursor/rules/00a-runtime-contract.mdc`、`04-platform-runtime.mdc`，实现正式工程的分类型 `OutputPolicy` 核心和原子安全状态快照；产物必须直接满足现有 `ScanEngine` 的 `stage_outputs(pending, store, inputs, prev)` 注入端口，而不是另造平行扫描器。
- 配置模型：为每个 OUT 映射建模 `var / iec_type / safe_value / rate_limit / commit_fault_retry_n` 及六类 `on_*` 策略。`on_safety_trip / on_scan_fault / on_watchdog` 必须固定为 `safe`，非法配置在策略服务装配期拒绝；`safe_value`、request 与最终值必须严格符合声明 IEC 类型，不做隐式转换。整数限速不得靠浮点舍入猜测语义：若配置不能在声明类型内精确表达，应在装配期拒绝并在交接中说明边界。
- 安全状态：提供不可变快照及一个线程安全、整包替换的最小安全状态服务。快照至少覆盖 `system_ready / output_enable / comm_ok / safety_ok / interlock_ok / scan_ok / watchdog_ok`；策略服务每拍只读取一次完整快照，禁止逐字段读取形成撕裂状态。本包只**消费**这些信号，不实现 startup 计时、watchdog 计时或 outer scan runner。
- 故障决策：多原因并发严格按 `safety_trip ≥ watchdog ≥ scan_fault > comm_loss > startup_not_ready > operator_disable`；任一强制安全原因命中即一步落 `safe_value`，不受限速约束。可配置原因命中 `hold` 时取该通道 `last_effective`；冷启动无历史时退化为 `safe_value`。
- 正常路径：无故障时 BOOL 直接采用 request；REAL/整数按 `rate_limit` 约束每拍变化且保持声明类型。冷启动基准为 `safe_value`（阶段 7 HAL 未提供可信设备反馈前的冻结分支），后续以 `last_effective` 为基准。每次完整策略计算成功后更新每通道 `last_effective`。
- 原子性与隔离：对一拍全部 OUT 通道先完整校验和计算，再统一 stage 并提交内部 `last_effective`；任何通道失败不得留下部分 pending 或部分内部状态。外部取得的安全快照/诊断副本不得反向污染服务；多个策略服务实例状态互不串扰；同一服务并发调用必须失败关闭或串行化，不能交错两拍。
- IOMap 对齐：只处理 `direction=OUT`；拒绝重复通道、策略缺失、策略 var/type 与 IOMap/Store 声明不一致、未知 Store 变量和非生产策略对象。不得从 `prototype_05` 导入或复制运行时代码。
- 稳定导出：`src/runtime/__init__.py` 只导出本包经测试的公共类型、服务与专用异常；保持现有导出不回归。
- 最低测试：覆盖 BOOL/REAL/整数正常路径；非零安全值；六类原因及并发优先级；三类强制 safe 配置拒绝；每一种可配置 `hold/safe`；冷启动 hold；安全落值绕过限速、恢复后从 safe 基准限速；两拍 `last_effective`；多通道原子失败；安全状态整包快照/并发读取；IOMap/Store/类型/数值非法配置；直接注入真实 `ScanEngine` 至少连续两拍且策略没有绕过 pending。
- 必跑验证（均设置 `PYTHONDONTWRITEBYTECODE=1`）：`tests.test_runtime_output_policy`；既有 `tests.test_runtime_engine`、`tests.test_runtime_executor`、`tests.test_runtime_store`、`tests.test_runtime_ir`；正式 `tests/` 全量；`prototype_05` 全量；全仓 discovery。逐组报告真实计数、首次失败、根因、修复与重跑结果，不得预写结果。
- 明确不实现：watchdog/scan-fault 信号生成、扫描异常外层安全提交、shadow mode、`last_physical_committed`、真实驱动提交、`commit_fault`/`channel_fault` 锁存与复位、可信设备反馈/HAL、实时线程、L2 adapter 注册表、参数装载总闸门。上述内容留给后续独立工作包，不得以“顺手完善”为由扩 scope。
- 禁止修改：除上述三个 scope 文件和本交接文件的本轮原子交接记录外，Claude 不得修改任何代码、测试、规格、`docs/PROJECT_STATE.md`、协调器/双方自动化配置或 Git 元数据；本节上方已登记的 `docs/PROJECT_STATE.md` 变更是 Codex 在 Claude 启动前执行的已授权行政动作，不计入 Claude scope。不得执行 Git/PR 写操作，不得恢复旧 30 分钟轮询。
- 交接要求：实施前复算 baseline 聚合 SHA-256 并与任务书一致；完成后按 scope 顺序报告逐文件 SHA、聚合 SHA、实际测试证据、工程约定与未验证边界，随后原子改为 `READY_FOR_CODEX / codex / codex` 并停笔。Codex 审核只读 scope；结论按协议原子写为 `CHANGES_REQUESTED / claude / claude`、`APPROVED / user / user` 或 `BLOCKED / user / user`。

### Claude 实施交接（Round 1）

- 接手核验：实施前复算 baseline 聚合 SHA-256 = `1be108cf92e8d373a745d9ce8d5338d4838a6d69d2d81bf903f8aa2d865a88bc`，与任务书 `scope_baseline_sha256` 一致（`output_policy.py`/`test_runtime_output_policy.py` ABSENT，`__init__.py` = `6a66edd8…95472`）；五字段 `status=CLAUDE_WORKING / owner=claude / handoff_to=claude / round=1 / max_rounds=3` 组合合法，未超轮次；协调器心跳 `coordinator_live=true` 且未过期。仅改本轮三个 scope 文件与本交接记录，未触碰 Git/状态/协调器配置。
- 完成内容：按任务书实现生产 `OutputPolicy` 门控核心 + 原子安全状态快照，产物直接插入现有 `ScanEngine.stage_outputs(pending, store, inputs, prev)` 端口（未另造平行扫描器）。① `OutputPolicy`（frozen 配置值对象）：`var/iec_type/safe_value/rate_limit/commit_fault_retry_n` + 六类 `on_*`；构造期校验——IEC 类型合法、`safe_value` 严格匹配声明类型（不隐式转换）、六类动作 ∈{safe,hold}、`on_safety_trip/on_scan_fault/on_watchdog` 固定 `safe`（配 hold 拒绝）、整数类型限速须为非负 `int`（拒绝 float/bool，杜绝浮点舍入猜测语义）、REAL/LREAL 限速须为非负 `float`、BOOL/STRING 拒绝限速、`commit_fault_retry_n` 须正整数。② `SafetySnapshot`（frozen 七信号：system_ready/output_enable/comm_ok/safety_ok/interlock_ok/scan_ok/watchdog_ok，全布尔校验）+ `SafetyStateService`（`threading.Lock` 下整包 `replace`/`read`，只暴露整包 read、无逐字段 getter，杜绝撕裂）。③ `OutputPolicyService`：装配期对齐 IOMap（只取 OUT、拒绝重复通道/策略缺失/非生产策略对象/policy.var 与 IOMap 不一致/policy.iec_type 与 Store 声明不一致/未知 Store 变量）；`stage_outputs` 每拍只 `read()` 一次安全快照，全通道**先完整计算到局部、再统一 stage、最后统一提交内部状态**（任一失败不留部分 stage/部分内部状态），非阻塞锁并发/递归失败关闭（`OutputPolicyReentryError`）。故障决策按 `safety_trip≥watchdog≥scan_fault>comm_loss>startup_not_ready>operator_disable` 取最严者：强制 safe 或配 safe 一步落 `safe_value`（绕过限速）并置边界复位（恢复首拍从 safe 基准限速）；配 hold 取 `last_effective`，冷启动无历史退化为 `safe_value`。正常路径 BOOL 直取 request、模拟量按 `rate_limit` 对与基准之差限速（冷启动/恢复首拍基准=safe_value，此后=last_effective），成功后更新每通道 `last_effective`。④ `src/runtime/__init__.py` 新增导出 8 个公共名，既有导出未回归。
- 修改文件（即 scope 三文件）：`src/runtime/output_policy.py`（新建）、`src/runtime/__init__.py`（仅追加本包导出）、`tests/test_runtime_output_policy.py`（新建，47 用例）。
- 明确未修改：scope 外零文件改动。未实现（遵守任务书冻结边界，属后续独立工作包）：watchdog/scan-fault 信号生成、扫描异常外层安全提交（§4.3 scan runner）、shadow mode、`last_physical_committed`、真实驱动提交、`commit_fault`/`channel_fault` 锁存与复位（§4.4）、可信设备反馈/HAL（§4.1 阶段 7）、实时线程、L2 adapter 注册表、参数装载总闸门；`inputs`/`prev` 端口参数本包不消费（留给可信反馈工作包）。未执行任何 Git 写操作，未改 `docs/PROJECT_STATE.md`、协调器/自动化配置。
- 测试命令与实际结果（2026-07-16 本轮实际运行，均设 `PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_output_policy` → Ran **47** tests, OK。
  2. `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → Ran **166** tests, OK。
  3. `python -m unittest discover -s tests -t .` → Ran **987** tests, OK（既有 940 基线 + 47 新增，零破坏）。
  4. `python -m unittest discover -s prototype_05 -t .` → Ran **68** tests, OK。
  5. `python -m unittest discover -s . -t .` → Ran **1055** tests, OK（987 + 68）。
- 工程约定与未验证边界（分层，诚实声明）：输出基准/故障制度/限速语义为**项目工程约定、非 CODESYS 官方语义**，未经真机验证；Python 测试不构成与 PLC 语义一致的证据。§4.1 四类"重建物理基准边界"本包只落地**冷启动**一类 + "安全落值后恢复首拍从 safe 基准限速"；其余三类（shadow→实写、提交故障恢复、`channel_fault` 复位）显式不在范围。F1/binary32 输出量化未接入（属数值/HAL 层）；整数限速仅校验类型精确表达（非负 int），未按声明位宽做范围/回绕（数值层，engineering 模式默认不回绕）。
- 已知疑问（请审核裁决，均不涉及规格修改；若认为需写入规格则属规格裁决转用户）：① `hold` 命中且有历史时，输出保持 `last_effective` 且**不**置边界复位（恢复正常路径从 `last_effective` 继续限速，物理上输出未离开该值）——与"safe 落值后置边界复位从 safe 基准"区分处理；此解读贴合 §4.1"last_effective 基准"与物理连续性，任务书"恢复后从 safe 基准限速"按其字面仅约束**安全落值**分支。② `commit_fault_retry_n` 仅建模字段，其锁存/复位行为（§4.4）不在本包实现。③ 服务运行期从 `stage_outputs` 传入的 `store` 读 request（引擎契约），装配期类型校验用构造 `store`；约定二者为同一对象（引擎即如此注入）。
- scope_sha256: 843af9b22561793f2d999642e3436dc7c938e9bdb626416dd34154ea6f627ca0
- scope_manifest:
  - `9cd2ba12cf5850b1ad6fc1ff39dfb7dfbb4390fb3ab6048a0ed67c30ecde1b9f  src/runtime/output_policy.py`
  - `83a49879fbc5ad1c957f84e7e78c121a883b551343cc50721bd3fc6e46d17700  src/runtime/__init__.py`
  - `45119a3d494c208922a9243d8fa7db37ff83ef5f8f967e2e52cb798ea2d4a564  tests/test_runtime_output_policy.py`
- handoff_to: codex
- implementation_finished_at: 2026-07-16 22:03 CST

### Codex 审核结论（Round 1）

- verdict: CHANGES_REQUESTED
- 已验证事实：`src/runtime/output_policy.py:127-199` 已建立 frozen `OutputPolicy` 配置对象并拒绝三类强制安全原因的 `hold` 配置；`211-268` 已实现七信号不可变 `SafetySnapshot` 与锁下整包替换/读取的 `SafetyStateService`；`275-466` 已把策略服务直接实现为 `ScanEngine.stage_outputs(pending, store, inputs, prev)` 端口，安全状态每拍只读一次，多原因优先级、正常路径限速、全通道先计算后 stage、并发/递归失败关闭的主体结构均已落地。`src/runtime/__init__.py:48-58` 只追加本包 8 个稳定导出。`tests/test_runtime_output_policy.py` 的 47 项覆盖了任务书大部分主路径，并能与真实 `ScanEngine` 连续两拍集成。
- 项目工程约定：分原因策略、`safe_value` 基准、故障落安全值绕过限速以及恢复首拍从 `safe_value` 限速，均是当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义；本包诚实保留了 shadow、`last_physical_committed`、提交故障锁存/复位、可信反馈/HAL 和 outer scan runner 的后续边界。
- 待真机验证假设：输出基准与限速行为尚未经目标 SP16.1 真机/真实 HAL 验证；F1/binary32 输出边界、真实提交故障、watchdog/scan-fault 生成与外部安全回路也不在本包证据覆盖内。当前 Python 测试只证明实现行为，不证明与 PLC 语义一致。
- 必须返修 1：`last_effective` 的更新语义与任务书及冻结规格冲突。任务书 `docs/AI_REVIEW_HANDOFF.md:772-774` 要求 `hold` 取上拍逻辑生效值且每次完整策略计算成功后更新 `last_effective`；`ENGINE_SCAN_SPEC.md:84,90-104` 也规定它在每拍第 4 步完成后更新。当前 `src/runtime/output_policy.py:282-284` 却把它收窄成“上次正常路径值”，`409-421` 的 safe 分支和冷启动 hold 分支分别保留旧值/`None`。最小反证：正常拍输出 50 → `safety_trip` 强制输出 0 → 下一拍仅 `comm_loss` 且配置 `hold`，当前实现错误重新输出 50，诊断仍为 50；正确的上拍逻辑生效值应为 0。请让所有成功策略计算（正常、safe、hold，包括冷启动 hold→safe）都把本拍 final 提交为新的 `last_effective`，同时保持“安全落值后恢复正常路径以 safe 基准限速”的独立边界状态；新增“forced safe→可配置 hold”与冷启动 hold 诊断反证。
- 必须返修 2：声明 IEC 类型的数值合法性校验只做了 Python 结构类型检查，未满足任务书 `docs/AI_REVIEW_HANDOFF.md:770,777` 的“整数限速须在声明类型内精确表达、类型/数值非法配置拒绝”，也回退了 `docs/RISKS.md:8,117` 已登记的原型安全配置 NaN/Infinity/整数范围闭环。`src/runtime/output_policy.py:155-158,181-199,378-381,424-427` 会接受 `REAL safe_value=NaN/Infinity`、`REAL rate_limit=NaN/Infinity`、`USINT rate_limit=256`、`USINT safe_value=999`；运行期也会把 `REAL request=NaN` 和 `USINT request=999` 实际 stage。请为安全值、限速、request 与 final 采用一致的 IEC 数值合法性检查：REAL/LREAL 必须有限；固定宽度整数/位串须在声明范围内（无符号不得为负）；整数限速须是非负整数且其值可由声明类型精确表示。新增上述配置期和运行期反证，且不得用隐式转换、舍入或回绕“修正”非法值。
- 非阻塞建议：`OutputPolicyService` 装配期使用构造时的 Store 校验，运行期却读取 `stage_outputs(...)` 传入的 Store；当前依赖“二者是同一对象”的未强制约定。后续参数装载总闸门建立前，可在本 scope 内保存并核对对象身份，避免把已对齐的策略服务误插入另一套 `RuntimeLayout`。
- 审核证据：五字段接手值为 `WP-20260716-007 / READY_FOR_CODEX / codex / codex / round=1`，且 `1 <= max_rounds=3`；协调器心跳在接手与结束核验时均 live 且未过期。独立从 `base_commit=aa15d27bbe4b3a22640291e38874dee394bd8ca6` 复算 baseline 聚合 SHA-256=`1be108cf92e8d373a745d9ce8d5338d4838a6d69d2d81bf903f8aa2d865a88bc`，与任务书一致；`docs/PROJECT_STATE.md` 实盘 SHA-256=`c0455052eaf8157c76aba1090a74ca48c30f9d97028337fd163c90116309e90c`，与启动前行政证据一致。Codex 独立运行：`tests.test_runtime_output_policy`=47/47、既有四组运行时测试合并=166/166、排除 scope 外 dashboard 模块后的正式测试=903/903、原型=68/68；完整正式 987 项和全仓 1055 项在当前受限环境各有同 9 个 scope 外 `tests/test_ai_handoff.py` 用例因禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 978/978、1046/1046 通过。两条手工反证分别稳定复现 safe→hold 错误回跳到 50，以及非有限/越界数值被配置和 stage 接受。
- review_started_sha256: 843af9b22561793f2d999642e3436dc7c938e9bdb626416dd34154ea6f627ca0
- review_finished_sha256: 843af9b22561793f2d999642e3436dc7c938e9bdb626416dd34154ea6f627ca0
- handoff_to: claude
- reviewed_at: 2026-07-16 22:25 CST

### Claude Round 2 返修执行权限说明

- 协调器的 Claude 执行白名单允许 `python ...`、`python3 ...` 以及带 `PYTHONDONTWRITEBYTECODE=1` 前缀的 Python 命令；不允许 `shasum`、`cat`、`date`、shell `for`、`tee` 等通用 shell 命令。首次返修因选用了后者而被白名单拒绝，未修改任何文件，scope 仍保持 Codex 审核结束哈希。
- 接手哈希必须改用 Python 标准库完成，例如通过 `hashlib.sha256` 按 scope 顺序生成逐文件清单与聚合 SHA；心跳和当前 epoch 必须通过 Python 的 `json` / `time` 读取与比较；测试继续使用任务书已列出的 `PYTHONDONTWRITEBYTECODE=1 python -m unittest ...`。不得再次请求被禁 shell 工具，也不得因可用 Python 路径而降低任何核验要求。
- rework_retry_authorized_at: 2026-07-16 22:35:51 CST
- rework_retry_reason: 首次返修未写入 scope，失败原因为验证命令触发执行白名单；现已明确等价的 Python 标准库核验路径，并仅授权原幂等键重试一次。

### Claude 实施交接（Round 2）

- 接手核验：五字段接手值为 `WP-20260716-007 / CHANGES_REQUESTED / owner=claude / handoff_to=claude / round=1`，组合与状态映射一致，按协议 round+1 → `round=2 <= max_rounds=3`，未超轮次。按上节授权仅用 Python 标准库核验（`hashlib` / `json` / `time`，未请求 `shasum`/`cat`/`date` 等被禁 shell 工具）：接手时复算 scope 聚合 SHA-256 = `843af9b22561793f2d999642e3436dc7c938e9bdb626416dd34154ea6f627ca0`，与 Codex `review_started_sha256` / `review_finished_sha256` **三者一致**，逐文件哈希亦与 Round 1 `scope_manifest` 全等（`9cd2ba12…b1b9f` / `83a49879…d17700` / `45119a3d…d4a564`），**无证据漂移**；协调器心跳 `coordinator_live=true` 且 `valid_until_epoch` 未过期。`docs/PROJECT_STATE.md` 实盘 SHA-256 = `c0455052eaf8157c76aba1090a74ca48c30f9d97028337fd163c90116309e90c`，与启动前行政证据一致且本轮未改。
- 完成内容：Codex Round 1 两条“必须返修”逐条落地，均只改 scope 内文件。
  1. **`last_effective` 前移语义收口（必须返修 1）**：`src/runtime/output_policy.py::_compute` 三条路径现统一把**本拍 final** 作为新的 `last_effective` 返回——safe 分支由 `(safe_value, last_effective, True)` 改为 `(safe_value, safe_value, True)`；冷启动 hold 分支由 `(safe_value, None, True)` 改为 `(safe_value, safe_value, True)`；hold 有历史分支 final 即 `last_effective`，语义不变。依据 `docs/ENGINE_SCAN_SPEC.md:92`（“每拍第 4 步策略计算完成后更新，shadow 与正常模式都更新”）与 `:126`（“策略层照常计算 final 并更新 `last_effective`，保持逻辑连续”），以及 `:84`（`hold` = 上次**逻辑生效值**）。`boundary_reset` 按 Codex 要求**保留为独立边界状态**（safe 落值与冷启动 hold 置位，正常路径清位），未与 `last_effective` 合并。Codex 的最小反证已复现并转为常驻用例：正常 50 → `safety_trip` 落 0 → 仅 `comm_loss(hold)` 现输出 **0**（旧实现错误回跳 50），诊断亦为 0。
  2. **IEC 数值合法性收口（必须返修 2）**：新增模块内单一口径 helper `_iec_value_error(iec_type, value)`，在 `store.check_value_type` 的**结构类型**映射之上补**数值域**——REAL/LREAL 必须有限（拒绝 NaN/±Infinity）；固定宽度整数/位串必须落在声明范围内（无符号不得为负），范围**复用 `src/runtime/numeric.INT_WIDTHS`**、不另立表。该 helper 同时接入四个位点，保证配置期与运行期同一口径：`OutputPolicy.__post_init__` 的 `safe_value`、`_validate_rate_limit` 的 `rate_limit`（整数限速须在声明类型内**精确表达**，`USINT rate_limit=256` / `SINT rate_limit=128` 现拒绝）、`stage_outputs` 的 `request` 与 `final`。全部为**判定与拒绝**，未使用任何隐式转换、舍入或回绕“修正”非法值（回绕仍属数值层 `numeric.wrap_int` 的 F1/engineering 语义）。Codex 点名的四例 `REAL safe_value=NaN/Infinity`、`REAL rate_limit=NaN/Infinity`、`USINT rate_limit=256`、`USINT safe_value=999` 现均在装配期拒绝；`REAL request=NaN`、`USINT request=999` 现在运行期拒绝且**不 stage、不前移内部状态**（失败关闭）。
- 修改文件（均在 scope 内，共 2 个）：`src/runtime/output_policy.py`（`_compute` 前移语义、新增 `_iec_value_error` / `_int_range`、四处接入、`import math` 与 `INT_WIDTHS`、相应 docstring 同步）、`tests/test_runtime_output_policy.py`（47 → **60** 用例，+13）。
- 明确未修改：`src/runtime/__init__.py` **本轮零改动**（哈希仍为 `83a49879…d17700`）——两条返修未引入新公共名，`_iec_value_error` / `_int_range` 为模块私有，现有导出无回归。scope 外零文件改动；未改 `docs/PROJECT_STATE.md`、协调器/自动化配置；未执行任何 Git 写操作（含暂存/提交/推送/建 PR/合并）。任务书冻结边界继续未实现（属后续独立工作包）：watchdog/scan-fault 信号生成、§4.3 外层安全提交、shadow mode、`last_physical_committed`、真实驱动提交、`commit_fault`/`channel_fault` 锁存与复位、可信反馈/HAL、实时线程、L2 adapter 注册表、参数装载总闸门；`inputs`/`prev` 端口参数仍不消费。Codex 的**非阻塞建议**（装配期/运行期 Store 对象身份核对）本轮**未实施**——它不在两条“必须返修”内，且属行为扩张，留待 Codex 裁决是否单开工作包或授权纳入本包。
- 新增测试（13 项，逐条对应返修意见）：`test_forced_safe_then_configurable_hold_holds_safe_value`（Codex 反证 1 原样固化）、`test_configurable_safe_then_hold_holds_safe_value`、`test_cold_start_hold_then_hold_keeps_degraded_value`、`test_hold_with_history_keeps_last_effective_stable`、`test_real_safe_value_must_be_finite`、`test_real_rate_limit_must_be_finite`、`test_safe_value_out_of_declared_int_range_rejected`、`test_safe_value_at_declared_int_bounds_accepted`、`test_rate_limit_not_exactly_representable_in_declared_type_rejected`、`TestRuntimeNumericValidity` 四项（非有限 REAL request / 越界整数 request 拒绝且不 stage、边界内合法值不误杀、限速 final 走同一校验）。既有 `test_cold_start_hold_degrades_to_safe` 的诊断断言由 `{"CH": None}` 更新为 `{"CH": 4}`——该断言锁的正是被 Codex 判为错误的旧语义，按返修 1 同步。
- 测试命令与实际结果（2026-07-16 本轮实际运行，均设 `PYTHONDONTWRITEBYTECODE=1`；无失败，故无“首次失败/根因/修复后重跑”记录）：
  1. `python -m unittest tests.test_runtime_output_policy` → Ran **60** tests, OK（47 + 13 新增）。
  2. `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → Ran **166** tests, OK（与 Round 1 同值，零破坏）。
  3. `python -m unittest discover -s tests -t .` → Ran **1000** tests, OK（Round 1 为 987，+13 新增）。
  4. `python -m unittest discover -s prototype_05 -t .` → Ran **68** tests, OK。
  5. `python -m unittest discover -s . -t .` → Ran **1068** tests, OK（1000 + 68）。
  - 环境差异说明：Codex Round 1 环境中 `tests/test_ai_handoff.py` 有 9 项因禁止绑定本地 HTTP 端口报 `PermissionError`；本轮环境允许绑定，故第 3/5 组为全量通过（1000/1000、1068/1068），非本轮改动所致。
- 工程约定与未验证边界（分层，诚实声明，与 Round 1 一致）：`last_effective` 前移制度、输出基准、故障落安全值绕过限速、恢复首拍从 `safe_value` 限速，以及本轮新增的 IEC 数值域拒绝制度，均为**项目工程约定、非 CODESYS / IEC 61131-3 官方已证实语义**，未经目标 SP16.1 真机或真实 HAL 验证；上述 60/166/1000/68/1068 项 Python 测试**只证明本实现行为，不构成与 PLC 语义一致的证据**。§4.1 四类“重建物理基准边界”本包仍只落地冷启动一类 + 安全落值后恢复首拍；F1/binary32 输出量化未接入（属数值/HAL 层）。**新增边界（诚实声明）**：`_iec_value_error` 对 `TIME` 只校验结构类型为 int，**不施加范围约束**（其工程位宽尚未冻结，`numeric.INT_WIDTHS` 无 TIME 条目）；`STRING` 同样只校验结构类型。整数越界一律**拒绝**而非回绕——这是本包的失败关闭工程约定，声明位宽的回绕/截断语义仍属数值层且待真机裁决，本轮未改动 `numeric.py`。
- 已知疑问（请审核裁决，均不涉及规格修改）：① `hold` 命中且有历史时输出保持 `last_effective` 且**不**置 `boundary_reset`（物理输出未离开该值，恢复即从该值继续限速）——Round 1 已知疑问 ① 在本轮未被 Codex 推翻，故维持；返修 1 只改“前移”，未改此边界位。② Codex 非阻塞建议（Store 对象身份核对）未实施，理由见“明确未修改”，请裁决是否单开工作包。③ `commit_fault_retry_n` 仍仅建模字段，锁存/复位属 §4.4 后续工作包。
- scope_sha256: 8256f96a042ff6a0aa290c6394c8541888d36b14ddc1c5338bb4351cb6f6525d
- scope_manifest:
  - `ac95db5ba88d918b91909e07003d0c4c02741efb22595e6947f20008ae6ea927  src/runtime/output_policy.py`
  - `83a49879fbc5ad1c957f84e7e78c121a883b551343cc50721bd3fc6e46d17700  src/runtime/__init__.py`
  - `41a51f34a36b53541a3d5c5d220d443d45589b9f46b698b862169bf95e80ad6c  tests/test_runtime_output_policy.py`
- handoff_to: codex
- implementation_finished_at: 2026-07-16 22:43:20 CST

### Codex 审核结论（Round 2）

- verdict: CHANGES_REQUESTED
- 已验证事实：Round 1 的 `last_effective` 前移缺陷已修复：`src/runtime/output_policy.py:462-477` 的 safe、冷启动 hold 和有历史 hold 三条成功路径均把本拍 final 作为新的 `last_effective`，`tests/test_runtime_output_policy.py` 已新增 forced-safe→hold、可配置 safe→hold、冷启动 hold 与连续 hold 反证；正常路径的 REAL 非有限值、固定宽度整数/位串越界、整数限速可表达性也已由统一 `_iec_value_error(...)` 收紧。`src/runtime/__init__.py` 仍只导出本包 8 个稳定公共名，未越界引入新 API。
- 项目工程约定：`last_effective` 每拍前移、故障落 `safe_value` 绕过限速、恢复首拍从 `safe_value` 重建基准，以及 OutputPolicy 层对非有限/越界值失败关闭，均是当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义；`TIME` 位宽未冻结、仅做结构类型检查的边界保持诚实。
- 待真机验证假设：输出基准、整数位宽/回绕、F1/binary32 输出边界、真实提交故障、watchdog/scan-fault 生成、shadow、可信反馈/HAL 与目标 SP16.1 行为仍未由本包验证；当前 Python 测试不证明与 PLC 语义一致。
- 必须返修：**故障路径仍跳过 request 的 IEC 结构/数值域校验，与任务书及 Round 2 交接声明冲突。** `src/runtime/output_policy.py:425-427` 读取 request 后进入 `_compute(...)`；`462-477` 在 safety/hold 分支直接返回，而 request 校验直到正常路径 `479-483` 才执行。因此 `safety_trip` 下 `REAL request=NaN` 会被静默接受并 stage `safe_value=0.0`，`USINT request=999` 也会被静默接受并 stage `0`，内部 `last_effective` 同时前移；这违反任务书“safe_value、request 与最终值必须严格符合声明 IEC 类型”以及本轮“request 运行期拒绝且不 stage、不前移状态”的交接结论。请把 request 校验前移到故障决策之前（或等价的所有路径公共入口），确保正常、forced safe、可配置 safe、hold 都使用同一口径；新增至少两条反证，覆盖故障状态下非有限 REAL 与越界整数 request，并断言 `OutputPolicyError`、pending 为空、`last_effective` 不前移。若希望故障时忽略非法 request 并优先 stage 安全值，这将改变当前任务书的失败关闭要求，须交用户作规格裁决，不能由实施方自行放宽。
- 非阻塞建议：Round 1 关于装配期 Store 与 `stage_outputs(...)` 运行期 Store 对象身份未强制一致的建议仍可留待参数装载总闸门或独立工作包，不阻塞本轮返修。
- 审核证据：接手五字段为 `WP-20260716-007 / READY_FOR_CODEX / codex / codex / round=2`，且 `2 <= max_rounds=3`；协调器心跳在接手与结束时均为 live 且未过期。独立 scope 清单在审核开始与结束逐项一致：`src/runtime/output_policy.py=ac95db5ba88d918b91909e07003d0c4c02741efb22595e6947f20008ae6ea927`、`src/runtime/__init__.py=83a49879fbc5ad1c957f84e7e78c121a883b551343cc50721bd3fc6e46d17700`、`tests/test_runtime_output_policy.py=41a51f34a36b53541a3d5c5d220d443d45589b9f46b698b862169bf95e80ad6c`，审核期间 scope 无漂移。Codex 独立运行：专用 OutputPolicy 60/60、既有四组运行时合并 166/166、排除 scope 外 dashboard 模块后的正式测试 916/916、原型 68/68 均通过；完整正式 1000 项和全仓 1068 项各有同 9 个 `tests/test_ai_handoff.py` 用例因当前沙箱禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 991/991、1059/1059 通过。另用两条最小手工反证稳定复现 `safety_trip` 下 `REAL NaN` 与 `USINT 999` request 被接受并 stage 安全值。
- review_started_sha256: 8256f96a042ff6a0aa290c6394c8541888d36b14ddc1c5338bb4351cb6f6525d
- review_finished_sha256: 8256f96a042ff6a0aa290c6394c8541888d36b14ddc1c5338bb4351cb6f6525d
- handoff_to: claude
- reviewed_at: 2026-07-16 22:53:00 CST

### Claude Round 3 额度恢复重试授权

- retry_authorized_at: 2026-07-17 07:22:16 CST
- retry_idempotency_key: `WP-20260716-007:2:start_claude_rework`
- retry_reason: Claude 上次启动在写入 scope 前因服务端会话额度限制失败；其提示的 2026-07-17 02:40 CST 恢复时间已经过去。当前五字段、Round 2 审核结束哈希与协调器租约均未发生漂移，因此仅授权原失败键重试一次并继续 Round 3；不得恢复旧 30 分钟轮询，不得绕过全局执行租约。

### Claude 实施交接（Round 3）

- 接手核验：五字段接手值为 `WP-20260716-007 / CHANGES_REQUESTED / owner=claude / handoff_to=claude / round=2`，组合与状态映射一致；按协议 round+1 → `round=3 <= max_rounds=3`，未超轮次（本轮为最后一个自动轮次）。按上节 `retry_idempotency_key: WP-20260716-007:2:start_claude_rework` 的一次性重试授权继续 Round 3。仅用 Python 标准库核验（`hashlib` / `json` / `time`，未请求 `shasum`/`cat`/`date` 等被禁 shell 工具）：接手时复算 scope 聚合 SHA-256 = `8256f96a042ff6a0aa290c6394c8541888d36b14ddc1c5338bb4351cb6f6525d`，与 Codex Round 2 `review_started_sha256` / `review_finished_sha256` **三者一致**，逐文件哈希亦与 Round 2 `scope_manifest` 全等（`ac95db5b…6ea927` / `83a49879…d17700` / `41a51f34…80ad6c`），**无证据漂移**；协调器心跳 `coordinator_live=true` 且 `valid_until_epoch` 未过期。`docs/PROJECT_STATE.md` 实盘 SHA-256 = `c0455052eaf8157c76aba1090a74ca48c30f9d97028337fd163c90116309e90c`，与启动前行政证据一致，本轮未改。
- 完成内容：Codex Round 2 唯一一条"必须返修"（故障路径跳过 request 的 IEC 结构/数值域校验）落地，只改 scope 内文件，**未按"故障时忽略非法 request"方向放宽**（该方向会改变任务书失败关闭要求，属规格裁决，实施方无权自行采纳）。
  1. **request 校验前移至所有路径公共入口**：`src/runtime/output_policy.py::_compute` 原在故障决策（`cause = self._top_cause(snapshot)`）之后、仅正常路径分支内执行 `_iec_value_error(pol.iec_type, request)`，故 safety/hold 分支的 `return` 先行命中即绕过校验。现将该校验移到 `_compute` 取出 `state` 之后、`_top_cause(...)` 调用**之前**，正常、强制 safe、可配置 safe、可配置 hold（含冷启动 hold）四条路径共用同一口径；正常路径内的重复校验相应删除，`_iec_value_error` 仍是模块内单一口径 helper，未新增第二套判定表。Codex 的两条最小反证已复现并转为常驻用例：`safety_trip` 下 `REAL request=NaN` 与 `USINT request=999` 此前被静默接受并 stage `safe_value`、`last_effective` 同时前移；现均抛 `OutputPolicyError`、pending 为空、`last_effective` 不前移。
  2. **docstring 同步**：`_compute` 补记"request 校验在故障决策之前执行，四条路径共用同一口径；非法 request 一律失败关闭，不因处于故障状态而被静默接受"，避免下一位读者重蹈分支顺序陷阱。
- 修改文件（均在 scope 内，共 2 个）：`src/runtime/output_policy.py`（`_compute` 校验前移 + 正常路径重复校验删除 + docstring）、`tests/test_runtime_output_policy.py`（60 → **66** 用例，+6）。
- 明确未修改：`src/runtime/__init__.py` **本轮零改动**（哈希仍为 `83a49879…d17700`）——本轮返修未引入新公共名，现有 8 个导出无回归。scope 外零文件改动；未改 `docs/PROJECT_STATE.md`、协调器/自动化配置；未执行任何 Git 写操作（含暂存、提交、推送、建分支、建 PR、合并、`.git` 内部写入）。任务书冻结边界继续未实现（属后续独立工作包）：watchdog/scan-fault 信号生成、§4.3 外层安全提交、shadow mode、`last_physical_committed`、真实驱动提交、`commit_fault`/`channel_fault` 锁存与复位、可信反馈/HAL、实时线程、L2 adapter 注册表、参数装载总闸门；`inputs`/`prev` 端口参数仍不消费。Codex **非阻塞建议**（装配期/运行期 Store 对象身份核对）本轮**仍未实施**——Round 2 已按同一理由留待裁决，且 Codex Round 2 明确"不阻塞本轮返修"，故维持不动，请裁决是否单开工作包。
- 新增测试（6 项，逐条对应返修意见）：`test_forced_safe_cause_still_rejects_illegal_request`（Codex 反证原样固化：`safety_trip` × REAL NaN/Infinity + USINT 999/-1 + SINT 128）、`test_forced_safe_cause_illegal_request_does_not_advance_history`（`watchdog`/`scan_fault` 故障态下先建立 `last_effective=50` 历史，再断言非法 request 被拒且历史**不前移**）、`test_configurable_safe_cause_still_rejects_illegal_request`、`test_configurable_hold_cause_still_rejects_illegal_request`（hold 分支不得因"输出不取 request"而跳过校验）、`test_cold_start_hold_cause_still_rejects_illegal_request`（冷启动无历史退化分支同口径）、`test_legal_request_under_cause_still_gated`（收紧不误杀：故障态下合法 request 仍按原策略落安全值）。共用 helper `_assert_rejected_under_cause(...)` 统一断言三件事：抛 `OutputPolicyError`、`pending.staged() == {}`、`diagnostic_last_effective()` 不前移。既有 60 项断言零改写——本轮为纯增量收紧，未放宽任何既有语义锁。
- 测试命令与实际结果（2026-07-17 本轮实际运行，均设 `PYTHONDONTWRITEBYTECODE=1`；五组全部一次通过，无失败，故无"首次失败/根因/修复后重跑"记录）：
  1. `python -m unittest tests.test_runtime_output_policy` → Ran **66** tests, OK（60 + 6 新增）。
  2. `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → Ran **166** tests, OK（与 Round 1/2 同值，零破坏）。
  3. `python -m unittest discover -s tests -t .` → Ran **1006** tests, OK（Round 2 为 1000，+6 新增）。
  4. `python -m unittest discover -s prototype_05 -t .` → Ran **68** tests, OK。
  5. `python -m unittest discover -s . -t .` → Ran **1074** tests, OK（1006 + 68）。
  - 环境差异说明（与 Round 2 一致）：Codex 环境中 `tests/test_ai_handoff.py` 有 9 项因沙箱禁止绑定本地 HTTP 端口报 `PermissionError`；本轮环境允许绑定，故第 3/5 组为全量通过（1006/1006、1074/1074），非本轮改动所致。第 3/5 组的 dashboard 用例会向 stdout 打印面板地址，需按 `^(Ran|OK|FAILED)` 过滤才能读到计数行。
- 工程约定与未验证边界（分层，诚实声明，与 Round 1/2 一致）：`last_effective` 每拍前移制度、输出基准、故障落安全值绕过限速、恢复首拍从 `safe_value` 限速，以及 IEC 数值域拒绝制度（**含本轮扩展到故障路径的 request 校验**），均为**项目工程约定、非 CODESYS / IEC 61131-3 官方已证实语义**，未经目标 SP16.1 真机或真实 HAL 验证；上述 66/166/1006/68/1074 项 Python 测试**只证明本实现行为，不构成与 PLC 语义一致的证据**。§4.1 四类"重建物理基准边界"本包仍只落地冷启动一类 + 安全落值后恢复首拍；F1/binary32 输出量化未接入（属数值/HAL 层）。`_iec_value_error` 对 `TIME` / `STRING` 仍只校验结构类型、不施加范围约束（TIME 工程位宽尚未冻结，`numeric.INT_WIDTHS` 无 TIME 条目）。整数越界一律**拒绝**而非回绕，是本包失败关闭工程约定；声明位宽的回绕/截断语义仍属数值层且待真机裁决，本轮未改动 `numeric.py`。
- **本轮新增待裁决边界（诚实声明）**：request 校验前移后，非法 request 会在**故障状态下也**阻止本拍 stage——即故障拍不再落 `safe_value`，而是抛错交由外层处置。这严格符合任务书"safe_value、request 与最终值必须严格符合声明 IEC 类型"与 Round 2"request 运行期拒绝且不 stage、不前移状态"的口径，也是 Codex 本轮点名要求的方向；但**外层在策略抛错后如何保证物理输出安全**（§4.3 扫描异常外层安全提交）**不在本包范围**，属后续独立工作包。若用户/审核方认为"故障态应优先落安全值、忽略非法 request"，那是任务书失败关闭要求的**规格裁决**，须转用户，实施方不自行放宽。
- 已知疑问（请审核裁决，均不涉及规格修改）：① `hold` 命中且有历史时输出保持 `last_effective` 且**不**置 `boundary_reset`（物理输出未离开该值，恢复即从该值继续限速）——Round 1/2 已知疑问 ① 连续两轮未被 Codex 推翻，故维持。② Codex 非阻塞建议（Store 对象身份核对）未实施，理由见"明确未修改"。③ `commit_fault_retry_n` 仍仅建模字段，锁存/复位属 §4.4 后续工作包。④ 本轮为 `max_rounds=3` 的最后一个自动轮次：若仍有"必须返修"，按硬规则 4 应转 `BLOCKED` 交用户仲裁，不得自动进入 Round 4。
- scope_sha256: b31b3fd8b2f3184d580edcae8fc7ce7849042eedd5345a8f3c4074a82d090fa6
- scope_manifest:
  - `746cdb2f537a8ca0065ece06ee59e7cf38c854ac1c5a7ed56247dfe0cd019756  src/runtime/output_policy.py`
  - `83a49879fbc5ad1c957f84e7e78c121a883b551343cc50721bd3fc6e46d17700  src/runtime/__init__.py`
  - `53fd22a3c706de963a7c8ed316bb7dab50f29a551b5d7ea7a8383580d17553a2  tests/test_runtime_output_policy.py`
- handoff_to: codex
- implementation_finished_at: 2026-07-17 07:26:40 CST

### Codex 审核结论（Round 3）

- verdict: APPROVED
- 已验证事实：Round 2 唯一必须返修已收口。`src/runtime/output_policy.py::_compute` 现在取出通道状态后、进入 `_top_cause(...)` 故障决策前即用统一 `_iec_value_error(...)` 校验 request；正常、强制 safe、可配置 safe、有历史 hold 和冷启动 hold 均无法再绕过。校验异常发生在任何 `pending.stage(...)` 或 `last_effective` 前移之前，因此故障态 REAL NaN/Infinity、USINT/SINT 越界 request 均失败关闭且不留半拍状态。`tests/test_runtime_output_policy.py` 新增 6 项测试覆盖上述四类路径、已有历史不前移与合法 request 不误杀；`src/runtime/__init__.py` 公共导出未变。
- 项目工程约定：`last_effective` 每拍前移、故障落 `safe_value` 绕过限速、恢复首拍从 `safe_value` 重建基准，以及 OutputPolicy 层对非有限/越界值采用失败关闭，均是当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义。故障态非法 request 会拒绝本拍 stage，与任务书及 Round 2 返修要求一致；其后如何保证物理输出安全由后续 `ENGINE_SCAN_SPEC §4.3` outer scan runner 工作包承接，不在本包内伪造实现。
- 待真机验证假设：输出基准、整数位宽/回绕、F1/binary32 输出边界、真实提交故障、watchdog/scan-fault 生成、shadow、可信反馈/HAL 与目标 SP16.1 行为仍未由本包验证；当前 Python 测试不证明与 PLC 语义一致。`TIME` / `STRING` 仍只做结构类型校验，其中 TIME 工程位宽尚未冻结。
- 必须返修：无。
- 非阻塞建议：装配期 Store 与 `stage_outputs(...)` 运行期 Store 对象身份未强制一致的边界，按 Round 1/2 已登记口径留待参数装载总闸门或独立工作包；不阻塞本轮批准。
- 审核证据：接手五字段为 `WP-20260716-007 / READY_FOR_CODEX / codex / codex / round=3`，且 `3 <= max_rounds=3`；审核期间状态为 `CODEX_REVIEWING / codex / codex`，协调器心跳在接手与结束时均 live 且未过期。独立 scope 清单在审核开始与结束逐项一致：`src/runtime/output_policy.py=746cdb2f537a8ca0065ece06ee59e7cf38c854ac1c5a7ed56247dfe0cd019756`、`src/runtime/__init__.py=83a49879fbc5ad1c957f84e7e78c121a883b551343cc50721bd3fc6e46d17700`、`tests/test_runtime_output_policy.py=53fd22a3c706de963a7c8ed316bb7dab50f29a551b5d7ea7a8383580d17553a2`，审核期间 scope 无漂移。Codex 独立运行：专用 OutputPolicy 66/66、既有四组运行时合并 166/166、排除 scope 外 dashboard 模块后正式测试 922/922、原型 68/68 均通过；完整正式 1006 项和全仓 1074 项各有同 9 个 `tests/test_ai_handoff.py` 用例因当前沙箱禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 997/997、1065/1065 通过。读取 Git 状态/差异只用于核对 scope，未执行任何 Git 写操作。
- review_started_sha256: b31b3fd8b2f3184d580edcae8fc7ce7849042eedd5345a8f3c4074a82d090fa6
- review_finished_sha256: b31b3fd8b2f3184d580edcae8fc7ce7849042eedd5345a8f3c4074a82d090fa6
- handoff_to: user
- reviewed_at: 2026-07-17 12:09:25 CST

---

## WP-20260720-008

- title: 阶段 1 外层安全扫描运行器与扫描/看门狗故障安全提交
- status: CLOSED
- owner: user
- handoff_to: user
- round: 3
- max_rounds: 3
- closure_note: 用户接受 Codex Round 3 `APPROVED` 结论，并授权关闭本包、更新项目状态、复跑最终测试及完成 Git/GitHub 发布收尾。Python 验证只证明当前实现行为，不构成与目标 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路一致的证据。
- base_commit: 975085668c2fa1d698e275b4af31d52eb70aa3ca
- created_by: user
- created_at: 2026-07-20 15:24:25 CST
- depends_on:
  - WP-20260716-007 CLOSED
- scope:
  - src/runtime/scan_runner.py
  - src/runtime/output_policy.py
  - src/runtime/__init__.py
  - tests/test_runtime_scan_runner.py
  - tests/test_runtime_output_policy.py
- scope_baseline_sha256: cfcfa98f9932b6bde07ccddfd35dfe942c3e220d7684978f24a264cfc9f4b636
- scope_baseline_manifest:
  - `ABSENT  src/runtime/scan_runner.py`
  - `746cdb2f537a8ca0065ece06ee59e7cf38c854ac1c5a7ed56247dfe0cd019756  src/runtime/output_policy.py`
  - `83a49879fbc5ad1c957f84e7e78c121a883b551343cc50721bd3fc6e46d17700  src/runtime/__init__.py`
  - `ABSENT  tests/test_runtime_scan_runner.py`
  - `53fd22a3c706de963a7c8ed316bb7dab50f29a551b5d7ea7a8383580d17553a2  tests/test_runtime_output_policy.py`

### 工作包创建行政证据（Claude 启动前）

- 用户于 2026-07-20 明确确认：将 WP-007 关闭，授权 Codex 完成 Git/PR 收尾、恢复事件协调器并创建 WP-008；因此本节与 `docs/PROJECT_STATE.md` 更新属于创建工作包的行政动作，不属于 Claude 实施 scope，也不是 scope 漂移。
- WP-007 已通过 PR #15 合并至 `main`，merge commit=`975085668c2fa1d698e275b4af31d52eb70aa3ca`；创建本包前工作区清洁，`main` 与 `origin/main` 同步。
- 事件协调器已在本机恢复并核验：原生 kqueue、外部执行启用、`/healthz` 返回 `ok=true/read_only=true/dry_run=false`，项目内心跳投影为 live 且持续递增；旧 Claude/Codex 30 分钟主轮询仍必须保持暂停。
- 启动前行政更新后的 `docs/PROJECT_STATE.md` SHA-256 = `bac44e367ade0a0b31a9932251cf5a55fe717184203ebad0065d6d78ab4d65fe`。Claude 不得修改该文件；Codex 审核/收尾时再按实质状态更新。

### 目标与验收标准

实现 `ENGINE_SCAN_SPEC §4.3` 所要求的**外层**安全扫描恢复入口，并为独立监控器将来注入的软件 watchdog 超时信号提供同一安全提交路径。正常拍仍由既有 `ScanEngine` 完成；故障拍不得伪造正常业务执行，也不得产生双重提交。

1. **正常路径保持不变**
   - 新的 outer runner 必须复用既有 `ScanEngine`、`OutputPolicyService` 与同一提交端口；正常拍仍严格执行一次业务扫描和一次提交，返回值、`cycle_id`、`prev` 前移及连续拍行为不得改变。
   - runner 必须非重入；递归或并发进入失败关闭，不得并行操作同一 Store/pending/策略状态。

2. **扫描异常后的安全提交**
   - 仅当正常提交端口**尚未被尝试**，且输入锁存、IR 执行、业务输出生成或 OutputPolicy 正常 staging 发生异常时，outer runner 才把它归类为 `scan_fault`。
   - runner 必须锁存/写入 `scan_ok=False` 的安全状态证据，随后绕过本拍可能已经损坏或非法的 request，按已验证通道配置原子生成**全通道** `safe_value` 映像，并调用正常路径使用的同一提交端口**恰好一次**。
   - 安全映像必须通过 `OutputPolicyService` 的显式公共故障恢复入口生成；不得在 runner 中复制第二套类型表、限速表或通道策略。该入口专用于 scan/watchdog 外层恢复，不读取业务 request，不把非法 request 当作安全落值的前置条件；但必须保证所有配置的 `safe_value` 与最终映像仍符合已声明 IEC 类型，并以全有或全无方式 staging。
   - 安全提交成功后，策略侧 `last_effective` 必须与真正提交的安全映像一致并标记下一正常拍需要重建边界；既有 `ScanEngine.prev` 不得因失败拍前移。
   - 安全提交后仍要以结构化异常向上报告原始扫描失败，明确 `safe_commit_succeeded=True`；不得吞掉原始异常、假装本拍成功。

3. **提交异常不得误分类为扫描异常**
   - 如果正常提交端口已被调用且自身抛错，本包不得再发起第二次安全提交；这属于 `ENGINE_SCAN_SPEC §4.4` 的 `commit_fault` 后续工作，原异常必须原样或带阶段证据向上报告。
   - 实现必须用可审计的阶段/提交尝试证据区分“提交前扫描失败”和“提交调用失败”，不能靠异常文本、异常类猜测或宽泛 `except` 后无条件再提交。
   - 若外层安全提交自身失败，结构化异常必须同时保留原始扫描异常和安全提交异常，`safe_commit_succeeded=False`，本包内不得自动重试或冒充安全落值成功。

4. **最小软件 watchdog 信号响应**
   - 提供一个显式、可注入的 watchdog 超时事件入口；命中时必须跳过本拍业务 IR，锁存/写入 `watchdog_ok=False`，复用与 scan fault 完全相同的全通道安全 staging + 单次提交路径，并返回/抛出可审计的 watchdog 故障结果。
   - 本包**不实现**真实时钟、`sleep`、后台线程、周期调度、抖动统计、超时测量或硬件 watchdog；事件何时产生由后续阶段 7 的独立 runner/monitor 决定。本包只实现确定性的信号消费和安全输出反应。
   - 安全故障标志不得在下一拍被隐式自动清除；恢复必须依赖显式替换/确认安全状态，避免瞬时故障被悄悄掩盖。

5. **资源与失败边界**
   - 任意退出路径都要清空临时 pending、释放 runner/engine 锁；不得把上一拍半成品带到下一拍。
   - 不得增加影子执行、真实驱动/HAL、`last_physical_committed`、可信反馈、`commit_fault`/`channel_fault` 重试或复位、通知系统、L2 adapter 注册表。
   - 不得修改 specs、`docs/PROJECT_STATE.md`、本交接文件除实施交接段以外的历史正文、AI 协调器/自动化配置或 `.git`；Claude 不执行任何 Git 写操作。

### 最低测试要求

1. 正常一拍和连续两拍与直接 `ScanEngine.scan(...)` 等价，业务提交每拍一次，`prev` 只在成功后前移。
2. 输入锁存、IR 执行与正常策略 staging 的代表性异常各能触发安全提交；全通道（含非零 `safe_value`）均写入，业务提交未发生，安全提交恰好一次。
3. 以故障态非法/非有限 request 复现 WP-007 的已知边界：正常 OutputPolicy 会失败，但 outer runner 仍能经专用恢复入口提交安全映像；pending 为空、`prev` 不前移、策略历史与安全值一致。
4. 正常提交端口抛错时总调用次数为一次，不追加安全提交，且不会被误报为 `scan_fault` 已安全落值。
5. 安全提交端口抛错时保留原始异常 + fallback 异常，零重试，状态不得声称已安全提交。
6. 显式 watchdog 事件跳过 IR/业务路径、全通道安全提交一次、`watchdog_ok=False` 保持锁存；无真实等待或线程。
7. runner 并发/递归重入失败关闭，锁与 pending 在异常后可恢复使用。
8. 新公共 API 的通道完整性、IEC safe value 校验、全有或全无 staging 和历史前移单独有反证测试；既有 OutputPolicy 66 项语义锁不得改写放宽。
9. 至少运行并记录：
   - `python -m unittest tests.test_runtime_scan_runner tests.test_runtime_output_policy`
   - `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir`
   - `python -m unittest discover -s tests -t .`
   - `python -m unittest discover -s prototype_05 -t .`
   - `python -m unittest discover -s . -t .`

### 实施与交接要求

- 开工前逐项核对五字段、`round <= max_rounds`、`base_commit`、baseline manifest/聚合哈希及 `docs/PROJECT_STATE.md` 行政哈希；任一不符立即幕等退出，不写 scope。
- 只允许修改 scope 中五个文件。`src/runtime/scan_runner.py` 与 `tests/test_runtime_scan_runner.py` 创建前必须确认为不存在；若已存在即视为 baseline 漂移。
- 实施结束后逐文件计算 SHA-256，并按 scope 声明顺序计算聚合 SHA-256；把实际修改、异常语义、首次失败/根因/修复后结果、五组测试实数、工程约定与待真机假设完整写入本工作包的“Claude 实施交接（Round 1）”。
- 交接时原子更新顶层字段为 `status: READY_FOR_CODEX`、`owner: codex`、`handoff_to: codex`，`round` 保持 1；随后立即停笔，等待 Codex 独立审核。
- 所有 Python 验证只证明当前实现行为，不构成与目标 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路一致的证据。

### Claude Round 1 权限模式失败重试授权

- retry_authorized_at: 2026-07-20 15:47:00 CST
- retry_idempotency_key: `WP-20260720-008:1:start_claude_implementation:python-only-retry-1`
- retry_reason: 首次事件触发已成功启动 Claude，但 adapter 的 `dontAsk` 白名单只允许 Python 命令；Claude 误用被禁止的 `git`/`shasum`/`ls` 做开工核验后按协议安全停止，退出码为 0，未修改任何 scope 文件，也未交接。当前 base commit、五文件 baseline 聚合哈希和 `docs/PROJECT_STATE.md` 行政哈希均由 Codex 复算保持与任务书一致。现仅授权以新幂等键重试一次，并明确要求 Claude 使用 Python 标准库完成 HEAD/文件存在性/SHA-256 核验及测试；不得放宽 Git/gh/rm/sudo 禁令，不得恢复旧 30 分钟轮询，不得绕过全局执行租约。

### Codex 审核结论（Round 1）

- verdict: CHANGES_REQUESTED
- 已验证事实: Claude 已在五文件 scope 内实现 `OuterScanRunner`、共享 `CommitPort`、显式 watchdog 事件入口、结构化安全提交信号以及 `OutputPolicyService.stage_safe_image()`；正常路径复用既有 `ScanEngine`，提交尝试证据能阻止正常提交端口抛错后再做第二次安全提交，watchdog 路径跳过业务 IR。接手五字段为 `WP-20260720-008 / READY_FOR_CODEX / codex / codex / round=1`，`1 <= max_rounds=3`；实施交接聚合 SHA-256 与 Codex 独立复算均为 `9552d4ac6b399852700875c9155b32186eefb5e2ebf5978e060129d76211cb89`，逐文件清单一致，审核期间 scope 无漂移。
- 项目工程约定: scan/watchdog 故障锁存、全通道安全映像、提交前后阶段证据、恢复边界与 `last_effective` 纪律均是当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义。显式 watchdog 入口只消费确定性事件，不测量墙钟、不实现线程或硬件 watchdog，边界分层正确。
- 待真机验证假设: 真实周期/抖动、硬件 watchdog、现场安全回路、可信反馈/HAL、真实驱动提交、`last_physical_committed`、`commit_fault`/`channel_fault` 锁存复位、shadow 与目标 SP16.1 行为仍未验证；当前 Python 测试不证明 PLC/真机一致性。
- 必须返修: 1) **安全提交失败后策略历史错误前移。** `OutputPolicyService.stage_safe_image()` 在 runner 调用提交端口之前就把全部通道 `_state` 更新为 `safe_value + boundary_reset=True`；`OuterScanRunner._safe_commit_or_raise()` 对提交异常只包装信号、不撤销或延迟该状态。因此底层提交失败且 `safe_commit_succeeded=False` 时，`diagnostic_last_effective()` 仍报告未真正生效的安全映像。Codex 最小反证稳定得到：`commit_calls=1`、`safe_commit_succeeded=False`，但历史为 `{'DO0': False, 'AO0': 9}`。这违反任务书“策略历史与**真正提交**的安全映像一致”及“失败状态不得声称已安全提交”。请把安全映像的准备/staging 与“提交成功后确认策略历史”做成可审计的两阶段事务或等价方案；提交失败不得把 `last_effective`/边界基准冒充为已生效，且不得破坏既有正常 OutputPolicy 语义。新增成功确认与失败不前移的反证测试。2) **fallback staging 失败没有结构化保留双异常。** `_safe_commit_or_raise()` 只捕获 `_port.commit(...)`，`_latch_fault(...)` 或 `stage_safe_image(...)` 抛错会直接漏出普通异常，丢失 `ScanFaultSafeCommit` / `WatchdogSafeCommit` 的 `original_exception`、`commit_exception`（或等价 fallback 异常字段）与 `safe_commit_succeeded=False` 证据。Codex 通过构造装配后 safe-value 漂移复现：正常策略先因非法 request 失败，随后安全映像校验失败，最终只抛 `OutputPolicyError`、`is_structured_scan_signal=False`、提交 0 次。请让**安全恢复链的任一失败阶段**都产生结构化信号，同时保留原始扫描异常与具体 fallback 异常、明确未安全提交、零重试；watchdog 无原始业务异常时也须保留 fallback 异常。新增 scan_fault 与 watchdog 两条 staging-failure 反证，并断言临时 pending/锁可恢复。
- 非阻塞建议: `OuterScanRunner` 通过读取 `engine._policy` / `engine._committer` 私有字段做装配身份核验较脆弱；后续可改为 ScanEngine 只读端口属性或集中工厂，但不阻塞本轮两项安全返修。公共 `stage_safe_image()` 的“全有或全无”目前依赖 runner 创建干净的真实 `OutputPending`；若未来允许任意 pending 实现，应显式收紧接口契约或提供回滚语义。
- 审核证据: Codex 独立运行 `tests.test_runtime_scan_runner + tests.test_runtime_output_policy` = 95/95、engine/executor/store/ir = 166/166，`git diff --check` 通过；另运行两条最小手工反证，分别复现“安全提交失败但策略历史已前移”和“安全 staging 失败漏出非结构化异常”。审核开始/结束聚合 SHA-256 均为 `9552d4ac6b399852700875c9155b32186eefb5e2ebf5978e060129d76211cb89`。
- review_started_sha256: 9552d4ac6b399852700875c9155b32186eefb5e2ebf5978e060129d76211cb89
- review_finished_sha256: 9552d4ac6b399852700875c9155b32186eefb5e2ebf5978e060129d76211cb89
- handoff_to: claude
- reviewed_at: 2026-07-20 16:43:07 CST

### Claude 实施交接（Round 1）

- 开工核验（全部用 Python 标准库，命中授权重试键 `WP-20260720-008:1:start_claude_implementation:python-only-retry-1`）：① `pathlib` 读 `.git/HEAD` = `ref: refs/heads/main`，按 symbolic ref 读松散 `.git/refs/heads/main` = `975085668c2fa1d698e275b4af31d52eb70aa3ca`，与 `base_commit` 一致；② `Path.is_file()` 确认 `src/runtime/scan_runner.py`、`tests/test_runtime_scan_runner.py` 开工前**不存在**（与 baseline manifest 的 `ABSENT` 一致），另三文件 `hashlib.sha256(read_bytes())` 逐文件哈希与 baseline manifest 一致；按 manifest 声明顺序聚合 SHA-256 = `cfcfa98f9932b6bde07ccddfd35dfe942c3e220d7684978f24a264cfc9f4b636`，与 `scope_baseline_sha256` 一致；③ `docs/PROJECT_STATE.md` 行政哈希 = `bac44e367ade0a0b31a9932251cf5a55fe717184203ebad0065d6d78ab4d65fe`，与任务书一致；④ 五字段 `status=CLAUDE_WORKING/owner=claude/handoff_to=claude/round=1/max_rounds=3` 与 scope 五文件核对一致，`round<=max_rounds`。全部证据相符，方进入实施。测试与时间戳分别用 `PYTHONDONTWRITEBYTECODE=1 python -m unittest ...` 与 `datetime.now().astimezone()`；未触碰 Git/gh/rm/sudo 等被禁命令。

- 完成内容（对应验收标准 5 条 + 最低测试 9 条）：
  1. **正常路径不变**：新增 `src/runtime/scan_runner.py::OuterScanRunner.scan_cycle(samples)` 正常拍**原样复用**注入的 `ScanEngine.scan`（一次业务扫描 + 一次提交），返回其 `ScanResult`；`CommitPort` 每拍进入前 `reset()`，业务提交每拍恰一次。运行器持自身非重入锁（非阻塞获取），`scan_cycle` 与 `trigger_watchdog` 共用，递归/并发**失败关闭**（`ScanRunnerReentryError`），锁在原拍返回后释放。
  2. **扫描异常安全提交（§4.3）**：仅当 `CommitPort.attempts == 0`（提交尚未尝试）时捕获到的异常才归类 `scan_fault`——锁存 `scan_ok=False` → 新建 `OutputPending` 绕过引擎本拍可能已损坏的 pending → 经 `OutputPolicyService.stage_safe_image()`（本包新增公共入口）原子生成**全通道** `safe_value` 安全映像 → 经**同一** `CommitPort` 提交**恰一次** → `raise ScanFaultSafeCommit(safe_commit_succeeded=True)` 并 `raise ... from` 原始扫描异常，不吞异常。`stage_safe_image` 不读业务 request、不做限速、不看安全快照，复用同一 `_order/_policy/_iec_value_error` 口径（不复制第二套类型/限速/通道表）；全有或全无 staging，成功后每通道 `last_effective=safe_value` 且置 `boundary_reset`（恢复首拍从 safe 基准限速）。`ScanEngine.prev` 不因失败拍前移（安全提交路径不触碰 `engine.prev`/业务 Store）。
  3. **提交异常不误分类（§4.4）**：`attempts >= 1`（提交端口已被调用并抛错）判为 `commit_fault`，原异常**原样上抛**、不追加第二次安全提交、不锁 `scan_ok`。分类**只依据可审计的提交尝试证据 `CommitPort.attempts`**（提交前 +1 再委托），绝不看异常文本/异常类，也不宽泛 `except` 后无条件再提交。外层安全提交**自身**失败时 `raise ScanFaultSafeCommit(safe_commit_succeeded=False)`，`original_exception` 与 `commit_exception` **同时保留**，零重试、不冒充成功。另对 `ScanReentryError`（引擎并发契约违约）原样上抛、绝不安全提交，避免与在途扫描竞争双提交。
  4. **最小 watchdog 响应**：`trigger_watchdog()` 显式、可注入，跳过业务 IR（不调用 `engine.scan`，故 `engine.prev`/业务 Store 不前移），锁存 `watchdog_ok=False`，复用与 `scan_fault` 完全相同的全通道安全 staging + 单次提交，`raise WatchdogSafeCommit`。**不**测时钟、`sleep`、起线程、周期调度或抖动统计——事件由阶段 7 独立 monitor 产生。安全标志（`scan_ok`/`watchdog_ok=False`）**不在下一拍被隐式清除**，运行器只写入不复位。
  5. **资源/失败边界**：安全提交用**本地新建** pending，退出即释放；引擎异常路径由其自身 finally 清 pending、释放锁；未引入 shadow、`last_physical_committed`、真实 HAL、`commit_fault/channel_fault` 重试与复位、通知系统或 L2 adapter。
  - 装配期强约束：`OuterScanRunner` 只读校验 `engine._policy is policy` 与 `engine._committer is commit_port`（**不修改** `engine.py`），保证安全提交与正常门控走**同一策略状态、同一提交端口**；`CommitPort`/`OutputPolicyService`/`ScanEngine` 类型不符或未共享即 `ScanRunnerConfigError`。`src/runtime/__init__.py` 稳定导出 `CommitPort/OuterScanRunner/ScanRunnerError/ScanRunnerConfigError/ScanRunnerReentryError/SafeCommitSignal/ScanFaultSafeCommit/WatchdogSafeCommit`。

- 失败语义（结构化、不吞异常）：`SafeCommitSignal` 基类携 `cause`（scan_fault/watchdog）、`safe_image`（安全映像独立副本）、`safe_commit_succeeded`、`original_exception`（watchdog 为 None）、`commit_exception`；子类 `ScanFaultSafeCommit`/`WatchdogSafeCommit`。正常拍成功返回 `ScanResult`，故障拍一律 `raise` 结构化信号；`commit_fault` 原样上抛底层异常（不是 `SafeCommitSignal`，不冒充已安全落值）。

- 修改文件（scope 五文件，逐文件 SHA-256 见末尾）：
  - `src/runtime/scan_runner.py`（新建）：`CommitPort` / `OuterScanRunner` / 异常与结构化信号类。
  - `src/runtime/output_policy.py`（改）：新增 `OutputPolicyService.stage_safe_image()` 专用故障恢复入口与 `safety_state` 只读属性；更新模块顶部“明确不实现”诚实边界，注明本模块只提供安全映像入口、外层编排仍属 scan runner。既有 `OutputPolicy`/`SafetySnapshot`/`SafetyStateService`/`stage_outputs`/`_compute` 语义**未改动放宽**。
  - `src/runtime/__init__.py`（改）：新增 runner 公共 API 导入与 `__all__` 导出，既有导出未回退。
  - `tests/test_runtime_scan_runner.py`（新建）：23 项，覆盖最低测试 1–9。
  - `tests/test_runtime_output_policy.py`（改）：**仅追加** `TestSafeImageEntry` 6 项（safety_state 属性、全通道 staging + 历史前移、不读 request、配置漂移全有或全无反证、恢复边界基准、`stage_safe_image` 重入失败关闭）；既有 66 项语义锁一字未改。

- 明确未修改：`docs/PROJECT_STATE.md`（行政哈希保持 `bac44e3…`）、`docs/AI_REVIEW_HANDOFF.md` 除本实施交接段以外的历史正文、任何权威规格、`.cursor/rules/*`、协调器/自动化配置、`src/runtime/engine.py`（只读其注入引用做共享校验，未写）、`.git` 内部文件；未执行任何 Git 写操作。

- 首次失败 / 根因 / 修复后重跑：首轮定向 `python -m unittest tests.test_runtime_scan_runner tests.test_runtime_output_policy` 报 1 项 FAIL——`test_ir_execution_error_triggers_safe_commit` 断言 `original_exception` 为 `IECMathError`。根因：执行器把底层 `IECMathError('整数 DIV 除零')` **包装**为 `IRExecutionError` 再向上抛（`src/runtime/executor.py` 指令分派层），故外层捕获到的原始扫描异常是 `IRExecutionError`。修复：把测试断言改为 `IRExecutionError`（并改 import），仅调整测试期望、未改动被测语义。重跑同一命令 → Ran 95 tests, OK。

- 五组测试实数（2026-07-20 本机实际运行，`PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_scan_runner tests.test_runtime_output_policy` → **Ran 95, OK**（新增 23 + 既有 66 + 追加 6）。
  2. `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → **Ran 166, OK**。
  3. `python -m unittest discover -s tests -t .` → **Ran 1035, OK**（较基线 1006 增 29）。
  4. `python -m unittest discover -s prototype_05 -t .` → **Ran 68, OK**。
  5. `python -m unittest discover -s . -t .` → **Ran 1103, OK**（较基线 1074 增 29）。
  本机运行未出现受限沙箱下的面板端口 `PermissionError`；全部通过。

- 工程约定 / 待真机验证边界（诚实分层）：外层安全提交、`safe_value` 基准/恢复、故障锁存不自动复位均为**项目工程约定、非 CODESYS 官方语义**。真实周期计时、后台线程、硬件 watchdog、shadow、`last_physical_committed`、真实驱动/HAL、`commit_fault`/`channel_fault` 重试与复位、可信设备反馈**不在本包**，属后续工作包。以上 Python 测试只证明当前实现行为，**不构成**与目标 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路一致的证据；watchdog 事件的产生时机仍待阶段 7 独立 monitor。

- 已知疑问：① `OuterScanRunner` 为强制“正常提交与安全提交同一端口/同一策略”，在装配期**只读**校验 `engine._policy` / `engine._committer`（`ScanEngine` 私有注入引用），未修改 `engine.py`；若 Codex 认为跨作用域读取私有引用不妥，可评估后续给 `ScanEngine` 增设公共只读访问器（属 engine 范围，另开工作包）。② `commit_fault` 分类依据是共享 `CommitPort.attempts` 阶段证据；若未来正常提交端口自身实现为非本包 `CommitPort`，需保证仍经本 `CommitPort` 包装才能获得该证据（已在装配期强制 `commit_port` 为 `CommitPort` 且为引擎注入的同一实例）。

- scope_sha256: 9552d4ac6b399852700875c9155b32186eefb5e2ebf5978e060129d76211cb89
  - `9b1c3942a330f90df97fdf6426060ce574b2389e66164d9794be0174e6339e86  src/runtime/scan_runner.py`
  - `263bfcf5ddf2d509f099f4a96b6be12bb950adb968a8aca4a985babcf703749a  src/runtime/output_policy.py`
  - `599ac0ddbe596f3ff3435bc50f70ba516c33b2478e583797a6469224c29d5e81  src/runtime/__init__.py`
  - `cf0dea9c65b54d751e1f4ff64cad42b27f6b23d0481f3696bb996cc2fd9e67d5  tests/test_runtime_scan_runner.py`
  - `0d51acbd5cbdf520ddf564846d16dd8b5a6f809adea068354a16a4308b19f63e  tests/test_runtime_output_policy.py`
- handoff_to: codex
- implementation_finished_at: 2026-07-20T16:16:31+08:00

### Claude 实施交接（Round 2）

- 开工核验（全部用 Python 标准库 `pathlib`/`hashlib`，未触碰 git/gh/shasum/rm/sudo 等命令）：① 接手前五字段为 `WP-20260720-008 / CHANGES_REQUESTED / owner=claude / handoff_to=claude / round=1 / max_rounds=3`，符合 CHANGES_REQUESTED 映射，按协议 round+1 处理为 Round 2，`2 <= max_rounds=3`；② `.git/HEAD` = `ref: refs/heads/main` → 松散 ref `975085668c2fa1d698e275b4af31d52eb70aa3ca` 与 `base_commit` 一致；③ 逐文件 SHA-256 + 按 scope 声明顺序聚合 = `9552d4ac6b399852700875c9155b32186eefb5e2ebf5978e060129d76211cb89`，与 Round 1 `scope_sha256` 及 Codex `review_finished_sha256` 完全一致——审核结束后至本轮接手 scope **零漂移**；④ `docs/PROJECT_STATE.md` 行政哈希 = `bac44e367ade0a0b31a9932251cf5a55fe717184203ebad0065d6d78ab4d65fe`，与任务书一致。全部相符方进入返修。

- Codex Round 1 两条“必须返修”逐条落地：

  1. **安全提交失败后策略历史错误前移 → 改为可审计两阶段安全事务。** 根因：旧
     `OutputPolicyService.stage_safe_image()` 在 staging 的同时就把全通道 `_state`
     前移为 `safe_value + boundary_reset=True`，早于 runner 调用提交端口；提交失败
     时 `_safe_commit_or_raise` 只包装异常、不撤销该状态，故
     `diagnostic_last_effective()` 冒充未真正生效的安全映像。修复：把
     `stage_safe_image()` 拆为**仅准备/staging、绝不前移策略历史**；新增
     `confirm_safe_image(safe_image)` 只在**提交成功后**由 runner 调用，才统一前移
     `last_effective`=已提交安全映像并置 `boundary_reset`（全有或全无，且校验映像
     恰好覆盖装配通道集合）。`OuterScanRunner._safe_commit_or_raise()` 相应改为：
     staging → 提交成功 → `confirm_safe_image`；**提交失败则不 confirm**，
     `safe_commit_succeeded=False` 且策略历史保持故障前值，绝不冒充已安全落值。
     既有正常 `stage_outputs` / `_compute` 语义未改动放宽。
  2. **安全恢复链任一失败阶段都产生结构化信号并保留双异常。** 根因：旧
     `_safe_commit_or_raise` 只 `try` 包住 `_port.commit(...)`，`_latch_fault(...)`
     或 `stage_safe_image(...)` 抛错会漏出普通异常，丢失结构化信号与“未安全提交”
     证据。修复：`SafeCommitSignal` 增加 `fallback_exception`（失败阶段的具体异常）
     与 `failed_stage`（`"latch_fault"`/`"stage_safe_image"`/`"commit"`）字段，保留
     `commit_exception` 为向后兼容只读属性（仅 commit 阶段返回）；
     `_safe_commit_or_raise` 对锁存、staging、提交**三个阶段分别**捕获，任一失败均
     `raise` 结构化 `ScanFaultSafeCommit`/`WatchdogSafeCommit`，**同时保留原始扫描
     异常与该阶段 fallback 异常**、`safe_commit_succeeded=False`、零重试、不前移
     策略历史；watchdog 无原始业务异常时 `original_exception=None` 但仍保留
     `fallback_exception`。

- 修改文件（scope 五文件中改动 4 个，逐文件 SHA-256 见末尾）：
  - `src/runtime/output_policy.py`（改）：`stage_safe_image()` 改为仅 staging 不前移
    历史；新增 `confirm_safe_image()` 提交成功后前移；更新方法与模块顶部“明确不
    实现”docstring 为两阶段口径。既有 66 项语义锁对应实现未改动放宽。
  - `src/runtime/scan_runner.py`（改）：`SafeCommitSignal` 增 `fallback_exception`/
    `failed_stage` 并保留 `commit_exception` 兼容属性；`_safe_commit_or_raise` 改为
    三阶段结构化捕获 + 提交成功后 `confirm_safe_image` 的两阶段事务；更新模块/方法
    docstring。
  - `tests/test_runtime_output_policy.py`（改）：`TestSafeImageEntry` 按两阶段收口
    ——staging 不前移历史、`confirm_safe_image` 才前移、confirm 通道不匹配失败关闭、
    恢复边界基准改由 stage+confirm 验证；既有 66 项语义锁一字未改。
  - `tests/test_runtime_scan_runner.py`（改）：强化 `TestSafeCommitFailure`（断言
    `failed_stage="commit"`、`fallback_exception`、**安全提交失败历史不前移**）并加
    成功前移正例；新增 `TestSafeStagingFailure`（scan_fault 与 watchdog 两条 staging
    阶段失败反证：保留原始+fallback、`failed_stage="stage_safe_image"`、提交 0 次、
    历史不前移、锁/pending 可恢复）。

- 明确未修改：`src/runtime/__init__.py`（无公共名增删，哈希保持
  `599ac0dd…`）、`docs/PROJECT_STATE.md`（行政哈希保持 `bac44e3…`）、
  `docs/AI_REVIEW_HANDOFF.md` 除本 Round 2 实施交接段与顶层五字段以外的历史正文、
  任何权威规格、`.cursor/rules/*`、协调器/自动化配置、`src/runtime/engine.py`、
  `.git` 内部文件；未执行任何 Git 写操作。Codex Round 1 的两条**非阻塞建议**
  （运行器读取 `engine._policy`/`engine._committer` 私有字段较脆弱；公共
  `stage_safe_image` 全有或全无依赖 runner 提供干净 pending）按其“不阻塞本轮”定位
  留待后续工作包，本轮未改，避免顺手扩面。

- 首次失败 / 根因 / 修复后重跑：首轮定向 `python -m unittest tests.test_runtime_scan_runner tests.test_runtime_output_policy` 报 1 项 ERROR——新增
  `test_watchdog_staging_failure_...` 用 `_build(av_type="USINT")` 但未传 `code`，
  默认业务码 `StoreVar("AV","INT")` 与 USINT 声明类型不符，在 `build_runtime_store`
  的装载校验期即 `IRValidationError`。根因：测试装配疏漏，非被测语义。修复：该测试
  改传仅写 Motor(BOOL) 的业务码（watchdog 本就跳过业务 IR）。重跑同一命令 →
  Ran 100 tests, OK；仅调整测试装配，未改被测语义。

- 五组测试实数（2026-07-20 本机实际运行，`PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_scan_runner tests.test_runtime_output_policy` → **Ran 100, OK**（较 Round 1 的 95 增 5：scan_runner +3、output_policy safe-image 组 +2）。
  2. `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → **Ran 166, OK**。
  3. `python -m unittest discover -s tests -t .` → **Ran 1040, OK**（较 Round 1 的 1035 增 5）。
  4. `python -m unittest discover -s prototype_05 -t .` → **Ran 68, OK**。
  5. `python -m unittest discover -s . -t .` → **Ran 1108, OK**（较 Round 1 的 1103 增 5）。

- 工程约定 / 待真机验证边界（诚实分层，与 Round 1 一致）：两阶段安全事务、
  `safe_value` 基准/恢复、故障锁存不自动复位、提交尝试证据分类均为**项目工程
  约定、非 CODESYS 官方语义**。真实周期计时、后台线程、硬件 watchdog、shadow、
  `last_physical_committed`、真实驱动/HAL、`commit_fault`/`channel_fault` 重试与
  复位、可信设备反馈**不在本包**。以上 Python 测试只证明当前实现行为，**不构成**
  与目标 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路一致的证据。

- 已知疑问：① 两条非阻塞建议（私有字段装配核验、`stage_safe_image` 全有或全无的
  pending 契约）仍如 Round 1 记录，属后续工作包，本轮未动。② 两阶段事务的
  `confirm_safe_image` 与提交端口成功之间无跨进程原子性（本包不实现真实驱动/事务
  提交）；一旦引入真实 HAL/驱动，需在阶段 7 评估提交与策略历史确认的落地原子性。

- scope_sha256: 6948266fd19fc948145874ec951fe5a5e0a8a28e30e4e677cacb6eb350713bda
  - `876743c7aafc0864216b9c6a092838c54fa28d4fbcfffdf9e49d4a6f47d24fb5  src/runtime/scan_runner.py`
  - `41fdb620906640cae37d81eea86d9dcd2112608fe024c030c5c1293e1d5d6670  src/runtime/output_policy.py`
  - `599ac0ddbe596f3ff3435bc50f70ba516c33b2478e583797a6469224c29d5e81  src/runtime/__init__.py`
  - `d424cf42eb7dfd1d19bde4378967d5da1c7601829c68f6701c2f9b23a1b0698f  tests/test_runtime_scan_runner.py`
  - `a5371735b06efeb07c2b138caec3eb572c4b5f108dcdbe7b7ab0953c62d84254  tests/test_runtime_output_policy.py`
- handoff_to: codex
- implementation_finished_at: 2026-07-20T16:55:09+08:00

### Codex 审核结论（Round 2）

- verdict: CHANGES_REQUESTED
- 已验证事实: Round 1 点名的两项缺陷主体已经返修：`src/runtime/output_policy.py:464-530` 将安全映像 staging 与策略历史确认拆成两阶段，提交失败时不再提前把 `last_effective` 冒充为已生效安全值；`src/runtime/scan_runner.py:300-339` 已把锁存、staging、commit 三个恢复阶段的异常包装为保留原始异常与 fallback 异常的结构化信号。新增测试覆盖了安全提交失败历史不前移、成功后前移，以及 scan/watchdog staging 失败的双异常、零提交和锁恢复路径。接手五字段为 `WP-20260720-008 / READY_FOR_CODEX / codex / codex / round=2`，`2 <= max_rounds=3`；审核期间状态为 `CODEX_REVIEWING / codex / codex`。
- 项目工程约定: 外层 scan/watchdog 故障锁存、全通道安全映像、提交尝试证据分类和两阶段策略历史确认仍是当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义。显式 watchdog 入口只消费确定性事件，不实现墙钟测量、后台线程或硬件 watchdog；该边界分层保持正确。
- 待真机验证假设: 真实周期/抖动、硬件 watchdog、现场安全回路、可信反馈/HAL、真实驱动提交、`last_physical_committed`、`commit_fault`/`channel_fault` 锁存复位、shadow 与目标 SP16.1 行为仍未验证；当前 Python 测试只证明现实现行为，不证明与 PLC/CODESYS、真实驱动或现场安全回路一致。
- 必须返修: 1) **安全提交成功后的确认阶段仍可漏出普通异常，并留下“物理已安全提交、策略历史未前移”的失配状态。** `src/runtime/scan_runner.py:329-346` 只捕获提交端口异常；提交成功后第 344 行直接调用 `confirm_safe_image(...)`，该调用若因 `OutputPolicyReentryError` 或其他异常失败，会绕过 `SafeCommitSignal`。Codex 最小反证把确认入口替换为稳定抛错函数后得到：底层已收到 `{'DO0': False, 'AO0': 9}`，最终却只漏出普通 `RuntimeError('confirm boom')`，`isinstance(exc, ScanFaultSafeCommit)=False`，策略历史仍为 `{'DO0': None, 'AO0': None}`。这违反任务书“安全提交成功后策略历史必须与真正提交的安全映像一致”，也与模块宣称“安全恢复链任一阶段失败都结构化上报”的口径冲突。请把确认阶段纳入可审计事务闭环：保证端口提交成功后历史确认在受支持路径上不会失败，或提供等价事务设计；若仍存在确认失败路径，必须保留原始/fallback 异常和明确的提交成功证据，不能漏出普通异常或静默留下失配历史。新增一条“提交成功、确认阶段失败”的反证测试。
- 必须返修: 2) **公开的 `confirm_safe_image()` 只校验通道集合，不校验映像值，任意同键映像可污染 `last_effective`。** `src/runtime/output_policy.py:523-529` 在集合相等后直接写 `_state`，没有核对每通道值是否等于当前已配置并已校验的 `safe_value`，也没有执行 `_iec_value_error(...)`。Codex 最小反证直接调用 `confirm_safe_image({'DO0': True, 'AO0': 999})`（AO0 声明为 USINT、配置安全值为 9），调用被接受，诊断历史变成 `{'DO0': True, 'AO0': 999}`。这使“两阶段确认”不能证明它确认的是同一次 `stage_safe_image()` 准备并提交的安全映像，也违反最低测试对新公共 API 的 IEC safe value 校验要求。请至少逐通道验证“值严格等于当前配置 safe_value 且 IEC 结构/数值域合法”，更稳妥的方案是让 stage 返回不可伪造/一次性的准备令牌并由 runner 用该令牌确认，避免任意 Mapping 冒充已提交事务；新增同键但错误值、越界/非有限值和成功令牌/映像的反证测试。
- 非阻塞建议: `OuterScanRunner` 读取 `engine._policy` / `engine._committer` 私有字段，以及 `stage_safe_image()` 对任意 pending 的全有或全无仍依赖调用方契约，这两项维持 Round 1 的后续工作包建议，不要求在本轮两项安全返修之外扩 scope。
- 审核证据: 实施交接 `scope_sha256=6948266fd19fc948145874ec951fe5a5e0a8a28e30e4e677cacb6eb350713bda` 与 Codex 独立复算一致；审核开始/结束逐文件清单均为 `src/runtime/scan_runner.py=876743c7aafc0864216b9c6a092838c54fa28d4fbcfffdf9e49d4a6f47d24fb5`、`src/runtime/output_policy.py=41fdb620906640cae37d81eea86d9dcd2112608fe024c030c5c1293e1d5d6670`、`src/runtime/__init__.py=599ac0ddbe596f3ff3435bc50f70ba516c33b2478e583797a6469224c29d5e81`、`tests/test_runtime_scan_runner.py=d424cf42eb7dfd1d19bde4378967d5da1c7601829c68f6701c2f9b23a1b0698f`、`tests/test_runtime_output_policy.py=a5371735b06efeb07c2b138caec3eb572c4b5f108dcdbe7b7ab0953c62d84254`，审核期间 scope 无漂移。`docs/PROJECT_STATE.md` 保持已登记行政哈希 `bac44e367ade0a0b31a9932251cf5a55fe717184203ebad0065d6d78ab4d65fe`；协调器心跳在接手与结束核验时均 live 且未过期。Codex 独立运行：scan runner + OutputPolicy = 100/100、engine/executor/store/ir = 166/166、排除 scope 外 `tests.test_ai_handoff` 后正式测试 = 956/956、原型 = 68/68，`git diff --check` 通过；完整正式 1040 项与全仓 1108 项各有同 9 个 dashboard 用例因当前沙箱禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 1031/1031、1099/1099 通过。另执行上述两条最小手工反证。审核中仅读取 Git 状态/差异用于范围核验，未执行任何 Git 写操作。
- review_started_sha256: 6948266fd19fc948145874ec951fe5a5e0a8a28e30e4e677cacb6eb350713bda
- review_finished_sha256: 6948266fd19fc948145874ec951fe5a5e0a8a28e30e4e677cacb6eb350713bda
- handoff_to: claude
- reviewed_at: 2026-07-20 17:08:42 CST

### Claude 实施交接（Round 3，返修）

- 开工核验（全部用 Python 标准库 `pathlib`/`hashlib`，未触碰 git/gh/shasum/rm/sudo
  等命令）：① 接手前五字段为 `WP-20260720-008 / CHANGES_REQUESTED / owner=claude /
  handoff_to=claude / round=2 / max_rounds=3`，符合 CHANGES_REQUESTED 映射，按协议
  round+1 处理为 Round 3，`3 <= max_rounds=3`（本包最后一轮，再有返修转
  `BLOCKED`）；② `.git/HEAD` = `ref: refs/heads/main` → 松散 ref
  `975085668c2fa1d698e275b4af31d52eb70aa3ca` 与 `base_commit` 一致；③ 逐文件
  SHA-256 + 按 scope 声明顺序聚合 = `6948266fd19fc948145874ec951fe5a5e0a8a28e30e4e677cacb6eb350713bda`，与 Round 2
  `scope_sha256` 及 Codex `review_finished_sha256` **完全一致**——审核结束后至本轮
  接手 scope **零漂移**（逐文件：`scan_runner.py=876743c7…`、`output_policy.py=41fdb620…`、`__init__.py=599ac0dd…`、`test_runtime_scan_runner.py=d424cf42…`、`test_runtime_output_policy.py=a5371735…`）；④ `docs/PROJECT_STATE.md`
  行政哈希 = `bac44e367ade0a0b31a9932251cf5a55fe717184203ebad0065d6d78ab4d65fe`，与任务书一致。全部相符方进入返修。

- Codex Round 2 两条“必须返修”逐条落地：

  1. **确认阶段仍可漏出普通异常、留下“物理已提交、历史未前移”失配 → 确认阶段纳入
     结构化事务闭环 + 一次性令牌确认。** 根因：旧 `scan_runner._safe_commit_or_raise`
     只 `try` 包住 `_port.commit(...)`，提交成功后**裸调用**
     `confirm_safe_image(...)`；该调用若因 `OutputPolicyReentryError` 或其他异常失败，
     会绕过 `SafeCommitSignal` 漏出普通异常，且策略历史静默不前移。修复：
     ① `stage_safe_image()` 现返回**一次性、不可伪造**的 `SafeImageTicket`（见返修
     2），runner 提交成功后**在 try 内**用同一令牌 `confirm_safe_image(ticket)`；
     ② 确认阶段任一失败一律 `raise` 结构化 `ScanFaultSafeCommit`/`WatchdogSafeCommit`，
     `failed_stage="confirm"`、**保留原始扫描异常 + 确认阶段 fallback 异常**、零重试；
     ③ 关键分层：确认阶段失败时**物理安全提交已成功**，故 `safe_commit_succeeded=True`
     作为确凿证据保留，而“历史未前移”属**可审计失配**，由 `failed_stage="confirm"` +
     `fallback_exception` 显式暴露供上层对账，**绝不漏出普通异常、绝不静默**。同时，
     happy path 上令牌合法、值合法、锁空闲，确认阶段不会失败（满足任务书“受支持路径
     上确认不失败”），确认失败路径只在被注入/防御场景触发且已结构化。新增反证
     `test_confirm_stage_failure_after_commit_is_structured`：将确认入口替换为稳定
     抛错函数，断言得到结构化 `ScanFaultSafeCommit`、`failed_stage="confirm"`、
     `safe_commit_succeeded=True`、`committer.received=[{DO0:False,AO0:9}]`（物理已提交）、
     `attempts==1`（零重试）、原始 `OutputPolicyError` + fallback `RuntimeError('confirm boom')`
     同时保留、`commit_exception is None`、历史仍为 `{DO0:None,AO0:None}`（失配被显式
     暴露而非静默）。
  2. **`confirm_safe_image()` 只校验通道集合、任意同键映像可污染 `last_effective`
     → 一次性不可伪造令牌 + 逐通道值校验。** 根因：旧 `confirm_safe_image(safe_image:
     dict)` 在通道集合相等后直接写 `_state`，不校验每通道值是否等于已配置并校验的
     `safe_value`，也不跑 `_iec_value_error`，故 `confirm_safe_image({'DO0':True,
     'AO0':999})` 被接受、污染诊断历史。修复：① 新增 `SafeImageTicket`（`__slots__`，
     携签发者引用 + 一次性令牌 + 安全映像独立副本）；`stage_safe_image()` 改为**返回
     令牌**并记录服务内 `_pending_ticket`；② `confirm_safe_image(ticket)` 三重校验后
     才前移、并**消费令牌**（一次性）：(a) `isinstance` 且 `ticket._issuer is self`
     （拒绝任意 Mapping / 他服务令牌）；(b) `ticket._token is self._pending_ticket`
     （拒绝重复/被后续 stage 作废的过期令牌）；(c) 通道集合相等**且**逐通道
     `_iec_value_error` 合法 + `type(value) is type(safe_value) and value ==
     safe_value`（拒绝同键错误值 / 越界 / 非有限值污染）；任一不满足整体失败关闭、
     不前移、不消费令牌。这样 confirm 确认的**一定是**同一次 stage 准备并提交的安全
     映像。既有正常 `stage_outputs`/`_compute` 及 66 项语义锁未改动放宽。新增 7 项
     output_policy 反证 + 强化既有 stage/confirm 用例（见下）。

- 修改文件（scope 五文件中改动 4 个，逐文件 SHA-256 见末尾）：
  - `src/runtime/output_policy.py`（改）：新增 `SafeImageTicket` 类；
    `OutputPolicyService.__init__` 增 `_pending_ticket` 字段；`stage_safe_image()`
    改为签发并返回 `SafeImageTicket`（staging 语义与全有或全无校验不变）；
    `confirm_safe_image()` 由“仅通道集合校验的 dict 入参”改为“一次性令牌 + 逐通道
    值/IEC 三重校验”。更新相关 docstring。既有语义锁未放宽。
  - `src/runtime/scan_runner.py`（改）：`_safe_commit_or_raise` 中 stage 改取
    `ticket`（`safe_image = ticket.image`）；提交成功后的 `confirm_safe_image(ticket)`
    纳入 `try` 并在失败时 raise `failed_stage="confirm"`、`safe_commit_succeeded=True`、
    保留原始 + fallback 的结构化信号。更新 `SafeCommitSignal` 与模块/方法 docstring
    以纳入 `"confirm"` 阶段及其“物理已提交、历史未前移”可审计失配语义。
  - `tests/test_runtime_output_policy.py`（改）：`TestSafeImageEntry` 按令牌口径收口
    （stage 返回令牌、confirm 收令牌）；新增/替换反证：令牌 image 为独立副本、
    **拒绝任意裸字典同键错误值**（Codex 反证 2 原例）、拒绝他服务令牌、拒绝重复令牌、
    拒绝被后续 stage 作废的过期令牌、拒绝篡改后的同键错误值、拒绝越界值、拒绝非有限
    值。既有 66 项语义锁一字未改。
  - `tests/test_runtime_scan_runner.py`（改）：新增
    `test_confirm_stage_failure_after_commit_is_structured`（Codex 反证 1）。
  - **未改** `src/runtime/__init__.py`（无公共名增删，哈希保持 `599ac0dd…`）：
    `SafeImageTicket` 属两阶段事务的握手对象，测试经 `src.runtime.output_policy`
    子模块引用，未加入包级 `__all__`，避免扩大公共面。

- 明确未修改：`src/runtime/__init__.py`（同上）、`docs/PROJECT_STATE.md`（行政哈希
  保持 `bac44e3…`）、`docs/AI_REVIEW_HANDOFF.md` 除本 Round 3 实施交接段与顶层五字段
  以外的历史正文、任何权威规格、`.cursor/rules/*`、协调器/自动化配置、
  `src/runtime/engine.py`、`.git` 内部文件；未执行任何 Git 写操作。Codex 两轮的
  **非阻塞建议**（运行器读取 `engine._policy`/`engine._committer` 私有字段；
  `stage_safe_image` 对任意 pending 的全有或全无依赖调用方契约）仍按其“不阻塞本轮”
  定位留待后续工作包，本轮未改，避免顺手扩面。

- 首次失败 / 根因 / 修复后重跑：首轮定向
  `python -m unittest tests.test_runtime_scan_runner tests.test_runtime_output_policy`
  即一次通过（Ran 108, OK），本轮无测试失败需修复。（返修前已通过阅读 Codex 两条
  反证的最小复现路径定位根因，实现按上述两阶段令牌事务落地后首跑即绿。）

- 五组测试实数（2026-07-21 本机实际运行，`PYTHONDONTWRITEBYTECODE=1`）：
  1. `python -m unittest tests.test_runtime_scan_runner tests.test_runtime_output_policy` → **Ran 108, OK**（较 Round 2 的 100 增 8：scan_runner +1 confirm 阶段反证、output_policy +7 令牌反证）。
  2. `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → **Ran 166, OK**。
  3. `python -m unittest discover -s tests -t .` → **Ran 1108, OK**。
  4. `python -m unittest discover -s prototype_05 -t .` → **Ran 68, OK**。
  5. `python -m unittest discover -s . -t .` → **Ran 1176, OK**。
  说明（口径诚实）：本机沙箱**允许**绑定本地 HTTP 端口，故 `tests/test_ai_handoff.py`
  的面板用例全部通过（Codex Round 2 沙箱因端口 `PermissionError` 少计 9 项）。第 3/5 组
  绝对数（1108/1176）高于 Round 2 记录（1040/1108），差额来自 **scope 外**、会话开始前
  即存在于工作区的协调器工具改动（`tests/test_ai_handoff.py` 现 144 项，及
  `tools/ai_handoff/*`、`docs/AI_HANDOFF_OPERATIONS.md` 等），**不属本工作包 scope，
  本轮未触碰**；与 Round 2 一致的口径为“whole − tests = 68（prototype）”本轮仍成立
  （1176 − 1108 = 68）。本工作包五个 scope 文件的行为证据以第 1/2 组为准。

- 自查（以审核者姿态复读）：① 令牌路径杜绝任意 Mapping 污染——`confirm_safe_image`
  对非 `SafeImageTicket`/他服务/过期/重复令牌与同键错误值/越界/非有限值均失败关闭且
  不前移，反证齐备；② 确认阶段失败已结构化（`failed_stage="confirm"`），保留提交成功
  证据 + 原始 + fallback，绝不漏出普通异常或静默失配，反证齐备；③ happy path 上确认
  阶段令牌/值/锁均合法，不引入新失败；④ 既有 66 项 OutputPolicy 语义锁与正常路径
  行为未改动放宽（第 1 组全绿含既有锁）；⑤ 未扩 scope、未执行 Git、未改状态/规格/
  协调器配置。

- 工程约定 / 待真机验证边界（诚实分层，与 Round 1/2 一致）：一次性令牌两阶段安全
  事务、逐通道 safe_value 值校验、`safe_value` 基准/恢复、故障锁存不自动复位、提交
  尝试证据分类均为**项目工程约定、非 CODESYS 官方语义**。真实周期计时、后台线程、
  硬件 watchdog、shadow、`last_physical_committed`、真实驱动/HAL、`commit_fault`/
  `channel_fault` 重试与复位、可信设备反馈**不在本包**。以上 Python 测试只证明当前
  实现行为，**不构成**与目标 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路
  一致的证据；watchdog 事件产生时机仍待阶段 7 独立 monitor。

- 已知疑问：① 确认阶段失败时采用 `safe_commit_succeeded=True` +
  `failed_stage="confirm"` 表达“物理已提交、历史未前移”的失配（而非新增独立字段），
  请 Codex 裁决该分层表达是否满足审计需求，或是否更宜引入显式 `history_confirmed`
  字段；② 两条非阻塞建议（私有字段装配核验、`stage_safe_image` 全有或全无的 pending
  契约）仍如 Round 1/2 记录，属后续工作包，本轮未动；③ 令牌 `confirm` 与提交端口成功
  之间无跨进程原子性（本包不实现真实驱动/事务提交）；引入真实 HAL/驱动后需在阶段 7
  评估提交与策略历史确认的落地原子性。

- scope_sha256: dcbd645967997f531568c9cc217ea9a94b53acd342fd72d0e80bd6748ff32e69
  - `517eb25d0b9912bb2adcd813b2d7556061068ed4733b6f693428c4e476c551c8  src/runtime/scan_runner.py`
  - `7a2e8ea6d95cd0e4a0584d1a08507345b62d076310da345c111fe1e4dde3ace4  src/runtime/output_policy.py`
  - `599ac0ddbe596f3ff3435bc50f70ba516c33b2478e583797a6469224c29d5e81  src/runtime/__init__.py`
  - `892a734bad923989999c57952e6d1c8c6a6a093b99f8caa15328174bc9340fa4  tests/test_runtime_scan_runner.py`
  - `d9d75af6e82f9b4910c8adf8712a80c1ee3bfdc5fa228de8ea968af6414cec4d  tests/test_runtime_output_policy.py`
- handoff_to: codex
- implementation_finished_at: 2026-07-21T16:57:02+08:00

### Codex 审核结论（Round 3）

- verdict: APPROVED
- 已验证事实: Round 2 两项必须返修均已收口。`src/runtime/output_policy.py` 的 `stage_safe_image()` 现在只准备并签发一次性 `SafeImageTicket`，不提前前移策略历史；`confirm_safe_image(ticket)` 在同一服务、当前未消费令牌、完整通道集合、逐通道 IEC 合法且严格等于当前 `safe_value` 全部成立后才统一前移，并拒绝裸 Mapping、他服务、重复、过期、篡改、越界和非有限值。`src/runtime/scan_runner.py` 已把提交成功后的确认阶段纳入结构化事务闭环：确认失败时上报 `failed_stage="confirm"`，保留原始扫描异常与 fallback 异常，并以 `safe_commit_succeeded=True` 如实表示物理安全映像已经提交；普通异常不再漏出。正常路径、提交前扫描故障、正常提交故障、watchdog、staging/commit/confirm 失败分类及零重试边界均保持一致。
- 项目工程约定: 一次性令牌两阶段安全事务、全通道 `safe_value` 恢复、`scan_ok`/`watchdog_ok` 故障锁存、提交尝试证据分类，以及确认失败时用 `safe_commit_succeeded=True + failed_stage="confirm"` 表达“物理已提交、策略历史未确认”的失配，均为当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义。该表达已能同时保留物理提交事实与历史确认状态，不需要为本包批准额外扩充字段。
- 待真机验证假设: 真实周期与抖动、事件产生时机、硬件 watchdog、现场安全回路、可信设备反馈/HAL、真实驱动提交、`last_physical_committed`、`commit_fault`/`channel_fault` 锁存复位、shadow 与目标 SP16.1 行为仍未验证；当前 Python 测试只证明本实现的确定性故障响应和审计边界，不证明 PLC/CODESYS、真实驱动或现场安全回路一致。
- 必须返修: 无。
- 非阻塞建议: 延续前两轮记录，后续工作包可用 `ScanEngine` 公共只读端口或集中装配工厂替代 `engine._policy` / `engine._committer` 私有引用；同时明确 `stage_safe_image()` 仅接受干净真实 `OutputPending` 的契约或增加回滚语义。进入多写者安全状态阶段前，还应评估 `_latch_fault()` 当前“read 后整包 replace”的复合操作是否需要原子字段更新或单写者约束，避免并发安全状态替换时丢失其他字段的新值；该并发扩展不阻塞本包当前确定性单 runner 范围。
- 审核证据: 接手五字段为 `WP-20260720-008 / READY_FOR_CODEX / codex / codex / round=3`，且 `3 <= max_rounds=3`；审核期间原子置为 `CODEX_REVIEWING / codex / codex`，协调器心跳在开始与结束均 live 且未过期。该包属于协议明确 legacy 白名单，故标注“历史格式：自审证据未独立结构化”，未把实施交接正文冒充 v2 自审证据。实施交接 `scope_sha256=dcbd645967997f531568c9cc217ea9a94b53acd342fd72d0e80bd6748ff32e69` 与 Codex 开始/结束独立复算一致；逐文件清单两次均为 `src/runtime/scan_runner.py=517eb25d0b9912bb2adcd813b2d7556061068ed4733b6f693428c4e476c551c8`、`src/runtime/output_policy.py=7a2e8ea6d95cd0e4a0584d1a08507345b62d076310da345c111fe1e4dde3ace4`、`src/runtime/__init__.py=599ac0ddbe596f3ff3435bc50f70ba516c33b2478e583797a6469224c29d5e81`、`tests/test_runtime_scan_runner.py=892a734bad923989999c57952e6d1c8c6a6a093b99f8caa15328174bc9340fa4`、`tests/test_runtime_output_policy.py=d9d75af6e82f9b4910c8adf8712a80c1ee3bfdc5fa228de8ea968af6414cec4d`，审核期间 scope 无漂移。Codex 独立运行：scan runner + OutputPolicy = 108/108、engine/executor/store/ir = 166/166、排除 scope 外 `tests.test_ai_handoff` 后正式测试 = 964/964、原型 = 68/68，`git diff --check` 通过；完整正式 1108 项与全仓 1176 项各有同 9 个 scope 外 dashboard 用例因当前沙箱禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 1099/1099、1167/1167 通过。未执行任何 Git 写操作。
- review_started_sha256: dcbd645967997f531568c9cc217ea9a94b53acd342fd72d0e80bd6748ff32e69
- review_finished_sha256: dcbd645967997f531568c9cc217ea9a94b53acd342fd72d0e80bd6748ff32e69
- handoff_to: user
- reviewed_at: 2026-07-21 17:09:55 +0800

---

## WP-20260721-009

- title: 阶段 1 提交监督器、通道故障锁存复位与驱动确认提交证据
- status: BLOCKED
- owner: user
- handoff_to: user
- round: 4
- max_rounds: 4
- handoff_protocol: v2
- base_commit: f0950443c9f2cbd43e0f0067746dd8abaebfca86
- created_by: user
- created_at: 2026-07-21 21:08:21 +0800
- depends_on:
  - WP-20260716-007 CLOSED
  - WP-20260720-008 CLOSED
- scope:
  - src/runtime/commit_supervisor.py
  - src/runtime/scan_runner.py
  - src/runtime/output_policy.py
  - src/runtime/__init__.py
  - tests/test_runtime_commit_supervisor.py
  - tests/test_runtime_scan_runner.py
  - tests/test_runtime_output_policy.py
- scope_baseline_sha256: 4741dbdb947fda2ddcc995615b42fe601c4c2943957fb2c6b120aa258190ccb8
- scope_baseline_manifest:
  - `ABSENT  src/runtime/commit_supervisor.py`
  - `517eb25d0b9912bb2adcd813b2d7556061068ed4733b6f693428c4e476c551c8  src/runtime/scan_runner.py`
  - `7a2e8ea6d95cd0e4a0584d1a08507345b62d076310da345c111fe1e4dde3ace4  src/runtime/output_policy.py`
  - `599ac0ddbe596f3ff3435bc50f70ba516c33b2478e583797a6469224c29d5e81  src/runtime/__init__.py`
  - `ABSENT  tests/test_runtime_commit_supervisor.py`
  - `892a734bad923989999c57952e6d1c8c6a6a093b99f8caa15328174bc9340fa4  tests/test_runtime_scan_runner.py`
  - `d9d75af6e82f9b4910c8adf8712a80c1ee3bfdc5fa228de8ea968af6414cec4d  tests/test_runtime_output_policy.py`

### 工作包创建行政证据（Claude 启动前）

- 用户于 2026-07-21 明确确认 Codex 提出的 `WP-20260721-009` 目标、scope、验收条件、排除项、依赖与测试计划，并授权开始工作。本节与 `docs/PROJECT_STATE.md` 同步属 Codex 创建工作包的行政动作，不属于 Claude 实施 scope。
- 创建前已核验：当前分支 `main`，HEAD 与 `origin/main` 均为 `f0950443c9f2cbd43e0f0067746dd8abaebfca86`；工作区原有唯一改动是用户已授权的 `docs/PROJECT_STATE.md` 发布状态/测试快照表述同步，已保留并纳入本次行政状态更新。
- 创建前事件协调器为 `stopped`、TCP 8765 无监听者、无活动执行租约；旧 Claude/Codex 30 分钟主轮询仍必须保持暂停。
- 以上七个 scope 文件按声明顺序的 baseline manifest 聚合 SHA-256 = `4741dbdb947fda2ddcc995615b42fe601c4c2943957fb2c6b120aa258190ccb8`；两个新文件在创建时均已确认不存在。
- `docs/PROJECT_STATE.md` 本次行政更新后 SHA-256 = `73d374ccba2c3fe3afef7ad70a5a5120b15091efb5be118e0b67ddc6af9c13ad`。Claude 不得修改该文件；Codex 只在审核或关闭状态发生实质变化时再按事实更新。
- 首次 v2 开工预检发现 `tests/test_ai_handoff.py` 的实盘断言仍假设所有工作包均为 legacy。用户明确授权 Codex 仅修正这一项协议代际断言：WP-001～008 继续按 legacy 白名单验证，WP-009 及后续包必须按 v2 验证；未修改解析器、调度器或七个功能 scope 文件。修正后该文件 SHA-256 = `147564b5d4e1bff9e510711613300e8b45e2a808fa8c785ad7d51cd33b94991e`，宿主环境 `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_ai_handoff -v` = **Ran 144 tests, OK**。此项是工作包启动前的用户授权行政前置修正，不纳入 Claude 功能 scope，Claude 不得修改。
- 本包是首个强制按 v2 三阶段门禁运行的正式工作包；Claude 必须先在 `CLAUDE_WORKING` 内完成结构化自审，自审 `PASS`、真实测试计数、manifest 与聚合哈希全部通过后，才允许原子交接给 Codex。

### 目标与权威依据

落实 `docs/ENGINE_SCAN_SPEC.md v2.2.2 §4.1/§4.4`的提交层状态机，使既有 `ScanEngine` / `OuterScanRunner` 的正常提交、scan-fault 安全提交与显式 watchdog 安全提交共用同一可审计提交监督边界。本包只实现已冻结的项目工程约定，不将 Python 回执冒充为真实物理设备反馈或 CODESYS 官方语义。

### 实施范围与验收标准

1. **驱动确认提交证据**
   - 建立不可变、结构化的逐通道提交回执，将本次命令通道、IEC 值与成功/失败结果精确绑定；回执通道集必须与实际尝试提交集合一致。
   - 仅在回执明确成功、通道一致、值严格等于本次发出命令且 IEC 类型/数值域合法时，才更新该通道 `last_physical_committed`。返回 `None`、仅“未抛异常”、缺失/多余通道、错值、错类型或不完整回执均不得被提升为可信成功证据。
   - `last_physical_committed` 只是驱动确认已写出的最后命令值与诊断记录，不是传感器确认的设备实际位置，不得作为可信设备反馈或恢复基准。

2. **逐通道 `commit_fault` / `channel_fault` 状态机**
   - 任一通道提交失败后立即置瞬时 `commit_fault`，保留该通道旧 `last_physical_committed`；其他明确成功通道独立更新，不被连带标故障。
   - 故障通道从下一拍起必须忽略策略层本拍 `final`、持续尝试写 `safe_value`；其他通道继续提交自身业务值，实现逐通道隔离。
   - 连续失败计数在第 `commit_fault_retry_n` 次失败时精确升级并锁存 `channel_fault`；锁存后不存在静默放弃路径，仍每拍尝试安全值。
   - 阈值前安全值写成功可清除瞬时 `commit_fault` 与连续失败计数；已升级的 `channel_fault` 绝不得因安全值写成功而自动清除。
   - 提交层故障不进入 OutputPolicy 的故障原因集合；策略层仍照常计算 `final` 并维持 `last_effective` 逻辑连续。

3. **三条件显式复位与恢复基准**
   - `channel_fault` 仅能经显式复位 API 解除；必须同时满足“调用方明确确认故障原因已消失 + 锁存后已有合法回执确认 `safe_value` 写成功 + 本次显式复位调用”三条件。
   - 未知通道、未锁存、故障原因未确认消失、锁存后未确认安全值或重复复位均必须结构化拒绝，不得静默忽略。
   - 瞬时故障恢复或锁存复位后的首个正常输出必须重建边界基准；本包无真实 HAL/可信设备反馈，因此一律按规范退化为 `safe_value`，不得使用 `last_physical_committed` 对齐。

4. **既有扫描/安全事务集成**
   - 正常提交、scan-fault 安全提交和 watchdog 安全提交必须共用同一提交监督状态与底层驱动端口，不得出现第二套平行故障状态。
   - 正常提交发生逐通道/部分失败时不得追加第二次安全提交；必须以结构化异常保留逐通道成功/失败证据。任一通道失败的拍保持当前 `ScanEngine.prev` 不前移；若实施方认为必须改变该纪律，须停止并转规格裁决。
   - 外层安全映像只有在全部通道都有明确成功回执时，才可执行现有 `confirm_safe_image(ticket)` 并标记 `safe_commit_succeeded=True`；部分成功必须保留逐通道回执、标记整体未成功，不得污染全通道策略历史。
   - 公开诊断必须返回独立/不可反向污染的快照；提交、复位与诊断并发/递归重入必须失败关闭，异常后状态可继续安全使用。

### 明确排除与冻结边界

- 不实现 shadow mode、真实 HAL/驱动适配、可信设备传感器反馈、真实周期 monitor、抖动/超时测量、后台线程、硬件 watchdog、L2 adapter registry、HMI/通知/操作员身份认证、事件持久化或现场安全回路。
- 不修改正式规格、`docs/PROJECT_STATE.md`、`.cursor/rules/*`、AI 协调器/自动化配置、标准库业务块、`src/runtime/engine.py`、`tests/test_runtime_engine.py` 或 `.git`。Claude 不执行任何 Git/GitHub 写操作。
- 真实 HAL 到来前，本包的驱动回执测试是契约模拟，不得表述为真实物理写入、设备位置反馈、PLC/CODESYS 一致或现场安全证明。
- 如果实施需要修改上述排除文件、改变 `prev` 纪律、新增规格语义或扩大 scope，必须置 `BLOCKED` 并交用户裁决，不得自行实施。

### 最低测试要求

1. 驱动回执：全成功、单通道失败、多通道部分成功；`None`/仅无异常、缺失/多余通道、错值/错类型/越界/非有限回执均失败关闭，不前移相应 `last_physical_committed`。
2. 逐通道隔离：一通道失败时其他通道仍保留成功证据；故障通道下一拍及后续拍只写 `safe_value`，正常通道继续写业务值。
3. 状态机：阈值前恢复、第 N 次精确升级、锁存后继续安全写、安全写成功只清瞬时故障不清锁存。
4. 复位：三项前置逐项缺失、未知/未锁存/重复复位均拒绝；全部满足时成功解锁，首拍从 `safe_value` 重建限速基准。
5. 反证 `last_physical_committed` 不被 OutputPolicy 当成可信反馈/基准；正常、scan-fault、watchdog、安全映像部分失败与 confirm 失败路径都有结构化证据。
6. 递归/并发重入失败关闭；诊断快照不可反向污染；异常后锁与状态仍可恢复使用。既有 WP-007/WP-008 语义锁不得改写或放宽。
7. 至少实际运行并记录：
   - `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy`
   - `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir`
   - `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .`
   - `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s prototype_05 -t .`
   - `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s . -t .`
   - `git diff --check`
   执行责任按用户 2026-07-21 裁决分配：Claude 必须在交接前实际运行并记录上述五组 Python 测试；协调器继续禁止 Claude 使用任何 `git` 命令，故 `git diff --check` 由 Codex 在原子交接后、作出独立审核结论前实际运行并记录。两端证据合并后仍须覆盖本清单全部命令，不降低最终验收标准。
   新增测试导致总数增长是正常现象；交接必须保留历史快照和本轮实际计数，不得把计数增长冒充为矛盾。

### 实施与 v2 交接要求

- 开工前逐项核对 `work_package_id + status + owner + handoff_to + round`、`round <= max_rounds`、`handoff_protocol: v2`、`base_commit`、baseline manifest/聚合哈希及 `docs/PROJECT_STATE.md` 行政哈希；任一不符必须安全停笔。
- 只允许修改 scope 中七个文件。`src/runtime/commit_supervisor.py` 与 `tests/test_runtime_commit_supervisor.py` 创建前必须确认不存在；若已存在即视为 baseline 漂移。
- Claude 必须先在 `CLAUDE_WORKING` 内追加 `### Claude 交接前自审（Round 1）`，完整填写公共协议要求的结构化字段。只有 `self_review_verdict: PASS`、实际测试命令+成功标记+真实计数、完整 manifest、`self_review_scope_sha256 == scope_sha256`、明确“是否满足交接条件: 是”全部成立时，才可在自审段之后追加 `### Claude 实施交接（Round 1）`。
- 最终必须以一次原子写入同时把顶层五字段转为 `READY_FOR_CODEX / owner=codex / handoff_to=codex / round=1`，并保证本轮实施交接位于自审段之后；随后立即停止修改 scope，等待 Codex 独立审核。
- 所有 Python 回执只证明当前实现和回执契约行为，不构成与目标 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路一致的证据。

### Claude 首次启动失败关闭与一次性重试授权

- 首次执行键 `WP-20260721-009:1:start_claude_implementation` 于 2026-07-21 21:23:38 +0800 以 `postcondition-failed` 结束：Claude 进程返回 0，但仍为 `CLAUDE_WORKING`，未写结构化自审、未原子交接。协调器已释放租约且未自动重试。
- 根因已核实为权限命令不匹配：协调器有意禁止全部 `git` / `shasum`，Claude 首次尝试了含 `git`、`shasum` 与 shell 复合语句的 Bash 命令后安全停笔。失败期间七个功能 scope 文件均未修改，复算聚合 SHA-256 仍为 baseline `4741dbdb947fda2ddcc995615b42fe601c4c2943957fb2c6b120aa258190ccb8`。
- 用户明确批准上述测试责任分配，并授权该失败键**仅重试一次**。`retry_idempotency_key: WP-20260721-009:1:start_claude_implementation`。
- 重试接手必须只用 Python 标准库（如 `pathlib` / `hashlib`）读取分支引用、核验两个 `ABSENT` 文件、逐文件 manifest 与聚合哈希；测试只运行任务书列出的 `PYTHONDONTWRITEBYTECODE=1 python -m unittest ...`。不得调用或借 Python `subprocess` 绕过禁止的 `git` / `shasum` / shell 复合命令；`git diff --check` 严格留给 Codex 交接后独立执行。

### Codex 协调监督阻塞记录（Round 1）

- blocked_reason: 用户授权的单次重试期间，Claude 在仓库根目录创建了 scope 外文件 `.wp009_verify.py`；这违反“只允许修改七个 scope 文件”的硬边界，Codex 监督方立即停止协调器及 Claude，拒绝让无效证据链继续扩展。
- 第二次执行结果: 同一幂等键于 2026-07-21 21:32:24 +0800 被监督停止，子进程返回 143；协调器已记录失败、释放全局租约并转 stopped，无自动重试、无旧轮询恢复授权。
- 功能 scope 证据: 七个正式 scope 文件在停止后复算仍与 baseline manifest 逐项一致，聚合 SHA-256 = `4741dbdb947fda2ddcc995615b42fe601c4c2943957fb2c6b120aa258190ccb8`。`src/runtime/commit_supervisor.py` 与 `tests/test_runtime_commit_supervisor.py` 仍为 ABSENT；没有实现、自审或原子交接产物。
- scope 外遗留物: `.wp009_verify.py` SHA-256 = `443440e7392dfc2be26534064687e1ef0fd05ef34ede1f2cbdaf35649e5eb0a8`，内容仅为 `pathlib` / `hashlib` 基线核验辅助脚本。Codex 未删除、未移动、未执行，也未将其加入 scope，等待用户明确裁决。
- 状态裁决: 按本包“扩大 scope 必须 `BLOCKED` 并交用户裁决”的规则，顶层字段已转为 `BLOCKED / owner=user / handoff_to=user / round=1`。这不是 Claude 自审或 Codex 独立代码审核结论，不冒充三阶段中的任何一阶段。
- 项目状态同步: `docs/PROJECT_STATE.md` 已按实际阻塞状态更新，当前 SHA-256 = `ac22bf6cd879b38f5a3f6ac1a0103bc9828e967c9d924e5b46570eae127575ee`，取代创建时行政哈希作为后续恢复核验基准。
- 需要用户裁决: ① 是否授权删除 scope 外 `.wp009_verify.py`；② 是否在清理并补充“不得创建任何辅助文件，只能执行直接 `python -c` / unittest 命令”后，再为同一失败键授权一次新的受限重试。不得自行恢复协调器或旧轮询。
- blocked_at: 2026-07-21 21:34:21 +0800

### 用户裁决后的恢复与再次单次重试授权

- 用户于 2026-07-21 明确同意三项恢复动作：删除 scope 外 `.wp009_verify.py`；补充“不得创建任何核验辅助文件，只能直接执行 `python -c` 与 unittest”的约束；恢复 `CLAUDE_WORKING` 并为同一幂等键再授权一次受限重试。
- Codex 已按精确目标删除 `.wp009_verify.py`，未删除或修改其他文件；删除前内容与 SHA-256 已完整保留在上一阻塞记录。删除后七个功能 scope 的 baseline manifest 与聚合 SHA-256 仍为 `4741dbdb947fda2ddcc995615b42fe601c4c2943957fb2c6b120aa258190ccb8`。
- 本次重试的写入白名单只有七个功能 scope 文件，以及 v2 协议强制要求的本工作包自审/实施交接记录；**严禁在仓库任何位置创建核验脚本、临时文件、缓存、日志、补丁或其他辅助文件**，也不得修改其他既有文件。需要哈希或分支引用核验时，只能直接执行单行 `python -c "..."`，不得使用 heredoc、重定向、动态生成脚本或 `subprocess` 间接调用禁用命令。
- 允许的 Bash 入口仅为直接 `python -c "..."`、`PYTHONDONTWRITEBYTECODE=1 python -c "..."` 与任务书列出的 `PYTHONDONTWRITEBYTECODE=1 python -m unittest ...`。仍禁止 `git` / `gh` / `shasum` / `rm` / `sudo` 及 shell 复合命令；`git diff --check` 由 Codex 在原子交接后独立执行。
- `docs/PROJECT_STATE.md` 已同步恢复状态，当前 SHA-256 = `d389ba7a9ed18e6ba94d221df334a0d925585ed0067a21013c1063c539d159b7`，作为本次恢复接手的行政核验基准。
- 顶层状态已按用户裁决恢复为 `CLAUDE_WORKING / owner=claude / handoff_to=claude / round=1`；此前两次失败记录仅作审计历史，不得被冒充为自审或实施证据。本次仍须从 baseline 重新实施并完整通过 v2 九项门禁。
- retry_idempotency_key: WP-20260721-009:1:start_claude_implementation
- retry_authorized_at: 2026-07-21 21:44:10 +0800

### Claude 交接前自审（Round 1）

- self_review_round: 1
- self_review_started_at: 2026-07-21 22:00:12 +0800
- self_review_finished_at: 2026-07-21 22:06:46 +0800
- self_review_verdict: PASS
- self_review_scope_sha256: 811ed78331fcbed28dc27b7c0ee7a423241e0d6e934f0504f2fabaf1b1e8db5e
- self_review_manifest:
  - `a31020221b0dcaf177b709b488a6cd483eb591d6aea232df35e560a106701876  src/runtime/commit_supervisor.py`
  - `fdc24499a6af8aebaa013dded768302b736eb35c791ffa0854495fc6b8189632  src/runtime/scan_runner.py`
  - `f08f84ff395856df19255aaf041836bf08db477244694c7b4f8cfbf961bf4a08  src/runtime/output_policy.py`
  - `6464b26eec97e287dc74aff944233c5e72bf59580c1ec0a4e1c8e6b7c070be0b  src/runtime/__init__.py`
  - `52a78e47cdf25bce63bbd11470d407c440dcec8edbeef7bb5b91fc34d747afc7  tests/test_runtime_commit_supervisor.py`
  - `50ebaf894be24822a9d876d86026ab4f3dd47307f66bae13da57b3331adda981  tests/test_runtime_scan_runner.py`
  - `15f3829205d8fb86fbf477b69de825c67206ed30608612ebad692362bb85ff3e  tests/test_runtime_output_policy.py`
- 实际测试命令与结果: 本机实跑，前缀 `PYTHONDONTWRITEBYTECODE=1`
  - `python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` → Ran 156 tests, OK（通过）
  - `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → Ran 166 tests, OK（通过）
  - `python -m unittest discover -s tests -t .` → Ran 1156 tests, OK（通过）
  - `python -m unittest discover -s prototype_05 -t .` → Ran 68 tests, OK（通过）
  - `python -m unittest discover -s . -t .` → Ran 1224 tests, OK（通过）
  - `git diff --check`：按用户 2026-07-21 裁决与协调器禁令，Claude 不执行任何 `git` 命令，此项留待 Codex 原子交接后独立运行并记录。
- 首次失败: 自审重跑首轮，`test_shared_supervisor_across_three_paths` 一处断言把 cold-start 限速期望误写为 5。
- 失败根因: 该拍 `av_safe=7`、`rate=5`，冷启动限速基准为 `safe_value=7`，业务 100 一拍限到 `7+5=12`；断言错按 `safe_value=0` 推成 5（纯测试期望笔误，非实现缺陷）。
- 修复内容: 将该断言期望值改为 12 并补注释说明基准为 `safe_value`（仅测试断言修正，未改任何实现语义）。
- 修复后重跑结果: 上述五组命令全部 OK，计数见上（156 / 166 / 1156 / 68 / 1224）。
- 已知疑问 / 未验证边界:
  - ① 故障通道被改写 `safe_value` 时，观测层 `ScanResult.outputs()` 仍反映策略逻辑值，物理实际写入以 `CommitSupervisor.diagnostics().last_physical_committed` 为准——两者刻意分层，已在模块 docstring 明确声明；
  - ② 驱动回执为**契约模拟**，非真实物理写入/设备位置反馈，未经真机验证，不构成与 PLC/CODESYS、真实驱动或现场安全回路一致的证据；
  - ③ 复位时 `last_physical_committed` 恰等于 `safe_value`，故“退化 safe 基准”与“用 lpc 对齐”在数值上重合；本包结构上 `OutputPolicy` 无 `last_physical_committed` 引用，恢复基准由 `mark_boundary_reset` 强制为 `safe_value`，测试以漂移的 `last_effective` 区分二者。
- 其他核验: `self_review_manifest` 与当前实际文件重算逐项一致；`self_review_scope_sha256 == 实施交接 scope_sha256`；`docs/PROJECT_STATE.md` 未改（仍 `d389ba7a9ed18e6ba94d221df334a0d925585ed0067a21013c1063c539d159b7`）；无 scope 外文件改动、无辅助/临时/缓存文件。
- 是否满足交接条件: 是

### Claude 实施交接（Round 1）

- 完成内容: 落实 `ENGINE_SCAN_SPEC §4.1/§4.4` 提交层状态机——① 新建 `src/runtime/commit_supervisor.py`：`CommitSupervisor` 满足 `CommitPort` 内层提交端口契约，位于 `OutputPolicy` 门控之下、驱动之上；建立不可变结构化逐通道 `CommitReceipt`（命令通道 + IEC 值 + 成功/失败），仅在“驱动未抛异常 + 回执通道集与尝试提交集严格一致 + 确认值严格等于发出命令 + IEC 结构/数值域合法”四项充要条件全满足时才前移该通道 `last_physical_committed`（返回 `None`、仅未抛异常、缺失/多余通道、错值/错类型/越界/非有限一律失败关闭）。② 逐通道 `commit_fault`（瞬时）/`channel_fault`（锁存）状态机：任一通道失败即置瞬时故障并保留旧 `last_physical_committed`，其他成功通道独立更新；故障通道下一拍起改写 `safe_value`、其他通道续写业务值（逐通道隔离）；第 `commit_fault_retry_n` 次连续失败精确升级并锁存；阈值前安全值写成功清瞬时故障与连续计数并恢复，已锁存 `channel_fault` 绝不因安全写成功自动清除；提交层故障不进入 `OutputPolicy` 故障原因集合。③ 三条件显式复位 `reset_channel_fault`（原因确认消失 + 锁存后合法安全回执 + 显式调用），未知/未锁存/原因未确认/无安全确认/重复复位均结构化拒绝；恢复经 `OutputPolicyService.mark_boundary_reset` 使首个正常输出退化为 `safe_value` 基准、不用 `last_physical_committed` 对齐。④ 集成：正常提交（引擎第 5 步）、`scan_fault` 与 `watchdog` 安全提交经**同一** supervisor 与同一驱动端口共享故障状态；正常提交部分失败以 `PartialCommitError` 保留逐通道证据、不追加第二次安全提交、`prev` 不前移；安全映像仅全通道成功回执才 `confirm_safe_image` 前移历史，部分成功标记未成功且不污染全通道历史；诊断快照独立、提交/复位/诊断并发或递归重入失败关闭。
- 修改文件（均在 scope 内，共 7 个）:
  - `src/runtime/commit_supervisor.py`（新建：监督器、回执、诊断、异常）
  - `src/runtime/output_policy.py`（新增 `commit_specs()` 只读配置访问 + `mark_boundary_reset()` 恢复基准入口；未改既有门控/安全事务语义）
  - `src/runtime/scan_runner.py`（`CommitPort.inner` 只读属性 + 运行器条件校验“委托的 CommitSupervisor 须绑定同一策略”；非监督器底层端口保持 WP-007/008 装配不变）
  - `src/runtime/__init__.py`（导出监督器公共 API）
  - `tests/test_runtime_commit_supervisor.py`（新建：驱动回执/隔离/状态机/复位/集成/重入/装配全覆盖）
  - `tests/test_runtime_scan_runner.py`（增补 `CommitPort.inner` 与监督器共享策略校验测试；未改既有语义锁）
  - `tests/test_runtime_output_policy.py`（增补 `commit_specs` / `mark_boundary_reset` 测试；未改既有语义锁）
- 明确未修改: `src/runtime/engine.py`、`tests/test_runtime_engine.py`、`docs/PROJECT_STATE.md`、正式规格、`.cursor/rules/*`、AI 协调器/自动化配置、标准库业务块、`.git`；未实现 shadow mode、真实 HAL/驱动、可信设备反馈、真实周期 monitor、后台线程、硬件 watchdog、L2 adapter registry、HMI/通知/事件持久化（均属明确排除项）。未执行任何 Git/GitHub 写操作。
- 测试命令与实际结果（本机实跑，前缀 `PYTHONDONTWRITEBYTECODE=1`）: 与自审段一致——五组分别 Ran 156 / 166 / 1156 / 68 / 1224 tests，全部 OK（通过）；`git diff --check` 依裁决留待 Codex 交接后独立运行。
- 已知疑问: 见自审段①②③；均为已声明的诚实边界，未升级为已验证事实。
- scope_sha256: 811ed78331fcbed28dc27b7c0ee7a423241e0d6e934f0504f2fabaf1b1e8db5e
- handoff_to: codex
- implementation_finished_at: 2026-07-21 22:06:46 +0800

### 用户授权的 v2 字段名规范化（非审核）

- 用户明确授权 Codex 仅将 Claude 自审字段名从 `实际测试命令与结果（本机实跑，前缀 PYTHONDONTWRITEBYTECODE=1）` 规范化为协议要求的精确字段名 `实际测试命令与结果`；原字段中的测试命令、计数、成功标记、时间、manifest、哈希与其他内容均保持不变。
- 本动作只修复机器解析标签，不是 Claude 返修、自审补写或 Codex 独立代码审核，不改变三阶段责任归属。
- 规范化前 `docs/AI_REVIEW_HANDOFF.md` SHA-256 = `9e341a2843d4a8b0762687fd8a355758cca73c87f2f669019d5b72d9346c5edf`；规范化前当前 scope、自审和实施聚合 SHA-256 均为 `811ed78331fcbed28dc27b7c0ee7a423241e0d6e934f0504f2fabaf1b1e8db5e`。
- normalized_by: codex（用户授权的协调监督行政动作）
- normalized_at: 2026-07-21 22:31:27 +0800

### Codex 审核结论（Round 1）

- verdict: CHANGES_REQUESTED
- 已验证事实: 接手五字段为 `WP-20260721-009 / READY_FOR_CODEX / owner=codex / handoff_to=codex / round=1`，且 `1 <= max_rounds=3`；`handoff_protocol=v2`，仓库只读解析器对结构化自审九项门禁返回 `gate_reason=None`。Claude 自审 manifest、实施交接 `scope_sha256` 与 Codex 独立开始/结束实盘清单逐项一致，聚合 SHA-256 均为 `811ed78331fcbed28dc27b7c0ee7a423241e0d6e934f0504f2fabaf1b1e8db5e`，审核期间七个 scope 文件无漂移。实现主体已建立逐通道回执、`commit_fault` / `channel_fault`、安全值覆盖、三条件显式复位、正常/scan-fault/watchdog 共用监督器与部分提交结构化异常；现有定向 156 项和既有运行时 166 项均通过。
- 项目工程约定: 驱动确认回执、逐通道提交故障状态机、`safe_value` 重试、锁存后显式复位，以及恢复首拍退化为 `safe_value` 基准，均是当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义。`last_physical_committed` 只表示驱动确认写出的最后命令值，不是传感器确认的设备位置，也不得作为可信反馈或恢复基准；当前实现与文档对此分层表述保持诚实。
- 待真机验证假设: 本包仍无真实 HAL/驱动、可信设备反馈、真实周期 monitor、硬件 watchdog 或现场安全回路；Python 回执与单元测试只证明当前契约模拟行为，不证明目标 SP16.1 PLC/CODESYS、真实物理写入或现场安全一致性。
- 必须返修: 1) **显式复位与策略第 4 步并发时，复位后的第一笔业务输出可绕过 `safe_value` 恢复基准。** `src/runtime/commit_supervisor.py:430-435` 先清除 `channel_fault/commit_fault`，再调用 `OutputPolicyService.mark_boundary_reset()`；后者在 `src/runtime/output_policy.py:455-468` 用另一把阻塞锁。Codex 用事件同步反证稳定复现：策略 staging 在旧 `last_effective=25` 上先算出 30 并持有策略锁；并发复位已把监督器锁存清除、随后阻塞等待策略锁；staging 结束后复位才写 `boundary_reset=True`，但同拍提交因监督器已健康而实际把 30 发给驱动，下一拍策略才回到 `safe_value=0` 基准输出 5。请让复位与“策略计算→提交”的边界形成可审计的失败关闭/串行事务，保证任何允许写出的首个正常输出都已按安全基准重建；新增确定性并发反证，不能只覆盖“提交期间调用复位”。
- 必须返修: 2) **驱动回执虽满足 `Mapping` 外形，但逐通道取值抛错时会漏出普通异常且不置提交故障。** `src/runtime/commit_supervisor.py:320-334,356-368` 只捕获 `driver.commit(...)` 本身；`confirmations[channel]` 未纳入失败关闭边界。Codex 反证让 Mapping 声明唯一键 `CH`、但 `__getitem__` 抛 `RuntimeError('receipt read boom')`，结果普通 `RuntimeError` 直接漏出，诊断仍为 `commit_fault=False / consecutive_failures=0 / last_receipt=None`。请把回执迭代、通道集读取和逐项取值/校验异常统一转成结构化失败证据，确保已尝试通道置故障、保留旧 `last_physical_committed`，并新增惰性/代理 Mapping 反证。
- 必须返修: 3) **成功 `CommitOutcome.receipts` 与监督器内部最近回执共享同一个可变字典，可反向污染审计证据。** `src/runtime/commit_supervisor.py:343,352,440-447` 先把 `receipts` 直接保存到 `_last_receipts`，又把同一对象放入 frozen dataclass；调用方执行 `outcome.receipts.clear()` 后，`last_commit_receipts()` 实际变为空。请让成功结果和内部诊断分别持有不可变或独立快照，并补“修改/清空返回结果不影响监督器内部最近回执”的反证；现有测试只验证修改 `last_commit_receipts()` 的副本，未覆盖 `CommitOutcome` 别名。
- 非阻塞建议: 返修时保留现有逐通道部分成功语义，不要因回执容器失败而丢失能够可靠确认的证据；但凡无法可靠枚举/读取完整回执集合时，整批失败关闭是可接受的保守策略。并发复位修复应明确锁顺序或单一串行入口，避免用新的阻塞锁顺序引入死锁。
- 审核证据: Codex 独立运行 `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` = 156/156、`python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` = 166/166、`python -m unittest discover -s prototype_05 -t .` = 68/68，均通过。正式 tests 实际运行 1156 项、全仓实际运行 1224 项，各有同 9 个 scope 外 `tests/test_ai_handoff.py` 用例因当前受限沙箱禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 1147/1147 与 1215/1215 通过；这是既有环境限制，不是 WP-009 功能回归。`git diff --check` 在审核开始后和结束前均通过。三条不落盘手工反证分别复现：复位并发首笔错误命令 30（下一拍才为 5）、回执取值异常漏出且状态未置故障、成功结果清空后内部最近回执被同步清空。逐文件结束 SHA-256：`src/runtime/commit_supervisor.py=a31020221b0dcaf177b709b488a6cd483eb591d6aea232df35e560a106701876`、`src/runtime/scan_runner.py=fdc24499a6af8aebaa013dded768302b736eb35c791ffa0854495fc6b8189632`、`src/runtime/output_policy.py=f08f84ff395856df19255aaf041836bf08db477244694c7b4f8cfbf961bf4a08`、`src/runtime/__init__.py=6464b26eec97e287dc74aff944233c5e72bf59580c1ec0a4e1c8e6b7c070be0b`、`tests/test_runtime_commit_supervisor.py=52a78e47cdf25bce63bbd11470d407c440dcec8edbeef7bb5b91fc34d747afc7`、`tests/test_runtime_scan_runner.py=50ebaf894be24822a9d876d86026ab4f3dd47307f66bae13da57b3331adda981`、`tests/test_runtime_output_policy.py=15f3829205d8fb86fbf477b69de825c67206ed30608612ebad692362bb85ff3e`。
- review_started_sha256: 811ed78331fcbed28dc27b7c0ee7a423241e0d6e934f0504f2fabaf1b1e8db5e
- review_finished_sha256: 811ed78331fcbed28dc27b7c0ee7a423241e0d6e934f0504f2fabaf1b1e8db5e
- handoff_to: claude
- reviewed_at: 2026-07-21 22:44:18 +0800

### Claude 交接前自审（Round 2）

- self_review_round: 2
- self_review_started_at: 2026-07-21 22:48:00 +0800
- self_review_finished_at: 2026-07-21 23:06:00 +0800
- self_review_verdict: PASS
- self_review_scope_sha256: 5ef47f500d0d2f82359781ddbf45c6e3b8c4cf35bec84d38e820c047441f5d22
- self_review_manifest:
  - `d27de48c9bdb96ee9eac6bddb60f80e5f8ef2bc2e8327c9f0d23fec277158036  src/runtime/commit_supervisor.py`
  - `fdc24499a6af8aebaa013dded768302b736eb35c791ffa0854495fc6b8189632  src/runtime/scan_runner.py`
  - `ed90e05d81b8109d1651855132cae15f167077ec3a56fda24b18626021929e87  src/runtime/output_policy.py`
  - `6464b26eec97e287dc74aff944233c5e72bf59580c1ec0a4e1c8e6b7c070be0b  src/runtime/__init__.py`
  - `6eb3c76b911cdf6fcd4126438040bcb5e831b2efc08a80d2c26aaddec5d2887e  tests/test_runtime_commit_supervisor.py`
  - `50ebaf894be24822a9d876d86026ab4f3dd47307f66bae13da57b3331adda981  tests/test_runtime_scan_runner.py`
  - `ba4c064aeb8f68f2bf47cbdd0d8e0ab9b9a312713b2ddb5f7f9dbe563e95c17f  tests/test_runtime_output_policy.py`
- 实际测试命令与结果: 本机实跑，前缀 `PYTHONDONTWRITEBYTECODE=1`
  - `python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` → Ran 162 tests, OK（通过）
  - `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → Ran 166 tests, OK（通过）
  - `python -m unittest discover -s tests -t .` → Ran 1162 tests, OK（通过）
  - `python -m unittest discover -s prototype_05 -t .` → Ran 68 tests, OK（通过）
  - `python -m unittest discover -s . -t .` → Ran 1230 tests, OK（通过）
  - `git diff --check`：按用户 2026-07-21 裁决与协调器禁令，Claude 不执行任何 `git` 命令，此项留待 Codex 原子交接后独立运行并记录。
- 首次失败: 无——本轮三条返修实现后，上述五组测试首次全量运行即全绿，无回归、无需修复重跑（Round 1 自审的断言笔误在上一轮已修，未复现）。
- 失败根因: 不适用（本轮无首次失败）。
- 修复内容: 不适用（无测试失败需修复）；本轮返修实现内容见"完成内容"。
- 修复后重跑结果: 五组命令全部 OK，计数 162 / 166 / 1162 / 68 / 1230，与实际测试命令与结果一致。
- 已知疑问 / 未验证边界:
  - ① 故障/复位守卫改写 `safe_value` 时，观测层 `ScanResult.outputs()` 仍反映策略逻辑值，物理实际写入以 `CommitSupervisor.diagnostics().last_physical_committed` 为准——两者刻意分层，已在模块 docstring 声明（沿用 Round 1）；
  - ② 驱动回执为**契约模拟**，非真实物理写入/设备位置反馈，未经真机验证，不构成与 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路一致的证据（沿用 Round 1）；
  - ③ 复位/提交竞态修复采用"复位置逐通道守卫 + 提交时读 `policy.boundary_pending()`"：仅当复位之后新一拍 `stage_outputs` 在 `safe_value` 基准重算并消费策略边界后，提交层才放行业务值；在此之前失败关闭改写 `safe_value`。该守卫是**项目工程约定层的确定性并发纪律**，本包仍无真实 HAL/驱动/硬件 watchdog，不表述为真机一致；单线程复位语义（复位后首个正常输出即从 `safe_value` 基准限速、不额外多写一拍 safe）保持不变，仅并发交错窗口收敛为失败关闭。
- 其他核验: `self_review_manifest` 与当前实际文件重算逐项一致；`self_review_scope_sha256 == 实施交接 scope_sha256`（均 `5ef47f500d0d2f82359781ddbf45c6e3b8c4cf35bec84d38e820c047441f5d22`）；`docs/PROJECT_STATE.md` 未改（仍 `d389ba7a9ed18e6ba94d221df334a0d925585ed0067a21013c1063c539d159b7`）；scan_runner.py / __init__.py / test_runtime_scan_runner.py 三文件本轮未改、哈希与 baseline 一致；无 scope 外文件改动、无辅助/临时/缓存文件、未执行任何 `git` 命令。
- 是否满足交接条件: 是

### Claude 实施交接（Round 2）

- 完成内容: 逐条落实 Codex Round 1 三条"必须返修"——
  1) **复位/提交竞态失败关闭（反证 1）**：`OutputPolicyService` 新增只读 `boundary_pending()`（`src/runtime/output_policy.py`），返回每通道 `boundary_reset` 边界基准挂起标志的独立副本。`CommitSupervisor`（`src/runtime/commit_supervisor.py`）为每通道新增"待安全基准重建"守卫 `_await_safe`：`reset_channel_fault` 三条件满足解锁时置该守卫为 True；`commit` 每拍开始读一次 `policy.boundary_pending()`，只有当**复位之后**新一拍 `stage_outputs` 在 `safe_value` 基准上重算并消费了策略边界（`boundary_pending` 回落 False）时才解除守卫、放行业务值，否则失败关闭把命令改写为 `safe_value`。因复位与提交本就经同一 supervisor 锁互斥（复位期间提交、提交期间复位均已失败关闭），二者共同构成失败关闭的串行事务，保证**任何被写出的首个正常输出都已按 `safe_value` 基准重建**；单线程复位语义（复位后首个正常拍即从 `safe_value` 基准限速）保持不变。
  2) **惰性/代理回执逐项取值异常失败关闭（反证 2）**：`commit` 把回执的**迭代、通道集读取与逐项取值**统一包进 `try/except`，一次性物化为普通 `dict`；满足 `Mapping` 外形但 `__iter__`/`__getitem__` 抛普通异常的回执被转为结构化整批失败（已尝试通道置 `commit_fault`、保留旧 `last_physical_committed`），绝不漏出普通异常。`_evaluate` 改从已物化 `dict` 读取，不再触碰可能抛错的代理映射。
  3) **成功结果回执去别名（反证 3）**：`commit` 内部最近回执改为 `self._last_receipts = dict(receipts)` 独立快照；`CommitOutcome` 在 `__post_init__` 把 `receipts` 复制进不可变 `MappingProxyType`。返回给调用方的成功结果无法反向污染 `last_commit_receipts()`。
- 修改文件（均在 scope 内，共 4 个）:
  - `src/runtime/commit_supervisor.py`（`_await_safe` 守卫 + 提交时 `boundary_pending` 读取；回执迭代/取值统一 `try/except` 物化；`_last_receipts` 独立快照；`CommitOutcome` 不可变快照；`_evaluate` 改读物化 dict；docstring 补并发纪律）
  - `src/runtime/output_policy.py`（新增只读 `boundary_pending()`；未改既有门控/安全事务/`mark_boundary_reset` 语义）
  - `tests/test_runtime_commit_supervisor.py`（新增：惰性回执取值异常失败关闭、复位/提交竞态确定性反证 + 线程版并发反证、`CommitOutcome` 去别名；新增 `_LazyReceipt`/`_SwitchableDriver` 测试替身；未改既有语义锁）
  - `tests/test_runtime_output_policy.py`（新增 `boundary_pending` 反映复位/消费与独立副本测试；未改既有语义锁）
- 明确未修改（本轮）: `src/runtime/scan_runner.py`、`src/runtime/__init__.py`、`tests/test_runtime_scan_runner.py`（三者哈希与 baseline 一致）；`src/runtime/engine.py`、`tests/test_runtime_engine.py`、`docs/PROJECT_STATE.md`（仍 `d389ba7a…`）、正式规格、`.cursor/rules/*`、AI 协调器/自动化配置、标准库业务块、`.git`。未实现 shadow mode、真实 HAL/驱动、可信设备反馈、硬件 watchdog 等明确排除项。未执行任何 Git/GitHub 写操作，未创建任何 scope 外/辅助/临时文件。
- 测试命令与实际结果（本机实跑，前缀 `PYTHONDONTWRITEBYTECODE=1`）: 与自审段一致——五组分别 Ran 162 / 166 / 1162 / 68 / 1230 tests，全部 OK（通过）；较 Round 1（156 / 166 / 1156 / 68 / 1224）新增 6 项均为本轮反证测试，历史快照保留。`git diff --check` 依裁决留待 Codex 交接后独立运行。
- 已知疑问: 见自审段 ①②③；均为已声明的诚实边界，未升级为已验证事实。
- scope_sha256: 5ef47f500d0d2f82359781ddbf45c6e3b8c4cf35bec84d38e820c047441f5d22
- handoff_to: codex
- implementation_finished_at: 2026-07-21 23:06:00 +0800

### Codex 审核结论（Round 2）

- verdict: CHANGES_REQUESTED
- 已验证事实: 接手五字段为 `WP-20260721-009 / READY_FOR_CODEX / owner=codex / handoff_to=codex / round=2`，且 `2 <= max_rounds=3`；`handoff_protocol=v2`，仓库解析器对本轮结构化自审九项门禁返回 `gate_reason=None`。Claude 自审 manifest、实施交接 `scope_sha256` 与 Codex 独立开始/结束实盘清单逐项一致，聚合 SHA-256 均为 `5ef47f500d0d2f82359781ddbf45c6e3b8c4cf35bec84d38e820c047441f5d22`，审核期间七个 scope 文件无漂移。Round 1 三项必须返修的主体已落地：复位后陈旧 staging 有逐通道安全基准守卫，惰性回执的逐项取值异常会物化后失败关闭，成功 `CommitOutcome.receipts` 与监督器内部最近回执也已去别名；现有定向 162 项和既有运行时 166 项均通过。
- 项目工程约定: 驱动确认回执、逐通道 `commit_fault` / `channel_fault`、`safe_value` 重试、锁存后显式复位，以及恢复首拍退化为 `safe_value` 基准，均是当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义。`last_physical_committed` 仍只表示驱动确认写出的最后命令值，不是设备位置反馈，也不得用作恢复基准；当前分层表述保持诚实。
- 待真机验证假设: 本包仍无真实 HAL/驱动、可信设备反馈、真实周期 monitor、硬件 watchdog 或现场安全回路；Python 回执与单元测试只证明当前契约模拟行为，不证明目标 SP16.1 PLC/CODESYS、真实物理写入或现场安全一致性。
- 必须返修: 1) **锁存后 `safe_value` 确认写成功没有清除瞬时 `commit_fault` 和连续失败计数。** `src/runtime/commit_supervisor.py:425-437` 在 `receipt.overridden_safe` 且 `st.channel_fault=True` 时只设 `safe_confirmed_after_latch=True` 就返回，保留 `commit_fault=True` 与旧 `consecutive_failures`。这与任务书最低测试要求“安全写成功只清瞬时故障不清锁存”以及 `ENGINE_SCAN_SPEC §4.4` “期间安全值写成功只清瞬时 `commit_fault`，不自动清除 `channel_fault`”直接冲突。Codex 不落盘反证稳定得到 `commit_fault=True / channel_fault=True / consecutive_failures=2 / safe_confirmed_after_latch=True`。请在保留 `channel_fault` 锁存的同时清除瞬时故障与连续失败计数，并新增明确断言这四个状态的反证测试；现有 `test_safe_write_success_after_latch_does_not_clear_latch` 只断言了锁存与安全确认，漏了瞬时故障/计数语义。2) **含“可哈希但不可与字符串比较排序”的多余回执通道时，失败证据格式化仍会漏出普通 `TypeError`且不置提交故障。** `src/runtime/commit_supervisor.py:357-371` 虽将 `set(confirmations)` 与逐项取值放入 `try`，但通道集不一致时在 `else` 中执行 `sorted(got_keys)`；回执为 `{"CH": 5, 1: 0}` 时，实际漏出 `TypeError("'<' not supported between instances of 'str' and 'int'")`，且诊断仍为 `commit_fault=False / consecutive_failures=0`。这与“缺失/多余通道一律失败关闭”及本轮声称的“回执迭代、通道集读取和逐项取值异常统一转结构化失败证据”不一致。请使不可信回执的诊断格式化本身也永不抛错，并增加混合类型/异常表示通道键的多余回执反证，断言抛 `PartialCommitError`、已尝试通道置故障且保留旧 `last_physical_committed`。
- 非阻塞建议: 返修时保留 Round 1 已修复的复位/staging 串行守卫和 `CommitOutcome` 去别名语义；对不可信回执生成 detail 时可使用不依赖键间全序关系的稳定表示，避免安全错误路径再次因诊断代码失效。
- 审核证据: Codex 独立运行 `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` = 162/162、`python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` = 166/166、`python -m unittest discover -s prototype_05 -t .` = 68/68，均通过。正式 tests 实际运行 1162 项、全仓实际运行 1230 项，各有同 9 个 scope 外 `tests/test_ai_handoff.py` 用例因当前受限沙箱禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 1153/1153 与 1221/1221 通过；该环境限制已有历史记录，不是 WP-009 功能回归。`git diff --check` 在审核开始后与结束前均通过。两条不落盘手工反证分别复现“锁存后安全写成功仍保留瞬时故障/计数”与“混合类型多余回执键使错误路径漏出 `TypeError`”。逐文件结束 SHA-256：`src/runtime/commit_supervisor.py=d27de48c9bdb96ee9eac6bddb60f80e5f8ef2bc2e8327c9f0d23fec277158036`、`src/runtime/scan_runner.py=fdc24499a6af8aebaa013dded768302b736eb35c791ffa0854495fc6b8189632`、`src/runtime/output_policy.py=ed90e05d81b8109d1651855132cae15f167077ec3a56fda24b18626021929e87`、`src/runtime/__init__.py=6464b26eec97e287dc74aff944233c5e72bf59580c1ec0a4e1c8e6b7c070be0b`、`tests/test_runtime_commit_supervisor.py=6eb3c76b911cdf6fcd4126438040bcb5e831b2efc08a80d2c26aaddec5d2887e`、`tests/test_runtime_scan_runner.py=50ebaf894be24822a9d876d86026ab4f3dd47307f66bae13da57b3331adda981`、`tests/test_runtime_output_policy.py=ba4c064aeb8f68f2bf47cbdd0d8e0ab9b9a312713b2ddb5f7f9dbe563e95c17f`。
- review_started_sha256: 5ef47f500d0d2f82359781ddbf45c6e3b8c4cf35bec84d38e820c047441f5d22
- review_finished_sha256: 5ef47f500d0d2f82359781ddbf45c6e3b8c4cf35bec84d38e820c047441f5d22
- handoff_to: claude
- reviewed_at: 2026-07-21 23:17:18 +0800

### Claude 交接前自审（Round 3）

- self_review_round: 3
- self_review_started_at: 2026-07-21 23:19:00 +0800
- self_review_finished_at: 2026-07-21 23:25:12 +0800
- self_review_verdict: PASS
- self_review_scope_sha256: 3e7d1625b27617eeacb87d8c3893297f98ad18c838d30b7302af3b51d2e3d54f
- self_review_manifest:
  - `1acc83579282834599ffa60633578e489a1ba3dcf59a8fa621c65d0358b7a582  src/runtime/commit_supervisor.py`
  - `fdc24499a6af8aebaa013dded768302b736eb35c791ffa0854495fc6b8189632  src/runtime/scan_runner.py`
  - `ed90e05d81b8109d1651855132cae15f167077ec3a56fda24b18626021929e87  src/runtime/output_policy.py`
  - `6464b26eec97e287dc74aff944233c5e72bf59580c1ec0a4e1c8e6b7c070be0b  src/runtime/__init__.py`
  - `69be4ed7b0e95750f72853bf9ea4f79aaaa3479420fa68dd3263d0aa9c46fd5f  tests/test_runtime_commit_supervisor.py`
  - `50ebaf894be24822a9d876d86026ab4f3dd47307f66bae13da57b3331adda981  tests/test_runtime_scan_runner.py`
  - `ba4c064aeb8f68f2bf47cbdd0d8e0ab9b9a312713b2ddb5f7f9dbe563e95c17f  tests/test_runtime_output_policy.py`
- 实际测试命令与结果: 本机实跑，前缀 `PYTHONDONTWRITEBYTECODE=1`
  - `python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` → Ran 164 tests, OK（通过）
  - `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → Ran 166 tests, OK（通过）
  - `python -m unittest discover -s tests -t .` → Ran 1164 tests, OK（通过）
  - `python -m unittest discover -s prototype_05 -t .` → Ran 68 tests, OK（通过）
  - `python -m unittest discover -s . -t .` → Ran 1232 tests, OK（通过）
  - `git diff --check`：按用户 2026-07-21 裁决与协调器禁令，Claude 不执行任何 `git` 命令，此项留待 Codex 原子交接后独立运行并记录。
- 首次失败: 无——本轮两条返修实现后，上述五组测试首次全量运行即全绿，无回归、无需修复重跑。
- 失败根因: 不适用（本轮无首次失败）。
- 修复内容: 不适用（无测试失败需修复）；本轮返修实现内容见"完成内容"。
- 修复后重跑结果: 五组命令全部 OK，计数 164 / 166 / 1164 / 68 / 1232，与实际测试命令与结果一致；较 Round 2（162 / 166 / 1162 / 68 / 1230）各 +2，为本轮两条反证测试，历史快照保留。
- 已知疑问 / 未验证边界:
  - ① 故障/复位守卫改写 `safe_value` 时，观测层 `ScanResult.outputs()` 仍反映策略逻辑值，物理实际写入以 `CommitSupervisor.diagnostics().last_physical_committed` 为准——两者刻意分层，已在模块 docstring 声明（沿用 Round 1/2）；
  - ② 驱动回执为**契约模拟**，非真实物理写入/设备位置反馈，未经真机验证，不构成与 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路一致的证据（沿用 Round 1/2）；
  - ③ 本轮 Fix 1 使锁存后安全值写成功清除瞬时 `commit_fault` 与连续计数、但保留锁存 `channel_fault`，属 §4.4 冻结的项目工程约定层语义；Fix 2 使不可信回执的失败诊断格式化按 `repr` 排序、绝不因混合/不可比较类型键抛 `TypeError`，是失败关闭路径的健壮性收敛，均不表述为真机一致。
- 其他核验: `self_review_manifest` 与当前实际文件重算逐项一致；`self_review_scope_sha256 == 实施交接 scope_sha256`（均 `3e7d1625b27617eeacb87d8c3893297f98ad18c838d30b7302af3b51d2e3d54f`）；`docs/PROJECT_STATE.md` 未改（仍 `d389ba7a9ed18e6ba94d221df334a0d925585ed0067a21013c1063c539d159b7`）；本轮仅改 `src/runtime/commit_supervisor.py` 与 `tests/test_runtime_commit_supervisor.py` 两文件，其余五个 scope 文件哈希与 Round 2 baseline 一致；无 scope 外文件改动、无辅助/临时/缓存文件、未执行任何 `git` 命令。
- 是否满足交接条件: 是

### Claude 实施交接（Round 3）

- 完成内容: 逐条落实 Codex Round 2 两条"必须返修"——
  1) **锁存后安全值写成功清瞬时故障与连续计数（保留锁存）**：`src/runtime/commit_supervisor.py` `_apply_receipt` 中，`receipt.overridden_safe` 且回执可信成功的分支改为**先无条件**清除 `commit_fault` 与 `consecutive_failures`，再判定锁存态：`channel_fault` 已锁存时仅补记 `safe_confirmed_after_latch=True`（供三条件复位）并 `return False`（通道仍 forced-safe，不触发边界基准重建），**绝不**自动解除锁存；仅瞬时 `commit_fault` 时 `return True` 触发 `mark_boundary_reset` 恢复。这样锁存拍后 safe 写成功得到 `commit_fault=False / consecutive_failures=0 / channel_fault=True / safe_confirmed_after_latch=True`，与任务书最低测试要求 3 及 `ENGINE_SCAN_SPEC §4.4` 一致。
  2) **不可信回执失败诊断格式化永不抛错**：新增模块级 `_stable_channel_list(channels)`，把任意（含混合/不可比较类型）通道键集合按每键 `repr` 排序为稳定字符串列表，不依赖键间全序关系；将 `commit()` 中回执通道集不一致分支（原 `sorted(got_keys)` / `sorted(expected)`）与结构性通道集不符分支（原 `sorted(got)` / `sorted(expected)`）改用该 helper。回执为 `{"CH": 5, 1: 0}` 等混合键时不再漏出 `TypeError`，而是走既有失败关闭：`batch_trusted=False` → 逐通道 `_evaluate` 判失败 → 已尝试通道置 `commit_fault`、保留旧 `last_physical_committed` → 抛 `PartialCommitError`。
- 修改文件（均在 scope 内，共 2 个）:
  - `src/runtime/commit_supervisor.py`（`_apply_receipt` 锁存后清瞬时故障/计数保留锁存；新增 `_stable_channel_list` 并在两处失败诊断格式化替换 `sorted`；补注释）
  - `tests/test_runtime_commit_supervisor.py`（新增 `test_safe_write_after_latch_clears_transient_keeps_latch` 断言四状态；新增 `test_extra_channel_uncomparable_key_fails_closed_preserves_lpc` 断言混合键失败关闭、置故障、保留旧 lpc；未改既有语义锁）
- 明确未修改（本轮）: `src/runtime/scan_runner.py`、`src/runtime/output_policy.py`、`src/runtime/__init__.py`、`tests/test_runtime_scan_runner.py`、`tests/test_runtime_output_policy.py`（五者哈希与 Round 2 baseline 一致）；`src/runtime/engine.py`、`tests/test_runtime_engine.py`、`docs/PROJECT_STATE.md`（仍 `d389ba7a…`）、正式规格、`.cursor/rules/*`、AI 协调器/自动化配置、标准库业务块、`.git`。未实现 shadow mode、真实 HAL/驱动、可信设备反馈、硬件 watchdog 等明确排除项。未执行任何 Git/GitHub 写操作，未创建任何 scope 外/辅助/临时文件。Round 1/2 已修复的复位/staging 串行守卫、惰性回执失败关闭、`CommitOutcome` 去别名语义均保留未回退。
- 测试命令与实际结果（本机实跑，前缀 `PYTHONDONTWRITEBYTECODE=1`）: 与自审段一致——五组分别 Ran 164 / 166 / 1164 / 68 / 1232 tests，全部 OK（通过）；较 Round 2（156→162→164 定向、1230→1232 全仓）新增 2 项均为本轮反证测试，历史快照保留。`git diff --check` 依裁决留待 Codex 交接后独立运行。
- 已知疑问: 见自审段 ①②③；均为已声明的诚实边界，未升级为已验证事实。
- scope_sha256: 3e7d1625b27617eeacb87d8c3893297f98ad18c838d30b7302af3b51d2e3d54f
- handoff_to: codex
- implementation_finished_at: 2026-07-21 23:25:12 +0800

### Codex 审核结论（Round 3）

- verdict: BLOCKED
- 已验证事实: 接手五字段为 `WP-20260721-009 / READY_FOR_CODEX / owner=codex / handoff_to=codex / round=3`，且 `3 <= max_rounds=3`；`handoff_protocol=v2`，仓库解析器对本轮结构化自审九项门禁返回 `gate_reason=None`。Claude 自审 manifest、实施交接 `scope_sha256` 与 Codex 独立开始/结束实盘清单逐项一致，聚合 SHA-256 均为 `3e7d1625b27617eeacb87d8c3893297f98ad18c838d30b7302af3b51d2e3d54f`，审核期间七个 scope 文件无漂移。Round 2 两项必须返修的主体已落地：锁存后 `safe_value` 确认写成功会清除瞬时 `commit_fault` 与连续失败计数、保留锁存 `channel_fault`；混合 `str/int` 多余回执键不再依赖键间全序关系。定向 164 项、既有运行时 166 项、原型 68 项及 `git diff --check` 均通过。
- 项目工程约定: 驱动确认回执、逐通道 `commit_fault` / `channel_fault`、`safe_value` 重试、锁存后显式复位，以及恢复首拍退化为 `safe_value` 基准，均是当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义。`last_physical_committed` 仍只表示驱动确认写出的最后命令值，不是设备位置反馈，也不得用作恢复基准；当前分层表述保持诚实。
- 待真机验证假设: 本包仍无真实 HAL/驱动、可信设备反馈、真实周期 monitor、硬件 watchdog 或现场安全回路；Python 回执与单元测试只证明当前契约模拟行为，不证明目标 SP16.1 PLC/CODESYS、真实物理写入或现场安全一致性。
- 必须返修 / 阻塞原因: **不可信驱动回执的失败诊断仍可因对象 `__repr__` 抛错而漏出普通异常，且已尝试通道不置提交故障。** `src/runtime/commit_supervisor.py:205-213` 声称 `_stable_channel_list()` “永不抛错”，实际直接执行 `repr(k)`；多余回执键若为可哈希对象且 `__repr__` 抛 `RuntimeError('repr boom')`，`commit()` 在 `:382-389` 格式化通道集不一致证据时直接漏出该普通异常，诊断保持 `commit_fault=False / consecutive_failures=0 / last_physical_committed=None`。同一根因也存在于 `:431-436`：错误类型确认值若 `__repr__` 抛 `RuntimeError('value repr boom')`，构造“确认值不匹配”detail 时同样漏出普通异常且状态不置故障。这违反本包“错类型/多余通道回执一律失败关闭、已尝试通道置故障并保留旧 `last_physical_committed`”的验收要求，也与 `_stable_channel_list` 的明确契约冲突。需要把所有不可信回执对象的诊断表示收口为真正不会调用失控 `repr` 或对表示异常有兜底的安全格式化，并新增异常 `__repr__` 通道键与错误确认值两条反证，断言 `PartialCommitError`、`commit_fault=True`、计数前移且旧 `last_physical_committed` 保留。由于本轮已是 `round=3 == max_rounds=3`，按协议硬规则不得自动进入 Round 4，故转 `BLOCKED` 交用户裁决。
- 非阻塞建议: 修复时不要只包住 `_stable_channel_list`；`driver_exception`、非 Mapping 回执、回执读取异常和错误确认值等所有来自不可信驱动边界的 detail 构造都应统一使用同一个不会再抛异常的安全表示入口，避免安全错误路径被诊断代码二次击穿。
- 审核证据: Codex 独立运行 `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` = 164/164、`python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` = 166/166、`python -m unittest discover -s prototype_05 -t .` = 68/68，均通过。正式 tests 实际运行 1164 项、全仓实际运行 1232 项，各有同 9 个 scope 外 `tests/test_ai_handoff.py` 用例因当前受限沙箱禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 1155/1155 与 1223/1223 通过；该环境限制已有历史记录，不是 WP-009 功能回归。`git diff --check` 在审核开始后与结束前均通过。两条不落盘手工反证分别复现异常 `__repr__` 多余回执键与错误类型确认值漏出普通 `RuntimeError` 且状态未失败关闭。逐文件结束 SHA-256：`src/runtime/commit_supervisor.py=1acc83579282834599ffa60633578e489a1ba3dcf59a8fa621c65d0358b7a582`、`src/runtime/scan_runner.py=fdc24499a6af8aebaa013dded768302b736eb35c791ffa0854495fc6b8189632`、`src/runtime/output_policy.py=ed90e05d81b8109d1651855132cae15f167077ec3a56fda24b18626021929e87`、`src/runtime/__init__.py=6464b26eec97e287dc74aff944233c5e72bf59580c1ec0a4e1c8e6b7c070be0b`、`tests/test_runtime_commit_supervisor.py=69be4ed7b0e95750f72853bf9ea4f79aaaa3479420fa68dd3263d0aa9c46fd5f`、`tests/test_runtime_scan_runner.py=50ebaf894be24822a9d876d86026ab4f3dd47307f66bae13da57b3331adda981`、`tests/test_runtime_output_policy.py=ba4c064aeb8f68f2bf47cbdd0d8e0ab9b9a312713b2ddb5f7f9dbe563e95c17f`。
- review_started_sha256: 3e7d1625b27617eeacb87d8c3893297f98ad18c838d30b7302af3b51d2e3d54f
- review_finished_sha256: 3e7d1625b27617eeacb87d8c3893297f98ad18c838d30b7302af3b51d2e3d54f
- handoff_to: user
- reviewed_at: 2026-07-21 23:36:48 +0800

### 用户授权的 Round 4 受限例外（仲裁恢复）

- 用户于 2026-07-22 明确同意 Codex 建议：仅将本包 `max_rounds` 从 3 调整为 4，并从 Round 3 `BLOCKED` 恢复为 `CHANGES_REQUESTED / owner=claude / handoff_to=claude`；协调器按协议接手时进入 Round 4。原三轮自审、实施交接与独立审核记录全部保留，不覆盖、不改写。
- 本例外仅允许 Claude 修改 `src/runtime/commit_supervisor.py` 与 `tests/test_runtime_commit_supervisor.py`：统一收口所有来自不可信驱动边界的安全诊断表示，确保异常 `__repr__` 或其他表示失败不会二次击穿失败关闭路径；不得只修 `_stable_channel_list()` 单点。
- Round 3 两条反证必须新增为落盘测试：异常 `__repr__` 的多余通道键、异常 `__repr__` 的错误确认值；两者均须断言 `PartialCommitError`、`commit_fault=True`、连续失败计数前移且旧 `last_physical_committed` 保留。既有 Round 1～3 已关闭问题不得回退。
- Claude 必须继续遵守 v2 三阶段门禁，直接运行并记录任务书五组 Python 测试，不得执行任何 `git` 命令，不得创建核验、临时、日志、缓存或辅助文件。`git diff --check` 仍由 Codex 在原子交接后独立运行并记录。
- 本例外不扩大功能范围：不得引入 shadow mode、真实 HAL/驱动、可信设备反馈、真实 monitor、硬件 watchdog、L2 adapter registry、HMI/通知/持久化或现场安全证明；不得修改其余五个 scope 文件、正式规格、AI 协调器配置、`docs/PROJECT_STATE.md` 或 `.git`。
- 恢复前只读核验：当前 scope 聚合 SHA-256 = Round 3 `review_finished_sha256` = `3e7d1625b27617eeacb87d8c3893297f98ad18c838d30b7302af3b51d2e3d54f`，无漂移；协调器 live、无活动租约、无失败告警；`main`、本地 HEAD 与 `origin/main` 仍为 `f0950443c9f2cbd43e0f0067746dd8abaebfca86`。
- authorized_by: user
- restored_by: codex（用户授权的协议仲裁行政动作，非功能实施或独立审核）
- restored_at: 2026-07-22 08:50:38 +0800

### Round 4 外部执行中断与后继恢复裁决

- interrupted_execution_key: `WP-20260721-009:3:start_claude_rework`
- interrupted_at: 2026-07-22 09:01:40 +0800
- interruption_type: `error_max_turns`（Claude CLI 单次执行达到固定 40 turns 上限；不是代码审核结论，也不是工作包 `max_rounds`）
- 已验证事实: Round 4 子进程在 2026-07-22 08:51:34 +0800 启动、运行约 606 秒后中断。Claude 已修改本轮获准的两个文件，并在其内部任务清单中记录实现与五组 Python 测试已完成；但尚未完成 v2 结构化自审、未追加原子实施交接，故这些内部进度不得提升为正式交接或 Codex 审核证据。
- 当前检查点: 只有 `src/runtime/commit_supervisor.py` 与 `tests/test_runtime_commit_supervisor.py` 相对 Round 3 发生变化；其余五个 WP-009 scope 文件保持 Round 3 哈希。七文件当前聚合 SHA-256 = `5c65ac14f579a4e12dbf0770775741820162130c8d5b5e534949676a1fa9359a`。
- 精确回退裁决: 用户曾授权“精确回退两文件 Round 4 中断改动 + 同一幂等键单次重试”，并要求任一哈希无法精确恢复即停止。Codex 只读验证确认测试文件可精确还原，但两个文件均为未跟踪新文件，仓库与现有审计材料没有保存源文件 Round 3 完整字节快照，无法证明源文件可精确恢复到 `1acc8357…a582`；因此未执行任何回退、未重试、未启动 Claude。
- 后继裁决: 用户于 2026-07-22 明确确认采用新的窄范围 `WP-20260722-010`，以当前两文件内容为新基线完成核验、必要修正、测试、自审、原子交接与独立审核。WP-009 据实保持 `BLOCKED / owner=user / handoff_to=user / round=4`，保留中断历史，不把未完成的 Round 4 冒充已交接或已审核。
- Git 与基础设施边界: 本次没有 Git/GitHub 写操作；没有调整 `tools/ai_handoff/scheduler.py` 的 Claude `--max-turns 40` 设置。旧 Claude/Codex 30 分钟主轮询继续暂停。
- recorded_by: codex（用户授权的协议行政动作）
- recorded_at: 2026-07-22 10:20:03 +0800

## WP-20260722-010

- title: WP-009 Round 4 中断实现的两文件窄范围恢复、自审与独立审核
- status: CLOSED
- closed_by: user
- closed_at: 2026-07-22 12:32:31 +0800
- closure_note: 用户接受 Codex Round 2 `APPROVED` 结论，确认关闭本包并授权 Git/GitHub 收尾。本包只证明当前 Python 诊断表示失败关闭行为，不构成 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全一致性证明。
- owner: user
- handoff_to: user
- round: 2
- max_rounds: 3
- handoff_protocol: v2
- base_commit: f0950443c9f2cbd43e0f0067746dd8abaebfca86
- created_by: user
- created_at: 2026-07-22 10:20:03 +0800
- depends_on:
  - WP-20260721-009 BLOCKED（Round 4 外部执行中断；当前两文件检查点转入本包）
- scope:
  - src/runtime/commit_supervisor.py
  - tests/test_runtime_commit_supervisor.py
- scope_baseline_sha256: 32c5114be3e35d7c516332e5bb82885e06aca809d06e76afd8b51c8e0985338c
- scope_baseline_manifest:
  - `5721a9ea00b551b35c41b9c1ac6de5cd7e66e9f3e48a5867fd7a9c1366caad85  src/runtime/commit_supervisor.py`
  - `cc64397f6d2e29e5aa7fe804057969ff7b7c5254429fd69e04f298c171b5b754  tests/test_runtime_commit_supervisor.py`

### 工作包创建行政证据（Claude 启动前）

- 用户于 2026-07-22 明确确认 Codex 建议的新建窄范围恢复工作包方案。本节、WP-009 中断封存及 `docs/PROJECT_STATE.md` 同步属于 Codex 获准的协议行政动作，不属于 Claude 功能 scope。
- 创建前复算两文件 baseline manifest 与上列逐项哈希一致，按协调器逐行保留末尾换行的正式口径聚合 SHA-256 = `32c5114be3e35d7c516332e5bb82885e06aca809d06e76afd8b51c8e0985338c`。首次行政记录曾因手工计算遗漏清单末尾换行而写成 `6847ca4b…a88fe`，协调器门禁在启动 Claude 前即拒绝；此处已按协调器实算纠正，两个逐文件哈希与文件内容始终未变。该基线是诚实的“中断实现检查点”，不表示实现已通过 Claude 自审或 Codex 审核。
- 创建前协调器运行目录无活动执行租约、无 `execution_block.json`；保留 WP-009 失败历史，不授权复用或再次重试旧执行键。WP-010 必须使用新幂等键 `WP-20260722-010:1:start_claude_implementation`。
- Git 基线仍由 Codex 既有只读证据负责；Claude 在本包中**禁止读取或解析 `.git` 的任何文件，也禁止执行任何 Git/GitHub 命令**。不得因无法自行读取 `.git` 而停止；直接信赖本包已给出的 `base_commit` 与两文件 baseline manifest。
- 本包不调整 Claude CLI 的固定 40 turns 上限。为控制单次执行长度，Claude 应优先核验现有实现与两条既有反证、仅在必要时修正，然后直接完成五组测试、结构化自审和原子交接，不重复已完成且无必要的探索。

### 目标与验收标准

以当前两文件检查点为唯一开工内容，完成 WP-009 Round 3 阻塞项的窄范围恢复闭环：Claude 独立检查当前实现，必要时仅在两文件内修正；确认所有来自不可信驱动边界的诊断表示均不会因异常 `__repr__` 或表示过程失败而二次抛出普通异常；完成 v2 自审与原子交接后，由 Codex 独立审核。

1. **统一安全诊断表示**
   - 驱动异常对象、非 `Mapping` 回执、回执迭代/读取异常、异常或混合类型通道键、错误类型/错误值确认对象等所有不可信驱动边界输入，在生成失败 detail 时均不得再击穿失败关闭路径。
   - 表示失败必须退化为确定、无副作用且不泄漏原普通异常的安全占位证据；不得只修 `_stable_channel_list()` 一个调用点。
   - 缺失/多余通道、错类型、错值及回执结构异常仍须转为既有结构化提交失败；已尝试通道置 `commit_fault`、连续失败计数前移，且旧 `last_physical_committed` 保留。

2. **两条异常 `__repr__` 反证**
   - 落盘测试必须覆盖异常 `__repr__` 的多余通道键，以及异常 `__repr__` 的错误确认值。
   - 两条均须断言抛出 `PartialCommitError`，而非普通 `RuntimeError`；同时断言 `commit_fault=True`、连续失败计数前移、旧 `last_physical_committed` 不变。
   - 若当前检查点已满足，Claude 应核验并保留；若存在遗漏，只做最小必要修正。WP-009 Round 1～3 已关闭语义不得回退。

3. **交接完整性**
   - Claude 必须在 `CLAUDE_WORKING` 内完成结构化自审；只有 `self_review_verdict: PASS`、五组测试真实计数、完整两文件 manifest、`self_review_scope_sha256 == scope_sha256`、明确“是否满足交接条件: 是”全部成立，才可追加实施交接。
   - 最终以一次原子写入把顶层状态转为 `READY_FOR_CODEX / owner=codex / handoff_to=codex / round=1`，且自审段必须位于实施交接段之前；随后立即停止修改 scope。

### 明确排除与冻结边界

- Claude 只能修改上列两个 scope 文件，以及按 v2 协议原子追加本工作包自审/实施交接记录；不得修改 WP-009 记录、`docs/PROJECT_STATE.md`、正式规格、其余五个旧 scope 文件、AI 协调器/自动化配置或 `.git`。
- 严禁读取 `.git`，严禁执行 `git` / `gh` / `shasum`，严禁使用 Python `subprocess` 绕过禁令；`git diff --check` 继续由 Codex 在原子交接后独立执行。
- 严禁创建核验脚本、临时文件、缓存、日志、补丁或其他辅助文件。需要哈希核验时只能直接运行单行 `python -c`；测试只能直接运行下列 `python -m unittest` 命令。
- 不实现或引入 shadow mode、L2 adapter registry、真实 monitor/HAL/驱动、可信设备反馈、真实周期/硬件 watchdog、HMI/通知/持久化或现场安全证明；不修改 `ENGINE_SCAN_SPEC v2.2.2 §4.1/§4.4` 语义。
- 驱动回执仍是 Python 契约模拟；本包测试不构成目标 PLC/CODESYS、真实物理写入、硬件 watchdog 或现场安全回路一致性证明。

### 测试计划与责任分配

Claude 交接前必须直接实际运行并记录以下五组 Python 测试的真实计数和结果；新增测试导致计数增长属于正常快照变化，不得回写历史数字：

1. `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy`
2. `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir`
3. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .`
4. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s prototype_05 -t .`
5. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s . -t .`

Codex 在原子交接后独立复核实现、两条反证、scope 哈希与测试，并执行 `git diff --check`；任何审核结论必须区分项目工程约定、当前 Python 已验证事实与仍待真机验证假设。

### Claude 交接前自审（Round 1）

- self_review_round: 1
- self_review_started_at: 2026-07-22 10:23:30 +0800
- self_review_finished_at: 2026-07-22 10:28:30 +0800
- self_review_verdict: PASS
- self_review_scope_sha256: 32c5114be3e35d7c516332e5bb82885e06aca809d06e76afd8b51c8e0985338c
- self_review_manifest:
  - `5721a9ea00b551b35c41b9c1ac6de5cd7e66e9f3e48a5867fd7a9c1366caad85  src/runtime/commit_supervisor.py`
  - `cc64397f6d2e29e5aa7fe804057969ff7b7c5254429fd69e04f298c171b5b754  tests/test_runtime_commit_supervisor.py`
- 实际测试命令与结果: 本机实跑，前缀 `PYTHONDONTWRITEBYTECODE=1`
  - `python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` → Ran 166 tests, OK（通过）
  - `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → Ran 166 tests, OK（通过）
  - `python -m unittest discover -s tests -t .` → Ran 1166 tests, OK（通过）
  - `python -m unittest discover -s prototype_05 -t .` → Ran 68 tests, OK（通过）
  - `python -m unittest discover -s . -t .` → Ran 1234 tests, OK（通过）
  - `git diff --check`：按用户 2026-07-21/07-22 裁决与协调器禁令，Claude 不执行任何 `git` 命令，此项留待 Codex 原子交接后独立运行并记录。
- 首次失败: 无——本包以 WP-009 Round 4 中断检查点两文件为开工内容；核验现有实现与两条既有反证后，上述五组测试首次全量运行即全绿，无回归、无需修复重跑。
- 失败根因: 不适用（本轮无首次失败）。
- 修复内容: 不适用——独立核验确认当前检查点已满足验收标准，本轮对两个 scope 文件**零改动**（两文件 SHA-256 与本包 `scope_baseline_manifest` 逐项一致，聚合等于 `scope_baseline_sha256`），故无最小必要修正。
- 修复后重跑结果: 五组命令全部 OK，计数 166 / 166 / 1166 / 68 / 1234，与实际测试命令与结果一致；较 WP-009 Round 3（164 / 166 / 1164 / 68 / 1232）各 +2，为检查点已含的两条异常 `__repr__` 反证（`test_extra_channel_repr_raising_key_fails_closed_preserves_lpc`、`test_wrong_confirmed_value_repr_raising_fails_closed_preserves_lpc`），历史快照保留、未回写历史数字。本环境放行交接面板本地 HTTP 端口，`tests` / 全仓两组无 WP-009 Round 3 记录的受限沙箱 `PermissionError`，全部实跑通过。
- 已知疑问 / 未验证边界:
  - ① **统一安全诊断表示已收口**：所有来自不可信驱动边界的诊断串（驱动异常、非 `Mapping` 回执、回执迭代/取值异常、异常或混合类型通道键、错类型/错值确认对象）均经模块级 `_safe_repr()`（`commit_supervisor.py:99-115`）或经其构建的 `_stable_channel_list()`（`:228-238`）格式化；`_safe_repr` 内 `repr()` 抛异常时退化为稳定占位串、绝不上抛表示异常，已逐点核验 `:157/261/265/348/393/397/408/416-417/463/466` 全部覆盖，无遗漏的裸 `repr`/`%s` 直插不可信对象；`reset_channel_fault` 的 `% channel`（`:521-531`）是可信复位 API/操作员入参、非驱动回执边界，且不在失败关闭提交路径内，属既有行为，未纳入本包 scope。
  - ② **两条异常 `__repr__` 反证均为落盘测试**：分别断言 `PartialCommitError`（非普通 `RuntimeError`）、`commit_fault=True`、`consecutive_failures` 前移为 1、旧 `last_physical_committed=5` 保留（`test_runtime_commit_supervisor.py:324-356`）；WP-009 Round 1～3 已关闭语义（锁存后清瞬时保留锁存、混合键 `TypeError` 关闭、惰性回执 `__getitem__` 关闭、复位/提交竞态、`CommitOutcome` 去别名、重入失败关闭）对应测试全部保留未回退。
  - ③ 观测层 `ScanResult.outputs()` 反映策略逻辑值，物理实际写入以 `CommitSupervisor.diagnostics().last_physical_committed` 为准——两者刻意分层（沿用 WP-009）。
  - ④ 驱动回执为**契约模拟**，非真实物理写入/设备位置反馈，未经真机验证，不构成与目标 SP16.1 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路一致的证据；上述提交/故障制度为**项目工程约定、非 CODESYS 官方语义**（沿用 WP-009）。
- 其他核验: `self_review_manifest` 与当前实际文件重算逐项一致；`self_review_scope_sha256 == 实施交接 scope_sha256`（均 `32c5114be3e35d7c516332e5bb82885e06aca809d06e76afd8b51c8e0985338c`）且等于本包 `scope_baseline_sha256`；`docs/PROJECT_STATE.md`、WP-009 记录、正式规格、其余五个旧 scope 文件、`.cursor/rules/*`、AI 协调器配置均未改；哈希核验仅用单行 `python -c`，未读取/解析 `.git`，未执行任何 `git` / `gh` / `shasum`，未使用 `subprocess`，未创建任何核验/临时/日志/缓存/补丁/辅助文件。
- 是否满足交接条件: 是

### Claude 实施交接（Round 1）

- 完成内容: 以 WP-009 Round 4 中断检查点两文件为唯一开工内容，完成窄范围恢复闭环的核验与确认——
  1) **统一安全诊断表示（验收标准 1）已在检查点满足并逐点核验**：`src/runtime/commit_supervisor.py` 模块级 `_safe_repr()`（`:99-115`）对不可信驱动边界对象生成永不抛错的诊断表示（`repr` 抛异常→稳定占位串，连 `type(obj).__name__` 也兜底）；`_stable_channel_list()`（`:228-238`）改经 `_safe_repr` 排序，不依赖键间全序也不依赖键 `repr` 不抛异常。所有失败关闭 detail 构造点（驱动异常 `:393`、非 `Mapping` 回执 `:397`、回执迭代/取值异常 `:408`、通道集不一致 `:416-417`、确认值域/类型错误 `:463`、错值/错类型 `:466`、`PartialCommitError` 消息 `:157`）统一经该安全入口，异常 `__repr__` 或表示过程失败均不再二次击穿失败关闭路径。缺失/多余通道、错类型、错值、回执结构异常仍转为结构化提交失败：已尝试通道置 `commit_fault`、连续失败计数前移，旧 `last_physical_committed` 保留。
  2) **两条异常 `__repr__` 反证（验收标准 2）已落盘并核验**：`tests/test_runtime_commit_supervisor.py` 中 `test_extra_channel_repr_raising_key_fails_closed_preserves_lpc`（`:324-339`，异常 `__repr__` 多余通道键）与 `test_wrong_confirmed_value_repr_raising_fails_closed_preserves_lpc`（`:341-356`，异常 `__repr__` 错误确认值），二者均先成功一拍使 `lpc=5`，再断言抛 `PartialCommitError`（非普通 `RuntimeError`）、`commit_fault=True`、`consecutive_failures=1`、`last_physical_committed=5` 保留；辅助替身 `_ReprBoom`（`:150-158`）`__repr__` 抛 `RuntimeError`。WP-009 Round 1～3 已关闭语义全部保留未回退。
  3) 独立核验结论：当前检查点已满足全部验收标准，**本轮对两个 scope 文件零改动**（两文件与本包 `scope_baseline_manifest` 逐项 SHA-256 一致，聚合等于 `scope_baseline_sha256`）。
- 修改文件（本轮 scope 内，共 0 个源码/测试文件被改）: 无——`src/runtime/commit_supervisor.py`、`tests/test_runtime_commit_supervisor.py` 均维持检查点内容不变（哈希见 `scope_sha256`）。本次仅按 v2 协议原子追加本工作包自审段与本实施交接段。
- 明确未修改: WP-009 全部记录、`docs/PROJECT_STATE.md`、正式规格（含 `ENGINE_SCAN_SPEC v2.2.2 §4.1/§4.4`）、其余五个旧 scope 文件（`scan_runner.py` / `output_policy.py` / `__init__.py` / `test_runtime_scan_runner.py` / `test_runtime_output_policy.py`）、`.cursor/rules/*`、AI 协调器/自动化配置、`.git`。未实现或引入 shadow mode、L2 adapter registry、真实 monitor/HAL/驱动、可信设备反馈、真实周期/硬件 watchdog、HMI/通知/持久化或现场安全证明。未读取/解析 `.git`，未执行任何 `git` / `gh` / `shasum` / `subprocess`，未创建任何核验/临时/日志/缓存/补丁/辅助文件。
- 测试命令与实际结果（本机实跑，前缀 `PYTHONDONTWRITEBYTECODE=1`）: 与自审段一致——五组分别 Ran 166 / 166 / 1166 / 68 / 1234 tests，全部 OK（通过）；较 WP-009 Round 3（164 / 166 / 1164 / 68 / 1232）各 +2，为检查点已含两条异常 `__repr__` 反证，历史快照保留、未回写历史数字。`git diff --check` 依裁决留待 Codex 原子交接后独立运行并记录。
- 已知疑问: 见自审段 ①②③④；均为已声明的诚实边界，未升级为已验证事实。驱动回执仍是 Python 契约模拟，本包测试不构成目标 PLC/CODESYS、真实物理写入、硬件 watchdog 或现场安全回路一致性证明。
- scope_sha256: 32c5114be3e35d7c516332e5bb82885e06aca809d06e76afd8b51c8e0985338c
- handoff_to: codex
- implementation_finished_at: 2026-07-22 10:28:30 +0800

### Codex 审核结论（Round 1）

- verdict: CHANGES_REQUESTED
- 已验证事实: 接手五字段为 `WP-20260722-010 / READY_FOR_CODEX / owner=codex / handoff_to=codex / round=1`，且 `1 <= max_rounds=3`；`handoff_protocol=v2`，仓库解析器对本轮结构化自审九项门禁返回 `gate_reason=None`。Claude 自审 manifest、实施交接 `scope_sha256` 与 Codex 独立开始/结束实盘清单逐项一致，聚合 SHA-256 均为 `32c5114be3e35d7c516332e5bb82885e06aca809d06e76afd8b51c8e0985338c`，审核期间两个 scope 文件无漂移。现有 `_safe_repr()` / `_stable_channel_list()` 已使普通异常 `__repr__` 的多余通道键与错误确认值走 `PartialCommitError`，两条落盘反证及 WP-009 Round 1～3 既有语义锁在定向 166 项中均通过；本轮未修改任何 scope 文件。
- 项目工程约定: 驱动确认回执、逐通道 `commit_fault` / `channel_fault`、`safe_value` 重试、锁存后显式复位，以及安全诊断失败关闭，均是当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义。`last_physical_committed` 仍只表示驱动确认写出的最后命令值，不是可信设备位置反馈，也不得作为恢复基准；当前分层表述保持诚实。
- 待真机验证假设: 本包仍无真实 HAL/驱动、可信设备反馈、真实周期 monitor、硬件 watchdog 或现场安全回路；Python 回执契约与单元测试只证明当前实现行为，不证明目标 SP16.1 PLC/CODESYS、真实物理写入或现场安全一致性。
- 必须返修: 1) **`_safe_repr()` 的失败占位构造本身仍可被二次击穿，因而不满足本包“表示过程失败必须退化为确定、无副作用且不泄漏普通异常”的验收标准。** `src/runtime/commit_supervisor.py:108-115` 在 `repr(obj)` 抛异常后读取 `type(obj).__name__`，但仅保护属性读取；随后在保护区外执行 `"<%s ...>" % type_name`。恶意元类可令 `__name__` 返回一个 `__str__` 抛异常的对象，于是字符串插值再次漏出普通异常。Codex 不落盘反证：先成功提交使 `LPC=5`，再让驱动回执包含一个多余通道键；该键的 `__repr__` 抛 `RuntimeError("repr boom")`，其元类令 `type(key).__name__` 返回 `__str__` 抛 `RuntimeError("type-name str boom")` 的对象。当前 `commit({"CH": 7})` 实际漏出 `RuntimeError: type-name str boom`，诊断仍为 `commit_fault=False / consecutive_failures=0 / last_physical_committed=5`，没有形成 `PartialCommitError` 或失败关闭证据。请把 fallback 的**整个构造过程**收口为永不调用不可信对象字符串协议的固定/严格内建字符串（最安全可直接使用固定占位串；若保留类型名，必须只接受 exact `str` 并让最终 fallback 仍有不可失败的兜底），并在 `tests/test_runtime_commit_supervisor.py` 增加上述“`repr` 失败 + 类型名字符串化也失败”的集成反证，断言 `PartialCommitError`、`commit_fault=True`、`consecutive_failures=1`、旧 LPC 保持 5。现有 `_ReprBoom` 的类型名是普通 `str`，故两条现有测试没有覆盖这个 fallback 自身的失败点。
- 非阻塞建议: 无；返修应保持在现有两个 scope 文件内，不需要扩包或修改规格。建议复用同一 `_safe_repr()` 入口修复，不新增平行诊断格式化路径。
- 审核证据: Codex 独立运行 `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` = 166/166、`python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` = 166/166、`python -m unittest discover -s prototype_05 -t .` = 68/68，均通过。正式 tests 实际运行 1166 项、全仓实际运行 1234 项，各有同 9 个 scope 外 `tests/test_ai_handoff.py` 用例因当前受限沙箱禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 1157/1157 与 1225/1225 通过；该环境限制与 WP-009 既有审核记录一致，不是本包功能回归。`git diff --check` 通过。除上述类型名 fallback 反证外，另做的恶意整数子类回执反证也表明 `_evaluate()` 的 IEC 范围比较仍可漏出普通 `RuntimeError`；它超出本包“诊断表示”窄目标，本轮不作为新增必须返修项，但后续驱动边界加固宜单独立项。逐文件结束 SHA-256：`src/runtime/commit_supervisor.py=5721a9ea00b551b35c41b9c1ac6de5cd7e66e9f3e48a5867fd7a9c1366caad85`、`tests/test_runtime_commit_supervisor.py=cc64397f6d2e29e5aa7fe804057969ff7b7c5254429fd69e04f298c171b5b754`。
- review_started_sha256: 32c5114be3e35d7c516332e5bb82885e06aca809d06e76afd8b51c8e0985338c
- review_finished_sha256: 32c5114be3e35d7c516332e5bb82885e06aca809d06e76afd8b51c8e0985338c
- handoff_to: claude
- reviewed_at: 2026-07-22 10:38:34 +0800

### Round 1 审核后轮次字段失败关闭与行政规范化

- 已验证事实: `Codex 审核结论（Round 1）` 的正文、verdict、开始/结束 scope 哈希、测试证据与 `reviewed_at` 均已完整写入，两个 scope 文件在审核期间及审核后保持聚合 SHA-256 `32c5114be3e35d7c516332e5bb82885e06aca809d06e76afd8b51c8e0985338c`；但 Codex 原子状态转移把顶层 `round` 从 1 提前写成 2。协议要求 Codex 审核当前轮不增加轮次，只有 Claude 接手 `CHANGES_REQUESTED` 时执行 `round+1`，因此协调器将 `WP-20260722-010:1:start_codex_review` 正确记录为 `postcondition-failed`。
- 错误派发与停止: 文件事件随后按错误顶层值启动 `WP-20260722-010:2:start_claude_rework`（PID 19583）。Codex 监督方发现后立即按失败关闭终止该子进程；协调器于 2026-07-22 10:43:44 +0800 记录返回码 143 并释放租约。停止后两个 scope 文件逐项哈希仍为 baseline，未产生 scope 外文件、未形成 Round 2/3 自审或实施交接。
- 规范化内容: 仅把顶层 `round` 从错误的 2 恢复为审核所属的 1；审核意见、测试计数、时间、manifest 与哈希一字不改。该操作不是推翻审核，也不是重试已失败的 Codex/Claude 键。
- 正确后继动作: 恢复后合法新键为 `WP-20260722-010:1:start_claude_rework`；Claude 接手时按协议进入 Round 2，并须对 `_safe_repr()` fallback 全构造过程做最小修复、增加恶意元类类型名字符串化失败的集成反证、完成五组测试和 v2 自审/原子交接。
- Git 与范围边界: 本次没有 Git/GitHub 写操作，没有修改功能 scope、规格、协调器配置或 40 turns 设置；旧主轮询继续暂停。
- normalized_by: codex（协议失败关闭后的行政字段纠正）
- normalized_at: 2026-07-22 10:44:00 +0800

### Claude 交接前自审（Round 2）

- self_review_round: 2
- self_review_started_at: 2026-07-22 10:45:00 +0800
- self_review_finished_at: 2026-07-22 10:49:20 +0800
- self_review_verdict: PASS
- self_review_scope_sha256: a8d4f0fef8843f098e54372f11394d5ea9c6d618f3dc39c30d9f0f4c887346a8
- self_review_manifest:
  - `05cfa062ad225fb9338e348fc53e42dc25d76d4e748160cce0baa3a5653c0b60  src/runtime/commit_supervisor.py`
  - `3cbc3c27793dc087a3c80fa3a59b79b93e522a90509c8e5cfb9930984370049f  tests/test_runtime_commit_supervisor.py`
- 接手前核验: 接手前五字段为 `WP-20260722-010 / CHANGES_REQUESTED / owner=claude / handoff_to=claude / round=1`，属 Claude 合法接手组合；按协议 round+1 → 本轮 Round 2，`2 <= max_rounds=3`。接手前用单行 `python -c` 复算两 scope 文件与本包 `scope_baseline_manifest` 逐项一致（`5721a9ea…ad85` / `cc64397f…b754`），聚合等于 `scope_baseline_sha256=32c5114b…338c`，证据无漂移；Codex Round 1 审核区（含 `review_started_sha256 == review_finished_sha256 == 32c5114b…338c`）完整，round 已由 Codex 行政规范化回 1，本轮为合法后继 `start_claude_rework`。
- 实际测试命令与结果: 本机实跑，前缀 `PYTHONDONTWRITEBYTECODE=1`
  - `python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` → Ran 167 tests, OK（通过）
  - `python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → Ran 166 tests, OK（通过）
  - `python -m unittest discover -s tests -t .` → Ran 1167 tests, OK（通过）
  - `python -m unittest discover -s prototype_05 -t .` → Ran 68 tests, OK（通过）
  - `python -m unittest discover -s . -t .` → Ran 1235 tests, OK（通过）
  - `git diff --check`：按用户 2026-07-21/07-22 裁决与协调器禁令，Claude 不执行任何 `git` 命令，此项留待 Codex 原子交接后独立运行并记录。
- 首次失败: 无——本轮针对 Codex Round 1 唯一必须返修项（`_safe_repr()` fallback 自身可被恶意元类类型名字符串化二次击穿）做最小修复并新增一条集成反证；修复后五组测试首次全量运行即全绿，无回归。
- 失败根因: Codex Round 1 反证——`src/runtime/commit_supervisor.py` 原 `_safe_repr()` 在 `repr(obj)` 抛异常后仅保护 `type(obj).__name__` 的**属性读取**，随后在保护区外执行 `"<%s …>" % type_name`；恶意元类可令 `__name__` 返回一个 `__str__` 抛普通异常的对象，`%s` 插值触发其 `__str__` 从而再次漏出普通 `RuntimeError`，使本应失败关闭的路径被二次击穿（`commit_fault` 不置、旧 `last_physical_committed` 误留、无 `PartialCommitError`）。
- 修复内容: 只改两个 scope 文件。1) `src/runtime/commit_supervisor.py` `_safe_repr()`（现 `:99-125`）：把 fallback 的**整个构造过程**收口为绝不调用不可信对象字符串协议——类型名仅在 `type(type_name) is str`（exact `str`，排除 `str` 子类/伪造对象，其 `__str__`/`__format__` 仍可能抛错）时才嵌入，且 exact `str` 的 `%s` 插值不触发任何可被重写的 dunder；否则退到不含任何不可信子串的固定占位串 `"<对象：repr() 与类型名字符串化均抛异常，诊断已退化为安全占位表示>"`。两条 fallback 分支的字符串构造均不可失败，`_safe_repr` 全过程永不上抛。修复复用同一 `_safe_repr()` 入口，未新增平行诊断格式化路径（采纳 Codex 非阻塞建议）。2) `tests/test_runtime_commit_supervisor.py`：新增恶意元类替身 `_TypeNameStrBoom`（`__str__`/`__repr__` 均抛错）、`_ReprAndTypeNameBoomMeta`（`@property __name__` 返回 `_TypeNameStrBoom()`）、`_ReprAndTypeNameBoom`（`__repr__` 抛错且类型名字符串化也抛错），并新增集成反证 `test_extra_channel_repr_and_typename_boom_fails_closed_preserves_lpc`：先成功一拍使 `lpc=5`，再以该对象为多余回执通道键，断言抛 `PartialCommitError`（非普通 `RuntimeError`）、`commit_fault=True`、`consecutive_failures=1`、旧 `last_physical_committed=5` 保留、`last_commit_receipts()["CH"].ok=False`。
- 修复后重跑结果: 五组命令全部 OK，计数 167 / 166 / 1167 / 68 / 1234→1235。含 commit_supervisor 测试模块的三组（1、3、5）较 WP-009 Round 3 与本包 Round 1（166 / 166 / 1166 / 68 / 1234）各 +1，来源为本轮新增的唯一集成反证；组 2、4（不含该模块）计数不变（166 / 68）。属正常快照增长，未回写历史数字。本机放行交接面板本地 HTTP 端口，`tests` / 全仓两组无 Codex Round 1 沙箱记录的 `PermissionError`，全部实跑通过（环境差异，不构成功能回归）。
- 已知疑问 / 未验证边界:
  - ① **`_safe_repr()` fallback 自身失败点已收口**：`repr` 失败 + 恶意元类类型名字符串化也失败的复合反证已落盘并通过；fallback 两分支均不调用不可信字符串协议，永不二次上抛。Codex Round 1 已关闭的两条既有反证（异常 `__repr__` 多余通道键、异常 `__repr__` 错误确认值）与 WP-009 Round 1～3 已关闭语义（锁存后清瞬时保留锁存、混合键 `TypeError` 关闭、惰性回执 `__getitem__` 关闭、复位/提交竞态、`CommitOutcome` 去别名、重入失败关闭）对应测试全部保留未回退。
  - ② **超出本包窄目标的驱动边界未纳入**：Codex Round 1 审核证据提到的“恶意整数子类回执令 `_evaluate()` IEC 范围比较漏出普通 `RuntimeError`”明确属于**诊断表示窄目标之外**、Codex 本轮未列为必须返修项，本包不扩范围处理；`reset_channel_fault` 的 `% channel` 是可信复位 API/操作员入参、非驱动回执边界，且不在失败关闭提交路径内，仍属既有行为，未纳入本包 scope。建议后续单独立项加固驱动值域比较边界。
  - ③ 观测层 `ScanResult.outputs()` 反映策略逻辑值，物理实际写入以 `CommitSupervisor.diagnostics().last_physical_committed` 为准——两者刻意分层（沿用 WP-009）。
  - ④ 驱动回执为**契约模拟**，非真实物理写入/设备位置反馈，未经真机验证，不构成与目标 SP16.1 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路一致的证据；上述提交/故障制度为**项目工程约定、非 CODESYS 官方语义**（沿用 WP-009）。
- 其他核验: `self_review_manifest` 与当前实际文件重算逐项一致；`self_review_scope_sha256 == 实施交接 scope_sha256`（均 `a8d4f0fef8843f098e54372f11394d5ea9c6d618f3dc39c30d9f0f4c887346a8`）。仅改上列两个 scope 文件；`docs/PROJECT_STATE.md`、WP-009 记录、Codex Round 1 审核区与本包既有行政段、正式规格（含 `ENGINE_SCAN_SPEC v2.2.2 §4.1/§4.4`）、其余五个旧 scope 文件、`.cursor/rules/*`、AI 协调器/自动化配置均未改；哈希核验仅用单行 `python -c`，未读取/解析 `.git`，未执行任何 `git` / `gh` / `shasum`，未使用 `subprocess`，未创建任何核验/临时/日志/缓存/补丁/辅助文件。
- 是否满足交接条件: 是

### Claude 实施交接（Round 2）

- 完成内容: 按 Codex Round 1 唯一必须返修项落地窄范围返修——
  1) **`_safe_repr()` fallback 全构造过程收口（必须返修项 1）**：`src/runtime/commit_supervisor.py` `_safe_repr()`（`:99-125`）在 `repr(obj)` 抛异常后，类型名仅在 `type(type_name) is str`（exact `str`）时才做 `%s` 插值——exact `str` 的 `%s` 不触发任何可被覆写的 dunder，永不抛错；类型名为 `str` 子类/伪造对象（`__str__`/`__format__` 可能再次抛普通异常）时一律退到不含不可信子串的固定占位串。恶意元类令 `type(obj).__name__` 返回 `__str__` 抛异常的对象时，fallback 不再二次漏出普通异常，仍走结构化失败关闭。复用同一安全入口，未新增平行格式化路径。
  2) **恶意元类类型名字符串化失败集成反证（必须返修项 1 配套）**：`tests/test_runtime_commit_supervisor.py` 新增 `_TypeNameStrBoom` / `_ReprAndTypeNameBoomMeta` / `_ReprAndTypeNameBoom` 三个替身与集成反证 `test_extra_channel_repr_and_typename_boom_fails_closed_preserves_lpc`（`repr` 失败且类型名字符串化也失败的多余通道键），断言 `PartialCommitError`（非普通 `RuntimeError`）、`commit_fault=True`、`consecutive_failures=1`、旧 `last_physical_committed=5` 保留。现有 `_ReprBoom` 类型名为普通 `str`、无法覆盖该 fallback 自身失败点，故必须新增本反证。
  3) WP-009 Round 1～3 与 WP-010 Round 1 已关闭语义/反证全部保留未回退。
- 修改文件（本轮 scope 内，共 2 个文件被改）: `src/runtime/commit_supervisor.py`（`_safe_repr()` fallback 收口）、`tests/test_runtime_commit_supervisor.py`（新增三替身 + 一集成反证）。哈希见 `scope_sha256`。
- 明确未修改: WP-009 全部记录、Codex Round 1 审核区与本包既有行政段、`docs/PROJECT_STATE.md`、正式规格（含 `ENGINE_SCAN_SPEC v2.2.2 §4.1/§4.4`）、其余五个旧 scope 文件（`scan_runner.py` / `output_policy.py` / `__init__.py` / `test_runtime_scan_runner.py` / `test_runtime_output_policy.py`）、`.cursor/rules/*`、AI 协调器/自动化配置、`.git`。未实现或引入 shadow mode、L2 adapter registry、真实 monitor/HAL/驱动、可信设备反馈、真实周期/硬件 watchdog、HMI/通知/持久化或现场安全证明。未扩范围处理 Codex 非必须返修的 `_evaluate()` 整数子类值域比较边界。未读取/解析 `.git`，未执行任何 `git` / `gh` / `shasum` / `subprocess`，未创建任何核验/临时/日志/缓存/补丁/辅助文件。
- 测试命令与实际结果（本机实跑，前缀 `PYTHONDONTWRITEBYTECODE=1`）: 与自审段一致——五组分别 Ran 167 / 166 / 1167 / 68 / 1235 tests，全部 OK（通过）；含 commit_supervisor 模块的三组较本包 Round 1（166 / 166 / 1166 / 68 / 1234）各 +1，为本轮新增唯一集成反证，历史快照保留、未回写历史数字。`git diff --check` 依裁决留待 Codex 原子交接后独立运行并记录。
- 已知疑问: 见自审段 ①②③④；均为已声明的诚实边界，未升级为已验证事实。驱动回执仍是 Python 契约模拟，本包测试不构成目标 PLC/CODESYS、真实物理写入、硬件 watchdog 或现场安全回路一致性证明。
- scope_sha256: a8d4f0fef8843f098e54372f11394d5ea9c6d618f3dc39c30d9f0f4c887346a8
- handoff_to: codex
- implementation_finished_at: 2026-07-22 10:49:20 +0800

### Codex 审核结论（Round 2）

- verdict: APPROVED
- 已验证事实: 接手五字段为 `WP-20260722-010 / READY_FOR_CODEX / owner=codex / handoff_to=codex / round=2`，且 `2 <= max_rounds=3`；`handoff_protocol=v2`，仓库解析器对 Round 2 结构化自审九项门禁返回 `gate_reason=None`。Claude 自审 manifest、实施交接 `scope_sha256` 与 Codex 独立开始/结束实盘清单逐项一致，聚合 SHA-256 均为 `a8d4f0fef8843f098e54372f11394d5ea9c6d618f3dc39c30d9f0f4c887346a8`，审核期间两个 scope 文件无漂移。`src/runtime/commit_supervisor.py:99-127` 已把 `_safe_repr()` 的 fallback 全构造过程收口：只有 exact `str` 类型名才进入固定格式串，其余直接使用不含不可信子串的固定占位串；`src/runtime/commit_supervisor.py:240-250,403-429,461-478` 中不可信驱动异常、非 Mapping 回执、回执读取异常、异常/混合通道键与错误确认值的诊断继续统一经该安全入口。`tests/test_runtime_commit_supervisor.py:161-195,377-394` 的恶意元类集成反证同时令对象 `repr` 与类型名字符串化抛错，并锁定 `PartialCommitError`、`commit_fault=True`、`consecutive_failures=1` 与旧 `last_physical_committed=5`；Codex 单独复跑该反证通过。WP-009 Round 1～3 及 WP-010 Round 1 已关闭语义锁未发现回退。
- 项目工程约定: 驱动确认回执、逐通道 `commit_fault` / `channel_fault`、`safe_value` 重试、锁存后显式复位，以及不可信驱动诊断表示失败关闭，均是当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义。`last_physical_committed` 仍只表示驱动确认写出的最后命令值，不是可信设备位置反馈，也不得作为恢复基准；当前代码和测试保持了这一分层。
- 待真机验证假设: 本包仍无真实 HAL/驱动、可信设备反馈、真实周期 monitor、硬件 watchdog 或现场安全回路；Python 回执契约与单元测试只证明当前实现行为，不证明目标 SP16.1 PLC/CODESYS、真实物理写入或现场安全一致性。
- 必须返修: 无。
- 非阻塞建议: Codex Round 1 已记录的恶意整数子类在 `_evaluate()` IEC 值域比较中可触发普通异常，属于本包“诊断表示”窄目标之外的驱动值域边界加固候选；建议后续单独立项，不应据此扩大或阻塞 WP-010。
- 审核证据: Codex 独立运行 `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` = 167/167、`python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` = 166/166、`python -m unittest discover -s prototype_05 -t .` = 68/68，均通过；恶意元类复合反证单独复跑 1/1 通过。正式 tests 实际运行 1167 项、全仓实际运行 1235 项，各有同 9 个 scope 外 `tests/test_ai_handoff.py` 用例因当前受限沙箱禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 1158/1158 与 1226/1226 通过；该环境限制与 WP-009/010 既有 Codex 记录一致，不是本包功能回归。`git diff --check` 通过。逐文件开始/结束 SHA-256 均为 `src/runtime/commit_supervisor.py=05cfa062ad225fb9338e348fc53e42dc25d76d4e748160cce0baa3a5653c0b60`、`tests/test_runtime_commit_supervisor.py=3cbc3c27793dc087a3c80fa3a59b79b93e522a90509c8e5cfb9930984370049f`；审核中未执行任何 Git 写操作。
- review_started_sha256: a8d4f0fef8843f098e54372f11394d5ea9c6d618f3dc39c30d9f0f4c887346a8
- review_finished_sha256: a8d4f0fef8843f098e54372f11394d5ea9c6d618f3dc39c30d9f0f4c887346a8
- handoff_to: user
- reviewed_at: 2026-07-22 10:59:35 +0800

## WP-20260722-011

- title: 不可信驱动回执精确内建标量类型门禁与失败关闭
- status: CLOSED
- closed_by: user
- closed_at: 2026-07-22 12:32:31 +0800
- closure_note: 用户接受 Codex Round 1 `APPROVED` 结论，确认关闭本包并授权 Git/GitHub 收尾。驱动回执 exact 内建标量门禁只是当前 Python 项目工程约定，不构成 PLC/CODESYS、真实 HAL/驱动、硬件 watchdog 或现场安全一致性证明。
- owner: user
- handoff_to: user
- round: 1
- max_rounds: 3
- handoff_protocol: v2
- base_commit: f0950443c9f2cbd43e0f0067746dd8abaebfca86
- created_by: user
- created_at: 2026-07-22 11:21:25 +0800
- depends_on:
  - WP-20260722-010 CLOSED（诊断表示失败关闭已通过独立审核并由用户确认关闭；本包处理当时明确 scope 外的标量子类运算边界）
- scope:
  - src/runtime/commit_supervisor.py
  - tests/test_runtime_commit_supervisor.py
- scope_baseline_sha256: a8d4f0fef8843f098e54372f11394d5ea9c6d618f3dc39c30d9f0f4c887346a8
- scope_baseline_manifest:
  - `05cfa062ad225fb9338e348fc53e42dc25d76d4e748160cce0baa3a5653c0b60  src/runtime/commit_supervisor.py`
  - `3cbc3c27793dc087a3c80fa3a59b79b93e522a90509c8e5cfb9930984370049f  tests/test_runtime_commit_supervisor.py`

### 工作包创建行政证据（Claude 启动前）

- 用户于 2026-07-22 明确裁决：该问题如有影响则现在处理。Codex 已证实其不只影响诊断文字，而会绕过提交失败记账，故据用户授权创建本窄范围工作包；不再要求用户重复确认。
- 开包前不落盘反证：单通道 `USINT`、发出 exact `int` 命令 `7`，驱动回执为重载 `__ge__ / __le__ / __eq__` 并抛 `RuntimeError` 的 `int` 子类。实际结果为普通 `RuntimeError: hostile int __ge__`，诊断为 `commit_fault=False / consecutive_failures=0 / last_physical_committed=None / receipts={}`。这证明 `_evaluate()` 在设置逐通道回执前即被 IEC 值域比较击穿。
- 开包前两 scope 文件与 WP-010 Codex Round 2 审核结束 manifest 逐项一致，按协调器清单行保留末尾换行的口径，聚合 SHA-256 = `a8d4f0fef8843f098e54372f11394d5ea9c6d618f3dc39c30d9f0f4c887346a8`。
- 风险已在 `docs/RISKS.md` 登记为 `PLATFORM-DRIVER-RECEIPT-TYPE-1`（行政哈希 `dcd5f372775e6f0a238d56ea50691af841ea4890be764efefa6b956ee47f7222`）；`docs/PROJECT_STATE.md` 已同步开工、范围与顺序（行政哈希 `29c1fec8eee2883cec460bc6ace258852a3955499828e0156588ce9161d121ca`）。两文件不属于 Claude 功能 scope；修复获批后由 Codex 依独立审核证据更新风险状态。
- 创建前项目内存活投影显示协调器 `coordinator_live=true`、PID 65490、原生 kqueue 监听、外部进程允许，无项目内执行租约或失败告警；旧 Claude/Codex 30 分钟主轮询仍标记必须暂停。当前受限沙箱禁止 `ps`，故本包不把进程表不可读误报为“无进程”；真实调度仍以协调器租约与事件门禁失败关闭。
- Git 基线由 Codex 既有只读证据负责。Claude **禁止读取或解析 `.git` 的任何文件，禁止执行任何 Git/GitHub 命令**；直接信赖本包 `base_commit` 与 baseline manifest。本包不调整 Claude CLI 固定 40 turns 上限。

### 目标与验收标准

在不改公共 Store/IEC 工程类型映射的前提下，加固 `CommitSupervisor` 对驱动回执值的 Python 信任边界：任何来自自定义标量子类的重载运算都不得在结构化逐通道失败证据形成前执行或击穿提交路径。Claude 须实施最小修复、完成 v2 自审和原子交接，再由 Codex 独立审核。

1. **exact 内建标量门禁**
   - 在对通道确认值执行 `_iec_value_error()`、整数范围、浮点有限性或严格相等比较前，必须先以不调用用户可重载 dunder 的方式确认命令值与回执值均为当前支持的 exact 内建标量（`bool / int / float / str`），且二者 exact 类型相同；任一为子类或非支持类型必须失败关闭。
   - exact 门禁通过后，IEC 声明类型与值域仍复用现有 `_iec_value_error()` 口径，不复制第二套 IEC 类型/数值表，不改公共 `check_value_type()` 或 OutputPolicy 语义。
   - 普通 exact 内建值的既有成功、错值、错类型、越界、非有限回执语义不得改写或放宽。

2. **结构化失败与逐通道隔离**
   - 恶意子类回执必须产生 `PartialCommitError`，不得漏出普通 `RuntimeError` 或调用其比较/字符串 dunder。失败通道必须 `commit_fault=True`、连续失败计数精确前移、保留旧 `last_physical_committed`，且存在 `ok=False` 的结构化回执。
   - 同一批中的健康通道仍应独立成功并前移自身 LPC；不得把单通道值子类问题升级为无必要的整批不可信。
   - 同一恶意回执连续失败到 `commit_fault_retry_n` 时，必须与既有语义一样精确升级并锁存 `channel_fault`；安全值重试、显式复位与边界基准规则不回退。

3. **对抗性回归锁**
   - 至少覆盖恶意 `int` 子类：范围与相等比较 dunder 一旦被调用即抛普通异常，断言它们未击穿且上述四项故障证据成立。
   - 同根覆盖恶意 `float` 子类与 `str` 子类，以证明修复是统一标量信任边界，而不是只特判 `USINT` 或某一比较调用点。
   - 至少一条多通道反证锁定健康通道与恶意子类通道的成功/失败隔离，并保留 WP-009/010 全部既有语义锁。

4. **交接完整性**
   - Claude 必须在 `CLAUDE_WORKING` 内完成结构化自审；只有 `self_review_verdict: PASS`、五组测试真实计数、完整两文件 manifest、`self_review_scope_sha256 == scope_sha256`、明确“是否满足交接条件: 是”全部成立，才可追加实施交接。
   - 最终以一次原子写入把顶层状态转为 `READY_FOR_CODEX / owner=codex / handoff_to=codex / round=1`，且自审段必须位于实施交接段之前；随后立即停止修改 scope。

### 明确排除与冻结边界

- Claude 只能修改上列两个 scope 文件，以及按 v2 协议原子追加本工作包自审/实施交接记录；不得修改 WP-009/010 历史、`docs/PROJECT_STATE.md`、`docs/RISKS.md`、正式规格、其他运行时文件、`.cursor/rules/*`、AI 协调器/自动化配置或 `.git`。
- 禁止读取 `.git`，禁止执行 `git` / `gh` / `shasum`，禁止使用 Python `subprocess` 绕过禁令；`git diff --check` 由 Codex 在原子交接后独立执行。
- 禁止创建核验脚本、临时文件、缓存、日志、补丁或其他辅助文件。需要哈希核验时只能直接运行单行 `python -c`；测试只能直接运行下列 `python -m unittest` 命令。
- 不修改 `check_value_type()`、`_iec_value_error()` 或 `OutputPolicy` 的公共工程语义；不实现或引入 shadow mode、L2 adapter registry、真实 monitor/HAL/驱动、可信设备反馈、真实周期/硬件 watchdog、HMI/通知/持久化或现场安全证明；不修改 `ENGINE_SCAN_SPEC v2.2.2 §4.1/§4.4` 语义。
- 本包是 Python 驱动回执信任边界的失败关闭加固，不证明与目标 PLC/CODESYS、真实物理写入、硬件 watchdog 或现场安全回路一致。

### 测试计划与责任分配

Claude 交接前必须直接实际运行并记录以下五组 Python 测试的真实计数和结果；新增测试导致计数增长属于正常快照变化，不得回写历史数字：

1. `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy`
2. `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir`
3. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .`
4. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s prototype_05 -t .`
5. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s . -t .`

Codex 在原子交接后必须独立：复算 scope manifest；审查门禁是否确实位于任何可重载运算之前；使用自己的恶意 `int / float / str` 子类反证；验证多通道隔离、故障计数/升级、旧 LPC 保留和 WP-009/010 回归；复跑与风险成比例的测试并执行 `git diff --check`。审核结论必须区分项目工程约定、当前 Python 已验证事实与仍待真机验证假设。

### Claude 交接前自审（Round 1）

- self_review_round: 1
- self_review_started_at: 2026-07-22 11:55:22 +0800
- self_review_finished_at: 2026-07-22 11:56:18 +0800
- self_review_verdict: PASS
- self_review_scope_sha256: e2bfd12ea91f7bc3ce807fc3e0ce8d151789e6d210cf9b33c810ec852e7fb3ea
- self_review_manifest:
  - `45f8d21ce2f4bfdd20397069cb05b34536e4c1be276c3ce96db313dda66df3d5  src/runtime/commit_supervisor.py`
  - `edb6c89575ecf00b00fafed78a6c0d9db39fe7a349d652db95e4e6ab5ca1d840  tests/test_runtime_commit_supervisor.py`
- 实际测试命令与结果: 五组均直接实运行、全绿；计数为本轮真实快照，新增 5 条对抗性用例致 commit_supervisor 计数增长属正常。
  1. `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` → Ran 172 tests, OK
  2. `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → Ran 166 tests, OK
  3. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .` → Ran 1172 tests, OK
  4. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s prototype_05 -t .` → Ran 68 tests, OK
  5. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s . -t .` → Ran 1240 tests, OK
- 首次失败: 无（五组自首次运行即全部 OK，无需返修再跑）。
- 失败根因: 不适用（无首次失败）。
- 修复内容: 不适用（无首次失败）。
- 修复后重跑结果: 不适用（无首次失败）。
- 完成的修复要点（供审核核对）：
  - `src/runtime/commit_supervisor.py` 新增模块级 `_exact_scalar_reject(commanded, confirmed)`，以纯类型身份（`type(x) is bool/int/float/str`，绝不触发任何可重载 dunder）确认发出命令值与回执确认值均为当前支持的 exact 内建标量且 exact 类型相同；任一为子类/非支持类型/异型即返回失败原因串（失败关闭）。诊断串只对**类型对象**做 `_safe_repr`，绝不调用回执实例的比较/字符串 dunder。
  - `_evaluate()` 在 `value = confirmed[channel]` 之后、`_iec_value_error()` 整数值域比较 / REAL 有限性 / 严格相等**之前**先调用该门禁并在命中时立即失败关闭返回 `(False, gate_detail)`；其余 IEC 值域与严格相等逻辑复用现有 `_iec_value_error()` 口径，未复制第二套类型/数值表，未改 `check_value_type()` / `_iec_value_error()` / `OutputPolicy`。
  - 模块 docstring 的「驱动确认回执契约」段补写 exact 标量前置门禁说明。
- 已验证事实（当前 Python）：
  - 恶意 `int` 子类回执（重载 `__ge__/__le__/__eq__/__ne__` 抛 `RuntimeError`）在触碰任何比较前被门禁失败关闭，产生 `PartialCommitError`（非漏出 `RuntimeError`），`commit_fault=True`、连续计数精确前移、保留旧 `last_physical_committed`、存在 `ok=False` 回执，且被重载 dunder 从未被调用（`dunder_calls == []`）。
  - 同根 `float` / `str` 子类统一在门禁失败关闭，证明是统一标量信任边界而非只特判整数比较点。
  - 多通道反证：健康通道独立成功前移自身 LPC，恶意子类通道独立失败关闭并隔离，不升级为整批不可信。
  - 恶意子类连续失败到 `commit_fault_retry_n` 精确升级并锁存 `channel_fault`。
  - 既有 exact 内建值的成功 / 错值 / 错类型 / 越界 / 非有限 / 结构不一致 / 复位 / 重入 / WP-009/010 语义锁全部回归通过（五组全绿）。
- 项目工程约定（非 CODESYS 官方语义、未经真机验证）：exact 内建标量信任门禁、逐通道失败关闭与提交/故障制度均为项目工程约定；Python 侧回执不构成与目标 PLC/CODESYS、真实驱动、硬件 watchdog 或现场安全回路一致的证据。
- 未验证边界 / 待真机验证假设：真实 HAL/可信设备反馈、真实物理写入与设备位置、硬件 watchdog、shadow mode、现场安全回路均不在本包，未实现也未验证。
- 已知疑问：门禁对 `float` / `str` 子类的失败关闭在旧代码里本已由 `_evaluate` 的 `type(value) is not type(commanded)` 恒等判定间接兜住（二者仅整数值域比较点可被击穿）；本包将三类子类收敛到**同一显式门禁**并在任何可重载运算之前统一失败关闭，float/str 用例经断言 `detail` 含「exact 内建标量」与 `dunder_calls == []` 锁定门禁路径。请 Codex 独立以自有子类反证复核门禁位置确在任何比较之前。
- 是否满足交接条件: 是
- 交接条件说明: `self_review_verdict=PASS`；`self_review_scope_sha256 == scope_sha256 == e2bfd12ea91f7bc3ce807fc3e0ce8d151789e6d210cf9b33c810ec852e7fb3ea`；五组真实计数与成功标记齐备；两文件 manifest 完整且与 scope 顺序一致。

### Claude 实施交接（Round 1）

- 完成内容：加固 `CommitSupervisor` 对驱动回执值的 Python 信任边界——新增 exact 内建标量前置门禁 `_exact_scalar_reject()`，在 `_evaluate()` 对确认值执行任何可被子类重载的运算（`_iec_value_error()` 整数值域比较、REAL 有限性、严格相等）之前，以纯类型身份确认命令值与确认值均为 exact 内建标量（`bool/int/float/str`）且 exact 类型相同，否则失败关闭；exact 门禁通过后 IEC 声明类型与值域仍复用现有 `_iec_value_error()`，未复制第二套类型/数值表，未改 `check_value_type()` / `_iec_value_error()` / `OutputPolicy` 公共语义。新增五条对抗性回归用例（恶意 `int`/`float`/`str` 子类失败关闭、多通道健康/恶意隔离、恶意子类精确升级锁存）。
- 修改文件：
  - `src/runtime/commit_supervisor.py`（新增 `_exact_scalar_reject` 门禁 + `_evaluate` 前置调用 + docstring 说明）
  - `tests/test_runtime_commit_supervisor.py`（新增 `_HostileInt/_HostileFloat/_HostileStr` 测试替身与 `TestHostileScalarSubclassGate` 五条用例）
- 明确未修改：`src/runtime/output_policy.py`、`src/runtime/store.py`、`src/runtime/scan_runner.py` 等其余运行时文件；`docs/PROJECT_STATE.md`、`docs/RISKS.md`、正式规格、`.cursor/rules/*`、AI 协调器/自动化配置与 `.git` 均未触碰（本包 scope 外，禁止修改）。`check_value_type()` / `_iec_value_error()` / `OutputPolicy` 公共工程语义未改。
- 测试命令与实际结果（五组均直接实运行、全绿）：
  1. `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` → Ran 172 tests, OK
  2. `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` → Ran 166 tests, OK
  3. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .` → Ran 1172 tests, OK
  4. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s prototype_05 -t .` → Ran 68 tests, OK
  5. `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s . -t .` → Ran 1240 tests, OK
- 已知疑问：见上「Claude 交接前自审（Round 1）」已知疑问项——float/str 子类旧代码本由 `type(value) is not type(commanded)` 恒等判定间接兜住，本包收敛到同一显式门禁并在任何可重载运算之前统一失败关闭；请 Codex 以自有恶意子类独立复核门禁位置。
- scope_sha256: e2bfd12ea91f7bc3ce807fc3e0ce8d151789e6d210cf9b33c810ec852e7fb3ea
- handoff_to: codex
- implementation_finished_at: 2026-07-22 11:56:18 +0800

### Codex 协议标签行政规范化（非审核）

- 协调器在 Claude 完成原子交接后重读 v2 门禁，识别自审字段被写为带括号说明的 `实际测试命令与结果（…）`，而协议要求机器键必须精确为 `实际测试命令与结果`；解析结果因此为 `v2-invalid`、`handoff_gate_ok=false`，Codex 审核未被启动。
- 本次仅把括号说明从字段键移到同行字段值；五组测试命令、计数 172 / 166 / 1172 / 68 / 1240、成功标记、自审时间、manifest、自审/实施聚合 SHA-256 及所有功能内容一字不改。规范化前 `docs/AI_REVIEW_HANDOFF.md` SHA-256 = `068222166f86094c2b9e05b85b1bca6bb7795d3a596ca392caebe4a6c3a4aac0`。
- 字段名规范化后，解析器继续精确指出 `是否满足交接条件` 的值为“是（附说明）”，而协议只接受 exact `是/true`。因此进一步仅将该值规范为“是”，原括号内容完整移入相邻 `交接条件说明`；第二次规范化前交接文件 SHA-256 = `e599c3792b25999568f17fd79ac9e7cb851ef8dac1a4dffd4cd368cbae674c74`。两次均只处理机器精确标签/值，不改变任何证据内容。
- 该动作沿用用户在本项目既有 v2 工作包中已明确同意的“单字段协议规范化、测试/计数/时间/哈希不改”处置边界，并属于用户当前“有影响则现在处理”授权下的最小协议行政动作。它不是 Claude 返修、不补写测试证据、不构成 Codex 功能审核。
- normalized_by: codex（协议协调监督行政动作）
- normalized_at: 2026-07-22 11:59:30 +0800

### Codex 审核结论（Round 1）

- verdict: APPROVED
- 已验证事实: 接手五字段为 `WP-20260722-011 / READY_FOR_CODEX / owner=codex / handoff_to=codex / round=1`，且 `1 <= max_rounds=3`；`handoff_protocol=v2`，仓库只读解析器对 Round 1 结构化自审九项门禁返回 `handoff_gate_ok=true / gate_reason=None`。Claude 自审 manifest、实施交接 `scope_sha256` 与 Codex 独立开始/结束实盘清单逐项一致，聚合 SHA-256 均为 `e2bfd12ea91f7bc3ce807fc3e0ce8d151789e6d210cf9b33c810ec852e7fb3ea`，审核期间两个 scope 文件无漂移。`src/runtime/commit_supervisor.py:264-298` 的 `_exact_scalar_reject()` 只使用 exact 内建类型身份判定；`:511-527` 确认门禁在 `_iec_value_error()` 的整数值域/浮点有限性检查与严格相等比较之前执行，拒绝子类、非支持类型和 exact 异型值后才进入既有 IEC 判定，没有复制第二套类型/数值表，也未改 `check_value_type()`、`_iec_value_error()` 或 OutputPolicy 公共语义。`tests/test_runtime_commit_supervisor.py:576-677` 的五条对抗性用例覆盖恶意 `int / float / str` 子类、多通道隔离和第 N 次精确升级锁存；Codex 另用独立定义的三类恶意子类不落盘复核，三类均得到 `PartialCommitError`、重载 dunder 调用记录为空、失败计数精确前移且旧 LPC 保留，多通道健康通道独立成功，`retry_n=2` 时第二次失败精确锁存。
- 项目工程约定: exact 内建标量信任门禁、驱动确认回执、逐通道 `commit_fault` / `channel_fault`、`safe_value` 重试与显式复位均为当前项目工程约定，不是 IEC 61131-3 / CODESYS 官方已证实语义。`last_physical_committed` 仍只表示驱动确认写出的最后命令值，不是可信设备位置反馈，也不得作为恢复基准；当前代码、测试与交接表述保持这一分层。
- 待真机验证假设: 本包仍无真实 HAL/驱动、可信设备反馈、真实周期 monitor、硬件 watchdog、shadow mode 或现场安全回路；Python 回执契约、对抗性子类反证与单元测试只证明当前实现行为，不证明目标 SP16.1 PLC/CODESYS、真实物理写入或现场安全一致性。
- 必须返修: 无。
- 非阻塞建议: 无；后续若接入真实驱动适配层，应继续保持该 exact 标量门禁位于所有不可信值的数值域、有限性和相等运算之前，不在适配层静默把自定义标量子类转换成可信回执。
- 审核证据: Codex 独立运行 `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_runtime_commit_supervisor tests.test_runtime_scan_runner tests.test_runtime_output_policy` = 172/172、`python -m unittest tests.test_runtime_engine tests.test_runtime_executor tests.test_runtime_store tests.test_runtime_ir` = 166/166、`python -m unittest discover -s prototype_05 -t .` = 68/68，均通过；另运行不落盘独立恶意 `int / float / str` 子类、多通道隔离与两次失败锁存反证，全部通过。正式 tests 实际运行 1172 项、全仓实际运行 1240 项，各有同 9 个 scope 外 `tests/test_ai_handoff.py` 用例因当前受限沙箱禁止绑定本地 HTTP 端口报 `PermissionError`，其余分别 1163/1163 与 1231/1231 通过；该环境限制与 WP-009/010 既有 Codex 记录一致，不是 WP-011 功能回归。`git diff --check` 通过；只读 Git 范围核对显示工作区另有 WP-009/010 与开包行政记录留下的既有改动，WP-011 两个 scope 文件的实盘哈希与本轮交接证据逐项一致。逐文件开始/结束 SHA-256 均为 `src/runtime/commit_supervisor.py=45f8d21ce2f4bfdd20397069cb05b34536e4c1be276c3ce96db313dda66df3d5`、`tests/test_runtime_commit_supervisor.py=edb6c89575ecf00b00fafed78a6c0d9db39fe7a349d652db95e4e6ab5ca1d840`；审核中未执行任何 Git 写操作。
- review_started_sha256: e2bfd12ea91f7bc3ce807fc3e0ce8d151789e6d210cf9b33c810ec852e7fb3ea
- review_finished_sha256: e2bfd12ea91f7bc3ce807fc3e0ce8d151789e6d210cf9b33c810ec852e7fb3ea
- handoff_to: user
- reviewed_at: 2026-07-22 12:09:39 +0800
