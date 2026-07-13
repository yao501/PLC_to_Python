"""双前端 mini-lowering（IR_SPEC §4/§6 的原型子集）。

- ST 侧：源模型 = 迷你 ST AST（IR_SPEC §4 规定源模型即 ST AST；文本解析器属阶段 3，
  原型直接手构 AST，类型标注代表 lowering 期符号表查询结果，§5.1）。
  顺序 = 代码书写顺序（显式）。
- CFC 侧：源模型 = 迷你 CFC 图（节点 + 连线 + 保留的执行序号，D3 导入不重新推断）。
  本原型图无反馈环，LOAD_PREV 不在 0.5 原型范围（PLATFORM_ROADMAP 阶段 0.5）。

两条路径产出同一种指令列表（Instr 结构相等）——"ST 与 CFC 同引擎"的工程证明。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import ir
from .ir import Instr


# ---------------------------------------------------------------- ST AST

@dataclass
class Var:
    name: str
    type: str


@dataclass
class Const:
    value: Any
    type: str


@dataclass
class Not:
    expr: Any          # BOOL
    type: str = "BOOL"


@dataclass
class Bin:
    op: str            # ADD/AND/GT/...
    left: Any
    right: Any
    type: str          # 求值类型（lowering 期已按 IEC 提升规则定死，§5.1）


@dataclass
class Assign:
    target: str
    target_type: str
    expr: Any


@dataclass
class FBCall:
    """库块调用语句：TON1(IN := ..., PT := ...)。"""

    inst: str
    params: list       # [(pin名, pin类型, expr)]


@dataclass
class FBInstanceCall:
    """用户 FB 实例调用语句（绑定表 lowering 期生成，随指令携带）。"""

    path: str
    bindings: list     # [ir.Binding]


@dataclass
class If:
    cond: Any
    then: list
    orelse: list = field(default_factory=list)


def _expr_type(e) -> str:
    return e.type


class _STLowerer:
    def __init__(self):
        self.instrs: list = []
        self._label_n = 0

    def _label(self) -> str:
        self._label_n += 1
        return f"L{self._label_n}"

    def expr(self, e) -> None:
        """表达式后序遍历生成 LOAD/BINOP/UNOP（IR_SPEC §6）。"""
        if isinstance(e, Var):
            self.instrs.append(ir.LOAD_VAR(e.name, e.type))
        elif isinstance(e, Const):
            self.instrs.append(ir.LOAD_CONST(e.value, e.type))
        elif isinstance(e, Not):
            self.expr(e.expr)
            self.instrs.append(ir.UNOP("NOT", "BOOL"))
        elif isinstance(e, Bin):
            self.expr(e.left)
            self.expr(e.right)
            self.instrs.append(ir.BINOP(e.op, e.type))
        else:
            raise TypeError(f"未知表达式节点 {e!r}")

    def stmt(self, s) -> None:
        if isinstance(s, Assign):
            self.expr(s.expr)
            et = _expr_type(s.expr)
            if et != s.target_type:      # 赋值转换显式化为 CONVERT（§5.1）
                self.instrs.append(ir.CONVERT(et, s.target_type))
            self.instrs.append(ir.STORE_VAR(s.target, s.target_type))
        elif isinstance(s, FBCall):
            # 库 FB 调用 = 逐输入 STORE_VAR <inst>.<in> → CALL_FB（§6）
            for pin, pin_type, e in s.params:
                self.expr(e)
                self.instrs.append(ir.STORE_VAR(f"{s.inst}.{pin}", pin_type))
            self.instrs.append(ir.CALL_FB(s.inst))
        elif isinstance(s, FBInstanceCall):
            self.instrs.append(ir.CALL_FB_INSTANCE(s.path, s.bindings))
        elif isinstance(s, If):
            l_else = self._label()
            l_end = self._label()
            self.expr(s.cond)
            self.instrs.append(ir.JMP_IF_FALSE(l_else))
            for st in s.then:
                self.stmt(st)
            self.instrs.append(ir.JMP(l_end))
            self.instrs.append(ir.LABEL(l_else))
            for st in s.orelse:
                self.stmt(st)
            self.instrs.append(ir.LABEL(l_end))
        else:
            raise TypeError(f"未知语句节点 {s!r}")


def lower_st(stmts: list) -> list:
    """ST AST → 可执行 IR。顺序即书写顺序。"""
    lw = _STLowerer()
    for s in stmts:
        lw.stmt(s)
    return lw.instrs


# ---------------------------------------------------------------- CFC 图

@dataclass
class CFCSource:
    """连线源：变量 / 上游管脚 / 常量。"""

    kind: str          # "var" / "pin" / "const"
    ref: Any           # 变量名 / "TON1.Q" / 常量值
    type: str


@dataclass
class CFCBlockNode:
    """库块框（保留的执行序号 = order，D3）。"""

    inst: str
    order: int
    inputs: list       # [(pin名, pin类型, CFCSource)]


@dataclass
class CFCOperatorNode:
    """运算框（如 AND），可带取反输入脚；输出接到 sink 变量。"""

    op: str
    order: int
    type: str
    inputs: list       # [(CFCSource, negated: bool)]
    sink: tuple        # (目标变量, 类型)


@dataclass
class CFCGraph:
    nodes: list
    order_preserved: bool = True    # 导入图：保留原始序号，不重新推断（D3）


def _load_source(instrs: list, src: CFCSource) -> None:
    if src.kind in ("var", "pin"):
        instrs.append(ir.LOAD_VAR(src.ref, src.type))
    elif src.kind == "const":
        instrs.append(ir.LOAD_CONST(src.ref, src.type))
    else:
        raise TypeError(f"未知连线源 {src!r}")


def lower_cfc(graph: CFCGraph) -> list:
    """CFC 图 → 可执行 IR：按保留的执行序号逐节点（IR_SPEC §6、ENGINE_SCAN_SPEC §5.1）。"""
    if not graph.order_preserved:
        raise NotImplementedError("新建图拓扑排序属阶段 2，原型只做导入保留顺序路径")
    orders = [n.order for n in graph.nodes]
    if len(set(orders)) != len(orders):
        raise ValueError("执行序号重复")
    instrs: list = []
    for node in sorted(graph.nodes, key=lambda n: n.order):
        if isinstance(node, CFCBlockNode):
            for pin, pin_type, src in node.inputs:
                _load_source(instrs, src)
                instrs.append(ir.STORE_VAR(f"{node.inst}.{pin}", pin_type))
            instrs.append(ir.CALL_FB(node.inst))
        elif isinstance(node, CFCOperatorNode):
            n_in = 0
            for src, negated in node.inputs:
                _load_source(instrs, src)
                if negated:
                    instrs.append(ir.UNOP("NOT", src.type))
                n_in += 1
                if n_in >= 2:
                    instrs.append(ir.BINOP(node.op, node.type))
            var, var_type = node.sink
            instrs.append(ir.STORE_VAR(var, var_type))
        else:
            raise TypeError(f"未知 CFC 节点 {node!r}")
    return instrs
