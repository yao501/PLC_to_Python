"""装载期静态校验（L3 → L4 之间的闸门，`docs/IR_SPEC.md` v2.2.2 §5.1）。

公开入口：``validate_task(task)``——校验失败抛 ``IRValidationError``，
**阻止 IR 进入后续执行层**。本包不创建 Store、不展开实例内存、不运行引擎。

校验覆盖（WP-20260713-002 实施范围第 2 条）：

1. 声明与引用：IEC 类型合法、同一作用域名称唯一、pou_lib 键/名一致、
   PROGRAM/FB/FUNCTION 结构约束、定义/实例/变量/标签/跳转目标存在、
   实例引用种类正确；递归实例声明的循环检测（不分配运行时 Store）。
2. 控制流感知的栈类型验证：所有可达路径不下溢；产生/消费值的指令类型
   齐全；CONVERT 显式 from/to；STORE_VAR 类型严格匹配；比较结果为 BOOL；
   JMP_IF_FALSE 消费 BOOL；控制流汇合点栈深与逐项类型一致；正常出口栈
   状态符合契约（PROGRAM/FB 空栈；FUNCTION 恰余一个返回值）。
3. CALL_FUNC / CALL_FB_INSTANCE 绑定校验：齐全性（**全部接口形参必绑**：
   当前 IR 模型没有"默认值已解析完毕"或"显式丢弃 OUT"的独立编码，任何
   缺省均按加载错误拒绝，待相关形态显式建模后方可放宽）、重复、
   formal/mode/type 匹配、actual 可写性（OUT 禁 Const；INOUT 必须可写
   StoreKey）；CALL_FUNC 返回类型与 FUNCTION 定义一致。CALL_FB 只验证
   引用已声明的 library 实例（L2 描述符注册表不在本包接入）。
4. FUNCTION 禁止 GVL 访问；VAR_TEMP 仅允许 PROGRAM/FUNCTION_BLOCK。

诚实边界（不得伪称已验证）：

- 库块实例管脚（``<inst>.<pin>``）的类型来自 L2 描述符，本包无法核对，
  仅校验实例已声明；管脚名与类型正确性留 L2 接入后验证。
- ``CALL_STD`` 只做签名结构与栈类型校验，不核对标准函数名册与 IEC 语义。
- 数值语义（REAL 量化、整数回绕、越界转换）不在本包验证。
- 绑定 actual 为 **StackSlot** 的栈效应建模（``index`` 语义为项目工程
  约定，IR_SPEC §5.2 未细化该字段）：``index`` = 距调用点栈顶的偏移
  （0 = 栈顶）；同一调用的全部 IN×StackSlot 索引必须互不重复且恰好连续
  覆盖 {0..k-1}，调用按 index 逐一核对类型后消费这 k 个栈值；OUT 绑定
  writable StackSlot 在当前静态 pass 中**保守拒绝**（栈槽写回语义待
  执行器工作包定义后放开）——宁可拒绝可疑 IR，不伪称已验证。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.runtime.ir import (
    BINDING_MODES,
    BINOP_ARITH_OPS,
    BINOP_COMPARE_OPS,
    BINOP_LOGIC_OPS,
    BINOP_OPS,
    BIT_TYPES,
    IEC_TYPES,
    INSTANCE_KINDS,
    INT_TYPES,
    INTERFACE_SECTIONS,
    IO_DIRECTIONS,
    LOGIC_TYPES,
    NUMERIC_TYPES,
    ORDERED_TYPES,
    POU_KINDS,
    POU_LANGUAGES,
    REAL_TYPES,
    SIGNED_INT_TYPES,
    UNOP_OPS,
    VAR_SECTIONS,
    BinOp,
    Binding,
    CallFb,
    CallFbInstance,
    CallFunc,
    CallStd,
    Const,
    Convert,
    IOMap,
    InstanceDecl,
    Instr,
    INSTRUCTION_TYPES,
    Jmp,
    JmpIfFalse,
    Label,
    LoadConst,
    LoadPrev,
    LoadVar,
    POUDefinition,
    ProgramInstance,
    StackSlot,
    StdSig,
    StoreKey,
    StoreVar,
    Task,
    UnOp,
    VarDecl,
)
from src.runtime.standard_functions import standard_signature_error


class IRValidationError(Exception):
    """装载期校验失败；``errors`` 为全部错误消息列表。"""

    def __init__(self, errors):
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(
            "IR 装载校验失败（%d 处）：\n  - %s" % (len(self.errors), joined)
        )


# ---------------------------------------------------------------------------
# 内部：单个 POU 的作用域视图
# ---------------------------------------------------------------------------

@dataclass
class _Scope:
    """一个 POU 定义体的静态作用域：本地变量、实例、可见的 GVL。"""
    pou: POUDefinition
    local_types: dict = field(default_factory=dict)    # name -> iec_type（interface+locals）
    instances: dict = field(default_factory=dict)      # name -> InstanceDecl
    gvl_types: dict = field(default_factory=dict)      # name -> iec_type（FUNCTION 为空）
    registry: object = None                            # 可选 L2 注册表（接入后核验管脚）

    def resolve(self, key: str) -> Optional[str]:
        """解析变量键，返回声明类型；解析不到返回 None。

        支持三种形态（IR_SPEC §7）：本地名、GVL 名、``<inst>.<pin>``。
        库块实例管脚：**未接入 L2 注册表**时返回 ``"*"``（实例已声明、类型
        未知，调用方跳过核对，属诚实边界）；**接入注册表**后按 Schema 返回
        管脚 IEC 类型，未知管脚 / 未注册块型返回 None（由调用方报错）。
        """
        if key in self.local_types:
            return self.local_types[key]
        if key in self.gvl_types:
            return self.gvl_types[key]
        if "." in key:
            inst_name, pin = key.split(".", 1)
            inst = self.instances.get(inst_name)
            if inst is not None and inst.kind == "library":
                if self.registry is None:
                    return "*"      # 库块管脚：类型待 L2 描述符核对
                schema = self._schema_for(inst)
                if schema is None:
                    return None     # 未注册块型（错误在 lib_pin_info 报出）
                p = schema.pin(pin)
                return p.iec_type if p is not None else None
        return None

    def _schema_for(self, inst):
        """取库块实例的 engineering Schema；未注册返回 None。"""
        try:
            return self.registry.resolve(inst.block_type, "engineering")[0]
        except Exception:       # noqa: BLE001 —— 未注册 / 缺变体：由上层报错
            return None

    def lib_pin_info(self, key: str):
        """库块管脚解析（仅 registry 接入时有意义）。

        返回 ``None``（非库块管脚引用）或 ``(status, payload)``：
        ``("unregistered", block_type)`` / ``("unknown_pin", pin_name)`` /
        ``("ok", Pin)``。
        """
        if self.registry is None or "." not in key:
            return None
        inst_name, pin = key.split(".", 1)
        inst = self.instances.get(inst_name)
        if inst is None or inst.kind != "library":
            return None
        schema = self._schema_for(inst)
        if schema is None:
            return ("unregistered", inst.block_type)
        p = schema.pin(pin)
        if p is None:
            return ("unknown_pin", pin)
        return ("ok", p)

    def is_gvl(self, key: str) -> bool:
        return key.split(".", 1)[0] in self.gvl_types


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------

def validate_task(task: Task, registry=None) -> None:
    """校验整个 Task；失败抛 ``IRValidationError``（汇总全部错误）。

    ``registry``（可选 L2 ``Registry``）接入后，库块实例的 ``block_type`` 必须
    已注册、``<inst>.<pin>`` 必须按 Schema 核验管脚存在性、方向与 IEC 类型；
    不再对库块管脚用 ``"*"`` 跳过类型检查。``registry=None`` 时保持历史诚实
    边界（库块管脚类型未知）。
    """
    errors: list = []
    if not isinstance(task, Task):
        raise IRValidationError(["validate_task 需要 ir.Task 实例"])

    _check_task_shell(task, errors)
    gvl_types = _check_gvl(task, errors)
    _check_io_map(task, gvl_types, errors)
    _check_pou_lib(task, errors, registry)
    _check_programs(task, errors)
    reachable = _check_instance_graph(task, errors)

    # 逐 POU 校验代码（结构错误已收集，仍尽力继续以汇总更多问题）
    for name, pou in task.pou_lib.items():
        if not isinstance(pou, POUDefinition):
            continue
        if pou.code is None:
            if name in reachable:
                errors.append(
                    "POU '%s'：被 Task 引用（可达）但 code=None，无可执行 IR" % name
                )
            continue
        scope = _build_scope(pou, gvl_types, registry)
        _check_code(pou, scope, task, errors)

    if errors:
        raise IRValidationError(errors)


def validate_pou_instruction_semantics(pou: POUDefinition) -> None:
    """受支持的内部跨组件 facade：对已通过既有信任边界的 typed POU 的整条
    ``code`` 做**与控制流可达性无关**的组合语义验证。

    这是 ``src.runtime.loader`` 子模块中的受支持内部 API（不以下划线命名）；
    **不重导出到 ``src.runtime`` 包根或 ``__all__``**。它不是通用不可信 Python
    对象安全入口：输入只应为已经现有信任边界形成的 typed ``POUDefinition``，本
    facade 不复制 CFC/ST 的 exact-shell 验证。

    背景：``validate_task`` 的可达性驱动 worklist 只对**控制流可达**指令做取值/
    引用级组合校验；位于无条件 ``Jmp`` 之后、无其它入边的死指令会被跳过。ST
    catalogue 需要在把编译好的 FUNCTION/FB 交给 Loader 前，对**整条**指令流做与
    可达性无关的组合校验，防止伪造目录借死代码夹带「各字段各自合法、组合非法」
    的指令。本 facade 即该 whole-stream 预检的**单一真值源**，复用 Loader 现有
    ``_build_scope`` / ``_step`` / ``standard_signature_error``，不在 facade 或
    ST lowering 建第二套 operator/type/signature 规则表：

    - ``LoadVar/LoadPrev/StoreVar``：指令 IEC 类型合法（对共享冻结常量
      ``IEC_TYPES`` 的集合预筛）；被引用变量已声明、指令类型等于其声明类型两项
      **实质裁决交由 Loader 自身 ``_step`` 在同一 ``_build_scope`` 作用域上完成**，
      facade 不手写声明表或类型比较，只把 ``_step`` 的裁决稳定收敛为诊断；
    - ``BinOp/UnOp``：operator 属枚举、类型属 IEC 集，且 operator+type 组合经
      Loader 自身 ``_step`` 在通配栈上判定合法（op/type 对的裁决只依赖指令本身，
      不受栈残留或未解析引用影响）；
    - ``Convert``：``from_type`` / ``to_type`` 的合法性裁决同样交由 Loader 自身
      ``_step`` 在通配栈上完成（复用 ``_require_type`` 单一真值源），facade 不手写
      IEC 集合判断，只把 ``_step`` 的裁决稳定收敛为诊断；
    - ``CallStd``：签名各类型合法，且 name+signature 满足 ``standard_signature_error``；
    - ``CallFunc/CallFb/CallFbInstance``：编译目录 POU 不得调用其它 FUNCTION/FB，
      任何此类指令都是死代码违禁物，失败关闭。

    合法输入无返回值；任一组合语义错误按**指令下标稳定顺序**汇总后抛现有
    ``IRValidationError``。本 facade 只读输入：不修改 ``pou``、指令、声明容器或
    全局目录，也不向外泄漏 ``_Scope``、合成栈或局部错误列表（三者均为函数内的
    一次性局部对象）。
    """
    scope = _build_scope(pou, {}, None)
    prefix = "POU '%s'" % pou.name
    errors: list = []
    for idx, ins in enumerate(pou.code):
        kind = type(ins)
        if kind is LoadVar or kind is LoadPrev or kind is StoreVar:
            # 「被引用变量已声明」与「指令 IEC 类型 == 声明类型」两项实质裁决全部
            # 委派给 Loader 自身 ``_step`` 在上面 ``_build_scope`` 造出的**同一**
            # ``scope`` 上完成——facade 不再手写第二套声明表或类型比较，避免 Loader
            # 日后扩展 scope / 管脚裁决时 facade 静默漂移（复用单一真值源）。``_step``
            # 需要一个栈：LOAD 只压不弹、STORE 弹一个待写值，故按需喂通配栈种子；因
            # 声明/类型裁决与栈残留无关，``"*"`` 待写值不会触发额外类型错。``probe`` /
            # ``seed`` 均为一次性局部对象，``_step`` 只读 ``scope`` / ``pou``，不修改
            # 输入对象图。
            #
            # 唯一保留在 facade 侧的是对 ``ins.type`` 的 IEC 集合归属预筛：它只读
            # 共享冻结常量 ``IEC_TYPES``（``_step`` 的 ``_require_type`` 用的是同一常量，
            # 随之同步、无漂移），仅用于把「非法 IEC 类型」与「未声明引用」两个稳定
            # 诊断分流——二者在 ``_step`` 中都以返回 ``None`` 表达、无法仅凭返回值区分，
            # 故需此一处粗筛路由，而非重建规则。
            if ins.type not in IEC_TYPES:
                errors.append(
                    "%s 指令 #%d：compiled POU instruction type is not a "
                    "supported IEC type" % (prefix, idx))
                continue
            probe: list = []
            seed = ["*"] if kind is StoreVar else []
            outcome = _step(pou, scope, None, idx, ins, seed, probe, prefix)
            if not probe:
                continue
            # ``_step`` 判本指令非法：类型已合法且非库块管脚（registry=None）时，返回
            # ``None`` 当且仅当被引用变量在 Loader 作用域不可解析（未声明），返回非
            # ``None`` 但携带诊断当且仅当声明类型与指令类型不一致。facade 只据 Loader
            # 的裁决把它稳定收敛为既有两类诊断，不复算规则本身。
            if outcome is None:
                errors.append(
                    "%s 指令 #%d：compiled POU instruction references an "
                    "undeclared variable" % (prefix, idx))
            else:
                errors.append(
                    "%s 指令 #%d：compiled POU instruction type does not match "
                    "its declared variable type" % (prefix, idx))
        elif kind is BinOp or kind is UnOp:
            op_set = BINOP_OPS if kind is BinOp else UNOP_OPS
            if ins.op not in op_set or ins.type not in IEC_TYPES:
                errors.append(
                    "%s 指令 #%d：compiled POU instruction uses an unsupported "
                    "operator or IEC type" % (prefix, idx))
                continue
            # 把指令交给 Loader 自身 ``_step``，栈按算子元数用通配值填充：不会
            # 下溢/栈类型错，故 ``probe`` 非空当且仅当 op/type 组合非法（如对 BOOL
            # 做算术、对无符号类型取负）。``seed`` / ``probe`` 均为一次性局部对象。
            probe: list = []
            seed = ["*", "*"] if kind is BinOp else ["*"]
            _step(pou, scope, None, idx, ins, seed, probe, prefix)
            if probe:
                errors.append(
                    "%s 指令 #%d：compiled POU instruction operator and IEC "
                    "type combination is unsupported" % (prefix, idx))
        elif kind is Convert:
            # ``from_type`` / ``to_type`` 的合法性裁决交由 Loader 自身 ``_step``
            # 完成（复用 ``_step`` 内 ``_require_type`` 的同一真值源），facade 不再
            # 手写第二套 IEC 集合判断。``_step`` 的 CONVERT 分支恰弹一个源值再压
            # 目标值，故喂单值通配栈 ``["*"]``：无栈下溢、源值 ``"*"`` 不触发额外
            # 类型错，故 ``probe`` 非空当且仅当 from/to 有一为非法 IEC 类型。
            # ``seed`` / ``probe`` 均为一次性局部对象，``_step`` 只读 ``scope`` /
            # ``pou``，不修改输入对象图。
            probe: list = []
            seed = ["*"]
            _step(pou, scope, None, idx, ins, seed, probe, prefix)
            if probe:
                errors.append(
                    "%s 指令 #%d：compiled POU conversion uses an unsupported "
                    "IEC type" % (prefix, idx))
        elif kind is CallStd:
            sig = ins.sig
            if sig.return_type not in IEC_TYPES or \
                    any(param not in IEC_TYPES for param in sig.param_types):
                errors.append(
                    "%s 指令 #%d：compiled POU CALL_STD signature uses an "
                    "unsupported IEC type" % (prefix, idx))
                continue
            # name + signature 必须满足 Loader 经 ``standard_signature_error`` 施加
            # 的同一契约（元数、参/返类型匹配、ABS 仅数值），复用该单一规则源。
            if standard_signature_error(ins.name, sig) is not None:
                errors.append(
                    "%s 指令 #%d：compiled POU CALL_STD signature is invalid "
                    "for its standard function" % (prefix, idx))
        elif kind is CallFunc or kind is CallFb or kind is CallFbInstance:
            errors.append(
                "%s 指令 #%d：compiled POU catalogue must not call a function "
                "or FB instance" % (prefix, idx))
        # ``LoadConst`` 的值/类型已由前端 clone 阶段完全门控；``Jmp`` /
        # ``JmpIfFalse`` / ``Label`` 的标签目标由 Loader 的标签遍历在整条流上校验，
        # 二者本就与可达性无关，此处无需重复。
    if errors:
        raise IRValidationError(errors)


# ---------------------------------------------------------------------------
# 结构校验
# ---------------------------------------------------------------------------

def _check_task_shell(task: Task, errors: list) -> None:
    if not isinstance(task.cycle_ms, int) or isinstance(task.cycle_ms, bool) \
            or task.cycle_ms != 500:
        errors.append(
            "Task.cycle_ms 当前冻结范围为单任务、固定 500ms（IR_SPEC §3；"
            "ROADMAP 阶段 1），得到 %r——多周期/多任务属后续扩展点，"
            "本阶段校验器不放行" % (task.cycle_ms,)
        )


def _check_gvl(task: Task, errors: list) -> dict:
    gvl_types: dict = {}
    for decl in task.gvl:
        if not isinstance(decl, VarDecl):
            errors.append("GVL 含非 VarDecl 项：%r" % (decl,))
            continue
        _check_var_decl(decl, "GVL", errors)
        if decl.section not in ("VAR_GLOBAL", "VAR"):
            errors.append(
                "GVL 变量 '%s' 的 section 应为 VAR_GLOBAL（或 VAR），得到 %r"
                % (decl.name, decl.section)
            )
        if decl.name in gvl_types:
            errors.append("GVL 变量名重复：'%s'" % decl.name)
        else:
            gvl_types[decl.name] = decl.iec_type
    return gvl_types


def _check_var_decl(decl: VarDecl, where: str, errors: list) -> None:
    if not decl.name or not isinstance(decl.name, str):
        errors.append("%s 存在无名/非法名变量声明：%r" % (where, decl))
    if decl.iec_type not in IEC_TYPES:
        errors.append(
            "%s 变量 '%s' 的 IEC 类型非法：%r" % (where, decl.name, decl.iec_type)
        )
    if decl.section not in VAR_SECTIONS:
        errors.append(
            "%s 变量 '%s' 的 section 非法：%r" % (where, decl.name, decl.section)
        )


def _check_io_map(task: Task, gvl_types: dict, errors: list) -> None:
    for io in task.io_map:
        if not isinstance(io, IOMap):
            errors.append("io_map 含非 IOMap 项：%r" % (io,))
            continue
        if io.direction not in IO_DIRECTIONS:
            errors.append("IOMap '%s' 方向非法：%r" % (io.var, io.direction))
        if io.var not in gvl_types:
            errors.append("IOMap 引用未声明的 GVL 变量：'%s'" % io.var)
        if io.direction == "OUT" and io.policy is None:
            errors.append(
                "IOMap '%s' 为 OUT 方向但缺 OutputPolicy（占位校验，"
                "policy 结构/行为属 ENGINE_SCAN_SPEC §4，本包不校验其内部）" % io.var
            )


def _check_pou_lib(task: Task, errors: list, registry=None) -> None:
    for key, pou in task.pou_lib.items():
        if not isinstance(pou, POUDefinition):
            errors.append("pou_lib['%s'] 不是 POUDefinition：%r" % (key, pou))
            continue
        if pou.name != key:
            errors.append(
                "pou_lib 键 '%s' 与定义名 '%s' 不一致" % (key, pou.name)
            )
        if pou.pou_kind not in POU_KINDS:
            errors.append("POU '%s' 的 pou_kind 非法：%r" % (pou.name, pou.pou_kind))
            continue
        if pou.language not in POU_LANGUAGES:
            errors.append("POU '%s' 的 language 非法：%r" % (pou.name, pou.language))

        # 接口/局部区段合法性
        seen: set = set()
        for decl in pou.interface:
            _check_var_decl(decl, "POU '%s' interface" % pou.name, errors)
            if decl.section not in INTERFACE_SECTIONS:
                errors.append(
                    "POU '%s' 接口变量 '%s' 的 section 应为 VAR_INPUT/VAR_OUTPUT/"
                    "VAR_IN_OUT，得到 %r" % (pou.name, decl.name, decl.section)
                )
            if decl.name in seen:
                errors.append("POU '%s' 内名称重复：'%s'" % (pou.name, decl.name))
            seen.add(decl.name)
        for decl in pou.locals:
            _check_var_decl(decl, "POU '%s' locals" % pou.name, errors)
            if decl.section not in ("VAR", "VAR_TEMP"):
                errors.append(
                    "POU '%s' 局部变量 '%s' 的 section 应为 VAR/VAR_TEMP，得到 %r"
                    % (pou.name, decl.name, decl.section)
                )
            if decl.section == "VAR_TEMP" and pou.pou_kind == "FUNCTION":
                errors.append(
                    "POU '%s'：FUNCTION 不允许 VAR_TEMP（IR_SPEC §3；其普通 VAR "
                    "本就按调用生存），变量 '%s'" % (pou.name, decl.name)
                )
            if decl.name in seen:
                errors.append("POU '%s' 内名称重复：'%s'" % (pou.name, decl.name))
            seen.add(decl.name)
        for inst in pou.instances:
            if not isinstance(inst, InstanceDecl):
                errors.append("POU '%s' instances 含非 InstanceDecl 项：%r"
                              % (pou.name, inst))
                continue
            if inst.kind not in INSTANCE_KINDS:
                errors.append(
                    "POU '%s' 实例 '%s' 的 kind 非法：%r（应为 library/user_fb）"
                    % (pou.name, inst.name, inst.kind)
                )
            if inst.kind == "user_fb":
                target = task.pou_lib.get(inst.block_type)
                if target is None:
                    errors.append(
                        "POU '%s' 实例 '%s' 引用未定义的用户 FB：'%s'"
                        % (pou.name, inst.name, inst.block_type)
                    )
                elif isinstance(target, POUDefinition) \
                        and target.pou_kind != "FUNCTION_BLOCK":
                    errors.append(
                        "POU '%s' 实例 '%s' 的目标 '%s' 不是 FUNCTION_BLOCK（是 %s）"
                        % (pou.name, inst.name, inst.block_type, target.pou_kind)
                    )
            elif inst.kind == "library" and registry is not None:
                # L2 接入：库块类型必须已注册（engineering 变体）
                if not registry.has(inst.block_type, "engineering"):
                    errors.append(
                        "POU '%s' 实例 '%s' 引用未在 L2 注册表登记的库块类型 '%s'"
                        % (pou.name, inst.name, inst.block_type)
                    )
            if inst.name in seen:
                errors.append("POU '%s' 内名称重复：'%s'" % (pou.name, inst.name))
            seen.add(inst.name)

        if pou.pou_kind == "FUNCTION":
            if pou.return_type is None:
                errors.append("FUNCTION '%s' 缺 return_type" % pou.name)
            elif pou.return_type not in IEC_TYPES:
                errors.append(
                    "FUNCTION '%s' 的 return_type 非法：%r" % (pou.name, pou.return_type)
                )
            if pou.instances:
                errors.append(
                    "FUNCTION '%s' 声明了 FB 实例——FUNCTION 无实例内存（IR_SPEC §3），"
                    "不允许" % pou.name
                )
        else:
            if pou.return_type is not None:
                errors.append(
                    "%s '%s' 不应有 return_type（仅 FUNCTION）"
                    % (pou.pou_kind, pou.name)
                )


def _check_programs(task: Task, errors: list) -> None:
    seen_prefix: set = set()
    for prog in task.programs:
        if not isinstance(prog, ProgramInstance):
            errors.append("Task.programs 含非 ProgramInstance 项：%r" % (prog,))
            continue
        target = task.pou_lib.get(prog.definition)
        if target is None:
            errors.append("ProgramInstance 引用未定义的 POU：'%s'" % prog.definition)
        elif isinstance(target, POUDefinition) and target.pou_kind != "PROGRAM":
            errors.append(
                "ProgramInstance '%s' 的定义 '%s' 不是 PROGRAM（是 %s）"
                % (prog.store_prefix, prog.definition, target.pou_kind)
            )
        if not prog.store_prefix:
            errors.append("ProgramInstance('%s') 缺 store_prefix" % prog.definition)
        elif prog.store_prefix in seen_prefix:
            errors.append("ProgramInstance store_prefix 重复：'%s'" % prog.store_prefix)
        seen_prefix.add(prog.store_prefix)


def _check_instance_graph(task: Task, errors: list) -> set:
    """检测用户 FB 实例声明的循环/不可展开引用；返回从 programs 可达的定义名集合。

    仅做定义图上的静态遍历，**不分配运行时 Store**（实例内存展开属装载
    执行层，后续工作包实现）。
    """
    # 定义 -> 其 user_fb 实例目标定义集合
    edges: dict = {}
    for name, pou in task.pou_lib.items():
        if not isinstance(pou, POUDefinition):
            continue
        targets = []
        for inst in pou.instances:
            if isinstance(inst, InstanceDecl) and inst.kind == "user_fb" \
                    and inst.block_type in task.pou_lib:
                targets.append(inst.block_type)
        edges[name] = targets

    # 循环检测（DFS 三色标记）
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in edges}
    reported_cycles: set = set()

    def dfs(node: str, path: list) -> None:
        color[node] = GRAY
        path.append(node)
        for nxt in edges.get(node, ()):
            if color.get(nxt, WHITE) == GRAY:
                cycle = tuple(path[path.index(nxt):] + [nxt])
                if cycle not in reported_cycles:
                    reported_cycles.add(cycle)
                    errors.append(
                        "递归实例声明循环（不可展开）：%s" % " -> ".join(cycle)
                    )
            elif color.get(nxt, WHITE) == WHITE:
                dfs(nxt, path)
        path.pop()
        color[node] = BLACK

    for name in edges:
        if color[name] == WHITE:
            dfs(name, [])

    # 可达集合：programs 引用的定义 + 其实例/调用闭包（调用可达在代码校验
    # 中逐指令核对；此处按实例图先收一层，CALL_FUNC/CALL_FB_INSTANCE 的
    # 目标在 _check_code 中要求存在，可达性以实例图 + 直接调用近似）
    reachable: set = set()
    stack = [p.definition for p in task.programs
             if isinstance(p, ProgramInstance) and p.definition in task.pou_lib]
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        stack.extend(edges.get(cur, ()))
        pou = task.pou_lib.get(cur)
        if isinstance(pou, POUDefinition) and pou.code:
            for ins in pou.code:
                if isinstance(ins, CallFunc) and ins.name in task.pou_lib:
                    stack.append(ins.name)
    return reachable


# ---------------------------------------------------------------------------
# 代码校验（控制流感知栈类型验证）
# ---------------------------------------------------------------------------

def _build_scope(pou: POUDefinition, gvl_types: dict, registry=None) -> _Scope:
    scope = _Scope(pou=pou, registry=registry)
    for decl in list(pou.interface) + list(pou.locals):
        if isinstance(decl, VarDecl):
            scope.local_types[decl.name] = decl.iec_type
    for inst in pou.instances:
        if isinstance(inst, InstanceDecl):
            scope.instances[inst.name] = inst
    if pou.pou_kind != "FUNCTION":       # FUNCTION 禁止 GVL（IR_SPEC §3）
        scope.gvl_types = dict(gvl_types)
    return scope


def _check_code(pou: POUDefinition, scope: _Scope, task: Task, errors: list) -> None:
    code = pou.code
    prefix = "POU '%s'" % pou.name

    # ---- 标签表 ----
    labels: dict = {}
    for idx, ins in enumerate(code):
        if not isinstance(ins, INSTRUCTION_TYPES):
            errors.append("%s 指令 #%d：未知指令对象 %r" % (prefix, idx, ins))
        if isinstance(ins, Label):
            if ins.id in labels:
                errors.append("%s：标签重复 '%s'（#%d 与 #%d）"
                              % (prefix, ins.id, labels[ins.id], idx))
            else:
                labels[ins.id] = idx
    for idx, ins in enumerate(code):
        if isinstance(ins, (Jmp, JmpIfFalse)) and ins.label not in labels:
            errors.append("%s 指令 #%d：跳转目标标签不存在 '%s'"
                          % (prefix, idx, ins.label))

    if any(e.startswith(prefix) and ("未知指令对象" in e or "跳转目标" in e or "标签重复" in e)
           for e in errors):
        return  # 结构已破损，栈模拟不再进行（避免连锁误报）

    # ---- 控制流感知栈类型模拟（worklist） ----
    n = len(code)
    entry_state: dict = {}          # 指令下标 -> tuple(栈类型自底向上)
    exit_states: list = []          # 正常出口（越过末尾）的栈状态集合
    worklist = [(0, tuple())] if n else []
    if n == 0:
        exit_states.append(tuple())

    def merge(idx: int, state: tuple) -> bool:
        """记录/比对汇合点状态；返回是否需要继续沿该点传播。"""
        if idx >= n:
            exit_states.append(state)
            return False
        prev = entry_state.get(idx)
        if prev is None:
            entry_state[idx] = state
            return True
        if prev != state:
            errors.append(
                "%s 指令 #%d：控制流汇合点栈不一致（一路 %s，另一路 %s）"
                % (prefix, idx, list(prev), list(state))
            )
        return False

    if n:
        entry_state[0] = tuple()

    while worklist:
        idx, stack = worklist.pop()
        while idx < n:
            ins = code[idx]
            nxt = _step(pou, scope, task, idx, ins, list(stack), errors, prefix)
            if nxt is None:
                break               # 本路径因错误终止
            stack_list, jumps, falls_through = nxt
            stack = tuple(stack_list)
            for target in jumps:
                t_idx = labels[target]
                if merge(t_idx, stack):
                    worklist.append((t_idx, stack))
            if not falls_through:
                break
            idx += 1
            if not merge(idx, stack):
                break
            # merge 返回 True 时继续线性推进（entry_state 已登记）

    # ---- 出口契约 ----
    for state in exit_states:
        if pou.pou_kind == "FUNCTION":
            expected = (pou.return_type,) if pou.return_type else None
            if expected is not None and state != expected:
                errors.append(
                    "%s：FUNCTION 正常出口栈应恰为 [%s]（返回值），实为 %s"
                    % (prefix, pou.return_type, list(state))
                )
        else:
            if state != tuple():
                errors.append(
                    "%s：%s 正常出口栈应为空，实为 %s"
                    % (prefix, pou.pou_kind, list(state))
                )


def _require_type(t, prefix: str, idx: int, what: str, errors: list) -> bool:
    if t not in IEC_TYPES:
        errors.append("%s 指令 #%d：%s 类型缺失或非法：%r（无类型的指令列表不是"
                      "合法 IR，IR_SPEC §5.1）" % (prefix, idx, what, t))
        return False
    return True


def _step(pou, scope, task, idx, ins, stack, errors, prefix):
    """单指令栈效应。返回 (新栈, 跳转标签列表, 是否顺序落下)；错误致命时返回 None。"""

    def err(msg):
        errors.append("%s 指令 #%d：%s" % (prefix, idx, msg))

    def pop1(expect=None, what="操作数"):
        if not stack:
            err("栈下溢（%s 缺失）" % what)
            return None
        top = stack.pop()
        if expect is not None and expect != "*" and top != "*" and top != expect:
            err("%s 类型应为 %s，栈顶是 %s" % (what, expect, top))
        return top

    # ---------------- LOAD / STORE ----------------
    if isinstance(ins, (LoadVar, LoadPrev)):
        if not _require_type(ins.type, prefix, idx, "LOAD", errors):
            return None
        info = scope.lib_pin_info(ins.key)
        if info is not None:
            status, payload = info
            if status == "unregistered":
                err("LOAD 引用未在 L2 注册表登记的库块类型 '%s'（管脚 '%s'）"
                    % (payload, ins.key))
                return None
            if status == "unknown_pin":
                err("LOAD 引用库块不存在的管脚 '%s'" % ins.key)
                return None
            pin = payload
            if pin.kind not in ("VAR_OUTPUT", "VAR_IN_OUT"):
                err("LOAD 读取库块输入管脚 '%s'（方向 %s；只读 VAR_OUTPUT/"
                    "VAR_IN_OUT）" % (ins.key, pin.kind))
            if pin.iec_type != ins.type:
                err("LOAD '%s' 指令类型 %s 与管脚声明类型 %s 不一致"
                    % (ins.key, ins.type, pin.iec_type))
            stack.append(ins.type)
            return stack, [], True
        declared = scope.resolve(ins.key)
        if declared is None:
            kind = "LOAD_PREV" if isinstance(ins, LoadPrev) else "LOAD_VAR"
            extra = ""
            if pou.pou_kind == "FUNCTION" and ins.key.split(".", 1)[0] not in scope.local_types:
                extra = "（注意：FUNCTION 禁止访问 GVL，IR_SPEC §3）"
            err("%s 引用未知变量 '%s'%s" % (kind, ins.key, extra))
            return None
        if declared != "*" and declared != ins.type:
            err("LOAD '%s' 的指令类型 %s 与声明类型 %s 不一致"
                % (ins.key, ins.type, declared))
        stack.append(ins.type)
        return stack, [], True

    if isinstance(ins, LoadConst):
        if not _require_type(ins.type, prefix, idx, "LOAD_CONST", errors):
            return None
        stack.append(ins.type)
        return stack, [], True

    if isinstance(ins, StoreVar):
        if not _require_type(ins.type, prefix, idx, "STORE_VAR", errors):
            return None
        info = scope.lib_pin_info(ins.key)
        if info is not None:
            status, payload = info
            if status == "unregistered":
                err("STORE_VAR 引用未在 L2 注册表登记的库块类型 '%s'（管脚 '%s'）"
                    % (payload, ins.key))
                return None
            if status == "unknown_pin":
                err("STORE_VAR 引用库块不存在的管脚 '%s'" % ins.key)
                return None
            pin = payload
            if pin.kind not in ("VAR_INPUT", "VAR_IN_OUT"):
                err("STORE_VAR 写库块输出管脚 '%s'（方向 %s；只写 VAR_INPUT/"
                    "VAR_IN_OUT）" % (ins.key, pin.kind))
            if pin.iec_type != ins.type:
                err("STORE_VAR '%s' 指令类型 %s 与管脚声明类型 %s 不一致"
                    % (ins.key, ins.type, pin.iec_type))
            top = pop1(what="STORE_VAR 待写值")
            if top is None:
                return None
            if top != "*" and top != ins.type:
                err("STORE_VAR '%s' 要求栈顶类型严格等于 %s，实为 %s"
                    % (ins.key, ins.type, top))
            return stack, [], True
        declared = scope.resolve(ins.key)
        if declared is None:
            extra = ""
            if pou.pou_kind == "FUNCTION" and ins.key.split(".", 1)[0] not in scope.local_types:
                extra = "（注意：FUNCTION 禁止访问 GVL，IR_SPEC §3）"
            err("STORE_VAR 写入未知变量 '%s'%s" % (ins.key, extra))
            return None
        if declared != "*" and declared != ins.type:
            err("STORE_VAR '%s' 的指令类型 %s 与声明类型 %s 不一致"
                % (ins.key, ins.type, declared))
        top = pop1(what="STORE_VAR 待写值")
        if top is None:
            return None
        if top != "*" and top != ins.type:
            err("STORE_VAR '%s' 要求栈顶类型严格等于 %s，实为 %s（IR_SPEC §5.1，"
                "赋值转换须显式 CONVERT）" % (ins.key, ins.type, top))
        return stack, [], True

    # ---------------- 运算 ----------------
    if isinstance(ins, BinOp):
        if ins.op not in BINOP_OPS:
            err("BINOP 操作符非法：%r" % (ins.op,))
            return None
        if not _require_type(ins.type, prefix, idx, "BINOP", errors):
            return None
        b = pop1(ins.type, "BINOP 右操作数")
        a = pop1(ins.type, "BINOP 左操作数")
        if a is None or b is None:
            return None
        if ins.op in BINOP_ARITH_OPS:
            if ins.op == "MOD" and ins.type not in INT_TYPES:
                err("MOD 仅支持整数族类型，得到 %s" % ins.type)
            elif ins.type not in NUMERIC_TYPES:
                err("算术 BINOP %s 不支持类型 %s" % (ins.op, ins.type))
            stack.append(ins.type)
        elif ins.op in BINOP_LOGIC_OPS:
            if ins.type not in LOGIC_TYPES:
                err("逻辑/位 BINOP %s 不支持类型 %s" % (ins.op, ins.type))
            stack.append(ins.type)
        else:  # 比较类：结果恒为 BOOL（IR_SPEC §5.2）
            if ins.op in ("GT", "GE", "LT", "LE") and ins.type not in ORDERED_TYPES:
                err("有序比较 %s 不支持类型 %s" % (ins.op, ins.type))
            stack.append("BOOL")
        return stack, [], True

    if isinstance(ins, UnOp):
        if ins.op not in UNOP_OPS:
            err("UNOP 操作符非法：%r" % (ins.op,))
            return None
        if not _require_type(ins.type, prefix, idx, "UNOP", errors):
            return None
        if ins.op == "NOT" and ins.type not in LOGIC_TYPES:
            err("NOT 不支持类型 %s" % ins.type)
        if ins.op == "NEG" and ins.type not in (SIGNED_INT_TYPES | REAL_TYPES | {"TIME"}):
            err("NEG 不支持类型 %s（无符号/位串类型不可取负）" % ins.type)
        top = pop1(ins.type, "UNOP 操作数")
        if top is None:
            return None
        stack.append(ins.type)
        return stack, [], True

    if isinstance(ins, Convert):
        ok_from = _require_type(ins.from_type, prefix, idx, "CONVERT from", errors)
        ok_to = _require_type(ins.to_type, prefix, idx, "CONVERT to", errors)
        if not (ok_from and ok_to):
            return None
        top = pop1(ins.from_type, "CONVERT 源值")
        if top is None:
            return None
        stack.append(ins.to_type)
        return stack, [], True

    # ---------------- 调用 ----------------
    if isinstance(ins, CallStd):
        if not ins.name or not isinstance(ins.name, str):
            err("CALL_STD 名称缺失")
            return None
        if not isinstance(ins.sig, StdSig):
            err("CALL_STD 缺签名（StdSig）")
            return None
        for t in ins.sig.param_types:
            if not _require_type(t, prefix, idx, "CALL_STD 实参", errors):
                return None
        if not _require_type(ins.sig.return_type, prefix, idx, "CALL_STD 返回", errors):
            return None
        signature_error = standard_signature_error(ins.name, ins.sig)
        if signature_error is not None:
            err("CALL_STD %s" % signature_error)
        for t in reversed(ins.sig.param_types):
            if pop1(t, "CALL_STD 实参") is None:
                return None
        stack.append(ins.sig.return_type)
        return stack, [], True

    if isinstance(ins, CallFb):
        inst = scope.instances.get(ins.instance)
        if inst is None:
            err("CALL_FB 引用未声明实例 '%s'" % ins.instance)
            return None
        if inst.kind != "library":
            err("CALL_FB 只用于库块实例；'%s' 是 %s（用户 FB 应使用 "
                "CALL_FB_INSTANCE）" % (ins.instance, inst.kind))
        return stack, [], True

    if isinstance(ins, CallFunc):
        target = task.pou_lib.get(ins.name)
        if target is None or not isinstance(target, POUDefinition):
            err("CALL_FUNC 引用未定义 FUNCTION '%s'" % ins.name)
            return None
        if target.pou_kind != "FUNCTION":
            err("CALL_FUNC 目标 '%s' 不是 FUNCTION（是 %s）"
                % (ins.name, target.pou_kind))
            return None
        if not _require_type(ins.ret_type, prefix, idx, "CALL_FUNC 返回", errors):
            return None
        if target.return_type != ins.ret_type:
            err("CALL_FUNC '%s' 的 ret_type=%s 与 FUNCTION 定义 return_type=%s 不一致"
                % (ins.name, ins.ret_type, target.return_type))
        new_stack = _check_bindings(
            "CALL_FUNC '%s'" % ins.name, ins.bindings, target, scope, pou,
            stack, err)
        if new_stack is None:
            return None
        new_stack.append(ins.ret_type)
        return new_stack, [], True

    if isinstance(ins, CallFbInstance):
        target_def = _resolve_fb_path(ins.instance_path, scope, task, err)
        if target_def is None:
            return None
        new_stack = _check_bindings(
            "CALL_FB_INSTANCE '%s'" % ins.instance_path, ins.bindings,
            target_def, scope, pou, stack, err)
        if new_stack is None:
            return None
        return new_stack, [], True

    # ---------------- 控制流 ----------------
    if isinstance(ins, Jmp):
        return stack, [ins.label], False

    if isinstance(ins, JmpIfFalse):
        top = pop1("BOOL", "JMP_IF_FALSE 条件")
        if top is None:
            return None
        if top != "BOOL" and top != "*":
            return None  # 已报类型错，该路径不再传播
        return stack, [ins.label], True

    if isinstance(ins, Label):
        return stack, [], True

    err("未知指令对象 %r" % (ins,))
    return None


def _resolve_fb_path(path: str, scope: _Scope, task: Task, err):
    """解析 CALL_FB_INSTANCE 的实例路径（定义体内相对路径，支持嵌套段）。"""
    if not path:
        err("CALL_FB_INSTANCE 实例路径为空")
        return None
    segments = path.split(".")
    cur_instances = scope.instances
    target_def = None
    for i, seg in enumerate(segments):
        inst = cur_instances.get(seg)
        if inst is None:
            err("CALL_FB_INSTANCE 路径 '%s' 第 %d 段 '%s' 不是已声明实例"
                % (path, i + 1, seg))
            return None
        if inst.kind != "user_fb":
            err("CALL_FB_INSTANCE 路径 '%s' 段 '%s' 是库块实例（应使用 CALL_FB）"
                % (path, seg))
            return None
        target_def = task.pou_lib.get(inst.block_type)
        if not isinstance(target_def, POUDefinition):
            err("CALL_FB_INSTANCE 路径 '%s' 段 '%s' 的定义 '%s' 不存在"
                % (path, seg, inst.block_type))
            return None
        cur_instances = {d.name: d for d in target_def.instances
                         if isinstance(d, InstanceDecl)}
    return target_def


def _check_bindings(what, bindings, target: POUDefinition, scope: _Scope,
                    caller: POUDefinition, stack: list, err):
    """校验绑定表；返回消费 StackSlot 后的新栈（list），致命错误返回 None。

    - formal 必须存在于被调接口，mode 与其 section 匹配，type 与声明一致；
    - 无重复 formal；**全部接口形参必须被绑定**（齐全性，IR_SPEC §5.2：
      必连形参缺失 = 加载错误；当前 IR 无"默认值已解析完毕"或"显式丢弃
      OUT"的独立编码，任何缺省均不可静态判定为合法，一律拒绝）；
    - actual：IN 允许 StoreKey/StackSlot/Const；OUT 禁止 Const、
      writable StackSlot 当前静态 pass 保守拒绝；INOUT 必须可写 StoreKey；
    - StoreKey 必须在调用方作用域可解析且类型匹配（FUNCTION 调用方绑到
      GVL 亦违反 GVL 禁令）。
    - 栈效应（IN×StackSlot；index 语义为工程约定，见模块 docstring）：
      index = 距调用点栈顶偏移（0 = 栈顶），须为非负整数、同一调用内
      互不重复且恰好连续覆盖 {0..k-1}；按 index 逐一核对类型后消费
      这 k 个栈值，绑定书写顺序不影响语义。
    """
    section_to_mode = {"VAR_INPUT": "IN", "VAR_OUTPUT": "OUT", "VAR_IN_OUT": "INOUT"}
    formals = {d.name: d for d in target.interface if isinstance(d, VarDecl)}
    seen: set = set()
    fatal = False
    stack_consumers = []

    for b in bindings:
        if not isinstance(b, Binding):
            err("%s：绑定表含非 Binding 项 %r" % (what, b))
            fatal = True
            continue
        if b.mode not in BINDING_MODES:
            err("%s：绑定 '%s' 模式非法 %r" % (what, b.formal, b.mode))
            fatal = True
            continue
        if b.formal in seen:
            err("%s：形参 '%s' 重复绑定" % (what, b.formal))
        seen.add(b.formal)
        decl = formals.get(b.formal)
        if decl is None:
            err("%s：形参 '%s' 不存在于 '%s' 接口" % (what, b.formal, target.name))
            fatal = True
            continue
        expect_mode = section_to_mode.get(decl.section)
        if expect_mode != b.mode:
            err("%s：形参 '%s' 声明为 %s（应绑 %s 模式），实际绑定模式 %s"
                % (what, b.formal, decl.section, expect_mode, b.mode))
        if b.type != decl.iec_type:
            err("%s：形参 '%s' 类型应为 %s，绑定类型 %s"
                % (what, b.formal, decl.iec_type, b.type))

        actual = b.actual
        if isinstance(actual, Const):
            if b.mode in ("OUT", "INOUT"):
                err("%s：形参 '%s' 为 %s 模式，禁止绑定 Const（须可写位置）"
                    % (what, b.formal, b.mode))
        elif isinstance(actual, StackSlot):
            if b.mode == "INOUT":
                err("%s：形参 '%s' 为 INOUT，必须绑定可写 StoreKey（IR_SPEC §5.2），"
                    "不接受 StackSlot" % (what, b.formal))
            elif b.mode == "OUT":
                if not actual.writable:
                    err("%s：形参 '%s' 为 OUT，绑定的 StackSlot 不可写" % (what, b.formal))
                else:
                    err("%s：形参 '%s'：OUT 绑定可写 StackSlot 的栈写回语义待执行器"
                        "工作包定义，当前静态校验保守拒绝（请改用 StoreKey）"
                        % (what, b.formal))
            else:  # IN：按 index 消费栈（语义见 docstring）
                if not isinstance(actual.index, int) \
                        or isinstance(actual.index, bool) or actual.index < 0:
                    err("%s：形参 '%s' 的 StackSlot.index 必须为非负整数，得到 %r"
                        % (what, b.formal, actual.index))
                    fatal = True
                else:
                    stack_consumers.append(b)
        elif isinstance(actual, StoreKey):
            declared = scope.resolve(actual.key)
            if declared is None:
                extra = ""
                if caller.pou_kind == "FUNCTION":
                    extra = "（注意：FUNCTION 禁止访问 GVL，IR_SPEC §3）"
                err("%s：形参 '%s' 的实参键 '%s' 在调用方作用域不可解析%s"
                    % (what, b.formal, actual.key, extra))
            elif declared != "*" and declared != b.type:
                err("%s：形参 '%s' 的实参 '%s' 声明类型 %s 与绑定类型 %s 不一致"
                    % (what, b.formal, actual.key, declared, b.type))
        else:
            err("%s：形参 '%s' 的 actual 形态未知 %r" % (what, b.formal, actual))
            fatal = True

    # 齐全性检查：全部接口形参必须绑定（必连形参缺失 = 加载错误，
    # IR_SPEC §5.2；当前 IR 无默认值/丢弃 OUT 的显式编码，缺省不可
    # 静态判定为合法，待相关形态显式建模后方可放宽）
    for name, decl in formals.items():
        if name not in seen:
            err("%s：形参 '%s'（%s）未绑定——绑定表须齐全（必连形参缺失 = "
                "加载错误，IR_SPEC §5.2；当前 IR 无默认值/丢弃 OUT 的显式编码）"
                % (what, name, decl.section))

    if fatal:
        return None

    new_stack = list(stack)
    if stack_consumers:
        k = len(stack_consumers)
        indices = [b.actual.index for b in stack_consumers]
        if len(set(indices)) != k:
            err("%s：多个 IN 绑定的 StackSlot.index 重复：%s"
                % (what, sorted(indices)))
            return None
        if set(indices) != set(range(k)):
            err("%s：IN 绑定的 StackSlot.index 须恰好连续覆盖 {0..%d}"
                "（0=栈顶，工程约定），实为 %s"
                % (what, k - 1, sorted(indices)))
            return None
        if k > len(new_stack):
            err("%s：IN 绑定 StackSlot 共 %d 个，但调用点栈深仅 %d（栈下溢）"
                % (what, k, len(new_stack)))
            return None
        for b in stack_consumers:
            ref = new_stack[-1 - b.actual.index]
            if ref != "*" and ref != b.type:
                err("%s：形参 '%s' 经 StackSlot(%d) 引用的栈值类型为 %s，应为 %s"
                    % (what, b.formal, b.actual.index, ref, b.type))
        del new_stack[len(new_stack) - k:]
    return new_stack
