"""对 AI_REVIEW_HANDOFF.md 的容错、只读解析。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
import re
from typing import Iterable


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
    "CLAUDE_WORKING": "Claude 完成实施并交接给 Codex。",
    "FABLE_WORKING": "Claude 完成实施并交接给 Codex。",  # legacy alias（只读）
    "READY_FOR_CODEX": "Codex 开始只读审核。",
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
_TIME_KEYS = ("implementation_finished_at", "reviewed_at")


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

        package.records = _records(section)
        implementations = [r for r in package.records if r.kind == "implementation"]
        reviews = [r for r in package.records if r.kind == "review"]
        if implementations:
            latest = implementations[-1]
            package.latest_implementation_at = latest.fields.get("implementation_finished_at")
            package.latest_implementation_summary = _summary(latest.fields.get("完成内容"))
            package.implementation_scope_sha256 = _record_hash(latest, "scope_sha256")
        if reviews:
            latest = reviews[-1]
            package.latest_review_at = latest.fields.get("reviewed_at")
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
        if package.status == "BLOCKED":
            package.blocked_reason = _blocked_reason(package.records) or _one(values, "blocked_reason")
            if not package.blocked_reason:
                package.warnings.append("BLOCKED 状态未找到明确阻塞原因")
        return package


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
        # 同时识别历史 "Fable5 实施交接" 与新 "Claude 实施交接"。
        is_implementation = "Claude 实施交接" in heading or "Fable5 实施交接" in heading
        kind = "implementation" if is_implementation else "review" if "Codex 审核结论" in heading else "other"
        if kind == "other":
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        body = section[match.end():end]
        round_match = re.search(r"Round\s+(\d+)", heading, re.IGNORECASE)
        records.append(Record(kind, heading, int(round_match.group(1)) if round_match else None, _record_fields(body), body))
    return records


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
