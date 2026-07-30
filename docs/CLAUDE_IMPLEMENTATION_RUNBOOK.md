# Claude 实施方运行手册（CLAUDE_IMPLEMENTATION_RUNBOOK）

> **用途**：把 Claude（实施方）在本仓库长期稳定、可执行的纪律集中到一个文件，作为每次开工的**第一必读**。它解决历史上多次重复出现的同类失误：未完整读取约束、使用不在 allowlist 的命令、结构化字段格式不可解析、以估算/旧时间冒充真实宿主时间、更新错来源包/承接包状态、以及应停笔时继续扩大 scope。
> **它不是**：不是新的技术规格，不覆盖 `CODEX_GUIDE.md`、`docs/AI_REVIEW_HANDOFF.md` 协议区、各主题规格或 `RISKS.md`；发现冲突时以那些权威源为准，本手册只提炼**长期稳定的实施纪律**。
> **它不证明正确性**：读完本手册不等于产出正确。Claude 的 v2 交接前自审、原子交接与 Codex 独立审核继续强制，任何一环都不能省略。
> **命名**：实施方人类可见称呼统一为 **Claude**；历史 `Fable5` / `fable5` / `FABLE_WORKING` 仅作只读兼容，新内容一律写 Claude / `CLAUDE_WORKING` / `Claude 实施交接`。

---

## 1. 权威读取顺序（任何写入前完成）

严格按下列顺序，先读后写；不得用旧对话快照覆盖仓库实盘：

1. **本手册** `docs/CLAUDE_IMPLEMENTATION_RUNBOOK.md`（第一必读，长期纪律）。
2. `CODEX_GUIDE.md`（长期协作方针与角色边界）。
3. `docs/AI_REVIEW_HANDOFF.md` 的**协议区**与**当前工作包**全文（状态机、五字段映射、写入权、硬规则、三阶段职责、九项交接门禁）。
4. 按当前包再读：`docs/AI_HANDOFF_OPERATIONS.md`、`docs/PROJECT_STATE.md`、`docs/PLATFORM_ROADMAP.md`、`docs/COMPONENT_CONTRACT.md` 与本任务适用的主题规格（`IR_SPEC.md` / `ENGINE_SCAN_SPEC.md` / `TARGET_PROFILE.md` / `GOLDEN_TRACE_FORMAT.md` 视任务而定）。
5. 代码任务再读 scope 内**源码与测试**，以及适用的 `.cursor/rules/*.mdc`。
6. 状态与功能索引：`docs/SOFT_PLC_FUNCTION_MATRIX.md`（只读涉及的 ID）与 `docs/RISKS.md` 相关条目。

“读”指用 `Read` 工具读取实盘文件内容，不是凭记忆或对话摘要。

## 2. 开工零写入检查表（不符即停笔）

完成必读后、**任何写入之前**，逐项核验，任一与任务书不符立即停笔并报告，不猜测、不擅自修复：

- **五字段 + 轮次 + 协议**：`work_package_id / status / owner / handoff_to / round / max_rounds / handoff_protocol`。Claude 只在 `CLAUDE_WORKING(owner=claude, handoff_to=claude)` 或 `CHANGES_REQUESTED(owner=claude, handoff_to=claude)` 两种组合接手；`round <= max_rounds`；非 legacy 白名单包必须 `handoff_protocol: v2`。
- **Git 基线**：`main == origin/main == HEAD == base_commit`，工作区符合任务书预期。
- **scope 证据（按接手状态取不同连续性基准，与 `tools/ai_handoff/scheduler.py` 的 `_expected_scope_hash` / `_validate_scope_integrity` 一致）**：一律按 `scope` 声明顺序逐文件重算 SHA-256 再算聚合，但**比对基准随状态不同**，切勿把两种状态都拿初始 baseline 比对：
  - **首轮 `CLAUDE_WORKING`（初次实施）**：当前聚合必须等于 `scope_baseline_sha256`；仅此状态允许尚未创建的文件以 `ABSENT  <path>` 参与基线。
  - **`CHANGES_REQUESTED`（返修接手）**：先确认上一轮审核 `review_started_sha256 == review_finished_sha256`（不一致说明审核期间 scope 已漂移，立即停笔），再要求当前聚合等于该 `review_finished_sha256`——正常工作包首轮修改后它通常**不等于** `scope_baseline_sha256`，若仍拿 baseline 比对会把合法返修误判为漂移。
  - 其他状态缺文件即拒绝。
- **冻结依赖**：任务书声明的检查点/冻结哈希与实盘一致。
- **协调器 / 租约 / 旧轮询**：`.ai-handoff-runtime/coordinator_status.json` 只作只读存活投影（同时校验 `coordinator_live` 与 `valid_until_epoch`）；心跳异常只告警，绝不据此恢复旧 30 分钟轮询或取得执行权。

## 3. 允许命令范例（与执行计划的 allow/deny 完全一致）

Claude 的执行计划 `--allowedTools` 只放行 `Read,Edit,Write,Glob,Grep` 与 `Bash(python *) / Bash(python3 *) / Bash(PYTHONDONTWRITEBYTECODE=1 python *) / Bash(PYTHONDONTWRITEBYTECODE=1 python3 *)`；`--disallowedTools` 明确阻断 `Bash(git *) / Bash(gh *) / Bash(rm *) / Bash(sudo *)`。据此：

**允许（可复制范例）**

- 文件读取 / 修改 / 检索：`Read`、`Edit`、`Write`、`Glob`、`Grep` 工具。
- 单文件 SHA-256：
  `python3 -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('tools/ai_handoff/scheduler.py').read_bytes()).hexdigest())"`
- scope manifest 与聚合哈希（按声明顺序；打印的每行与参与聚合的规范文本来自**同一字符串**，严格保留「64 位 SHA-256 + 两个空格 + 路径 + 换行」，避免复制输出与聚合不一致而被解析器拒绝）：
  `python3 -c "import hashlib,pathlib; s=['a.py','b.py']; lines=[(('ABSENT' if not pathlib.Path(p).exists() else hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest())+'  '+p+chr(10)) for p in s]; [print(line,end='') for line in lines]; print('AGG', hashlib.sha256(''.join(lines).encode()).hexdigest())"`
- 真实宿主时间（带时区）：
  `python3 -c "import datetime; print(datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S%z'))"`
- 运行测试（单条）：
  `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_ai_handoff`

**禁止（一律不使用）**

- `git`、`gh`、`shasum`、`sha256sum`、`rm`、`sudo`。
- 管道 `|`、命令替换 `$(...)` / 反引号、`for` / `while` 等 shell 循环、`&&` 或 `;` 串联、重定向拼接等**任何复合 Bash**。
- 需要哈希用 Python `hashlib`；**删除或移动文件的需求必须立即停笔并报告**，未经用户明确授权不得用 `Edit`/`Write` 清空、覆写或变相移动目标（与 §7 停笔清单及启动器 prompt 的失败关闭口径一致）；Git/GitHub 收尾一律留给 Codex。

若某个必要命令被 `dontAsk` 拒绝，视为失败关闭信号：停笔并报告，不迂回绕过。

## 4. 实施纪律

- **只改 scope**：保留工作区其它已有改动，不做顺手重构。
- **先反证后修复**：新增行为先写会失败的测试/反证，再实现使其通过；只能验证文档/接口时明确说明原因。
- **历史只读**：历史工作包结论、历史测试数字原样保留，不回写冒充当前基线。
- **状态索引分清角色**：区分**来源包**、**恢复包**、**当前承载包**；只更新当前包实际改变的状态轴，不为每轮测试制造无意义行。
- **四级验证分轴**：Python / PLC·CODESYS / HAL / 现场四级永不互推；Python 通过 ≠ 与 PLC 一致。
- **Git 分工**：任何 Git/GitHub 写操作（暂存、建分支、提交、推送、合并、写 `.git/`）一律由 Codex 审核并执行；Claude 只在交接文件提供修改清单与测试证据。

## 5. v2 精确交接模板（字段名逐字，禁止装饰）

交接记录必须能被解析器机器解析。以下字段名**逐字使用**，字段名后不得加括号、冒号说明或改成表格/小标题：

```
### Claude 交接前自审（Round N）
- self_review_started_at: 2026-07-30 10:00:00+08:00
- self_review_finished_at: 2026-07-30 10:20:00+08:00
- self_review_verdict: PASS
- self_review_round: N
- 实际测试命令与结果:
  - PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_ai_handoff —— Ran 152 tests, OK
  - PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t . —— Ran 1570 tests, OK
- self_review_scope_sha256: <64位十六进制>
- self_review_manifest:
  - <sha256>  docs/CLAUDE_IMPLEMENTATION_RUNBOOK.md
  - <sha256>  tools/ai_handoff/scheduler.py
- 首次失败: 无（本轮实现/返修一次通过）；如有则写首个失败的命令与断言。
- 失败根因: 不适用（无首次失败）；如有则写定位到的根因。
- 修复内容: 不适用（无首次失败）；如有则写针对根因的修复。
- 修复后重跑结果: 不适用（无首次失败）；如有则写重跑命令与同行 `Ran N tests, OK`。
- 已知疑问: 无新增（如有逐条列出，并说明是否阻塞交接）。
- 未验证边界: 本包仅验证协作工具与文档，不构成 PLC/CODESYS、HAL、物理 I/O、硬件 watchdog 或现场安全证明。
- 是否满足交接条件: 是

### Claude 实施交接（Round N）
- 完成内容: ...
- 修改文件: ...
- 明确未修改: ...
- scope_sha256: <64位十六进制，与 self_review_scope_sha256 相等>
- implementation_finished_at: 2026-07-30 10:22:00+08:00
- handoff_to: codex
```

自审 `PASS` 后，**同一次写入原子更新工作包顶层五字段**（`status + owner + handoff_to` 同步转移、`round` 保持当前轮，不留中间态），随后立即停止修改 scope：

```
- status: READY_FOR_CODEX
- owner: codex
- handoff_to: codex
- round: N
```

硬约束：

- 三个结构化字段名逐字为 `- 实际测试命令与结果:`、`- self_review_manifest:`、`- 是否满足交接条件: 是`。
- 每条 unittest 结果**同一行**写 `Ran N tests, OK`；出现 `FAILED` / `FAIL` / `ERROR` / `失败` 即拒绝（等额计数不能覆盖失败标记）；正文/已知疑问里的 `Ran N tests` 不算数。
- `self_review_manifest` 每项为「64 位十六进制 SHA-256 + 两个空格 + 路径」，路径与 `scope` **精确一致且顺序相同**；按声明顺序重建 `<sha256>  <path>\n` 的 SHA-256 必须等于 `self_review_scope_sha256`。
- 自审段须**完整**给出协议 `docs/AI_REVIEW_HANDOFF.md` 三阶段职责要求的全部字段：起止时间、`self_review_verdict`、`self_review_round`、实际测试命令与真实计数、`self_review_scope_sha256`、`self_review_manifest`、`首次失败 / 失败根因 / 修复内容 / 修复后重跑结果 / 已知疑问 / 未验证边界`、`是否满足交接条件`；无失败时相应字段明确写「无 / 不适用」，不得省略字段名或只放正文。
- 自审 `self_review_scope_sha256` 与实施交接 `scope_sha256` 必须**相等**；实施交接 `Round` 等于当前 `round` 且位于自审**之后**；随后按上方原子块**一次写入**顶层 `status / owner / handoff_to` 并保持 `round`。
- 时间戳整串匹配 `YYYY-MM-DD HH:MM[:SS][时区]`；时区只接受 `Z`/`UTC`/`CST`/`±HH:MM`/`±HHMM`（`CST` 本项目为 Asia/Shanghai=UTC+08:00，naive 也按 +08:00 解释）；结束不早于开始。
- 时间只能用第 3 节的单条 Python 命令读取真实宿主时间，禁止估算或沿用旧值。

## 6. 历史易错项与正确替代（只提炼规则，不改写历史结论）

- **WP-027 / WP-028（受限命令 / 复合 Bash）**：曾使用 `git` / `shasum` 或 `&&` 串联导致被拒或不可复核。**替代**：哈希用 Python `hashlib`，命令保持单条，Git 留给 Codex。
- **WP-030 / WP-031（承接包与状态索引）**：曾把行政同步/来源包与当前承载包状态混写。**替代**：明确区分来源包/恢复包/当前包，只更新当前包实际改变的轴，历史数字原样保留。
- **WP-043（测试计数格式）**：结构化字段写成 `OK，Ran N` 被判 `v2-invalid`。**替代**：每条同一行写 `Ran N tests, OK`。
- **WP-046（受限审核环境计数混用）**：把受限环境（部分端口权限被拒）的计数当成主线基线。**替代**：区分实施环境与审核环境计数，任何一方不得写成已批准主线。
- **WP-049 / WP-050（真实时间 / manifest 密码学绑定 / 应停笔时扩 scope）**：曾以旧时间冒充真实宿主时间、manifest 顺序或哈希与 scope 不绑定、应停笔时继续扩大范围。**替代**：真实时间用单条 Python 命令现读；manifest 顺序严格等于 scope 并重算聚合；触发停笔条件时立即停止，不扩 scope、不伪造 PASS。

## 7. 停笔清单（命中任一立即安全停止并报告）

- scope 或冻结依赖哈希漂移；需要修改 scope 外文件或扩大 scope。
- 规格、默认值或语义不明确，需规格裁决。
- 测试出现真实失败且未定位根因。
- 允许命令被拒（`dontAsk` 拒绝），或需要使用被禁命令。
- 触及 Git/GitHub 写、删除、依赖安装、外部网络或项目外文件。
- 达到本包 `max_rounds`（轮次耗尽）。
- 配额 / 认证 / 代理失败，或无法取得真实宿主时间、真实测试计数。

停笔时：如实报告阻塞点，保持 `CLAUDE_WORKING`（或按协议置 `BLOCKED` 交用户）；**不得伪造 PASS，不得自行创建恢复包**。

## 8. 交接后纪律

- 仅在自审 `PASS`、且自审哈希等于实施交接 `scope_sha256`、且有真实测试计数时，才原子写 `status: READY_FOR_CODEX / owner: codex / handoff_to: codex`（一次写入，同时更新三字段），随后**立即停止修改 scope**。
- 交接后 Claude 对 scope 保持只读，等待 Codex 独立审核；不得自行审核、关闭、提交、推送或更新矩阵的 Git 列。
- Codex 审核是独立阶段：Claude 不冒充审核，也不据自述代替 Codex 检查。
