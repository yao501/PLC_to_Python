# AI 协作进度面板与事件入口操作说明（v1.3）

## 它解决什么问题

Claude / Codex 协作历史上由两个 30 分钟定时任务轮询驱动，完整记录在
`docs/AI_REVIEW_HANDOFF.md` 中（其中历史 `Fable5` 记录仅作只读兼容保留）。
记录可追溯，但不适合用户快速看出“现在轮到谁、
做到哪一轮、有什么阻塞、下一步是什么”。

本工具把这些信息只读汇总到本地网页，并为事件触发提供文件监听、
幂等键、文件锁、防抖、异常记录和两端非交互执行契约。v1.2 已补齐异步执行生命周期、
跨进程全局单执行器锁、崩溃恢复和持久失败告警。真实执行仍需显式开关并通过 Claude
登录探针；普通启动始终是 dry-run。正式切换时旧主轮询必须保持暂停，避免绕过全局租约形成
第二条执行通路。

## 一眼看懂当前架构

```text
docs/AI_REVIEW_HANDOFF.md（唯一权威来源）
          |
          +-- 只读解析器 --> 内存状态 --> 本地网页 /api/status
          |
          +-- 目录 + 目标文件 kqueue 事件 --> 防抖 --> 重新读取
                                          |
                                          +-- dry-run 调度候选记录
                                          +-- 或显式 live 调度
                                                |
                                                +-- 全局执行租约
                                                +-- 异步进程生命周期
                                                +-- 崩溃恢复 / 持久失败告警
```

- 网页和 API 只读，没有改状态按钮。
- 运行时不生成权威 JSON 副本。页面中的数据可随时从 Markdown 重建。
- dry-run 记录默认放在 macOS 临时目录的 `ai-handoff-<项目哈希>/runs.jsonl`，
  它只是调度诊断和幂等记录，不是状态真相，删除后不影响交接文档。
- 本工具不需要修改项目 `.gitignore`，也不会在仓库内留下运行缓存。

## 启动、查看和停止

在项目根目录运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -m tools.ai_handoff
```

看到“只读”和“dry-run”启动提示后，打开：

```text
http://127.0.0.1:8765
```

停止时回到启动窗口按 `Control-C`。工具不会把自己安装为后台服务，
也不会开机自启。

如果 8765 端口已被占用，可以临时改用：

```bash
PYTHONDONTWRITEBYTECODE=1 python -m tools.ai_handoff --port 8766
```

服务只允许绑定 `127.0.0.1` / `localhost` / `::1`，不用于局域网或公网访问。

## 页面如何解读

- **当前等待**：从 `status + owner + handoff_to` 精确映射得出，不依赖文件修改时间。
- **当前写入权**：按交接协议显示谁可改 scope，或者当前是否只读。
- **状态最后更新**：取最近一条 `implementation_finished_at` 或 `reviewed_at`。
- **Scope SHA-256 核验**：分别显示 baseline、最近实施、审核开始和审核结束证据；
  调度前会按 scope 声明顺序重新读取当前文件并计算聚合哈希。
- **连接已断开**：浏览器与实时事件流中断。页面会明确标红，不会把旧内容冒充为实时状态；
  服务恢复后浏览器会自动重连。
- **降级模式**：macOS kqueue 不可用或运行失效时，页面会显示警告，并切换为低频文件检查。
  它不会把高频轮询包装成“事件驱动”。
- **原生监听范围**：同时监听交接目录与当前交接文件 inode。目录事件负责原子替换/重命名，
  文件事件负责编辑器或补丁工具的原地写入；原子替换后自动重新绑定新 inode。只监听目录会漏掉
  不改变目录项的原地内容写入，生产模式禁止采用该不完整方案。`start()` 会等待目录与文件
  两类监听完成注册后才返回，避免启动后立即交接时丢失首个事件。

## dry-run 的准确含义

v1 对合法状态只记录“如果启用，将触发谁”：

| 状态组合 | v1 候选动作 |
|---|---|
| `CLAUDE_WORKING / claude / claude` | 启动 Claude 首轮实施；允许按协议核验 `ABSENT  <path>` 基线 |
| `READY_FOR_CODEX / codex / codex` | 启动 Codex 审核（`round == max_rounds` 仍合法） |
| `CHANGES_REQUESTED / claude / claude` | 当 `round < max_rounds` 时启动 Claude 返修 |
| `APPROVED / user / user` | 通知用户已通过 |
| `BLOCKED / user / user` | 通知用户已阻塞 |

> 命名统一（自本次基础设施更新起）：新交接的实施方一律写 `claude`，实施中状态写
> `CLAUDE_WORKING`。历史记录中的 `FABLE_WORKING` / `fable5` / `Fable5 实施交接` 只作
> 只读 legacy alias 解析并统一显示为 Claude，任何新生成内容不得再输出旧名称。

在记录前会校验工作包 ID、状态、两个权属字段、轮次、上限、映射和 scope 哈希。
幂等键是 `work_package_id + round + action`，同一事件重复到达只记录一次；不同轮次可以生成新候选。
`CHANGES_REQUESTED` 接手会先执行 `round+1`，因此当前轮次已等于上限时也只生成
“需要用户处理”候选，不触发 Claude，不修改源状态。Codex 审核当前轮不增加轮次，
所以 `READY_FOR_CODEX` 在 `round == max_rounds` 时仍允许完成审核。

### 三阶段展示与交接门禁

面板把每轮拆成三个互不冒充的阶段，分别独立显示：

| 面板卡片 | 含义 |
|---|---|
| ① Claude 交接前自审 | `self_review_verdict`（PASS/BLOCKED）与自审起止时间 |
| ① 自审测试与哈希 | 自审实跑测试真实计数 + `self_review_scope_sha256` |
| 交接门禁 | 允许/拒绝交接，附具体拒绝原因 |
| ② Claude 实施交接（原子状态转移） | `implementation_finished_at` 与产物汇总 |
| ③ Codex 独立审核结论 | Codex `verdict` 与独立审核证据 |

时间线按 `self_review` / `implementation` / `review` 三种记录类型分别标注，
**不把 Claude 自审显示成 Codex 审核**，也不把交接后的 Codex 审核归到 Claude 名下。

交接门禁在调度层强制，任一项不满足即返回 `rejected-self-review`，
**不生成 Codex 审核候选**，并提示应保持 `CLAUDE_WORKING`：

1. 自审段存在且标题带明确 `Round N`；2. `self_review_round == 当前 round`；
3. 自审起止时间齐全，**整串完整匹配** `YYYY-MM-DD HH:MM[:SS][时区]`（禁止 substring，
   前后缀垃圾拒绝）；时区仅接受 `Z`/`UTC`/`CST`/`±HH:MM`/`±HHMM`，未知时区拒绝；
   **`CST` 项目约定为 Asia/Shanghai (+08:00)**，naive 时间戳同样按 +08:00 解释；
   aware/naive 混用直接拒绝；折算 **UTC** 后比较，结束不得早于开始；
4. `verdict == PASS`；
5. 结构化字段「实际测试命令与结果」须同时含实际命令、**明确成功标记**（OK/PASS/通过）与真实计数；
   出现 `FAILED`/`FAIL`/`ERROR`/`失败` 即拒绝（**等额计数不能覆盖失败标记**）；其他字段/正文中的计数无效；
6. `self_review_manifest` 与 scope 证据**密码学绑定**：每项「64 位 SHA-256 + 两空格 + 路径」，
   路径与 `scope` 精确一致**且顺序相同**；按声明顺序重建 `<sha256>  <path>\n` 的 SHA-256
   必须等于 `self_review_scope_sha256`（伪造 SHA 亦被拒）；调度时再与当前实际文件重算 manifest 逐项比对；
7. `是否满足交接条件` 明确为是/true；
8. 自审哈希与实施交接 `scope_sha256` 均存在且相等；
9. 实施交接 `Round` 等于当前轮次且位于自审之后。

**协议生效边界**：legacy 仅由 `LEGACY_WORK_PACKAGE_IDS` 白名单（现存 WP-001～008）界定。
其余工作包一律按 v2 处理，必须显式写 `handoff_protocol: v2`；**漏写直接拒绝，不自动降级**。
面板状态：`legacy` / `v2-ok` / `v2-missing` / `v2-invalid` / `v2-undeclared`；
显式 v2 却缺失或无效的自审显示「v2 自审缺失 / v2 自审无效」，**不显示为「历史格式」**。

### Scope 哈希的实际核验规则

解析器不再把任意一个哈希当成“已一致”，而是分别保留：

- `scope_baseline_sha256`
- 最近 Claude 实施记录的 `scope_sha256`
- 最近 Codex 审核的 `review_started_sha256`
- 最近 Codex 审核的 `review_finished_sha256`

当前 scope 聚合值按交接协议计算：每个文件按声明顺序生成
`<sha256>  <path>\n` 清单，再对整份清单计算 SHA-256。路径越界、文件缺失、不可读或计算期间发生变化，
都会明确拒绝调度。

- `CLAUDE_WORKING`：当前聚合值必须等于 `scope_baseline_sha256`；仅此状态允许尚未创建的
  scope 文件按 `ABSENT  <path>\n` 参与基线，其他状态缺失文件仍直接拒绝。

- `READY_FOR_CODEX`：当前聚合值必须等于最近 implementation `scope_sha256`。
- `CHANGES_REQUESTED / APPROVED / BLOCKED`：审核开始与结束哈希必须先彼此一致，
  当前聚合值再与 `review_finished_sha256` 比较。
- 当前方向应具备的独立证据任何一项缺失时，拒绝调度并生成 dry-run 哈希异常通知候选。

**dry-run 结构性禁止调用外部 adapter**，不是仅靠一个命令行参数约定。

## 显式 live 事件入口

默认启动方式不变，仍为 dry-run。只有用户明确授权真实执行时，才使用：

```bash
AI_HANDOFF_CLAUDE_PROXY=http://127.0.0.1:6789 \
PYTHONDONTWRITEBYTECODE=1 python -m tools.ai_handoff --enable-external-processes
```

live 启动会先执行 Claude Code 登录探针；登录无效、命令缺失、代理不合法或任一 adapter
未显式启用时直接拒绝启动。经本工具启动的 Codex 和 Claude 共用一个跨进程执行租约，因此
在**协调器控制的执行域内**任何时刻最多只有一个真实 AI 子进程。该租约不会被旧 Codex/Claude
独立定时任务获取，因而不能约束被另行恢复的定时任务；两侧旧主轮询保持暂停是 live 模式的
硬前提，而不是可由租约替代的可选防线。调度线程不阻塞文件监听和网页，页面会显示
`scheduled / running / completed / failed / timed-out / cancelled` 生命周期。
子进程退出码为 0 仍不等于协议成功：协调器会重读权威交接文件，校验目标状态、
`owner / handoff_to`、轮次与 scope 哈希证据。如果 AI 安全停笔却以 0 退出，或只输出
报告而未原子交接，生命周期必须记为 `postcondition-failed` 并保留失败告警。

运行状态仍放在项目对应的 macOS 临时目录，不写入仓库：

- `execution.lock`：跨进程互斥锁。
- `execution_lease.json`：当前执行租约，含工作包、轮次、动作、父/子 PID 和截止时间。
- `executions.jsonl`：只追加的生命周期历史。
- `execution_block.json`：需要人工处置的持久阻塞与失败告警。

Claude 的隔离执行环境无法访问宿主机的 `127.0.0.1:8765`，也不能读取上述 macOS 临时目录。
因此协调器另外把**只读存活投影**原子写到项目内被 Git 忽略的
`.ai-handoff-runtime/coordinator_status.json`。该文件包含 PID、UTC 更新时间、递增序号、
有效期、监听模式、真实外部进程开关和失败告警。只有 `coordinator_live=true` 且当前时间不晚于
`valid_until_epoch` 才能证明协调器仍在运行；缺失、损坏、`stopped` 或过期都必须失败关闭并告警。
该投影不含锁、令牌或执行授权，`legacy_polling_resume_authorized` 永远为 `false`；任何 AI 都不得
仅因心跳异常自行恢复旧 30 分钟轮询，恢复仍需用户或外部监督器明确授权。

进程重启时，若租约的父进程和子进程都已消失，会自动记录恢复事件并释放陈旧租约；若父进程
已消失但子进程仍存活，则安全停机并保持告警，绝不再启动第二个 AI。租约或历史损坏同样
失败关闭，不根据不完整数据猜测。

非零退出、超时、启动失败或取消会保留红色告警；同一幂等键不会自动重试。确认问题已处理后，
用户可单独授权该键重试一次：

```bash
PYTHONDONTWRITEBYTECODE=1 python -m tools.ai_handoff \
  --retry-failed-key 'WORK_PACKAGE_ID:ROUND:ACTION'
```

随后重新启动 live 面板。已成功完成的幂等键永远不能用此命令重复执行。

### 正式切换顺序

1. 确认没有遗留的 `BLOCKED` 工作包需要用户裁决，并关闭已经由后续工作包收口的历史包。
2. 暂停旧 Claude/Codex 30 分钟主轮询；两者不得与 live 协调器并行。
3. 在隔离临时工作包上跑一次真实 Claude → Codex 双端链路，并确认无持久失败告警。
4. 启动 live 面板，核对 `/healthz`、`/api/status`、Claude 登录探针和全局租约均正常。
5. 最后才原子创建正式 `CLAUDE_WORKING / claude / claude` 工作包，由文件事件触发 Claude。

切换后的低频恢复巡检只能检查面板进程、`/healthz`、监听模式和失败告警；发现异常应通知用户，
不得自行调用 Claude/Codex。这样即使巡检和文件事件同时发生，也只有协调器拥有执行权。

## 生产事件入口安全约束

- Codex 使用 ChatGPT App 内置的非交互 CLI。执行计划固定项目目录、
  `workspace-write` 沙箱和临时会话；不使用跳过沙箱的危险参数。
- Claude 使用官方 Claude Code CLI，安装位置为 `~/.local/bin/claude`。计划采用
  `-p` 非交互模式、JSON 输出、固定 `opus` 模型、`--max-turns` 默认 `80`、超时、`dontAsk`
  失败关闭，并显式禁止 `git`、`gh`、`rm`、`sudo` 命令。
- 必须区分四个互不等价的上限，任一先到即停止：① `--max-turns`（单个 Claude CLI 外部进程内
  agent 允许的最大 turns，默认 `80`，由 adapter 构造参数锁定并做正整数校验）；
  ② 工作包协议 `max_rounds=5`（自 WP-20260729-048 起的新包默认值；实施—审核自动往返轮次，
  历史包显式 `max_rounds=3` 原样保留）；③ 进程 `timeout_seconds=1800`
  （30 分钟墙钟超时）；④ Anthropic 账户订阅额度（五小时/每周）。把 40 提升为 80 只放宽 ①，
  不改变 ②③④，也不消除所有 Claude 中断，更不允许绕过订阅限制：达到 30 分钟超时、账户额度、
  权限拒绝、连接错误或协议门禁失败时仍必须失败关闭。
- 普通启动时两个 adapter 均为 `available=True, enabled=False`，`DryRunScheduler` 永不调用
  `adapter.execute()`；只有 `--enable-external-processes` 创建的 `EventDrivenScheduler`
  才会把合法状态交给异步执行协调器。
- Claude Desktop 的登录态不等于 Claude Code CLI 登录态。CLI 必须单独执行官方订阅登录；
  本工具的常规启动不会读取、输出或复制登录凭据。
- 不调用 Anthropic API、不申请或保存 API key、不模拟鼠标键盘点击 Claude GUI，
  也不把 Claude Scheduled 定时任务冒充成文件事件触发。
- 页面/API 会持久显示执行失败告警；macOS 系统通知仍未启用。
- 工具本身不会静默安装开机自启项；正式 live 运行由用户明确启动。旧双方 30 分钟主轮询在
  live 期间必须保持暂停。

### 事件入口当前结论

- API 用 `available-disabled` 表示“本机已发现命令，但真实执行未授权”；
  `unavailable` 才表示未发现可执行入口。旧字段 `fable5_trigger` 仅作只读兼容。
- 2026-07-15 的仓库外隔离演练已经覆盖：正常退出、非零退出、超时进程组清理、
  缺失命令失败关闭、输出截断与凭据脱敏、重复事件幂等、损坏运行记录后的锁释放、
  scope 哈希漂移拒绝，以及 Claude 实施到 Codex 审核的双向真实链路。
- 双向临时工作包最终原子落为 `APPROVED / user / user`；Claude 实施哈希和 Codex
  审核前后两次独立哈希均为
  `7cb4deaddb08078c701a01829465def87d02ffcd3e0d4f6a0a9bb5bb477b04d1`。
- v1.3 已实现异步执行生命周期、全局单执行器互斥、陈旧 PID/运行态回收、孤儿进程安全阻塞、
  持久失败告警、交接后置条件校验和人工授权单次重试。默认仍保持 `enabled=False` 与 `dry_run=True`；本轮没有
  启动真实后台服务，也没有恢复或修改双方定时任务。
- 2026-07-16 首次正式派发前的生产闸门发现：只监听目录 vnode 时，保留 inode 的原地写入不会
  稳定触发刷新。协调器当时保持旧 CLOSED 状态且没有启动外部 AI。现已改为目录/文件双 vnode
  监听并增加原地写入回归。WP-006 独立复核又暴露启动线程与首次写入之间的竞态；
  现已增加就绪握手，确保 `start()` 返回时 kqueue 监听已实际生效。

### Claude Code 登录核验

Claude Code 安装在用户目录，面板使用绝对路径，不依赖 shell 的 `PATH`：

```bash
/Users/guangyaosun/.local/bin/claude --version
/Users/guangyaosun/.local/bin/claude auth status
```

如果浏览器能访问 Claude、但终端直连 Anthropic 返回 403，而本机 HTTP 代理链路可用，
应只给 Claude Code 显式注入官方支持的 `HTTP_PROXY` / `HTTPS_PROXY`。Claude Code
不支持 SOCKS 代理，且面板不会假设它会自动继承 macOS 图形界面的系统代理：

```bash
HTTPS_PROXY=http://127.0.0.1:6789 HTTP_PROXY=http://127.0.0.1:6789 \
  /Users/guangyaosun/.local/bin/claude auth status

PYTHONDONTWRITEBYTECODE=1 python -m tools.ai_handoff \
  --claude-proxy http://127.0.0.1:6789
```

`--claude-proxy` 会进入可审计执行计划；dry-run 安全锁禁止外部进程，live 模式还会先用同一
代理执行 Claude 登录探针。
代理地址禁止内嵌账号密码，避免被状态 API 或运行日志暴露。

本机 2026-07-15 的实际根因不是 Claude 账号封禁，也没有证据表明是 DNS 劫持：

- Claude App / 浏览器使用了 macOS 图形界面的系统代理；Claude Code CLI 没有自动继承它。
- 终端直连 `platform.claude.com` 返回 403，直连 `api.anthropic.com` 也被边缘层拒绝；
  显式经 `http://127.0.0.1:6789` 后，平台请求返回 200，API 无凭据请求返回预期的 401。
- DNS 解析到 Anthropic 的真实地址而非 Clash fake-IP；6789 端口实际由本机 OneBox 的
  `sing-box` 提供。Clash Verge 的历史安装不是本次故障的直接证据。
- 显式注入 `HTTP_PROXY` / `HTTPS_PROXY` 后 OAuth 登录成功，`auth status` 显示
  `loggedIn: true`、`authMethod: claude.ai`、`subscriptionType: pro`，非交互 `-p`
  实跑也成功。因此故障点是 CLI 出站路径与图形应用路径不一致。

若浏览器已显示 “Sign in successful”，但 `auth status` 仍为 `loggedIn: false`，并且
`claude doctor` 报告 macOS Keychain 不可写，应在用户自己的“终端”应用中运行：

```bash
/Users/guangyaosun/.local/bin/claude auth login --claudeai
```

不要把账号密码、OAuth code 或长期令牌粘贴到聊天、交接文件或项目目录。若终端仍提示
登录钥匙串异常，使用 macOS“钥匙串访问”解锁或修复 `login` 钥匙串，再重跑上面的登录命令。

## 字段异常时怎么办

1. 先看页面红色错误，确认是缺少、重复、非法状态还是 `owner/handoff_to` 映射错误。
2. 不要用面板或派生记录“修复”交接状态。面板会拒绝调度，而不是猜测。
3. 回到原 Claude / Codex / 用户协作流程，按 `docs/AI_REVIEW_HANDOFF.md` 公共协议裁决。
4. 如果文件只是原子替换期间短暂不可读，等待页面自动重读；不要因面板报错而改源文档。

## 如何确认没有后台进程

正常停止后，页面应无法继续访问，或原页面显示“连接已断开”。技术检查可用：

```bash
ps -ax -o pid=,command= | grep '[t]ools.ai_handoff'
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

两条都没有输出时，表示没有本工具进程或 8765 监听者。

## 技术诊断

- 状态 API：`GET /api/status`
- 实时事件：`GET /api/events`（Server-Sent Events，SSE）。文件状态版本变化后，
  服务器必须发送新的 `event: status`；只有 15 秒无变化时才发 keepalive。
- 健康检查：`GET /healthz`
- 监听类型：API 的 `system.watcher_mode`，正常 macOS 值为 `native-kqueue`
- 调度结果：API 的 `dispatch`。默认模式应带 `dry_run: true` 且
  `external_process_started: false`；显式 live 模式为 `dry_run: false`，并通过
  `execution_lifecycle` 展示当前租约和最近事件。哈希核验还会显示 `scope_current_sha256`、
  `scope_expected_sha256` 和 `scope_hash_basis`。
- 失败告警：API 的 `system.execution_failure_alert`，页面顶部同步标红。
- 源文件暂时不可读时，API 返回 `source_error`，不继续冒充正常实时状态。

开发验证命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_ai_handoff -v
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -t .
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -t .
```

## 第二阶段启用计划

1. ✅ 用临时测试工作包真实调用 Claude 与 Codex，并验证固定目录、权限、超时、退出码和日志脱敏。
2. ✅ 完成非零退出、超时清理、锁记录损坏、哈希漂移和重复事件故障注入。
3. ✅ 实现生产异步执行生命周期、全局单执行器互斥和崩溃后的陈旧运行态恢复。
4. 🟡 页面断线重连与持久失败告警已验证；实际电脑休眠/唤醒和 macOS 系统通知尚待实机验证。
5. ✅ adapter 只能经显式开关和登录探针打开；正式迁移时暂停 30 分钟主轮询，低频恢复巡检
   仅做健康检查和告警，不具备 AI 执行权。
6. 任一验证失败立即回到 dry-run，不修改权威交接文件。

WP-004 的遗留阻塞已由 WP-005 收口并经用户确认关闭，协议迁移前置条件已满足。

## 回退方法

工具没有改动 PLC 代码；事件调度回退步骤如下：

1. 按 `Control-C` 停止面板。
2. 不再启动 `python -m tools.ai_handoff --enable-external-processes`。
3. 只有确认协调器及其子进程均已停止后，才能按用户决定临时恢复旧轮询。
4. 如需清理，在用户确认后删除本任务新增的 `tools/ai_handoff/`、`tests/test_ai_handoff.py`、
   `docs/AI_HANDOFF_OPERATIONS.md` 以及对应临时目录。

回退不需要改写历史工作包；任何轮询恢复都必须先确认不会与 live 协调器并行。
