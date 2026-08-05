"""对 AI_REVIEW_HANDOFF.md 的容错、只读解析。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
from typing import Iterable
import unicodedata


# 人类可见的角色统一称呼：Claude / Codex / 用户。
# `fable5` 仅作为历史记录里的只读 legacy alias 被接受，规范化后统一显示为 Claude。
# 任何新生成内容不得再输出 `fable5` / `Fable5` / `FABLE_WORKING`。
LEGACY_ACTOR_ALIASES = {"fable5": "claude"}

# 历史状态名 `FABLE_WORKING` 只作只读兼容别名；新交接一律写 `CLAUDE_WORKING`。
LEGACY_STATUS_ALIASES = {"FABLE_WORKING": "CLAUDE_WORKING"}


def canonical_actor(value: str | None) -> str | None:
    """把历史角色别名（fable5）规范化到统一称呼（claude）；其余原样返回。"""
    if value is None:
        return None
    return LEGACY_ACTOR_ALIASES.get(value, value)


def canonical_status(value: str | None) -> str | None:
    """把历史状态别名（FABLE_WORKING）规范化到新状态（CLAUDE_WORKING）。"""
    if value is None:
        return None
    return LEGACY_STATUS_ALIASES.get(value, value)


# STATUS_MAP 的 owner/handoff 一律使用**规范化后的**期望值（实施方=claude）。
# 同时保留 `FABLE_WORKING` 键，使历史工作包仍可只读解析。
STATUS_MAP = {
    "CLAUDE_WORKING": ("claude", "claude", "Claude 正在实施"),
    "FABLE_WORKING": ("claude", "claude", "Claude 正在实施"),  # legacy alias（只读）
    "READY_FOR_CODEX": ("codex", "codex", "已交给 Codex，等待审核"),
    "CODEX_REVIEWING": ("codex", "codex", "Codex 正在审核"),
    "CHANGES_REQUESTED": ("claude", "claude", "Codex 已退回，等待 Claude 返修"),
    "APPROVED": ("user", "user", "已通过，等待用户关闭"),
    "BLOCKED": ("user", "user", "自动流程已停止，需要用户处理"),
    "CLOSED": ("user", "user", "工作包已关闭"),
}

NEXT_ACTION = {
    "CLAUDE_WORKING": "Claude 完成实施 → 交接前自审（PASS 后）→ 原子交接给 Codex。",
    "FABLE_WORKING": "Claude 完成实施 → 交接前自审（PASS 后）→ 原子交接给 Codex。",  # legacy alias（只读）
    "READY_FOR_CODEX": "Codex 开始独立只读审核（与 Claude 自审是两个独立动作）。",
    "CODEX_REVIEWING": "Codex 完成审核并写回结论。",
    "CHANGES_REQUESTED": "Claude 按最近审核意见返修，再次交接 Codex。",
    "APPROVED": "用户确认后关闭工作包；Git 操作仍需单独授权。",
    "BLOCKED": "用户阅读阻塞原因并作出裁决。",
    "CLOSED": "无需自动动作。",
}

_WP_HEADING = re.compile(r"^##\s+(WP-[A-Za-z0-9-]+)\s*$", re.MULTILINE)
_SUBHEADING = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_FIELD = re.compile(r"^-\s+([^:：]+?)[：:]\s*(.*?)\s*$")
_HASH = re.compile(r"\b[0-9a-fA-F]{64}\b")
_TIME_KEYS = ("self_review_finished_at", "implementation_finished_at", "reviewed_at")

# 三阶段协议（v2）：
#   1. Claude 交接前自审  —— CLAUDE_WORKING 状态内、原子交接之前完成；
#   2. Claude 实施交接    —— 仅在自审 PASS 后才可原子写 READY_FOR_CODEX；
#   3. Codex 独立审核结论 —— 仅在交接完成后启动，保持独立的开始/结束哈希与 verdict。
# 历史工作包（无结构化自审段）继续只读解析，并显式标注 legacy，不据此伪造自审证据。
SELF_REVIEW_HEADINGS = ("Claude 交接前自审", "交接前自审")
IMPLEMENTATION_HEADINGS = ("Claude 实施交接", "Fable5 实施交接")  # 后者仅历史只读兼容
REVIEW_HEADINGS = ("Codex 审核结论",)

LEGACY_SELF_REVIEW_NOTE = "历史格式：自审证据未独立结构化"
V2_MISSING_NOTE = "v2 自审缺失：已声明三阶段协议但没有结构化自审段"
V2_INVALID_NOTE = "v2 自审无效：结构化自审段存在但未通过门禁校验"
V2_UNDECLARED_NOTE = "协议未声明：新工作包必须显式写 handoff_protocol: v2"

# legacy 范围**只**由这份明确 ID 白名单界定（协议生效边界，不可歧义）。
# 不得再用"缺少 handoff_protocol / 缺少自审段"来推断 legacy——否则新包漏写即被静默降级。
LEGACY_WORK_PACKAGE_IDS = frozenset({
    "WP-20260712-001",
    "WP-20260713-002",
    "WP-20260714-003",
    "WP-20260714-004",
    "WP-20260714-005",
    "WP-20260716-006",
    "WP-20260716-007",
    "WP-20260720-008",
})

# 只有这些取值算显式声明三阶段协议。
V2_PROTOCOL_TOKENS = {"v2", "2"}

# 自审测试证据必须来自这些**结构化字段**；正文/已知疑问里的 "Ran N tests" 一律不算。
SELF_REVIEW_TEST_FIELDS = (
    "实际测试命令与结果",
    "测试命令与实际结果",
    "实际执行的测试命令及结果",
    "self_review_tests",
)
# 命令特征：至少要能看出跑了什么（unittest/pytest/python -m ...）。
_TEST_COMMAND_HINT = re.compile(r"(python\s+-m\s+\w+|unittest|pytest|discover)", re.IGNORECASE)
_TRUE_TOKENS = {"true", "yes", "是", "满足", "已满足", "pass"}
# 自审时间戳必须**整串完整匹配**（禁止 substring 搜索，前后缀垃圾一律拒绝）。
# 允许：YYYY-MM-DD HH:MM[:SS] 后接可选时区标记。
# 时区标记只接受：Z / UTC / CST / ±HH:MM / ±HHMM；其余（如 XYZ、nonsense）一律拒绝。
# **项目约定**：`CST` 在本项目明确解释为 Asia/Shanghai，即 UTC+08:00（不是美国中部时间）。
_TIMESTAMP_FULL = re.compile(
    r"(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})[ T](?P<h>\d{2}):(?P<mi>\d{2})"
    r"(?::(?P<s>\d{2}))?"
    r"(?:\s*(?P<tz>Z|UTC|CST|[+-]\d{2}:?\d{2}))?"
)
PROJECT_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai，供 CST 与 naive 时间戳使用


def _timezone_from_token(token: str | None) -> timezone | None:
    if token is None:
        return None
    token = token.strip()
    if token in {"Z", "UTC"}:
        return timezone.utc
    if token == "CST":  # 项目约定 = Asia/Shanghai = +08:00
        return PROJECT_TZ
    sign = 1 if token[0] == "+" else -1
    digits = token[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:])
    if hours > 23 or minutes > 59:
        return None
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def parse_timestamp(value: str | None) -> datetime | None:
    """严格解析自审时间戳：整串完整匹配 + 合法日历日期 + 已知时区。

    返回 aware 或 naive datetime；无法解析（格式不符、前后缀垃圾、非法日期、
    未知时区）一律返回 None。naive 与 aware 的混用由调用方显式处理。
    """
    if not value:
        return None
    match = _TIMESTAMP_FULL.fullmatch(value.strip())
    if not match:
        return None
    tz_token = match.group("tz")
    tzinfo = _timezone_from_token(tz_token) if tz_token else None
    if tz_token and tzinfo is None:
        return None  # 偏移量数值非法
    try:
        return datetime(
            int(match.group("y")), int(match.group("mo")), int(match.group("d")),
            int(match.group("h")), int(match.group("mi")),
            int(match.group("s")) if match.group("s") else 0,
            tzinfo=tzinfo,
        )
    except ValueError:
        return None  # 非法日历日期/时刻，例如 2026-02-30 或 25:00


def to_utc(moment: datetime) -> datetime:
    """naive 视为项目本地时区（Asia/Shanghai），统一折算到 UTC 后比较。"""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=PROJECT_TZ)
    return moment.astimezone(timezone.utc)
# 自审 manifest 每项必须是「64 位十六进制 SHA-256 + 两空格 + scope 路径」。
_MANIFEST_ENTRY = re.compile(r"^([0-9a-fA-F]{64})\s{2}(\S.*)$")
# 结构化测试字段里出现任一失败标记即拒绝，不允许被等额计数掩盖。
_TEST_FAILURE_MARK = re.compile(r"(FAILED|FAIL\b|ERROR|ERRORS|失败|错误|不通过)", re.IGNORECASE)
# 等额计数还必须带明确成功标记。
_TEST_SUCCESS_MARK = re.compile(r"(\bOK\b|\bPASS(?:ED)?\b|通过|全绿)", re.IGNORECASE)


def canonical_manifest(entries: list[str]) -> str:
    """按交接协议构造规范 manifest 文本：每行 `<sha256>  <path>\\n`。"""
    return "".join(f"{sha}  {path}\n" for sha, path in entries)


def validate_manifest(
    entries: list[str],
    scope: list[str],
    expected_digest: str | None = None,
    current_manifest: list[str] | None = None,
) -> str | None:
    """校验逐文件 SHA-256 清单，并与自审聚合哈希做**密码学绑定**。

    仅检查"64 位 SHA + 路径"的外形不足以建立信任：还必须按 scope 声明顺序
    重建规范 manifest，其 SHA-256 必须等于 `self_review_scope_sha256`；
    若调度器提供了当前重算 manifest，则逐项比对，从而覆盖文件内容漂移。
    """
    if not entries:
        return "自审缺少逐文件 SHA-256（self_review_manifest）；不得交接"
    parsed: list[tuple[str, str]] = []
    for entry in entries:
        match = _MANIFEST_ENTRY.match(entry.strip())
        if not match:
            return f"自审 manifest 条目格式非法（应为 <64位SHA-256>␠␠<路径>）: {entry!r}"
        parsed.append((match.group(1).lower(), match.group(2).strip().strip("`")))
    paths = [path for _, path in parsed]
    duplicates = {path for path in paths if paths.count(path) > 1}
    if duplicates:
        return f"自审 manifest 存在重复路径: {sorted(duplicates)}"
    missing, extra = set(scope) - set(paths), set(paths) - set(scope)
    if missing:
        return f"自审 manifest 缺少 scope 文件: {sorted(missing)}"
    if extra:
        return f"自审 manifest 含 scope 之外的无关路径: {sorted(extra)}"
    # 顺序是规范 manifest 的一部分：必须与 scope 声明顺序完全一致。
    if paths != list(scope):
        return f"自审 manifest 顺序与 scope 声明顺序不一致: 期望 {list(scope)}, 实际 {paths}"
    # 密码学绑定：规范 manifest 的 SHA-256 必须等于自审聚合哈希。
    if expected_digest:
        rebuilt = hashlib.sha256(canonical_manifest(parsed).encode("utf-8")).hexdigest()
        if rebuilt != expected_digest.lower():
            return (
                "自审 manifest 与 self_review_scope_sha256 不匹配（聚合哈希无法由清单重建）："
                f"重建={rebuilt}, 声明={expected_digest}"
            )
    # 与调度器重算的当前 manifest 逐项比对：覆盖文件内容/顺序漂移。
    if current_manifest is not None:
        declared_lines = canonical_manifest(parsed).splitlines(keepends=True)
        if declared_lines != list(current_manifest):
            for index, (declared, current) in enumerate(zip(declared_lines, current_manifest)):
                if declared != current:
                    return (
                        f"自审 manifest 第 {index + 1} 项与当前实际文件不一致："
                        f"自审={declared.strip()!r}, 当前={current.strip()!r}"
                    )
            return "自审 manifest 与当前 scope 实际文件清单条目数不一致"
    return None


def validate_test_evidence(value: str | None) -> str | None:
    """校验结构化测试字段：需含实际命令、明确成功标记与真实计数。"""
    if not value:
        return "自审缺少结构化字段「实际测试命令与结果」；不得交接"
    if not _TEST_COMMAND_HINT.search(value):
        return "自审测试字段未包含可识别的实际测试命令；不得交接"
    if _TEST_FAILURE_MARK.search(value):
        return f"自审测试字段出现失败标记，不得视为通过: {value.strip()[:120]}"
    if not _TEST_SUCCESS_MARK.search(value):
        return "自审测试字段缺少明确成功标记（OK / PASS / 通过）；等额计数本身不代表成功"
    if not _test_count_in(value):
        return "自审测试字段缺少真实测试计数（如 Ran N tests, OK）；不得交接"
    return None


@dataclass
class Record:
    kind: str
    heading: str
    round: int | None
    fields: dict[str, str]
    body: str

    def to_dict(self) -> dict:
        data = asdict(self)
        # 长正文不必通过 API 重复传输。
        data.pop("body", None)
        return data


@dataclass
class WorkPackage:
    work_package_id: str
    title: str | None = None
    status: str | None = None
    canonical_status: str | None = None
    status_is_legacy: bool = False
    owner: str | None = None
    handoff_to: str | None = None
    round: int | None = None
    max_rounds: int | None = None
    scope: list[str] = field(default_factory=list)
    base_commit: str | None = None
    latest_implementation_at: str | None = None
    latest_review_at: str | None = None
    latest_review_round: int | None = None
    last_updated_at: str | None = None
    current_handler: str | None = None
    write_access: str | None = None
    latest_implementation_summary: str | None = None
    latest_review_verdict: str | None = None
    latest_review_summary: str | None = None
    latest_test_count: int | None = None
    latest_test_result: str | None = None
    scope_baseline_sha256: str | None = None
    implementation_scope_sha256: str | None = None
    review_started_sha256: str | None = None
    review_finished_sha256: str | None = None
    # —— 阶段 1：Claude 交接前自审（结构化证据；与 Codex 独立审核严格区分）——
    handoff_protocol: str | None = None
    protocol_is_v2: bool = False
    protocol_declared_v2: bool = False
    is_legacy_package: bool = False
    self_review_present: bool = False
    self_review_is_legacy: bool = False
    self_review_state: str = "unknown"  # legacy | v2-ok | v2-missing | v2-invalid | v2-undeclared
    self_review_note: str | None = None
    self_review_round: int | None = None
    implementation_round: int | None = None
    implementation_after_self_review: bool = False
    self_review_started_at: str | None = None
    self_review_finished_at: str | None = None
    self_review_verdict: str | None = None
    self_review_scope_sha256: str | None = None
    self_review_manifest: list[str] = field(default_factory=list)
    self_review_test_command: str | None = None
    self_review_test_count: int | None = None
    self_review_test_result: str | None = None
    self_review_first_failure: str | None = None
    self_review_root_cause: str | None = None
    self_review_fix: str | None = None
    self_review_rerun: str | None = None
    self_review_known_issues: str | None = None
    self_review_unverified: str | None = None
    self_review_ready: str | None = None
    handoff_gate_ok: bool = False
    handoff_gate_reason: str | None = None
    blocked_reason: str | None = None
    next_action: str | None = None
    status_explanation: str | None = None
    waiting_for: str | None = None
    records: list[Record] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        data = asdict(self)
        data["valid"] = self.valid
        data["records"] = [record.to_dict() for record in self.records]
        return data


@dataclass
class ParseResult:
    source: str
    packages: list[WorkPackage] = field(default_factory=list)
    source_error: str | None = None
    parsed_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))

    @property
    def ok(self) -> bool:
        return self.source_error is None and bool(self.packages)

    @property
    def current(self) -> WorkPackage | None:
        for package in reversed(self.packages):
            if package.status != "CLOSED":
                return package
        return self.packages[-1] if self.packages else None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_error": self.source_error,
            "parsed_at": self.parsed_at,
            "ok": self.ok,
            "current_work_package_id": self.current.work_package_id if self.current else None,
            "current": self.current.to_dict() if self.current else None,
            "packages": [package.to_dict() for package in self.packages],
        }


class HandoffParser:
    """读取稳定快照后解析；永不写源文件。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def parse_file(self) -> ParseResult:
        try:
            # 原子替换可能恰好发生在 open/read 之间；重读一次而不是猜测。
            last_error: OSError | UnicodeError | None = None
            for _ in range(2):
                try:
                    text = self.path.read_text(encoding="utf-8")
                    return self.parse_text(text)
                except (OSError, UnicodeError) as exc:
                    last_error = exc
            assert last_error is not None
            raise last_error
        except (OSError, UnicodeError) as exc:
            return ParseResult(source=str(self.path), source_error=f"交接文件暂时不可读: {exc}")

    def parse_text(self, text: str) -> ParseResult:
        result = ParseResult(source=str(self.path))
        matches = list(_WP_HEADING.finditer(text))
        if not matches:
            result.source_error = "未找到任何工作包标题（期望 ## WP-...）"
            return result
        seen_ids: set[str] = set()
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            wp_id = match.group(1)
            section = text[match.end():end]
            package = self._parse_package(wp_id, section)
            if wp_id in seen_ids:
                package.errors.append(f"重复的工作包 ID: {wp_id}")
            seen_ids.add(wp_id)
            result.packages.append(package)
        return result

    def _parse_package(self, wp_id: str, section: str) -> WorkPackage:
        package = WorkPackage(work_package_id=wp_id)
        first_sub = _SUBHEADING.search(section)
        top = section[: first_sub.start()] if first_sub else section
        values, duplicates = _top_fields(top)
        for key in duplicates:
            package.errors.append(f"顶层字段重复: {key}")

        package.title = _one(values, "title")
        package.status = _one(values, "status")
        package.owner = _one(values, "owner")
        package.handoff_to = _one(values, "handoff_to")
        package.base_commit = _one(values, "base_commit")
        package.scope = _scope(top)
        package.round = _integer(_one(values, "round"), "round", package.errors)
        package.max_rounds = _integer(_one(values, "max_rounds"), "max_rounds", package.errors)
        package.scope_baseline_sha256 = _hash_value(_one(values, "scope_baseline_sha256"))

        for required, value in (
            ("title", package.title), ("status", package.status), ("owner", package.owner),
            ("handoff_to", package.handoff_to), ("round", package.round),
            ("max_rounds", package.max_rounds), ("base_commit", package.base_commit),
        ):
            if value is None:
                package.errors.append(f"缺少顶层字段: {required}")
        if not package.scope:
            package.errors.append("缺少或空 scope 文件列表")

        if package.status not in STATUS_MAP:
            package.errors.append(f"未知状态: {package.status!r}")
        else:
            package.canonical_status = canonical_status(package.status)
            package.status_is_legacy = package.status in LEGACY_STATUS_ALIASES
            if package.status_is_legacy:
                package.warnings.append(
                    f"{package.status} 为历史兼容状态，仅供只读解析；"
                    f"新交接请写 {package.canonical_status}"
                )
            expected_owner, expected_handoff, explanation = STATUS_MAP[package.status]
            package.status_explanation = explanation
            package.current_handler = _waiting_label(package.owner)
            package.waiting_for = _waiting_label(package.owner)
            package.write_access = _write_access(package.status)
            package.next_action = NEXT_ACTION[package.status]
            # 历史 owner=fable5 规范化为 claude 后再比对，既接受历史记录也接受新记录。
            if (canonical_actor(package.owner), canonical_actor(package.handoff_to)) != (
                expected_owner,
                expected_handoff,
            ):
                package.errors.append(
                    f"状态字段映射异常: {package.status} 应为 "
                    f"owner={expected_owner}, handoff_to={expected_handoff}，实际为 "
                    f"owner={package.owner}, handoff_to={package.handoff_to}"
                )
        if package.round is not None and package.max_rounds is not None:
            if package.round > package.max_rounds:
                package.warnings.append(f"round 超限: {package.round} > {package.max_rounds}")

        package.handoff_protocol = _one(values, "handoff_protocol")
        package.records = _records(section)
        self_reviews = [r for r in package.records if r.kind == "self_review"]
        implementations = [r for r in package.records if r.kind == "implementation"]
        reviews = [r for r in package.records if r.kind == "review"]
        _apply_self_review(package, self_reviews)
        if implementations:
            latest = implementations[-1]
            package.latest_implementation_at = latest.fields.get("implementation_finished_at")
            package.latest_implementation_summary = _summary(latest.fields.get("完成内容"))
            package.implementation_scope_sha256 = _record_hash(latest, "scope_sha256")
            package.implementation_round = latest.round
            # 顺序校验：本轮实施交接必须出现在本轮自审**之后**（禁止先交接后补自审）。
            order = [r.kind for r in package.records]
            try:
                last_sr = len(order) - 1 - order[::-1].index("self_review")
                last_impl = len(order) - 1 - order[::-1].index("implementation")
                package.implementation_after_self_review = last_impl > last_sr
            except ValueError:
                package.implementation_after_self_review = False
        if reviews:
            latest = reviews[-1]
            package.latest_review_at = latest.fields.get("reviewed_at")
            package.latest_review_round = latest.round
            package.latest_review_verdict = latest.fields.get("verdict")
            package.review_started_sha256 = _record_hash(latest, "review_started_sha256")
            package.review_finished_sha256 = _record_hash(latest, "review_finished_sha256")
            package.latest_review_summary = _summary(
                latest.fields.get("必须返修")
                or latest.fields.get("必须返修 / 阻塞原因")
                or latest.fields.get("已验证事实")
            )
        package.last_updated_at = _last_record_time(package.records)
        if not package.scope_baseline_sha256:
            package.warnings.append("缺少 scope_baseline_sha256")
        if implementations and not package.implementation_scope_sha256:
            package.warnings.append("最近实施记录缺少 scope_sha256")
        if reviews and not package.review_started_sha256:
            package.warnings.append("最近审核记录缺少 review_started_sha256")
        if reviews and not package.review_finished_sha256:
            package.warnings.append("最近审核记录缺少 review_finished_sha256")
        package.latest_test_count, package.latest_test_result = _latest_test(package.records)
        package.handoff_gate_reason = self_review_gate(package)
        package.handoff_gate_ok = package.handoff_gate_reason is None
        # 显式 v2 且有自审段时，按门禁结果区分 ok / invalid；绝不回落成"历史格式"。
        if package.self_review_present:
            package.self_review_state = "v2-ok" if package.handoff_gate_ok else "v2-invalid"
            if not package.handoff_gate_ok:
                package.self_review_note = V2_INVALID_NOTE
        if package.status == "BLOCKED":
            package.blocked_reason = _blocked_reason(package.records) or _one(values, "blocked_reason")
            if not package.blocked_reason:
                package.warnings.append("BLOCKED 状态未找到明确阻塞原因")
        return package


def _first_field(record: Record, *names: str) -> str | None:
    for name in names:
        value = record.fields.get(name)
        if value:
            return value.strip().strip("`")
    return None


def _apply_self_review(package: WorkPackage, self_reviews: list[Record]) -> None:
    """把最近一条结构化自审记录投影到工作包；legacy 只由 ID 白名单界定，不靠缺字段推断。"""
    declared = (package.handoff_protocol or "").strip().lower()
    package.protocol_declared_v2 = declared in V2_PROTOCOL_TOKENS
    package.is_legacy_package = package.work_package_id in LEGACY_WORK_PACKAGE_IDS
    # 非白名单包一律按 v2 对待：漏写 handoff_protocol 也不降级，只会在门禁被拒。
    package.protocol_is_v2 = package.protocol_declared_v2 or not package.is_legacy_package
    package.self_review_present = bool(self_reviews)

    if not self_reviews:
        if package.is_legacy_package and not package.protocol_declared_v2:
            # 仅现存历史包可标 legacy。
            package.self_review_is_legacy = True
            package.self_review_state = "legacy"
            package.self_review_note = LEGACY_SELF_REVIEW_NOTE
        elif not package.protocol_declared_v2:
            package.self_review_state = "v2-undeclared"
            package.self_review_note = V2_UNDECLARED_NOTE
        else:
            # 显式 v2 却没有自审段：绝不能显示成"历史格式"。
            package.self_review_state = "v2-missing"
            package.self_review_note = V2_MISSING_NOTE
        return

    latest = self_reviews[-1]
    package.self_review_round = latest.round
    package.self_review_started_at = _first_field(latest, "self_review_started_at", "自审开始时间")
    package.self_review_finished_at = _first_field(latest, "self_review_finished_at", "自审结束时间")
    verdict = _first_field(latest, "self_review_verdict", "自审结论")
    package.self_review_verdict = verdict.upper() if verdict else None
    package.self_review_scope_sha256 = _record_hash(latest, "self_review_scope_sha256")
    package.self_review_manifest = _nested_list(latest.body, "self_review_manifest") or _nested_list(
        latest.body, "逐文件 SHA-256"
    )
    # 测试证据**只**从结构化字段取；不扫描整段正文，避免"已知疑问"里的计数被当成证据。
    package.self_review_test_command = _first_field(latest, *SELF_REVIEW_TEST_FIELDS)
    if package.self_review_test_command:
        package.self_review_test_count = _test_count_in(package.self_review_test_command)
        if package.self_review_test_count:
            package.self_review_test_result = (
                f"{package.self_review_test_count}/{package.self_review_test_count} 通过"
            )
    package.self_review_first_failure = _first_field(latest, "首次失败", "self_review_first_failure")
    package.self_review_root_cause = _first_field(latest, "失败根因", "self_review_root_cause")
    package.self_review_fix = _first_field(latest, "修复内容", "self_review_fix")
    package.self_review_rerun = _first_field(latest, "修复后重跑结果", "self_review_rerun")
    package.self_review_known_issues = _first_field(latest, "已知疑问", "self_review_known_issues")
    package.self_review_unverified = _first_field(latest, "未验证边界", "self_review_unverified")
    package.self_review_ready = _first_field(latest, "是否满足交接条件", "self_review_ready")

    if package.self_review_verdict not in {"PASS", "BLOCKED"}:
        package.warnings.append(
            f"自审 verdict 非法或缺失: {package.self_review_verdict!r}（应为 PASS 或 BLOCKED）"
        )
    if not package.self_review_scope_sha256:
        package.warnings.append("自审记录缺少 self_review_scope_sha256")


def self_review_gate(
    package: WorkPackage, current_manifest: list[str] | None = None
) -> str | None:
    """交接门禁：返回拒绝原因；None 表示允许交接到 READY_FOR_CODEX。

    legacy 仅由 `LEGACY_WORK_PACKAGE_IDS` 白名单界定；其余一律按 v2 强制。
    `current_manifest` 由调度器传入（当前实际文件重算结果），用于逐项比对。
    """
    # 门禁只约束"已交接/待审核"方向；CLAUDE_WORKING 内允许继续实施与自审。
    if canonical_status(package.status) not in {"READY_FOR_CODEX", "CODEX_REVIEWING"}:
        return None
    # 直接由 ID / 字段自行判定，不依赖解析期副作用（手工构造的 WorkPackage 同样受约束）。
    is_legacy = package.is_legacy_package or package.work_package_id in LEGACY_WORK_PACKAGE_IDS
    declared_v2 = package.protocol_declared_v2 or (
        (package.handoff_protocol or "").strip().lower() in V2_PROTOCOL_TOKENS
    )
    if is_legacy and not declared_v2:
        return None  # 现存历史包只读兼容
    if not declared_v2:
        return "新工作包必须显式声明 handoff_protocol: v2；漏写不得降级为历史格式"
    if not package.self_review_present:
        return "缺少 Claude 交接前自审记录；v2 工作包必须先完成结构化自审才能交接"

    # (1) 自审轮次必须存在且等于当前轮次（None 一律拒绝）
    if package.round is None:
        return "工作包缺少 round，无法校验自审轮次"
    if package.self_review_round is None:
        return "自审记录缺少明确 Round 编号；不得交接"
    if package.self_review_round != package.round:
        return (
            f"自审轮次({package.self_review_round})与当前轮次({package.round})不一致；本轮必须重新自审"
        )
    # (2) 自审起止时间必须存在、可解析为合法日期时间，且顺序正确
    started, finished = package.self_review_started_at, package.self_review_finished_at
    if not started or not finished:
        return "自审缺少 self_review_started_at / self_review_finished_at；不得交接"
    ts_start, ts_end = parse_timestamp(started), parse_timestamp(finished)
    if ts_start is None:
        return f"自审开始时间无法解析为合法时间（需完整匹配 YYYY-MM-DD HH:MM[:SS][时区]）: {started!r}"
    if ts_end is None:
        return f"自审结束时间无法解析为合法时间（需完整匹配 YYYY-MM-DD HH:MM[:SS][时区]）: {finished!r}"
    # 显式处理 aware/naive 混用，绝不静默忽略偏移量。
    if (ts_start.tzinfo is None) != (ts_end.tzinfo is None):
        return (
            "自审时间戳时区标注不一致（一个带时区、一个不带），无法可靠比较："
            f"开始={started!r}, 结束={finished!r}"
        )
    if to_utc(ts_end) < to_utc(ts_start):
        return (
            f"自审时间顺序非法：结束({finished} → {to_utc(ts_end).isoformat()}) "
            f"早于开始({started} → {to_utc(ts_start).isoformat()})"
        )
    # (3) verdict
    if package.self_review_verdict != "PASS":
        return f"自审结论为 {package.self_review_verdict or '缺失'}，未通过；必须保持 CLAUDE_WORKING，不得交接"
    # (4) 测试证据：结构化字段，须含命令 + 明确成功标记 + 真实计数，且无任何失败标记
    evidence_error = validate_test_evidence(package.self_review_test_command)
    if evidence_error:
        return evidence_error
    # (5) 逐文件 manifest：格式、无重复、顺序与 scope 一致，且与自审聚合哈希密码学绑定
    manifest_error = validate_manifest(
        package.self_review_manifest,
        package.scope,
        expected_digest=package.self_review_scope_sha256,
        current_manifest=current_manifest,
    )
    if manifest_error:
        return manifest_error
    # (6) 是否满足交接条件必须明确为真
    ready = (package.self_review_ready or "").strip().lower()
    if ready not in _TRUE_TOKENS:
        return f"自审「是否满足交接条件」未明确为是/true（实际: {package.self_review_ready or '缺失'}）"
    # (7)(8) 哈希证据
    if not package.self_review_scope_sha256:
        return "自审缺少 self_review_scope_sha256；不得交接"
    if not package.implementation_scope_sha256:
        return "实施交接缺少 scope_sha256；不得交接"
    if package.self_review_scope_sha256 != package.implementation_scope_sha256:
        return (
            "自审结束哈希与实施交接哈希不一致（自审后 scope 已漂移）："
            f"self_review={package.self_review_scope_sha256}, "
            f"implementation={package.implementation_scope_sha256}"
        )
    # (9) 实施交接必须属于当前轮次，且记录顺序在自审之后
    if package.implementation_round is None:
        return "实施交接记录缺少明确 Round 编号；不得交接"
    if package.implementation_round != package.round:
        return (
            f"实施交接轮次({package.implementation_round})与当前轮次({package.round})不一致；"
            "不得用过期交接记录冒充本轮"
        )
    if not package.implementation_after_self_review:
        return "记录顺序非法：本轮实施交接出现在自审之前；不得先交接后补自审"
    return None


def _nested_list(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    pattern = re.compile(rf"^\s*-\s+{re.escape(key)}\s*[:：]\s*$")
    for index, line in enumerate(lines):
        if pattern.match(line):
            items: list[str] = []
            for child in lines[index + 1:]:
                match = re.match(r"^\s{2,}-\s+(.+?)\s*$", child)
                if not match:
                    break
                value = match.group(1).strip().strip("`")
                if value:
                    items.append(value)
            return items
    return []


def _top_fields(text: str) -> tuple[dict[str, list[str]], set[str]]:
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = _FIELD.match(line.strip())
        if match:
            key, value = match.groups()
            values.setdefault(key, []).append(value.strip().strip("`"))
    return values, {key for key, entries in values.items() if len(entries) > 1}


def _one(values: dict[str, list[str]], key: str) -> str | None:
    entries = values.get(key, [])
    return entries[0] if len(entries) == 1 and entries[0] else None


def _scope(top: str) -> list[str]:
    lines = top.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^\s*-\s+scope:\s*$", line):
            scope: list[str] = []
            for child in lines[index + 1:]:
                match = re.match(r"^\s{2,}-\s+(.+?)\s*$", child)
                if not match:
                    break
                value = match.group(1).strip().strip("`")
                if value:
                    scope.append(value)
            return scope
    return []


def _integer(value: str | None, name: str, errors: list[str]) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        errors.append(f"{name} 不是整数: {value!r}")
        return None


def _records(section: str) -> list[Record]:
    matches = list(_SUBHEADING.finditer(section))
    records: list[Record] = []
    for index, match in enumerate(matches):
        heading = match.group(1)
        # 顺序要紧：先判自审，避免 "Claude 交接前自审" 被误分类。
        # 同时识别历史 "Fable5 实施交接" 与新 "Claude 实施交接"。
        if any(token in heading for token in SELF_REVIEW_HEADINGS):
            kind = "self_review"
        elif any(token in heading for token in IMPLEMENTATION_HEADINGS):
            kind = "implementation"
        elif any(token in heading for token in REVIEW_HEADINGS):
            kind = "review"
        else:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        body = section[match.end():end]
        if kind == "review":
            round_number = _strict_single_review_round(heading)
        else:
            round_match = re.search(r"Round\s+(\d+)", heading, re.IGNORECASE)
            round_number = int(round_match.group(1)) if round_match else None
        records.append(Record(kind, heading, round_number,
                              _record_fields(body), body))
    return records


_ROUND_TOKEN = "Round"
_MAX_REVIEW_ROUND_DIGITS = 64


def _is_unicode_attachment(character: str) -> bool:
    """Return whether a character can visually/lexically extend an identifier."""
    category = unicodedata.category(character)
    return category[0] in {"L", "N", "M"} or category in {"Pc", "Cf"}


def _is_numeric_continuation_separator(character: str) -> bool:
    """Recognize any punctuation/symbol that could continue a range, ratio,
    decimal, or list between two numbers.

    这是**失败关闭**判定：分隔符按 Unicode 通用类别识别（标点 ``P*`` 或符号
    ``S*``），不再逐字符枚举具体逗号/点号。因此跨脚本的逗号、分号、中点、
    点运算符（如 U+060C/U+061B/U+055D/U+1363/U+1802/U+30FB/U+00B7/U+22C5/
    U+3001 等）夹在两个数字之间都会被识别为续写而拒绝，避免枚举漏列。桥接字符
    （空格/Tab/``Zs``/组合标记/``Cf``/``Cc`` 控制字符）由 :func:`_is_numeric_bridge`
    单独处理，与本类别集合不重叠；``Pc``/``Cf`` 这类可粘连标识符的字符仍先由
    :func:`_is_unicode_attachment` 在数字紧邻处拒绝。分隔符后若紧跟的是普通说明
    文字（字母而非数字），:func:`_continues_numeric_expression` 不会判为续写，
    故 ``Round 2，返修``、``Round 2、返修`` 等合法标题不受影响。
    """
    return unicodedata.category(character)[0] in {"P", "S"}


def _is_numeric_bridge(character: str) -> bool:
    """Return whether an ignorable character may hide numeric continuation.

    这是**失败关闭**判定：bridge 按 Unicode 类别族识别，而不是逐子类枚举——凡
    分隔符类 ``Z*``（含 ``Zs`` 空白、``Zl`` 行分隔符 U+2028、``Zp`` 段落分隔符
    U+2029）、其它类 ``C*``（含 ``Cc`` 控制字符、``Cf`` 格式字符、``Cn`` 未分配码位、
    ``Co`` 私用区、``Cs`` 代理）与组合标记 ``M*`` 都视为可嵌入 Markdown 标题且不可见
    或不可审计的 bridge。逐子类枚举必然漏列（如此前遗漏 ``Zl``/``Zp``/``Cn``/``Co``，
    使 ``Round 2<U+2028>3`` 被误接受为轮次 2）；改按类别族后新出现的同族字符不必再
    追列即被识别为隐藏续写。分隔符（``P*``/``S*``）另由
    :func:`_is_numeric_continuation_separator` 处理，与本类别族不重叠；bridge 后若跟
    的是普通说明文字（字母而非数字），:func:`_continues_numeric_expression` 不判为续写，
    故 ``Round 2，返修``、``Round 2、返修`` 等合法标题不受影响。
    """
    if character in " \t":
        return True
    category = unicodedata.category(character)
    return category[0] in {"Z", "C"} or category.startswith("M")


def _has_numeric_value(character: str) -> bool:
    """Return whether a character reads as another number.

    不能只按通用类别 ``N*`` 判断：类别为 ``Lo`` 却带 Unicode 数值的数词（如
    三 U+4E09、五、十、百）同样是数字，续写到它们就是范围/列表表达式。仅按 ``N*``
    会漏掉这些，使 ``Round 2、三`` 被误接受为轮次 2。
    """
    if unicodedata.category(character).startswith("N"):
        return True
    return unicodedata.numeric(character, None) is not None


def _continues_numeric_expression(heading: str, start: int) -> bool:
    """Reject punctuation chains after N when they lead to another number."""
    cursor = start
    saw_bridge = False
    while cursor < len(heading) and _is_numeric_bridge(heading[cursor]):
        saw_bridge = True
        cursor += 1
    saw_separator = False
    while (cursor < len(heading)
           and _is_numeric_continuation_separator(heading[cursor])):
        saw_separator = True
        cursor += 1
        while cursor < len(heading) and _is_numeric_bridge(heading[cursor]):
            cursor += 1
    return ((saw_bridge or saw_separator) and cursor < len(heading)
            and _has_numeric_value(heading[cursor]))


def _single_bounded_round_start(heading: str) -> int | None:
    """Find one ``Round`` marker whose Unicode attachment boundaries are clear."""
    single_start: int | None = None
    search_from = 0
    while True:
        token_start = heading.find(_ROUND_TOKEN, search_from)
        if token_start < 0:
            return single_start
        token_end = token_start + len(_ROUND_TOKEN)
        left_ok = (token_start == 0
                   or not _is_unicode_attachment(heading[token_start - 1]))
        right_ok = (token_end == len(heading)
                    or not _is_unicode_attachment(heading[token_end]))
        if left_ok and right_ok:
            if single_start is not None:
                return None
            single_start = token_start
        search_from = token_end


def _strict_single_review_round(heading: str) -> int | None:
    """Return one unambiguous ASCII ``Round N`` token, otherwise no evidence."""
    token_start = _single_bounded_round_start(heading)
    if token_start is None:
        return None

    cursor = token_start + len(_ROUND_TOKEN)
    whitespace_start = cursor
    while cursor < len(heading) and heading[cursor] in " \t":
        cursor += 1
    if cursor == whitespace_start:
        return None

    digit_start = cursor
    while cursor < len(heading) and "0" <= heading[cursor] <= "9":
        cursor += 1
    digit_count = cursor - digit_start
    if digit_count == 0 or heading[digit_start] == "0":
        return None
    if digit_count > _MAX_REVIEW_ROUND_DIGITS:
        return None
    if cursor < len(heading) and _is_unicode_attachment(heading[cursor]):
        return None
    if _continues_numeric_expression(heading, cursor):
        return None
    try:
        return int(heading[digit_start:cursor])
    except ValueError:
        return None


def _record_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current: str | None = None
    for raw in body.splitlines():
        line = raw.strip()
        match = _FIELD.match(line)
        if match:
            current, value = match.groups()
            fields[current] = value.strip().strip("`")
        elif current and line and not line.startswith("###"):
            # 多行列表保留为一段，便于摘要与测试结果提取。
            fields[current] += " " + line
    return fields


def _summary(value: str | None, limit: int = 220) -> str | None:
    if not value:
        return None
    clean = re.sub(r"[*`#]", "", value)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _last_record_time(records: Iterable[Record]) -> str | None:
    last: str | None = None
    for record in records:
        for key in _TIME_KEYS:
            if record.fields.get(key):
                last = record.fields[key]
    return last


def _hash_value(value: str | None) -> str | None:
    if not value:
        return None
    match = _HASH.search(value)
    return match.group(0).lower() if match else None


def _record_hash(record: Record, name: str) -> str | None:
    """同时支持独立字段和“审核证据”句内的 name=<hash>。"""
    direct = _hash_value(record.fields.get(name))
    if direct:
        return direct
    for value in record.fields.values():
        candidate = _named_hash(value, name)
        if candidate:
            return candidate
    return None


def _named_hash(value: str | None, name: str) -> str | None:
    if not value:
        return None
    match = re.search(rf"\b{re.escape(name)}\s*=\s*([0-9a-fA-F]{{64}})\b", value)
    return match.group(1).lower() if match else None


def _test_count_in(body: str) -> int | None:
    """从一段记录正文里提取"全部通过"的真实测试计数；不匹配则返回 None。"""
    counts: list[int] = []
    for pattern in (
        r"Ran\s+\*\*(\d+)\*\*\s+tests?,\s*OK",
        r"Ran\s+(\d+)\s+tests?,\s*OK",
        r"(?:=|→)\s*\*\*(\d+)\*\*/\*\*(\d+)\*\*",
        r"(?:=|→)\s*(\d+)/(\d+)",
    ):
        for match in re.finditer(pattern, body, re.IGNORECASE):
            first = int(match.group(1))
            if match.lastindex and match.lastindex > 1 and int(match.group(2)) != first:
                continue
            counts.append(first)
    return max(counts) if counts else None


def _test_result_of(record: Record) -> tuple[int | None, str | None]:
    count = _test_count_in(record.body)
    return (count, f"{count}/{count} 通过") if count else (None, None)


def _latest_test(records: list[Record]) -> tuple[int | None, str | None]:
    for record in reversed(records):
        counts: list[int] = []
        for pattern in (
            r"Ran\s+\*\*(\d+)\*\*\s+tests?,\s*OK",
            r"(?:=|\u2192)\s*\*\*(\d+)\*\*/\*\*(\d+)\*\*",
            r"(?:=|\u2192)\s*(\d+)/(\d+)",
        ):
            for match in re.finditer(pattern, record.body, re.IGNORECASE):
                first = int(match.group(1))
                if match.lastindex and match.lastindex > 1 and int(match.group(2)) != first:
                    continue
                counts.append(first)
        if counts:
            count = max(counts)
            return count, f"{count}/{count} 通过"
    return None, None


def _blocked_reason(records: list[Record]) -> str | None:
    for record in reversed(records):
        if record.kind != "review":
            continue
        for key in ("必须返修 / 阻塞原因", "阻塞原因", "必须返修"):
            if record.fields.get(key):
                return _summary(record.fields[key], 320)
    return None


def _waiting_label(owner: str | None) -> str | None:
    # 历史 fable5 与新 claude 统一显示为 Claude。
    return {"claude": "Claude", "fable5": "Claude", "codex": "Codex", "user": "用户"}.get(owner, owner)


def _write_access(status: str) -> str:
    return {
        "CLAUDE_WORKING": "Claude 可修改 scope 内文件",
        "FABLE_WORKING": "Claude 可修改 scope 内文件",  # legacy alias（只读）
        "CHANGES_REQUESTED": "Claude 可修改 scope 内文件",
        "READY_FOR_CODEX": "无人可修改 scope；Codex 待接手只读审核",
        "CODEX_REVIEWING": "Codex 只读审核，仅可写交接审核区",
        "APPROVED": "自动实施/审核已停止",
        "BLOCKED": "自动实施/审核已停止",
        "CLOSED": "工作包已关闭，无写入权",
    }[status]
