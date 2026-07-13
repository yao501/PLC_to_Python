"""PLCopen XML（tc6_0200，CODESYS 导出）最小解析器——补充样本试验用。

样本：sample/test_fb_feedback.xml（用户 2026-07-09 提供，覆盖采集清单 ②反馈环 ③TON 实例框）。

与原生 .export 载体的关键差异（实测）：
1. **每元素 executionOrderId 显式存储**（block/outVariable 带该属性）——与 .export
   自动模式"序号不存储、须派生"相反。PLCopen XML 是阶段 5 导入器的候选首选载体。
2. **无显式反馈起点标记字段**（全文无 feedback 字样）：反馈环只以拓扑形式存在
   （本样本 ADD.In2 经 connector 接回 ADD 自身输出），环的入口由 executionOrderId
   最小者体现（ADD=1 全图最先执行）。原生 .export 的 `IsFeedbackStart` 落点仍待
   同工程 .export 再导对照（可选）。
3. 连线一律经 `<connector>` 中继元素（source → connector → 消费者），解析时需沿
   refLocalId 链解引用。
4. fileHeader.productVersion 给出精确版本（本样本 "CODESYS V3.5 SP16 Patch 1"）。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NS = "{http://www.plcopen.org/xml/tc6_0200}"


def _tag(el) -> str:
    return el.tag.split("}")[-1]


@dataclass
class Block:
    local_id: int
    type_name: str
    exec_order: int
    instance_name: str = ""
    call_type: str = ""
    inputs: dict = field(default_factory=dict)    # formal -> refLocalId
    outputs: list = field(default_factory=list)   # formal 名列表


@dataclass
class CFCModel:
    pou_name: str
    variables: dict                # 名 -> 类型
    in_vars: dict                  # localId -> 表达式
    out_vars: dict                 # localId -> (表达式, exec_order, refLocalId)
    connectors: dict               # localId -> (refLocalId, formalParameter)
    blocks: dict                   # localId -> Block


def parse_plcopen(path: str) -> dict:
    root = ET.parse(path).getroot()
    fh = root.find(f"{NS}fileHeader")
    result = {"product_version": fh.get("productVersion") if fh is not None else "",
              "tasks": [], "pous": []}

    for ts in root.iter():
        if _tag(ts) == "TaskSettings":
            wd = next((c for c in ts if _tag(c) == "Watchdog"), None)
            result["tasks"].append({
                "kind": ts.get("KindOfTask"),
                "interval": f"{ts.get('Interval')}{ts.get('IntervalUnit')}",
                "watchdog": wd.get("Enabled") == "true" if wd is not None else False})

    for pou in root.iter():
        if _tag(pou) != "pou":
            continue
        name = pou.get("name")
        variables = {}
        for v in pou.iter():
            if _tag(v) == "variable" and v.get("name"):
                t = next((c for c in v if _tag(c) == "type"), None)
                if t is not None and len(t):
                    tt = t[0]
                    variables[v.get("name")] = (tt.get("name") if _tag(tt) == "derived"
                                                else _tag(tt))
        cfc = next((e for e in pou.iter() if _tag(e) == "CFC"), None)
        st = next((e for e in pou.iter() if _tag(e) == "ST"), None)
        st_text = ""
        if st is not None:
            st_text = "".join(x.strip() for x in st.itertext()).strip()
        if cfc is None:
            result["pous"].append({"name": name, "lang": "ST", "vars": variables,
                                   "body": st_text})
            continue
        model = CFCModel(pou_name=name, variables=variables, in_vars={},
                         out_vars={}, connectors={}, blocks={})
        for el in cfc:
            t, lid = _tag(el), int(el.get("localId", -1))
            if t == "inVariable":
                expr = next((c.text for c in el if _tag(c) == "expression"), "")
                model.in_vars[lid] = expr
            elif t == "outVariable":
                expr = next((c.text for c in el if _tag(c) == "expression"), "")
                ref = next((int(c.get("refLocalId")) for c in el.iter()
                            if _tag(c) == "connection"), -1)
                model.out_vars[lid] = (expr, int(el.get("executionOrderId", -1)), ref)
            elif t == "connector":
                conn = next((c for c in el.iter() if _tag(c) == "connection"), None)
                model.connectors[lid] = (int(conn.get("refLocalId")),
                                         conn.get("formalParameter") or "")
            elif t == "block":
                b = Block(local_id=lid, type_name=el.get("typeName"),
                          exec_order=int(el.get("executionOrderId", -1)),
                          instance_name=el.get("instanceName") or "")
                for c in el.iter():
                    tc = _tag(c)
                    if tc == "CallType":
                        b.call_type = (c.text or "").strip()
                    elif tc == "variable" and c.get("formalParameter"):
                        conn = next((x for x in c.iter() if _tag(x) == "connection"), None)
                        if conn is not None:
                            b.inputs[c.get("formalParameter")] = int(conn.get("refLocalId"))
                        else:
                            b.outputs.append(c.get("formalParameter"))
                model.blocks[lid] = b
        result["pous"].append({"name": name, "lang": "CFC", "vars": variables,
                               "model": model})
    return result


def resolve(model: CFCModel, ref: int):
    """沿 connector 中继链解引用到真实源：返回 ("in", 表达式) / ("block", localId, 输出脚)。"""
    pin = ""
    seen = set()
    while ref in model.connectors:
        if ref in seen:
            raise ValueError("connector 环")
        seen.add(ref)
        ref, pin = model.connectors[ref]
    if ref in model.in_vars:
        return ("in", model.in_vars[ref])
    if ref in model.blocks:
        return ("block", ref, pin)
    raise ValueError(f"未知引用 {ref}")


def feedback_edges(model: CFCModel) -> list:
    """检测反馈边：消费者的解析源是执行序号 ≥ 自身的框（含自环）。
    PLCopen 载体无显式标记，这是按 executionOrderId 推断（待与原生 .export 对照）。"""
    edges = []
    for lid, b in model.blocks.items():
        for formal, ref in b.inputs.items():
            src = resolve(model, ref)
            if src[0] == "block":
                sb = model.blocks[src[1]]
                if sb.exec_order >= b.exec_order:
                    edges.append((b.type_name, formal, sb.type_name, src[2] or "Out"))
    return edges


def report(path: str) -> str:
    r = parse_plcopen(path)
    lines = [f"产品版本: {r['product_version']}"]
    for t in r["tasks"]:
        lines.append(f"任务: {t['kind']} {t['interval']} watchdog="
                     f"{'on' if t['watchdog'] else 'off'}")
    for p in r["pous"]:
        if p["lang"] == "ST":
            lines.append(f"ST POU: {p['name']} 实现: {p['body']}")
            continue
        m = p["model"]
        lines.append(f"CFC POU: {p['name']} 变量: {m.variables}")
        for lid, b in sorted(m.blocks.items(), key=lambda kv: kv[1].exec_order):
            src = {f: resolve(m, ref) for f, ref in b.inputs.items()}
            inst = f" 实例={b.instance_name}" if b.instance_name else ""
            lines.append(f"  框 {b.type_name}#{lid}{inst} 序号={b.exec_order} "
                         f"类别={b.call_type} 入={src} 出={b.outputs}")
        for lid, (expr, order, ref) in sorted(m.out_vars.items(),
                                              key=lambda kv: kv[1][1]):
            lines.append(f"  汇 '{expr}'#{lid} 序号={order} <- {resolve(m, ref)}")
        fb = feedback_edges(m)
        lines.append(f"  反馈边(按序号推断): {fb if fb else '无'}")
    return "\n".join(lines)


if __name__ == "__main__":
    import os
    print(report(os.path.join(os.path.dirname(__file__), "sample",
                              "test_fb_feedback.xml")))
