"""加载器：IR 类型验证 pass + OutputPolicy 校验 + 实例展开（IR_SPEC §5.1、ENGINE_SCAN_SPEC §4）。

验证不通过不进引擎（LoadError）。原型验证规则（与本仓库两条 lowering 路径的产物形态匹配）：
- 全类型化：产生/消费值的指令缺 type 即拒绝；
- 栈类型流模拟：BINOP 两操作数 == 指令 type（比较结果恒 BOOL）；STORE_VAR 栈顶类型严格相等；
- 语句边界栈空：JMP/LABEL 处、JMP_IF_FALSE 弹出 BOOL 后要求栈空（本原型 lowering 产物满足；
  完整基本块数据流验证留阶段 1）；
- OutputPolicy：on_safety_trip/on_scan_fault/on_watchdog 配 "hold" 拒绝加载；
- Binding：IN 可 var/const；OUT/INOUT 只能 var（可写 StoreKey）；INOUT 形参必须声明 VAR_IN_OUT；
  全部接口形参必须绑定（齐全性检查）。
"""
from __future__ import annotations

import math

from .descriptors import REGISTRY
from .ir import (BINOPS_ARITH, BINOPS_CMP, BINOPS_LOGIC, FORCED_SAFE_FIELDS,
                 UNOPS, POUDefinition, Task)
from .numeric import INT_TYPES, NumericMode, default_value, is_int_type


class LoadError(Exception):
    pass


VALUE_OPS = {"LOAD_VAR", "LOAD_CONST", "STORE_VAR", "BINOP", "UNOP"}

_SECTION_TO_MODE = {"VAR_INPUT": "IN", "VAR_OUTPUT": "OUT", "VAR_IN_OUT": "INOUT"}


_ALL_FAULT_FIELDS = FORCED_SAFE_FIELDS + (
    "on_startup_not_ready", "on_operator_disable", "on_comm_loss")


def _value_matches_type(value, iec_type: str) -> bool:
    """常量/安全值与 IEC 声明类型的加载期匹配检查（含有限数与整数范围）。"""
    if iec_type == "BOOL":
        return isinstance(value, bool)
    if is_int_type(iec_type):
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        bits, signed = INT_TYPES[iec_type]
        lo, hi = ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed
                  else (0, (1 << bits) - 1))
        return lo <= value <= hi                 # 如 UINT 不接受负值
    if iec_type == "TIME":
        return isinstance(value, int) and not isinstance(value, bool)
    if iec_type in ("REAL", "LREAL"):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value))        # 物理量必须是有限数，NaN/Inf 拒绝
    if iec_type == "STRING":
        return isinstance(value, str)
    return True


def validate_policy(policy) -> None:
    """OutputPolicy 是安全配置：任何非法字段加载期硬拒绝（ENGINE_SCAN_SPEC §4）。"""
    for f in _ALL_FAULT_FIELDS:
        v = getattr(policy, f)
        if v not in ("safe", "hold"):
            raise LoadError(f"非法工程：OutputPolicy({policy.var}).{f}={v!r} "
                            "不是合法 FaultAction（'safe'/'hold'）")
    for f in FORCED_SAFE_FIELDS:
        if getattr(policy, f) != "safe":
            raise LoadError(f"非法工程：OutputPolicy({policy.var}).{f} 必须为 'safe'，"
                            f"得到 {getattr(policy, f)!r}（ENGINE_SCAN_SPEC §4 约束 1）")
    if policy.rate_limit is not None:
        if not isinstance(policy.rate_limit, (int, float)) or isinstance(policy.rate_limit, bool) \
                or not math.isfinite(policy.rate_limit) or policy.rate_limit <= 0:
            raise LoadError(f"非法工程：OutputPolicy({policy.var}).rate_limit="
                            f"{policy.rate_limit!r} 必须为有限正数或 None"
                            "（NaN/Infinity 拒绝；不限速用 None）")
    if not isinstance(policy.commit_fault_retry_n, int) \
            or isinstance(policy.commit_fault_retry_n, bool) \
            or policy.commit_fault_retry_n < 1:
        raise LoadError(f"非法工程：OutputPolicy({policy.var}).commit_fault_retry_n="
                        f"{policy.commit_fault_retry_n!r} 必须为 ≥1 的整数"
                        "（0 意味着首次失败即无重试语义，名实不符）")
    if not _value_matches_type(policy.safe_value, policy.iec_type):
        raise LoadError(f"非法工程：OutputPolicy({policy.var}).safe_value="
                        f"{policy.safe_value!r} 与 iec_type={policy.iec_type} 不匹配"
                        "（含有限数与整数范围检查，如 UINT 不接受负值）")


def _validate_bindings(instr, definition: POUDefinition, ctx: str) -> None:
    iface = {v.name: v for v in definition.interface}
    bound = set()
    for b in instr.bindings:
        if b.formal not in iface:
            raise LoadError(f"{ctx}: 绑定了未声明形参 {b.formal!r}")
        if b.formal in bound:
            raise LoadError(f"{ctx}: 形参 {b.formal!r} 重复绑定（禁止静默覆盖）")
        if b.actual_kind not in ("var", "const"):
            raise LoadError(f"{ctx}: 形参 {b.formal} 非法 actual_kind={b.actual_kind!r}")
        if b.actual_kind == "const" and not _value_matches_type(b.actual, b.type):
            raise LoadError(f"{ctx}: 形参 {b.formal} 的 const 实参 {b.actual!r} "
                            f"与声明类型 {b.type} 不匹配")
        decl = iface[b.formal]
        expect_mode = _SECTION_TO_MODE[decl.section]
        if b.mode != expect_mode:
            raise LoadError(f"{ctx}: 形参 {b.formal} 声明 {decl.section}，绑定 mode={b.mode}")
        if b.type != decl.iec_type:
            raise LoadError(f"{ctx}: 形参 {b.formal} 类型 {decl.iec_type}，绑定 {b.type}")
        if b.mode in ("OUT", "INOUT") and b.actual_kind != "var":
            raise LoadError(f"{ctx}: {b.mode} 形参 {b.formal} 只能绑定可写 StoreKey，"
                            f"得到 {b.actual_kind}（IR_SPEC §5.2 模式约束）")
        bound.add(b.formal)
    missing = set(iface) - bound
    if missing:
        raise LoadError(f"{ctx}: 形参未绑定 {sorted(missing)}（齐全性检查）")


def validate_code(code, decl_types: dict, task: Task, ctx: str,
                  fb_paths: dict = None) -> None:
    """栈类型流模拟。decl_types: 本作用域可寻址键 -> IEC 类型。"""
    fb_paths = fb_paths or {}
    labels = set()
    for ins in code:
        if ins.op == "LABEL":
            if ins.key in labels:
                raise LoadError(f"{ctx}: 重复 LABEL {ins.key}")
            labels.add(ins.key)
    for ins in code:
        if ins.op in ("JMP", "JMP_IF_FALSE") and ins.key not in labels:
            raise LoadError(f"{ctx}: 跳转到不存在的 LABEL {ins.key}")

    stack: list = []

    def pop(expect=None, what=""):
        if not stack:
            raise LoadError(f"{ctx}: 栈下溢 @ {what}")
        t = stack.pop()
        if expect is not None and t != expect:
            raise LoadError(f"{ctx}: 类型不匹配 @ {what}：期望 {expect}，栈顶 {t}")
        return t

    for i, ins in enumerate(code):
        where = f"#{i} {ins.op}"
        if ins.op in VALUE_OPS and ins.type is None:
            raise LoadError(f"{ctx}: 无类型指令不是合法 IR @ {where}（IR_SPEC §5.1）")
        if ins.op == "LOAD_CONST":
            if not _value_matches_type(ins.value, ins.type):
                raise LoadError(f"{ctx}: LOAD_CONST 常量 {ins.value!r} 与类型 "
                                f"{ins.type} 不匹配 @ {where}")
            stack.append(ins.type)
        elif ins.op == "LOAD_VAR":
            if ins.key not in decl_types:
                raise LoadError(f"{ctx}: 引用未声明变量 {ins.key!r} @ {where}")
            if decl_types[ins.key] != ins.type:
                raise LoadError(f"{ctx}: {ins.key} 声明 {decl_types[ins.key]}，指令标注 {ins.type}")
            stack.append(ins.type)
        elif ins.op == "STORE_VAR":
            if ins.key not in decl_types:
                raise LoadError(f"{ctx}: 写入未声明变量 {ins.key!r} @ {where}")
            if decl_types[ins.key] != ins.type:
                raise LoadError(f"{ctx}: {ins.key} 声明 {decl_types[ins.key]}，指令标注 {ins.type}")
            pop(expect=ins.type, what=where)   # 栈顶类型必须严格相等（§5.1）
        elif ins.op == "BINOP":
            if ins.subop not in BINOPS_ARITH | BINOPS_LOGIC | BINOPS_CMP:
                raise LoadError(f"{ctx}: 未知 BINOP {ins.subop}")
            pop(expect=ins.type, what=where)
            pop(expect=ins.type, what=where)
            stack.append("BOOL" if ins.subop in BINOPS_CMP else ins.type)
        elif ins.op == "UNOP":
            if ins.subop not in UNOPS:
                raise LoadError(f"{ctx}: 未知 UNOP {ins.subop}")
            if ins.subop == "NOT" and ins.type != "BOOL":
                raise LoadError(f"{ctx}: 原型 NOT 仅支持 BOOL @ {where}")
            pop(expect=ins.type, what=where)
            stack.append(ins.type)
        elif ins.op == "CONVERT":
            if ins.type is None or ins.to_type is None:
                raise LoadError(f"{ctx}: CONVERT 缺 from/to 类型 @ {where}")
            pop(expect=ins.type, what=where)
            stack.append(ins.to_type)
        elif ins.op == "CALL_FB":
            insts = {d.name: d for d in task.instances}
            if ins.key not in insts or insts[ins.key].kind != "library":
                raise LoadError(f"{ctx}: CALL_FB 引用未声明库实例 {ins.key!r}")
        elif ins.op == "CALL_FB_INSTANCE":
            if ins.key not in fb_paths:
                raise LoadError(f"{ctx}: CALL_FB_INSTANCE 引用未展开实例 {ins.key!r}"
                                "（调用不建实例，IR_SPEC §3）")
            if ins.bindings is None:
                raise LoadError(f"{ctx}: CALL_FB_INSTANCE 缺绑定表 @ {where}")
            definition = task.pou_lib[fb_paths[ins.key]]
            _validate_bindings(ins, definition, f"{ctx} @ {where}")
            for b in ins.bindings:
                if b.actual_kind == "var":
                    if b.actual not in decl_types:
                        raise LoadError(f"{ctx}: 绑定 actual 未声明 {b.actual!r} @ {where}")
                    # 全类型化闭环：actual 变量的声明类型必须与绑定类型严格相等
                    if decl_types[b.actual] != b.type:
                        raise LoadError(
                            f"{ctx}: 绑定 actual {b.actual!r} 声明类型 "
                            f"{decl_types[b.actual]}，形参 {b.formal} 要求 {b.type} @ {where}")
        elif ins.op == "JMP":
            if stack:
                raise LoadError(f"{ctx}: JMP 处栈非空 @ {where}（语句边界约定）")
        elif ins.op == "JMP_IF_FALSE":
            pop(expect="BOOL", what=where)
            if stack:
                raise LoadError(f"{ctx}: JMP_IF_FALSE 后栈非空 @ {where}")
        elif ins.op == "LABEL":
            if stack:
                raise LoadError(f"{ctx}: LABEL 处栈非空 @ {where}")
        else:
            raise LoadError(f"{ctx}: 未知指令 {ins.op}")
    if stack:
        raise LoadError(f"{ctx}: 程序末尾栈非空 {stack}")


class Loaded:
    """装载结果：store 初值 + 类型表 + 实例表。"""

    def __init__(self):
        self.store: dict = {}
        self.decl_types: dict = {}          # 程序作用域可寻址键 -> 类型
        self.lib_instances: dict = {}       # 实例名 -> (descriptor, 块对象)
        self.fb_instances: dict = {}        # 实例路径 -> POUDefinition 名


def load(task: Task, mode: NumericMode, registry: dict = None) -> Loaded:
    registry = registry if registry is not None else REGISTRY
    out = Loaded()

    # GVL
    for v in task.gvl:
        out.decl_types[v.name] = v.iec_type
        init = v.initial if v.initial is not None else default_value(v.iec_type)
        out.store[v.name] = mode.on_store(init, v.iec_type)

    # 实例展开（装载期创建全部实例内存；运行期任何调用不得创建实例，IR_SPEC §3）
    for decl in task.instances:
        if decl.kind == "library":
            key = (decl.block_type, "engineering")
            if key not in registry:
                raise LoadError(f"未注册库块 {key}")
            desc = registry[key]
            out.lib_instances[decl.name] = (desc, desc.cls())
            for pin in desc.inputs + desc.outputs:
                k = f"{decl.name}.{pin.name}"
                out.decl_types[k] = pin.iec_type
                d = pin.default if pin.default is not None else default_value(pin.iec_type)
                out.store[k] = mode.on_store(d, pin.iec_type)
        elif decl.kind == "user_fb":
            if decl.block_type not in task.pou_lib:
                raise LoadError(f"用户 FB 定义缺失: {decl.block_type}")
            definition = task.pou_lib[decl.block_type]
            out.fb_instances[decl.name] = definition.name
            for v in definition.interface + definition.locals:
                k = f"{decl.name}.{v.name}"
                init = v.initial if v.initial is not None else default_value(v.iec_type)
                out.store[k] = mode.on_store(init, v.iec_type)
        else:
            raise LoadError(f"未知实例 kind: {decl.kind}")

    # I/O 映射与 OutputPolicy
    for io in task.io_map:
        if io.direction == "OUT":
            if io.policy is None:
                raise LoadError(f"OUT 通道 {io.channel} 缺 OutputPolicy")
            validate_policy(io.policy)
            if io.policy.var not in out.decl_types:
                raise LoadError(f"OutputPolicy.var 未声明: {io.policy.var}")
            if out.decl_types[io.policy.var] != io.policy.iec_type:
                raise LoadError(f"OutputPolicy({io.policy.var}) 类型与声明不一致")
        elif io.var not in out.decl_types:
            raise LoadError(f"IN 通道映射到未声明变量 {io.var}")

    # 用户 FB 定义体验证（作用域 = self.<接口/局部>）
    for name, definition in task.pou_lib.items():
        if definition.pou_kind != "FUNCTION_BLOCK":
            raise LoadError(f"原型仅支持 FUNCTION_BLOCK 定义，得到 {definition.pou_kind}")
        self_types = {f"self.{v.name}": v.iec_type
                      for v in definition.interface + definition.locals}
        validate_code(definition.code, self_types, task, f"FB定义 {name}",
                      fb_paths=out.fb_instances)

    # 程序体验证
    for prog in task.programs:
        validate_code(prog.code, out.decl_types, task, f"程序 {prog.name}",
                      fb_paths=out.fb_instances)

    return out
