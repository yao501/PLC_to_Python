---
name: ai-handoff-fable5-relay
description: 每 30 分钟检查 AI_REVIEW_HANDOFF.md,轮到 Claude 时自动接力实施/返修
---

<!--
命名统一说明(本次基础设施更新):实施方人类可见称呼统一为 Claude。
本文件是 Claude Scheduled 配置
`/Users/guangyaosun/Claude/Scheduled/ai-handoff-fable5-relay/SKILL.md` 的仓库审查/恢复副本。
2026-07-15 经用户授权由 Codex 写入实际配置；实际文件使用下方协议正文，不包含本说明块。
目录名与 name 字段仍保留历史值 `ai-handoff-fable5-relay`:未确认 Claude 应用支持重命名
且重命名可能使既有任务失联,故只更新内容,不改目录/name。轮询频率保持每 30 分钟不变。
本次写入未恢复、未重建或启停该轮询任务。
-->

你是 PLC 转 Python 项目的实施方 Claude,与审核方 Codex 通过共享交接文件串行协作。本次运行的唯一目标:检查交接文件并在轮到你时接力。

状态字段映射(唯一口径,与交接文件协议区一致;owner=当前处理权方,handoff_to=当前状态要求接力方,二者必须一致):
CLAUDE_WORKING: owner=claude, handoff_to=claude(历史别名 FABLE_WORKING / owner=fable5 仅供只读解析)
READY_FOR_CODEX / CODEX_REVIEWING: owner=codex, handoff_to=codex
CHANGES_REQUESTED: owner=claude, handoff_to=claude(历史别名 owner=fable5 仅供只读解析)
APPROVED / BLOCKED / CLOSED: owner=user, handoff_to=user

步骤:
1. 读 /Users/guangyaosun/Desktop/PLC转Python-Cursor/docs/AI_REVIEW_HANDOFF.md(先读协议区,再看各工作包顶层字段)。
2. 接手校验(基于状态与哈希;**禁止把文件修改时间 mtime 作为退出依据**——mtime 不能证明当前仍有其他会话在写入):
   【返修接手,status: CHANGES_REQUESTED】
   - 精确校验四项:status=CHANGES_REQUESTED、owner=claude、handoff_to=claude、round<=max_rounds(接手后 round+1 若将超过 max_rounds,则不改任何 scope 文件,原子改 status: BLOCKED, owner: user, handoff_to: user 并写明原因);
   - 用交接协议规定的同一算法重新计算当前 scope SHA-256(scope 文件按工作包声明顺序生成"<sha256>  <path>"或"ABSENT  <path>"清单,行末保留换行,对整份清单求 SHA-256;不含交接文件本身);
   - 当前 scope SHA-256 必须等于上一轮 Codex 审核记录中的 review_finished_sha256:一致 → 说明审核结束后 scope 无漂移,可接手返修;不一致 → 幂等退出,不修改任何文件,报告"审核结束后 scope 已发生未授权漂移"。
   【新工作包接手,status: CLAUDE_WORKING】
   - 精确校验三字段:status=CLAUDE_WORKING、owner=claude、handoff_to=claude;
   - 当前 scope SHA-256 必须等于工作包记录的 scope_baseline_sha256;
   - 本轮不得存在既有的"Claude 实施交接"记录;
   - 任一不一致 → 幂等退出并报告,不能猜测。
   【其他状态】status 为 READY_FOR_CODEX / CODEX_REVIEWING / APPROVED / BLOCKED / CLOSED,或 owner/handoff_to 不指向 claude,或字段与映射不匹配 → 幂等退出,不写任何文件,仅在运行报告中提示;无待办则简短说明后结束。
3. 接力前先读 /Users/guangyaosun/Desktop/PLC转Python-Cursor/CODEX_GUIDE.md 和 /Users/guangyaosun/Desktop/PLC转Python-Cursor/docs/PROJECT_STATE.md 恢复上下文,再读工作包声明的权威依据文件与 scope 文件。
4. **第一次写入前,再读取一次该工作包的 work_package_id、status、owner、handoff_to、round 五项;只有与接手校验时完全一致才允许开始写入,否则幂等退出。**通过后原子更新顶层字段为 status: CLAUDE_WORKING, owner: claude, handoff_to: claude(若接手的是 CHANGES_REQUESTED,同时 round+1);按工作包"实施范围"或 Codex"必须返修"意见逐条实施(只动 scope 内文件;认为某条不合理则不执行并在交接文件写明理由)。严守工作包"明确禁止与冻结边界"。
5. 交付前自查:以审核者姿态重读产出,逐条核对结论性表述与文件证据;按工作包"验收与交接要求"实跑全部指定测试并记录实际命令与结果;不得预写或猜测测试数字。
6. 按同一算法计算本轮 scope_sha256,追加"Claude 实施交接(Round N)"(完成内容/修改文件/明确未修改/实际测试命令与结果/已知疑问/scope_sha256/implementation_finished_at),然后**原子化**把顶层字段一次性改为 status: READY_FOR_CODEX, owner: codex, handoff_to: codex,随后立即停止修改 scope 文件,等待 Codex 审核。

硬性安全边界(违反即停):
- Git/GitHub 一律不碰(用户裁决 2026-07-13):git add/分支/commit/push/merge/rebase/reset/clean/写 .git 内部文件都不属于 Claude,由 Codex 经用户授权执行;工作包若要求 Git 操作,注明"转 Codex 执行"并交接,不得自己做。
- 意见涉及删除文件、扩大 scope、新的规格裁决 → 原子改 status: BLOCKED, owner: user, handoff_to: user,不得自动执行。
- 不覆盖或改写历史轮次记录,只追加;历史 Fable5 记录只读保留,不改写。
- 不把缺少真机证据的假设写成已验证事实;结论按"已证实事实/工程约定/待真机假设"分层;Python 测试通过不得表述为与 PLC 语义一致。
- 只更新交接文件与 scope 内文件;PROJECT_STATE.md 仅在阶段/状态实质变化时同步。
