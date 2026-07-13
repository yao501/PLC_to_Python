"""CODESYS V3 原生 .export（归档序列化 XML）最小解析器——可行性试验用。

识别范围（以 sample/test.export 实测锁定的编码，字段 GUID 见下）：
- POU 条目（TypeGuid 6f9dac99-…）：名称、路径、实现语言（ST TextDocument / CFC Items）；
- ST：接口区 + 实现区文本（TextLines 按数组顺序即文档顺序，Id 是创建序号非行号）；
- CFC 元素（Implementation.Items.InnerList，按元素类型 GUID 分类）：
    {5ae2e111-…} 连线：SourcePinId → DestPinId
    {d51129f5-…} 输入源框：Output 管脚 Id + 表达式文本（变量/常量）+ Negated
    {f5becf35-…} 调用框：Inputs/Outputs 管脚 Id 列表、框名（Texts 中 Modifiable 且非空项）、
                  KindOfCall（Operator/…）、IsFeedbackStart（反馈起点标记，元素级）
    其余带 Input 管脚 + 文本的元素 → 输出汇框（写变量）
- 任务配置（TypeGuid 98a2708a-…）：Kindoftask / Priority / Interval / Watchdog；
- POU Properties 中的 UseExplicitExecutionOrder（CFC 顺序模式）。

**实测关键结论**：自动数据流顺序模式下（UseExplicitExecutionOrder 缺省/False），
导出**不存储**每元素执行序号——编辑器里显示的 0..4 是派生值。故 D3"导入保留序号"
仅对显式顺序模式直接成立；自动模式必须由导入器按 CODESYS 的数据流规则重建顺序，
并须与真机显示序号对拍验证（登记 RISKS::PLATFORM-CFC-AUTOORDER-1）。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

GUID_POU = "6f9dac99-8de1-4efc-8465-68ac443b7d08"
GUID_TASK = "98a2708a-9b18-4f31-82ed-a1465b24fa2d"
GUID_CONN = "5ae2e111-ecff-4a21-b647-2d4da63f8db7"
GUID_SRCBOX = "d51129f5-df27-4886-99d1-c564d2e2c1f6"
GUID_CALLBOX = "f5becf35-b1f3-4274-b411-81d4b63a1516"


def _kids(node, name):
    return [c for c in node if c.get("Name") == name]


def _kid(node, name):
    k = _kids(node, name)
    return k[0] if k else None


def _val(node, name, default=None):
    k = _kid(node, name)
    return k.text if k is not None and k.text is not None else default


@dataclass
class STPou:
    name: str
    interface: list
    body: list


@dataclass
class CFCElement:
    kind: str                 # "source" / "box" / "sink" / "conn" / "other"
    id: int = -1
    text: str = ""            # 表达式 / 框名
    in_pins: list = field(default_factory=list)     # [(pin_id, negated)]
    out_pins: list = field(default_factory=list)
    negated: bool = False
    is_feedback_start: bool = False
    kind_of_call: str = ""
    src_pin: int = -1         # 仅连线
    dst_pin: int = -1


@dataclass
class CFCPou:
    name: str
    interface: list
    elements: list            # CFCElement（保留 InnerList 原始顺序）
    explicit_order: bool = False   # UseExplicitExecutionOrder


@dataclass
class TaskCfg:
    name: str
    kind: str
    priority: str
    interval: str
    watchdog_enabled: bool
    pous: list


def _text_lines(textdoc) -> list:
    """TextLines 数组顺序 = 文档顺序。"""
    arr = _kid(textdoc, "TextLines")
    out = []
    for line in (arr if arr is not None else []):
        out.append(_val(line, "Text", ""))
    return out


def _parse_st_impl(impl):
    td = _kid(impl, "TextDocument")
    return _text_lines(td) if td is not None else None


def _guid_of(el) -> str:
    return (el.get("Type") or "").strip("{}").lower()


def _inner_list(el, name) -> list:
    """取 el.<name>.InnerList 的子元素列表；避免对 Element 做真值判断（弃用行为）。"""
    parent = _kid(el, name)
    inner = _kid(parent, "InnerList") if parent is not None else None
    return [] if inner is None else list(inner)


def _parse_cfc_items(items) -> list:
    inner = _kid(items, "InnerList")
    elements = []
    for el in (inner if inner is not None else []):
        g = _guid_of(el)
        if g == GUID_CONN:
            elements.append(CFCElement(
                kind="conn", id=int(_val(el, "Id", -1)),
                src_pin=int(_val(el, "SourcePinId", -1)),
                dst_pin=int(_val(el, "DestPinId", -1))))
        elif g == GUID_SRCBOX:
            outp = _kid(el, "Output")
            txt = _kid(el, "Text")
            elements.append(CFCElement(
                kind="source", id=int(_val(el, "Id", -1)),
                text=_val(txt, "Text", "") if txt is not None else "",
                out_pins=[(int(_val(outp, "Id", -1)), _val(outp, "Negated") == "True")]
                if outp is not None else [],
                negated=_val(outp, "Negated") == "True" if outp is not None else False))
        elif g == GUID_CALLBOX:
            ins, outs = [], []
            for pin in _inner_list(el, "Inputs"):
                ins.append((int(_val(pin, "Id", -1)), _val(pin, "Negated") == "True"))
            for pin in _inner_list(el, "Outputs"):
                outs.append((int(_val(pin, "Id", -1)), _val(pin, "Negated") == "True"))
            name = ""
            for t in _inner_list(el, "Texts"):
                if _val(t, "Modifiable") == "True" and _val(t, "Text", ""):
                    name = _val(t, "Text", "")
            elements.append(CFCElement(
                kind="box", id=int(_val(el, "Id", -1)), text=name,
                in_pins=ins, out_pins=outs,
                kind_of_call=(_val(el, "KindOfCall", "") or "").strip(),
                is_feedback_start=_val(el, "IsFeedbackStart") == "True"))
        else:
            inp = _kid(el, "Input")
            txt = _kid(el, "Text")
            if inp is not None and txt is not None:      # 输出汇框（写变量）
                elements.append(CFCElement(
                    kind="sink", id=int(_val(el, "Id", -1)),
                    text=_val(txt, "Text", ""),
                    in_pins=[(int(_val(inp, "Id", -1)), _val(inp, "Negated") == "True")],
                    negated=_val(inp, "Negated") == "True"))
            else:
                elements.append(CFCElement(kind="other", id=int(_val(el, "Id", -1) or -1)))
    return elements


def parse_export(path: str) -> dict:
    root = ET.parse(path).getroot()
    result = {"pous_st": [], "pous_cfc": [], "tasks": [], "device": None}
    entry_list = root.find(".//List2[@Name='EntryList']")
    for entry in entry_list:
        meta = _kid(entry, "MetaObject")
        obj = _kid(entry, "Object")
        if meta is None or obj is None:
            continue
        name = _val(meta, "Name", "")
        type_guid = (_val(meta, "TypeGuid", "") or "").lower()
        if name == "Device":
            # 设备名在其 Object 内的子结构（目标：CODESYS Control Win V3 x64）
            for s in obj.iter():
                if s.get("Name") == "Name" and s.get("Type") == "string" \
                        and s.text and "CODESYS" in s.text:
                    result["device"] = s.text
                    break
        if type_guid == GUID_POU:
            impl = _kid(obj, "Implementation")
            iface = _kid(obj, "Interface")
            iface_lines = _parse_st_impl(iface) if iface is not None else []
            explicit = False
            props = _kid(meta, "Properties")
            if props is not None:
                for s in props.iter():
                    if s.get("Name") == "UseExplicitExecutionOrder":
                        explicit = s.text == "True"
            items = _kid(impl, "Items") if impl is not None else None
            if items is not None:                        # CFC 实现
                result["pous_cfc"].append(CFCPou(
                    name=name, interface=iface_lines,
                    elements=_parse_cfc_items(items), explicit_order=explicit))
            else:                                        # ST 实现
                body = _parse_st_impl(impl) if impl is not None else []
                result["pous_st"].append(STPou(name=name, interface=iface_lines,
                                               body=body or []))
        elif type_guid == GUID_TASK:
            interval = _kid(obj, "Interval")
            wd = _kid(obj, "Watchdog")
            pous = [s.text for s in obj.iter()
                    if s.get("Name") == "Name" and s.get("Type") == "string" and s.text]
            result["tasks"].append(TaskCfg(
                name=name, kind=_val(obj, "Kindoftask", ""),
                priority=_val(obj, "Priority", ""),
                interval=(f"{_val(interval, 'Time', '?')}"
                          f"{_val(interval, 'Unit', '')}" if interval is not None else "?"),
                watchdog_enabled=(_val(wd, "Enabled") == "True") if wd is not None else False,
                pous=pous))
    return result


# ------------------------------------------------ 图重建与自动数据流定序

def build_graph(cfc: CFCPou) -> dict:
    """管脚归属表 + 连线解析 → 邻接结构。"""
    pin_owner = {}
    nodes = {}
    for e in cfc.elements:
        if e.kind in ("source", "box", "sink"):
            nodes[e.id] = e
            for pid, _n in e.in_pins + e.out_pins:
                pin_owner[pid] = e.id
    edges = []          # (src_elem, dst_elem, src_pin, dst_pin)
    for e in cfc.elements:
        if e.kind == "conn":
            edges.append((pin_owner[e.src_pin], pin_owner[e.dst_pin],
                          e.src_pin, e.dst_pin))
    return {"nodes": nodes, "edges": edges}


def derive_dataflow_order(graph: dict) -> list:
    """自动数据流模式的派生执行序（导出不含序号）：对无环图做确定性拓扑排序，
    同层按元素 Id 升序（≈创建顺序）。**这是待与真机显示序号对拍验证的重建假设**，
    不声称与 CODESYS 算法逐一致（PLATFORM-CFC-AUTOORDER-1）。"""
    nodes, edges = graph["nodes"], graph["edges"]
    exec_nodes = [i for i, n in nodes.items() if n.kind in ("box", "sink")]
    deps = {i: set() for i in exec_nodes}
    for s, d, _sp, _dp in edges:
        if d in deps and nodes[s].kind in ("box", "sink"):
            deps[d].add(s)
    order, done = [], set()
    while len(order) < len(exec_nodes):
        ready = sorted(i for i in exec_nodes
                       if i not in done and deps[i] <= done)
        if not ready:
            raise ValueError("存在环且无反馈起点标记，无法定序")
        order.append(ready[0])
        done.add(ready[0])
    return order


def report(path: str) -> str:
    r = parse_export(path)
    lines = [f"设备: {r['device']}"]
    for t in r["tasks"]:
        lines.append(f"任务: {t.name} kind={t.kind} priority={t.priority} "
                     f"interval={t.interval} watchdog={'on' if t.watchdog_enabled else 'off'} "
                     f"调用={t.pous}")
    for p in r["pous_st"]:
        lines.append(f"ST POU: {p.name}")
        lines.append("  接口: " + " / ".join(x for x in p.interface if x.strip()))
        lines.append("  实现: " + " / ".join(x.strip() for x in p.body if x.strip()))
    for p in r["pous_cfc"]:
        lines.append(f"CFC POU: {p.name} 顺序模式="
                     f"{'显式' if p.explicit_order else '自动数据流(导出无每元素序号)'}")
        lines.append("  接口: " + " / ".join(x for x in p.interface if x.strip()))
        g = build_graph(p)
        for i, n in sorted(g["nodes"].items()):
            if n.kind == "box":
                lines.append(f"  框#{i} {n.text} ({n.kind_of_call}) "
                             f"in={[p_ for p_, _ in n.in_pins]} "
                             f"out={[p_ for p_, _ in n.out_pins]} "
                             f"feedback_start={n.is_feedback_start}")
            elif n.kind == "source":
                lines.append(f"  源#{i} '{n.text}' out={[p_ for p_, _ in n.out_pins]}"
                             + (" (取反)" if n.negated else ""))
            elif n.kind == "sink":
                lines.append(f"  汇#{i} '{n.text}' in={[p_ for p_, _ in n.in_pins]}"
                             + (" (取反)" if n.negated else ""))
        for s, d, sp, dp in g["edges"]:
            lines.append(f"  连线: #{s}(pin{sp}) -> #{d}(pin{dp})")
        try:
            order = derive_dataflow_order(g)
            named = [f"#{i}:{g['nodes'][i].text or g['nodes'][i].kind}" for i in order]
            lines.append(f"  派生执行序(待真机对拍): {named}")
        except ValueError as e:
            lines.append(f"  定序失败: {e}")
    return "\n".join(lines)


if __name__ == "__main__":
    import os
    print(report(os.path.join(os.path.dirname(__file__), "sample", "test.export")))
