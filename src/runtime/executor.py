"""正式 IR 执行器核心：显式顺序执行、TypedValue 求值栈、用户 POU 调用帧
（WP-20260714-004；IR_SPEC §3/§5/§7、ENGINE_SCAN_SPEC §3 第 2)+3) 步）。

职责边界（单一职责，**不是** ``scan()``）：本模块只负责"按
``Task.programs`` 列表顺序执行各 POU 的 IR 指令"。输入锁存、输出门控/
提交、扫描异常转安全输出、watchdog、shadow 均属后续 scan runner 工作包；
执行器内的异常带上下文**原样向上传播**，不转安全输出、不吞掉。

公开入口::

    executor = Executor(task, layout, numeric_mode=..., std_functions=...,
                        library_adapters=...)
    executor.execute_programs(prev_snapshot)

设计要点：

- **TypedValue 求值栈**：每个执行入口（每个 PROGRAM、每次 FUNCTION/FB
  调用）使用独立栈，被调 IR 不与调用方混栈。
- **运行期防御**：不只相信 loader——栈下溢、类型不匹配、出口栈契约、
  未知指令/标签在运行期再查一遍，违约抛 ``IRExecutionError``（含 POU、
  实例路径/frame id、pc、指令、原因）。
- **TypedValue 边界通道（WP-005）**：值进出 TypedValue 世界的全部边界
  （LOAD_CONST、LOAD_VAR、IN×Const/StoreKey、FUNCTION 播种与返回、
  CALL_STD 返回、STORE_VAR、OUT 写回、FB IN 拷入）统一走"原始值结构
  检查 → 数值钩子 → 结果复检"（``_checked_boundary``；Store/frame 来源
  的读取边界无钩子、只做结构检查），检查先于 ``on_const``/``on_store``
  ——F1 量化不得洗白结构性错误，不以"来源应当受守"代替边界自身防御。
  **库块管脚输出回收**（LOAD_VAR / IN×StoreKey 经 ``_PinLoc`` 读取，含
  INOUT 别名）是 IR_SPEC §5.3 边界 5 的 REAL 量化边界：F1 下管脚值先
  量化到 binary32 再进入 IR 世界（``_pin_recover_hook``），不得让未量化
  的 float64 直接参与后续运算。BINOP/UNOP/CONVERT
  的 ``on_result``/``convert`` 属"运算/显式转换"类：操作数在 ``_pop``
  消费点复检结构，结果由类型封闭的运算产生。
- **变量位置抽象**（``_Location``）统一访问持久 Store cell、frame cell、
  库块管脚与 INOUT 别名；INOUT 别名 = 直接传递调用方的位置对象本身，
  被调方读写即作用于调用方位置（真别名，非拷入拷回）。
- **调用帧**：FUNCTION 每次调用建独立 frame（VAR_INPUT 拷入、VAR_OUTPUT/
  VAR 按声明初值/类型默认重建、返回后销毁）；PROGRAM/FB 的 VAR_TEMP 每次
  进入按类型默认值重建、退出丢弃、不进持久 Store、不出现在 prev 快照。
  frame **播种阶段**（VAR_INPUT 拷入与 VAR_OUTPUT/VAR 初值）同样做结构性
  类型检查——装载后被篡改的绑定/声明初值不得把错误 Python 类型的值静默
  播入 frame（与写回阶段同口径的运行期防御）。
  异常路径经 try/finally 保证 frame 记录出栈，不污染下一次调用。
- **LoadPrev** 只从调用方传入的 ``StoreSnapshot`` 读（绝不读当前 Store
  冒充上一拍）；frame/VAR_TEMP 变量无上一拍语义，遇到即明确拒绝。反馈边
  到 LOAD_PREV 的 lowering 映射仍是待真机验证假设，本执行器只执行已生成
  的 LoadPrev 指令。
- **CallStd 注入边界**：标准函数经可注入的 ``std_functions`` 名册解析
  （本包不建完整 IEC 函数库）；缺实现明确报错，异常包入 ``IRExecutionError``
  原样携带 cause。
- **CallFb / 库块管脚（L2 注册表接入，WP-20260723-017）**：传入 ``registry``
  时，执行器按 COMPONENT_CONTRACT v2.1 的 ``Registry.resolve((block_type,
  variant))`` 为每个库块实例构造 ``_LibraryRuntime``（``adapter.construct``
  注入共享构造依赖如 ``license_context``、管脚过程映像落 Store、
  ``RuntimeAdapter.call_adapter`` 按省略语义驱动块并回收输出/VAR_IN_OUT），
  键 = 实例全路径。**Registry 路径不得被旧式 ``library_adapters`` 注入旁路**
  （两者同时提供即拒绝）。不传 registry 时保持历史注入式 ``library_adapters``
  边界（键 = 实例全路径、``read_pin``/``write_pin``/``pin_type``/``step``
  协议），供既有测试与过渡使用；缺 adapter/缺实现明确报错，adapter 异常
  包入 ``IRExecutionError`` 原样携带 cause。这些 Python 对照 ≠ 与 CODESYS
  语义一致（一致性属阶段 6 对拍）。
- 无限循环的外层终止（watchdog/扫描超时）属后续 scan runner，本包不实现
  也不伪造 PLC watchdog 语义。

Python 侧行为不构成与目标 PLC 语义一致的证据（一致性属阶段 6 对拍）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from src.runtime.ir import (
    BINOP_ARITH_OPS,
    BINOP_COMPARE_OPS,
    BINOP_LOGIC_OPS,
    BinOp,
    Binding,
    CallFb,
    CallFbInstance,
    CallFunc,
    CallStd,
    Const,
    Convert,
    InstanceDecl,
    Jmp,
    JmpIfFalse,
    Label,
    LoadConst,
    LoadPrev,
    LoadVar,
    POUDefinition,
    StackSlot,
    StoreKey,
    StoreVar,
    Task,
    UnOp,
    VarDecl,
)
from src.runtime.numeric import (
    IECMathError,
    NumericError,
    NumericMode,
    default_value,
    iec_mod,
    quantize_real32,
    trunc_div,
)
from src.runtime.store import (
    RuntimeLayout,
    StoreError,
    StoreSnapshot,
    StoreTypeError,
    check_value_type,
    persistent_key,
)


# One synchronous execute_programs entry may traverse multiple PROGRAMs and
# nested user FUNCTION/FB frames.  A single shared budget prevents backward
# jumps from occupying the scan thread forever.  It is deliberately internal:
# target-specific real-time budgets require a separate deployment decision.
_MAX_INSTRUCTIONS_PER_EXECUTE = 1_000_000


# ---------------------------------------------------------------------------
# 库块实例构造覆盖值的纯校验（无副作用；两个入口共用同源规则）
# ---------------------------------------------------------------------------

def check_ctor_value(value):
    """库块实例**单实例关键字构造覆盖**取值的纯校验（当前仅 APCHSACCUM
    IV/MS/MC）：须为**有限的 int/float 实数**，拒绝 ``bool``、字符串、``NaN``、
    ``±Inf``。返回 ``(ok: bool, why: str)``，**无副作用、不触碰 Store/Registry**。

    本函数是**唯一**值判定口径，供启动装配层
    （``parameters.build_runtime`` 纯校验汇总）与 ``Executor`` 直连闸门
    （``_build_library_runtimes`` 在 ``adapter.construct`` 前失败关闭）共同复用，
    杜绝两套易漂移规则（源码依赖裁决：``parameters → executor`` 单向依赖，
    值级校验收口在 ``executor``，``Executor`` 不反向导入 ``parameters``）。
    不依赖 ``bool`` 是 ``int`` 子类放宽 IEC 类型。"""
    if isinstance(value, bool):
        return False, "值不得为 bool（bool 是 int 子类，不放宽 IEC 类型）：%r" % (value,)
    if not isinstance(value, (int, float)):
        return False, ("值必须是有限的 int/float 实数，得到 %r（类型 %s）"
                       % (value, type(value).__name__))
    # Python 任意精度 int 恒为有限，且没有 NaN/±Inf；对其调用 math.isfinite 会先把
    # 大整数转成 C double，超范围时反而抛 OverflowError（例如 10**1000）。故整数直接
    # 接受，只有 float 才需 NaN/±Inf 判定（任务书未设整数位宽/binary64 上界）。
    if isinstance(value, float) and not math.isfinite(value):
        return False, "值必须有限，拒绝 NaN/±Inf：%r" % (value,)
    return True, ""


# ---------------------------------------------------------------------------
# 运行时值与异常
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TypedValue:
    """不可变运行时值：value + iec_type（IR_SPEC §5.1 TypedValue 栈元素）。"""
    value: Any
    iec_type: str


class IRExecutionError(Exception):
    """IR 运行期错误（带完整上下文；不吞异常、不转安全输出）。"""

    def __init__(self, pou: str, where: str, pc, instr, reason: str,
                 cause: Optional[BaseException] = None):
        self.pou = pou
        self.where = where          # 实例路径 或 frame id
        self.pc = pc
        self.instr = instr
        self.reason = reason
        self.cause = cause
        msg = ("POU '%s' @ %s, pc=%s, instr=%r：%s" %
               (pou, where, pc, instr, reason))
        if cause is not None:
            msg += "（原因：%r）" % (cause,)
        super().__init__(msg)
        if cause is not None:
            self.__cause__ = cause


class MissingStdFunctionError(IRExecutionError):
    """CALL_STD 缺少注入实现。"""


class MissingLibraryAdapterError(IRExecutionError):
    """CALL_FB / 库块管脚访问缺少注入的 library adapter。"""


# ---------------------------------------------------------------------------
# 变量位置抽象
# ---------------------------------------------------------------------------

class _StoreLoc:
    __slots__ = ("store", "key")

    def __init__(self, store, key):
        self.store = store
        self.key = key

    def read(self):
        return self.store.read(self.key)

    def write(self, value):
        self.store.write(self.key, value)

    def declared_type(self):
        return self.store.declared_type(self.key)


class _CellLoc:
    __slots__ = ("cells", "name")

    def __init__(self, cells, name):
        self.cells = cells
        self.name = name

    def read(self):
        return self.cells[self.name][1]

    def write(self, value):
        # 运行期防御（与 Store.write 同口径）：frame/VAR_TEMP cell 的写入
        # 同样做结构性类型检查，装载后被篡改的声明/值不得被静默接受
        declared = self.cells[self.name][0]
        if not check_value_type(declared, value):
            raise StoreTypeError(
                "frame/VAR_TEMP 变量 '%s' 写入值 %r 与声明类型 %s 不匹配"
                "（不做隐式转换）" % (self.name, value, declared))
        self.cells[self.name][1] = value

    def declared_type(self):
        return self.cells[self.name][0]


class _PinLoc:
    """库块管脚位置：全部读写委托给库块 runtime（``read_pin``/``write_pin``/
    ``pin_type`` 协议）。正式 L2 注册表接入时该 runtime 为 ``_LibraryRuntime``
    （按 Schema 报出管脚类型、管脚过程映像落在 Store）；未接入时为调用方注入
    的旧式 adapter（``pin_type`` 可缺省，返回 None=类型未知）。"""
    __slots__ = ("adapter", "pin")

    def __init__(self, adapter, pin):
        self.adapter = adapter
        self.pin = pin

    def read(self):
        return self.adapter.read_pin(self.pin)

    def write(self, value):
        self.adapter.write_pin(self.pin, value)

    def declared_type(self):
        getter = getattr(self.adapter, "pin_type", None)
        return getter(self.pin) if getter is not None else None   # None=未知（旧式注入）


# ---------------------------------------------------------------------------
# L2 注册表运行绑定（COMPONENT_CONTRACT v2.1）
# ---------------------------------------------------------------------------

class LibraryRuntimeError(Exception):
    """库块 runtime 绑定/调用错误（如 required 管脚本拍未驱动）。"""


class _RealRef:
    """``VAR_IN_OUT`` 写透用的最小可变引用（只暴露 ``value``）。

    ``RuntimeAdapter.call_adapter`` 通过 ``inout_refs[pin].value`` 读入调用前
    的 VAR_IN_OUT 当前值、写出调用后的新值；runtime 在 step 前后把它与 Store
    管脚键同步，实现"引用别名写透"而不改动块源码。"""
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class _LibraryRuntime:
    """单个库块实例的进程内运行绑定（Registry 驱动，非注入旁路）。

    职责：把 IR 侧的"管脚过程映像"（落在 Store 的
    ``<路径>.<管脚名>`` 键）与库块实例、``RuntimeAdapter.call_adapter`` 约定
    桥接起来，向执行器暴露与旧式注入 adapter 相同的
    ``read_pin``/``write_pin``/``pin_type``/``step`` 协议：

    - ``write_pin``：写入输入管脚 Store 键，并记录该管脚"本拍被驱动"；
    - ``step``：按 Schema 省略语义组装 ``resolved_inputs``——``required``
      本拍必被驱动否则 fail-closed；``use_default`` 恒传（未驱动用 Schema
      声明默认/类型默认，**非**上次驱动值）；``keep_previous`` **首拍**未驱动
      同样传 Schema 声明默认（使首拍值由 Schema 契约而非块构造器偶然初值
      决定），**此后**省略才不传、由块保持内部上次值；``none_means_no_write``
      未驱动即从首拍起一律不传（块保持内部值 / 不覆盖）——再组装
      ``VAR_IN_OUT`` 引用，转调 ``call_adapter``，把全部 VAR_IN_OUT 与声明输出
      候选值（经结构复检 + ``on_store``）暂存后经 ``Store.write_batch`` **一次性
      原子提交**（任一候选失败或提交异常时全部目标管脚保持调用前值，不半写）；
      无论成功或异常，都在 ``finally``
      清空本拍驱动记录（失败调用残留的驱动标记会污染下一拍的 required 判定）；
    - ``read_pin``：从输出/VAR_IN_OUT/输入管脚 Store 键读回（供 ``LOAD_VAR``）；
    - ``pin_type``：报出 Schema 管脚 IEC 类型（供 ``LOAD_VAR``/``STORE_VAR``
      指令类型核对与 F1 REAL 管脚量化）。

    块**内部**长期状态归块实例自身（``src/blocks``/``src/primitives`` 零改动）。
    """

    __slots__ = ("path", "schema", "adapter", "instance", "store", "mode",
                 "_driven", "_stepped")

    def __init__(self, path, schema, adapter, instance, store, mode):
        self.path = path
        self.schema = schema
        self.adapter = adapter
        self.instance = instance
        self.store = store
        self.mode = mode
        self._driven: set = set()
        # 该实例是否已被成功驱动过一次（整步——``call_adapter`` **与**全部
        # VAR_IN_OUT / 声明输出回收——至少完整成功一次）。``keep_previous``
        # 省略语义按 COMPONENT_CONTRACT §3 分首拍/后续拍：**首拍**用 Schema
        # 声明默认（不依赖块构造器偶然内部初值），此后省略才保持块内该管脚
        # 上次值。required 缺失 / adapter 异常 / 任一输出回收失败时不置真，
        # 下一拍仍按首拍取默认。
        self._stepped: bool = False

    def _key(self, pin: str) -> str:
        return persistent_key(self.path, pin)

    def pin_type(self, pin: str):
        p = self.schema.pin(pin)
        return p.iec_type if p is not None else None

    def read_pin(self, pin: str):
        return self.store.read(self._key(pin))

    def write_pin(self, pin: str, value) -> None:
        # Store.write 做结构性类型检查（不做隐式转换）；记录本拍已驱动
        self.store.write(self._key(pin), value)
        self._driven.add(pin)

    def _convert_output(self, pin: str, iec_type: str, value):
        """输出/VAR_IN_OUT 候选值：结构检查先于 on_store，返回转换后候选值。

        **只做检查与数值转换、不触碰 Store**——由 ``step`` 把全部候选集完整
        物化成功后再经 ``Store.write_batch`` 一次性原子提交，杜绝逐项可见半写
        （WP-035 反证：逐项 `Store.write` 会在后续管脚失败时残留已写管脚）。"""
        if not check_value_type(iec_type, value):
            raise StoreTypeError(
                "库块 '%s' 管脚 '%s' 输出值 %r 与声明类型 %s 结构不匹配"
                "（检查先于数值钩子，F1 量化不得洗白）"
                % (self.path, pin, value, iec_type))
        return self.mode.on_store(value, iec_type)

    def _default_input(self, pin):
        """管脚**省略拍取 Schema 声明默认**的统一取值（COMPONENT_CONTRACT §3）。

        两处调用：``use_default``（每拍未驱动即用 default）与 ``keep_previous``
        的**首拍**（首拍用 default，此后省略才保持块内上次值）。二者省略拍的
        "取默认"边界完全同口径，故共用本方法。

        取 Schema 声明 ``default``（为 ``None`` 时退化为该 IEC 类型默认值），
        经与驱动路径同口径的输入边界：结构性类型检查（``check_value_type``，
        不做隐式转换）**先于** ``on_store``（F1 下 REAL 量化到 binary32、整数
        按声明位宽回绕）——F1 量化不得洗白结构性错误的默认值。**绝不退化为
        持久 Store 的上次驱动值**：把 ``use_default`` 省略当作 ``keep_previous``
        会让"先驱动后省略"的管脚错误保持上次值（Codex WP-019 Round 1 复现）；
        把 ``keep_previous`` 首拍当作"读块构造器偶然初值"会让首拍值不由
        Schema 契约决定（Codex WP-019 Round 2 复现）。"""
        value = pin.default if pin.default is not None \
            else default_value(pin.iec_type)
        if not check_value_type(pin.iec_type, value):
            raise LibraryRuntimeError(
                "库块 '%s' use_default 管脚 '%s' 的默认值 %r 与声明类型 %s "
                "结构不匹配（结构检查先于 on_store，不做隐式转换）"
                % (self.path, pin.name, value, pin.iec_type))
        return self.mode.on_store(value, pin.iec_type)

    def step(self, dt_ms: int) -> None:
        schema = self.schema
        first = not self._stepped
        try:
            resolved: dict = {}
            for p in schema.inputs:
                if p.name in self._driven:
                    resolved[p.name] = self.store.read(self._key(p.name))
                elif p.omit_policy == "required":
                    raise LibraryRuntimeError(
                        "库块 '%s' 的 required 管脚 '%s' 本拍未被驱动"
                        "（fail-closed，不静默用默认值）" % (self.path, p.name))
                elif p.omit_policy == "use_default":
                    # COMPONENT_CONTRACT §3：use_default 本拍未驱动 → 用 Schema
                    # 声明 default（缺省则类型默认），**不是**读持久 Store 的上次
                    # 驱动值（那是 keep_previous）。默认值同走驱动路径输入边界。
                    resolved[p.name] = self._default_input(p)
                elif p.omit_policy == "keep_previous" and first:
                    # COMPONENT_CONTRACT §3：keep_previous **首拍**用 Schema 声明
                    # default（同 use_default 边界），使块首拍值由 Schema 契约
                    # 决定、而非块构造器偶然内部初值；此后省略才由块保持内部
                    # 上次值（落入下方省略分支、不传）。
                    resolved[p.name] = self._default_input(p)
                # keep_previous（非首拍）/ none_means_no_write：省略 → 不传 →
                # 块保持内部上次值 / 本拍不覆盖
            inout_refs = {p.name: _RealRef(self.store.read(self._key(p.name)))
                          for p in schema.inouts}
            outputs = self.adapter.call_adapter(self.instance, dt_ms, resolved,
                                                inout_refs)
            # 输出 Store 提交必须是**原子单元**：本次成功回收的全部 VAR_IN_OUT
            # 与全部 Schema 声明 VAR_OUTPUT 要么一起可见、要么一个都不写
            # （WP-035 反证：旧实现逐项 `Store.write` 会在后续管脚结构/数值/
            # 未知键失败时残留已写管脚，形成 ZLOUT 半写回）。
            # 步骤：① 先完整检查所有声明输出存在；② 对所有 inout/output 原始值
            # 先做结构检查再 on_store，把候选值全部暂存；③ 候选集完整成功后才
            # 调用 Store 原子批量写。任一步失败时全部目标 Store 键保持调用前值。
            out_by_name = {p.name: p for p in schema.outputs}
            for name in out_by_name:
                if name not in outputs:
                    raise LibraryRuntimeError(
                        "库块 '%s' step 未回收声明输出管脚 '%s'"
                        % (self.path, name))
            batch: list = []
            for p in schema.inouts:
                batch.append((self._key(p.name),
                              self._convert_output(p.name, p.iec_type,
                                                   inout_refs[p.name].value)))
            for name, p in out_by_name.items():
                batch.append((self._key(name),
                              self._convert_output(name, p.iec_type,
                                                   outputs[name])))
            self.store.write_batch(batch)
            # 块已被成功驱动一次：置于 call_adapter **与**全部 VAR_IN_OUT / 声明
            # 输出回收均成功之后——required 缺失、adapter 异常、或任一 VAR_IN_OUT
            # /输出回收失败（缺声明输出、候选转换结构错误、Store 原子提交失败）时
            # 保持 False，下一拍 keep_previous 仍按首拍取 Schema 默认。Codex WP-021 Round 2
            # 复现：把该赋值放在 call_adapter 之后、回收之前，一次整体失败的
            # CALL_FB 会错误推进"首次完整成功"状态，使下一拍省略不再取默认。
            self._stepped = True
        finally:
            # 无论成功或异常，都清空本拍驱动集合：失败调用残留的驱动标记会让
            # 下一拍缺失的 required 管脚被误判为"已驱动"（Codex WP-019 Round 2
            # 复现——第 1 拍缺 B 抛错后 A 标记残留，第 2 拍缺 A 却意外成功）。
            self._driven.clear()


# ---------------------------------------------------------------------------
# 执行上下文（一个 POU 体的一次执行）
# ---------------------------------------------------------------------------

class _Ctx:
    __slots__ = ("pou", "kind", "path", "where", "stack", "cells",
                 "aliases", "instances", "persist", "prev")

    def __init__(self, pou: POUDefinition, path: Optional[str], where: str,
                 prev: Optional[StoreSnapshot]):
        self.pou = pou
        self.kind = pou.pou_kind
        self.path = path                    # PROGRAM/FB 实例全路径；FUNCTION=None
        self.where = where                  # 报错定位：路径或 frame id
        self.stack: list = []               # 独立 TypedValue 求值栈
        self.cells: dict = {}               # frame/VAR_TEMP：name -> [type, value]
        self.aliases: dict = {}             # INOUT formal -> _Location（真别名）
        self.instances: dict = {}           # name -> InstanceDecl
        self.persist: dict = {}             # 持久变量 name -> iec_type（PROGRAM/FB）
        self.prev = prev


class Executor:
    """显式顺序 IR 执行器核心（非 scan；见模块 docstring 职责边界）。"""

    def __init__(self, task: Task, layout: RuntimeLayout,
                 numeric_mode: Optional[NumericMode] = None,
                 std_functions: Optional[Mapping[str, Callable]] = None,
                 library_adapters: Optional[Mapping[str, Any]] = None,
                 registry=None,
                 dependencies: Optional[Mapping[str, Any]] = None):
        self.task = task
        self.layout = layout
        self.store = layout.store
        self.mode = numeric_mode if numeric_mode is not None else NumericMode()
        self._std = dict(std_functions or {})
        # 库块运行绑定：接入 L2 注册表时按 Registry 为每个库块实例构造
        # `_LibraryRuntime`（构造依赖注入、管脚过程映像落 Store、call_adapter
        # 驱动）；**Registry 路径不得被旧式 adapter 注入旁路**——两者同时提供
        # 即拒绝。未接入 registry 时保持历史注入式 adapter 边界。
        if registry is not None:
            if library_adapters:
                raise ValueError(
                    "提供 registry 时不得再注入 library_adapters："
                    "正式 L2 注册表路径不得被旧式 adapter 注入旁路")
            self._adapters = self._build_library_runtimes(
                registry, dict(dependencies or {}))
        else:
            self._adapters = dict(library_adapters or {})
        self._gvl_types = {d.name: d.iec_type for d in task.gvl
                           if isinstance(d, VarDecl)}
        self._fb_paths = set(fb.path for fb in layout.fb_instances)
        self._frame_seq = 0
        self._active_frames: list = []      # 诊断用；异常路径 finally 出栈
        self._instruction_budget_remaining = None
        # 标签表按只读代码对象缓存（POU 名 -> {label: pc}）
        self._labels: dict = {}
        for name, pou in task.pou_lib.items():
            if isinstance(pou, POUDefinition) and pou.code is not None:
                self._labels[name] = {
                    ins.id: i for i, ins in enumerate(pou.code)
                    if isinstance(ins, Label)
                }

    # ------------------------------------------------------------------
    # L2 注册表 → 库块运行绑定
    # ------------------------------------------------------------------

    def _build_library_runtimes(self, registry, dependencies: dict) -> dict:
        """按 Registry 为 ``layout.library_instances`` 每个实例构造运行绑定。

        用当前数值模式选变体（``variant_for_mode``：engineering/fidelity_f1 →
        engineering）解析 ``(schema, adapter)``，经 ``adapter.construct`` 从注入
        依赖构造块实例（如 APCM 的共享 ``license_context``——同一 dependencies
        对象让同任务多实例共享同一 context），键 = 实例全路径。管脚过程映像
        键已由 ``build_runtime_store`` 装载期分配，运行期只读写既有键。
        """
        variant = "fidelity_f2" if self.mode.mode == "fidelity_f2" \
            else "engineering"
        runtimes: dict = {}
        for path, inst in self.layout.library_instances:
            schema, adapter = registry.resolve(inst.block_type, variant)
            # 纵向失败关闭（WP-20260728-041）：单实例关键字构造配置
            # （`InstanceDecl.ctor_args`）必须已由 Schema `init_overridable`
            # 授权、且不得与共享构造依赖同名。此为独立于启动装配层的防御闸门
            # ——未授权/未知/冲突配置一律拒绝，绝不臆测块构造签名自动开放参数。
            self._check_instance_ctor_args(path, inst, schema, adapter)
            instance = adapter.construct(dependencies, inst.ctor_args)
            runtimes[path] = _LibraryRuntime(path, schema, adapter, instance,
                                             self.store, self.mode)
        return runtimes

    @staticmethod
    def _check_instance_ctor_args(path, inst, schema, adapter) -> None:
        """库块实例关键字构造配置的授权/冲突/取值闸门（纵向失败关闭）。

        本闸门是**独立于启动装配层**（``parameters.build_runtime``）的防御纵深
        ——绕过 ``build_runtime`` 的既有直连路径
        ``build_runtime_store(task, registry) → Executor(..., registry=registry)``
        同样在此拒绝未授权/冲突键**以及**非法构造取值（``bool``/字符串/``NaN``/
        ``±Inf``）。取值判定复用同源纯校验 :func:`check_ctor_value`（值集合不漂移）。
        键按确定顺序遍历，报错稳定；本方法在 ``adapter.construct`` **之前**调用，
        非法配置在块实例构造前失败。"""
        shared = set(adapter.ctor_args)
        for key in sorted(inst.ctor_args):
            if key in shared:
                raise LibraryRuntimeError(
                    "库块实例 '%s'（%s）构造配置 '%s' 与共享构造依赖同名，"
                    "不能遮蔽任务依赖" % (path, inst.block_type, key))
            if key not in schema.init_overridable:
                raise LibraryRuntimeError(
                    "库块实例 '%s'（%s）构造配置 '%s' 未被 Schema "
                    "init_overridable 授权（未声明的构造覆盖一律拒绝，"
                    "不臆测块构造签名）" % (path, inst.block_type, key))
            ok, why = check_ctor_value(inst.ctor_args[key])
            if not ok:
                raise LibraryRuntimeError(
                    "库块实例 '%s'（%s）构造配置 '%s' %s"
                    % (path, inst.block_type, key, why))

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def execute_programs(self, prev_snapshot: StoreSnapshot) -> None:
        """按 ``Task.programs`` 列表顺序执行全部 PROGRAM 的 IR（D2）。

        ``prev_snapshot`` 为调用方（后续 scan runner）在上一拍提交点生成的
        只读快照，供 ``LoadPrev`` 使用；本方法不生成、不更新 prev。
        """
        if not isinstance(prev_snapshot, StoreSnapshot):
            raise IRExecutionError("<task>", "<entry>", None, None,
                                   "execute_programs 需要 StoreSnapshot 作 prev")
        if self._instruction_budget_remaining is not None:
            raise IRExecutionError(
                "<task>", "<entry>", None, None,
                "execute_programs 不允许在同一 Executor 上重入")
        self._instruction_budget_remaining = _MAX_INSTRUCTIONS_PER_EXECUTE
        try:
            for prog in self.task.programs:
                pou = self.task.pou_lib[prog.definition]
                ctx = self._make_ctx(pou, prog.store_prefix, prev_snapshot)
                self._run(ctx)
                if ctx.stack:
                    raise IRExecutionError(
                        pou.name, prog.store_prefix, len(pou.code or []), None,
                        "PROGRAM 正常出口栈应为空，实为 %d 项" % len(ctx.stack))
        finally:
            self._instruction_budget_remaining = None

    # ------------------------------------------------------------------
    # 上下文构建
    # ------------------------------------------------------------------

    def _make_ctx(self, pou: POUDefinition, path: Optional[str],
                  prev: Optional[StoreSnapshot],
                  where: Optional[str] = None) -> _Ctx:
        ctx = _Ctx(pou, path, where or (path or pou.name), prev)
        for inst in pou.instances:
            if isinstance(inst, InstanceDecl):
                ctx.instances[inst.name] = inst
        for d in list(pou.interface) + list(pou.locals):
            if not isinstance(d, VarDecl):
                continue
            if d.section == "VAR_TEMP":
                # VAR_TEMP：每次进入按类型默认值重建（不取 initial——任务书
                # §六.4 与 IR_SPEC §3"每次进入清零"）
                ctx.cells[d.name] = [d.iec_type, default_value(d.iec_type)]
            elif pou.pou_kind != "FUNCTION" and d.section in (
                    "VAR", "VAR_INPUT", "VAR_OUTPUT"):
                ctx.persist[d.name] = d.iec_type
        return ctx

    # ------------------------------------------------------------------
    # 变量解析（优先级与 loader 静态作用域一致）
    # ------------------------------------------------------------------

    def _resolve(self, ctx: _Ctx, key: str, pc, instr):
        # ① 当前 POU：INOUT 别名 → frame/temp cell → 持久变量
        if key in ctx.aliases:
            return ctx.aliases[key]
        if key in ctx.cells:
            return _CellLoc(ctx.cells, key)
        if key in ctx.persist:
            return _StoreLoc(self.store, persistent_key(ctx.path, key))
        if "." in key:
            inst_name, pin = key.split(".", 1)
            inst = ctx.instances.get(inst_name)
            if inst is not None:
                if inst.kind == "library":
                    full = "%s.%s" % (ctx.path, inst_name)
                    adapter = self._adapters.get(full)
                    if adapter is None:
                        raise MissingLibraryAdapterError(
                            ctx.pou.name, ctx.where, pc, instr,
                            "库块实例 '%s' 缺少注入的 library adapter"
                            "（正式 L2 注册表属独立工作包）" % full)
                    return _PinLoc(adapter, pin)
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, instr,
                    "用户 FB 管脚 '%s' 不支持点分直访（须经 CALL_FB_INSTANCE "
                    "绑定，loader 同样拒绝）" % key)
        # ② GVL（FUNCTION 禁止）
        if key in self._gvl_types:
            if ctx.kind == "FUNCTION":
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, instr,
                    "FUNCTION 禁止访问 GVL '%s'（IR_SPEC §3）" % key)
            return _StoreLoc(self.store, key)
        raise IRExecutionError(ctx.pou.name, ctx.where, pc, instr,
                               "无法解析变量键 '%s'" % key)

    # ------------------------------------------------------------------
    # 栈操作（运行期防御）
    # ------------------------------------------------------------------

    def _pop(self, ctx: _Ctx, expect: Optional[str], pc, instr,
             what: str) -> TypedValue:
        if not ctx.stack:
            raise IRExecutionError(ctx.pou.name, ctx.where, pc, instr,
                                   "栈下溢（%s 缺失）" % what)
        tv = ctx.stack.pop()
        if expect is not None and tv.iec_type != expect:
            raise IRExecutionError(
                ctx.pou.name, ctx.where, pc, instr,
                "%s 类型应为 %s，栈顶是 %s" % (what, expect, tv.iec_type))
        # WP-005 Round 2（采纳 Codex 非阻塞建议）：除类型标签外复检栈值的
        # Python 结构——即使某个创建点防御被绕过，消费点也不放行
        if not check_value_type(tv.iec_type, tv.value):
            raise IRExecutionError(
                ctx.pou.name, ctx.where, pc, instr,
                "%s：栈值 %r 与其类型标签 %s 结构不匹配（栈消费点防御）"
                % (what, tv.value, tv.iec_type))
        return tv

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def _run(self, ctx: _Ctx) -> None:
        code = ctx.pou.code or []
        labels = self._labels.get(ctx.pou.name, {})
        pc = 0
        n = len(code)
        while pc < n:
            ins = code[pc]
            remaining = self._instruction_budget_remaining
            if remaining is None:
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, ins,
                    "IR 执行缺少 active execute_programs 指令预算")
            if remaining <= 0:
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, ins,
                    "单次 execute_programs 指令预算已耗尽")
            self._instruction_budget_remaining = remaining - 1
            try:
                nxt = self._step(ctx, ins, pc, labels)
            except IRExecutionError:
                raise
            except (NumericError, StoreError, ZeroDivisionError, ValueError,
                    TypeError, KeyError, OverflowError) as e:
                raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                       "指令执行失败", cause=e)
            pc = nxt if nxt is not None else pc + 1

    def _jump_to(self, ctx: _Ctx, label: str, labels: dict, pc, ins) -> int:
        if label not in labels:
            raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                   "未知标签 '%s'" % label)
        return labels[label]

    # ------------------------------------------------------------------
    # 共享的"原始值先验检查"入口（WP-20260714-005）
    # ------------------------------------------------------------------

    def _checked_boundary(self, ctx: _Ctx, pc, instr, raw_value,
                          iec_type: str, hook, what: str):
        """TypedValue 边界的唯一检查通道：**先**验原始 Python 值结构，
        **再**过数值模式钩子，**最后**复检钩子结果。

        规则（E 与 F1 共享同一结构性检查，差别只来自钩子本身）：数值钩子
        只能做量化/回绕/已允许的显式转换，不得把结构性错误类型"修正"为
        合法类型——F1 的 on_const/on_store 不得洗白 int→REAL 之类的原始
        错误。检查口径 = ``check_value_type``（BOOL=bool、整数=int 且非
        bool、REAL/LREAL=float、STRING=str，不做隐式转换）。"""
        if not check_value_type(iec_type, raw_value):
            raise IRExecutionError(
                ctx.pou.name, ctx.where, pc, instr,
                "%s：原始值 %r 与声明类型 %s 结构不匹配（检查先于数值钩子，"
                "F1 量化不得洗白；不做隐式转换）" % (what, raw_value, iec_type))
        out = hook(raw_value, iec_type)
        if not check_value_type(iec_type, out):
            raise IRExecutionError(
                ctx.pou.name, ctx.where, pc, instr,
                "%s：数值钩子结果 %r 不再符合类型 %s（钩子越权）"
                % (what, out, iec_type))
        return out

    def _pin_recover_hook(self, value, iec_type: str):
        """库块管脚输出回收钩子（IR_SPEC §5.3 边界 5，WP-005 Round 3）。

        F1 下 REAL 管脚值在进入 IR 世界前量化到 binary32（"库块内部 64 位
        不受影响，边界被量化"）；L2 描述符未接入时以指令/绑定携带的 IEC
        类型充当本次运行的 ``Pin.iec_type``。**只做 REAL 量化**：§5.3 对
        整数仅承诺 STORE_VAR/CONVERT 按声明类型截断，管脚回收不是整数
        截断点，本钩子不对整数回绕/截断（不发明规格外语义）；LREAL 本就
        binary64，E 模式一律原样透传。"""
        if self.mode.is_fidelity and iec_type == "REAL":
            return quantize_real32(value)
        return value

    def _step(self, ctx: _Ctx, ins, pc, labels) -> Optional[int]:
        mode = self.mode

        if isinstance(ins, LoadConst):
            # 顺序（WP-005）：原始 ins.value 结构检查 → on_const → 结果复检
            value = self._checked_boundary(ctx, pc, ins, ins.value, ins.type,
                                           mode.on_const, "LOAD_CONST")
            ctx.stack.append(TypedValue(value, ins.type))
            return None

        if isinstance(ins, LoadVar):
            loc = self._resolve(ctx, ins.key, pc, ins)
            declared = loc.declared_type()
            if declared is not None and declared != ins.type:
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, ins,
                    "LOAD_VAR '%s' 指令类型 %s 与声明类型 %s 不一致"
                    % (ins.key, ins.type, declared))
            # WP-005 Round 2：读得的原始值一律先过结构检查再进 TypedValue
            # 世界——库块管脚（_PinLoc）值来自外部 adapter、无声明类型可比
            # （declared=None），但 LoadVar.type 已给出本次运行的期望类型，
            # 不得因 L2 延后而跳过；Store/frame 来源虽写入侧已受守，边界
            # 自身仍复检，不以"来源应当受守"代替本边界防御。
            raw = loc.read()
            if isinstance(loc, _PinLoc):
                # WP-005 Round 3（IR_SPEC §5.3 边界 5）：库块管脚输出回收
                # 是 REAL 量化边界——F1 下按指令类型量化后才进入 IR 世界，
                # 不得让未量化的 float64 直接参与后续 BINOP（结构检查仍
                # 先于量化钩子，量化不得洗白结构错误）。经 INOUT 别名读到
                # 的管脚位置同样落入本分支。
                ctx.stack.append(TypedValue(self._checked_boundary(
                    ctx, pc, ins, raw, ins.type, self._pin_recover_hook,
                    "LOAD_VAR '%s' 库块管脚回收值" % ins.key), ins.type))
                return None
            if not check_value_type(ins.type, raw):
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, ins,
                    "LOAD_VAR '%s' 读得原始值 %r 与指令类型 %s 结构不匹配"
                    "（检查先于任何数值钩子，F1 不得洗白；不做隐式转换）"
                    % (ins.key, raw, ins.type))
            ctx.stack.append(TypedValue(raw, ins.type))
            return None

        if isinstance(ins, LoadPrev):
            ctx.stack.append(TypedValue(self._read_prev(ctx, ins, pc),
                                        ins.type))
            return None

        if isinstance(ins, StoreVar):
            tv = self._pop(ctx, ins.type, pc, ins, "STORE_VAR 待写值")
            loc = self._resolve(ctx, ins.key, pc, ins)
            declared = loc.declared_type()
            if declared is not None and declared != ins.type:
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, ins,
                    "STORE_VAR '%s' 指令类型 %s 与声明类型 %s 不一致"
                    % (ins.key, ins.type, declared))
            # WP-005 Round 2：STORE_VAR 同走共享边界通道（原始值结构检查
            # → on_store → 结果复检），不以"栈值来源应当受守"代替边界防御
            loc.write(self._checked_boundary(
                ctx, pc, ins, tv.value, ins.type, mode.on_store,
                "STORE_VAR '%s' 待写值" % ins.key))
            return None

        if isinstance(ins, BinOp):
            b = self._pop(ctx, ins.type, pc, ins, "BINOP 右操作数")
            a = self._pop(ctx, ins.type, pc, ins, "BINOP 左操作数")
            ctx.stack.append(self._binop(ctx, ins, a.value, b.value, pc))
            return None

        if isinstance(ins, UnOp):
            tv = self._pop(ctx, ins.type, pc, ins, "UNOP 操作数")
            if ins.op == "NOT":
                ctx.stack.append(TypedValue(
                    mode.bitwise_not(tv.value, ins.type), ins.type))
            elif ins.op == "NEG":
                ctx.stack.append(TypedValue(
                    mode.on_result(-tv.value, ins.type), ins.type))
            else:
                raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                       "未知 UNOP 操作符 %r" % (ins.op,))
            return None

        if isinstance(ins, Convert):
            tv = self._pop(ctx, ins.from_type, pc, ins, "CONVERT 源值")
            ctx.stack.append(TypedValue(
                mode.convert(tv.value, ins.from_type, ins.to_type),
                ins.to_type))
            return None

        if isinstance(ins, CallStd):
            return self._call_std(ctx, ins, pc)

        if isinstance(ins, CallFb):
            return self._call_fb(ctx, ins, pc)

        if isinstance(ins, CallFunc):
            return self._call_func(ctx, ins, pc)

        if isinstance(ins, CallFbInstance):
            return self._call_fb_instance(ctx, ins, pc)

        if isinstance(ins, Jmp):
            return self._jump_to(ctx, ins.label, labels, pc, ins)

        if isinstance(ins, JmpIfFalse):
            tv = self._pop(ctx, "BOOL", pc, ins, "JMP_IF_FALSE 条件")
            if not tv.value:
                return self._jump_to(ctx, ins.label, labels, pc, ins)
            return None

        if isinstance(ins, Label):
            return None

        raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                               "未知指令对象")

    # ------------------------------------------------------------------
    # 运算
    # ------------------------------------------------------------------

    def _binop(self, ctx: _Ctx, ins: BinOp, a, b, pc) -> TypedValue:
        op, t = ins.op, ins.type
        mode = self.mode
        if op in BINOP_COMPARE_OPS:
            table = {"GT": a > b, "GE": a >= b, "LT": a < b,
                     "LE": a <= b, "EQ": a == b, "NE": a != b}
            return TypedValue(bool(table[op]), "BOOL")   # 比较结果恒 BOOL
        if op in BINOP_LOGIC_OPS:
            if t == "BOOL":
                v = {"AND": a and b, "OR": a or b, "XOR": a != b}[op]
                return TypedValue(bool(v), "BOOL")
            v = {"AND": a & b, "OR": a | b, "XOR": a ^ b}[op]     # 位串/整数按位
            return TypedValue(mode.on_result(v, t), t)
        if op in BINOP_ARITH_OPS:
            if op == "DIV":
                if t in ("REAL", "LREAL"):
                    if b == 0.0:
                        raise IECMathError("REAL DIV 除零")
                    v = a / b
                else:
                    v = trunc_div(a, b)                  # 纯整数、向零截断
            elif op == "MOD":
                v = iec_mod(a, b)                        # 符号随被除数
            elif op == "ADD":
                v = a + b
            elif op == "SUB":
                v = a - b
            else:                                        # MUL
                v = a * b
            return TypedValue(mode.on_result(v, t), t)
        raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                               "未知 BINOP 操作符 %r" % (op,))

    # ------------------------------------------------------------------
    # LoadPrev
    # ------------------------------------------------------------------

    def _read_prev(self, ctx: _Ctx, ins: LoadPrev, pc):
        key = ins.key
        # 先判 frame/VAR_TEMP/别名：这些变量根本没有上一拍语义（比"缺快照"
        # 更根本），必须给出明确拒绝（任务书 §八 LoadPrev）
        if key in ctx.aliases or key in ctx.cells or ctx.kind == "FUNCTION":
            raise IRExecutionError(
                ctx.pou.name, ctx.where, pc, ins,
                "LOAD_PREV '%s'：frame/VAR_TEMP/别名变量没有上一拍语义，"
                "明确拒绝（不猜测）" % key)
        if ctx.prev is None:
            raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                   "当前上下文没有可用的 prev 快照")
        if key in ctx.persist:
            full = persistent_key(ctx.path, key)
        elif key in self._gvl_types and ctx.kind != "FUNCTION":
            full = key
        else:
            raise IRExecutionError(
                ctx.pou.name, ctx.where, pc, ins,
                "LOAD_PREV 无法解析 '%s' 为持久变量/GVL（库块管脚与未知键均"
                "无 prev 语义）" % key)
        if full not in ctx.prev:
            raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                   "prev 快照中不存在键 '%s'" % full)
        return ctx.prev.read(full)

    # ------------------------------------------------------------------
    # 调用：公共绑定求值
    # ------------------------------------------------------------------

    def _eval_in_bindings(self, ctx: _Ctx, bindings, pc, instr) -> dict:
        """求值全部 IN 绑定 → {formal: TypedValue}；StackSlot 按 index 先
        快照后一次性消费（书写顺序无关，0=栈顶，工程约定）。"""
        slot_bindings = [b for b in bindings
                         if b.mode == "IN" and isinstance(b.actual, StackSlot)]
        values: dict = {}
        if slot_bindings:
            k = len(slot_bindings)
            depth = len(ctx.stack)
            if k > depth:
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, instr,
                    "IN×StackSlot 需 %d 个栈值，栈深仅 %d（栈下溢）" % (k, depth))
            indices = sorted(b.actual.index for b in slot_bindings)
            if indices != list(range(k)):
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, instr,
                    "StackSlot.index 须恰好连续覆盖 {0..%d}，实为 %s"
                    % (k - 1, indices))
            # 先按 index 完成取值快照，再一次性消费 k 项
            for b in slot_bindings:
                tv = ctx.stack[-1 - b.actual.index]
                if tv.iec_type != b.type:
                    raise IRExecutionError(
                        ctx.pou.name, ctx.where, pc, instr,
                        "形参 '%s' 经 StackSlot(%d) 引用栈值类型 %s，应为 %s"
                        % (b.formal, b.actual.index, tv.iec_type, b.type))
                values[b.formal] = tv
            del ctx.stack[len(ctx.stack) - k:]
        for b in bindings:
            if b.mode != "IN" or isinstance(b.actual, StackSlot):
                continue
            if isinstance(b.actual, Const):
                # WP-005：① Const 自带类型标签必须与绑定类型一致；② 原始
                # Const.value 先过结构检查，再 on_const，再复检（装载后被
                # 篡改的 Const(1,"INT")→REAL 形参不得被 F1 量化洗白）
                if b.actual.type != b.type:
                    raise IRExecutionError(
                        ctx.pou.name, ctx.where, pc, instr,
                        "IN 形参 '%s'：Const 类型标签 %s 与绑定类型 %s 不一致"
                        "（装载后篡改防御）" % (b.formal, b.actual.type, b.type))
                value = self._checked_boundary(
                    ctx, pc, instr, b.actual.value, b.type,
                    self.mode.on_const, "IN 形参 '%s' 的 Const 实参" % b.formal)
                values[b.formal] = TypedValue(value, b.type)
            elif isinstance(b.actual, StoreKey):
                # WP-005 Round 2：① 目标位置声明类型必须与绑定类型一致
                #（装载后把 REAL 位置篡改为 INT 位置不得被静默接受；
                # declared=None 仅见于 L2 未接入的库块管脚，诚实跳过）；
                # ② 读得的原始值先过结构检查再进 TypedValue，后续 on_store
                # 不得把结构性错误的值洗白
                loc = self._resolve(ctx, b.actual.key, pc, instr)
                declared = loc.declared_type()
                if declared is not None and declared != b.type:
                    raise IRExecutionError(
                        ctx.pou.name, ctx.where, pc, instr,
                        "IN 形参 '%s'：StoreKey '%s' 声明类型 %s 与绑定类型 "
                        "%s 不一致（装载后篡改防御）"
                        % (b.formal, b.actual.key, declared, b.type))
                raw = loc.read()
                if isinstance(loc, _PinLoc):
                    # WP-005 Round 3（IR_SPEC §5.3 边界 5）：StoreKey 指向
                    # 库块管脚时同属输出回收边界，F1 下 REAL 先量化再进入
                    # TypedValue 世界（当前下游 FUNCTION 播种/FB 拷入的
                    # on_store 也会量化、结果幂等，此处补齐是边界完备性：
                    # 不依赖"下游会再量化"）
                    values[b.formal] = TypedValue(self._checked_boundary(
                        ctx, pc, instr, raw, b.type, self._pin_recover_hook,
                        "IN 形参 '%s' 的库块管脚回收值" % b.formal), b.type)
                    continue
                if not check_value_type(b.type, raw):
                    raise IRExecutionError(
                        ctx.pou.name, ctx.where, pc, instr,
                        "IN 形参 '%s'：StoreKey '%s' 原始值 %r 与绑定类型 %s "
                        "结构不匹配（检查先于数值钩子，不做隐式转换）"
                        % (b.formal, b.actual.key, raw, b.type))
                values[b.formal] = TypedValue(raw, b.type)
            else:
                raise IRExecutionError(ctx.pou.name, ctx.where, pc, instr,
                                       "IN 形参 '%s' 的 actual 形态未知 %r"
                                       % (b.formal, b.actual))
        return values

    def _bind_aliases(self, caller: _Ctx, callee: _Ctx, bindings, pc, instr):
        """INOUT：把调用方位置对象本身作为别名传入被调（真别名）。"""
        for b in bindings:
            if b.mode != "INOUT":
                continue
            if not isinstance(b.actual, StoreKey):
                raise IRExecutionError(
                    caller.pou.name, caller.where, pc, instr,
                    "INOUT 形参 '%s' 必须绑定 StoreKey（IR_SPEC §5.2）" % b.formal)
            callee.aliases[b.formal] = self._resolve(caller, b.actual.key,
                                                     pc, instr)

    def _write_out_bindings(self, caller: _Ctx, bindings, out_reader,
                            pc, instr):
        """OUT：被调执行完成后按声明类型写回调用方 StoreKey 位置。"""
        for b in bindings:
            if b.mode != "OUT":
                continue
            if not isinstance(b.actual, StoreKey):
                raise IRExecutionError(
                    caller.pou.name, caller.where, pc, instr,
                    "OUT 形参 '%s' 当前仅支持 StoreKey 写回（OUT→StackSlot "
                    "由 loader 保守拒绝，本包不放开）" % b.formal)
            loc = self._resolve(caller, b.actual.key, pc, instr)
            # 运行期防御：写回目标位置的声明类型必须与绑定类型一致，装载后
            # 被篡改的调用方声明不得被静默写入（declared=None 仅见于 L2 未
            # 接入的库块管脚，类型未知时诚实跳过核对，交由 _CellLoc/Store
            # 的结构性检查兜底）
            declared = loc.declared_type()
            if declared is not None and declared != b.type:
                raise IRExecutionError(
                    caller.pou.name, caller.where, pc, instr,
                    "OUT 形参 '%s' 写回目标 '%s' 声明类型 %s 与绑定类型 %s "
                    "不一致" % (b.formal, b.actual.key, declared, b.type))
            # WP-005 Round 2：写回值同走共享边界通道（原始值结构检查先于
            # on_store）——被调 cell/Store 值虽在写入侧受守，边界自身复检
            loc.write(self._checked_boundary(
                caller, pc, instr, out_reader(b.formal), b.type,
                self.mode.on_store, "OUT 形参 '%s' 写回值" % b.formal))

    def _seed_cell(self, ctx: _Ctx, callee: _Ctx, pou_name: str, d: VarDecl,
                   value, pc, ins) -> None:
        """FUNCTION frame cell 播种（含结构性类型检查，装载后篡改防御）。

        WP-005 Round 2 起，须经数值钩子的播种值（VAR_INPUT 拷入）在调用点
        先走 ``_checked_boundary``（原始值检查先于 ``on_store``），本方法
        只对最终播种值做同口径复检（``check_value_type``，与
        ``Store.write`` / ``_CellLoc.write`` 一致，不做隐式转换）——声明
        初值等无钩子路径的检查也落在这里。"""
        if not check_value_type(d.iec_type, value):
            raise IRExecutionError(
                ctx.pou.name, ctx.where, pc, ins,
                "FUNCTION '%s' frame 变量 '%s' 播种值 %r 与声明类型 %s 不匹配"
                "（结构性检查，不做隐式转换；装载后篡改防御）"
                % (pou_name, d.name, value, d.iec_type))
        callee.cells[d.name] = [d.iec_type, value]

    # ------------------------------------------------------------------
    # CALL_FUNC
    # ------------------------------------------------------------------

    def _call_func(self, ctx: _Ctx, ins: CallFunc, pc) -> None:
        target = self.task.pou_lib.get(ins.name)
        if not isinstance(target, POUDefinition) \
                or target.pou_kind != "FUNCTION":
            raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                   "CALL_FUNC 目标 '%s' 不存在或非 FUNCTION"
                                   % ins.name)
        # 运行期防御：指令声称的返回类型必须与 FUNCTION 定义一致，不只信
        # loader——装载后被篡改的 ret_type 不得把错误类型的值压回调用方栈
        if ins.ret_type != (target.return_type or ""):
            raise IRExecutionError(
                ctx.pou.name, ctx.where, pc, ins,
                "CALL_FUNC 'ret_type'=%s 与 FUNCTION '%s' 定义返回类型 %s "
                "不一致" % (ins.ret_type, target.name, target.return_type))
        in_values = self._eval_in_bindings(ctx, ins.bindings, pc, ins)

        self._frame_seq += 1
        frame_id = "%s#%d" % (target.name, self._frame_seq)
        callee = self._make_ctx(target, None, None, where=frame_id)
        callee.prev = None            # FUNCTION frame 无上一拍语义
        # frame cells：VAR_INPUT（拷入）/ VAR_OUTPUT / VAR（每次调用按声明
        # 初值或类型默认重建）；INOUT 走别名。
        # 播种阶段运行期防御（Codex Round 2）：绑定值/声明初值先过结构性
        # 类型检查再入 frame——装载后被篡改的绑定或声明不得把错误 Python
        # 类型的值静默播入；检查作用于 on_store **之前**的原始值，F1 量化/
        # 回绕不得把被篡改的值"洗"成合法形态。
        for d in target.interface:
            if not isinstance(d, VarDecl):
                continue
            if d.section == "VAR_INPUT":
                tv = in_values.get(d.name)
                if tv is None:
                    raise IRExecutionError(
                        ctx.pou.name, ctx.where, pc, ins,
                        "FUNCTION '%s' 形参 '%s' 无 IN 绑定值（loader 应已拒绝）"
                        % (target.name, d.name))
                if tv.iec_type != d.iec_type:
                    raise IRExecutionError(
                        ctx.pou.name, ctx.where, pc, ins,
                        "FUNCTION '%s' 形参 '%s' 绑定类型 %s 与声明类型 %s "
                        "不一致（播种拒绝，不只信 loader）"
                        % (target.name, d.name, tv.iec_type, d.iec_type))
                # WP-005 Round 2：播种同样走共享边界通道——原始值结构检查
                # **先于** on_store（此前是先求 on_store 再把 raw 交给
                # _seed_cell 复检，顺序与本包声明的固定顺序不一致）
                value = self._checked_boundary(
                    ctx, pc, ins, tv.value, d.iec_type, self.mode.on_store,
                    "FUNCTION '%s' 形参 '%s' 播种值" % (target.name, d.name))
                self._seed_cell(ctx, callee, target.name, d, value, pc, ins)
            elif d.section == "VAR_OUTPUT":
                self._seed_cell(ctx, callee, target.name, d, _initial_of(d),
                                pc, ins)
        for d in target.locals:
            if isinstance(d, VarDecl) and d.section == "VAR":
                self._seed_cell(ctx, callee, target.name, d, _initial_of(d),
                                pc, ins)
        self._bind_aliases(ctx, callee, ins.bindings, pc, ins)

        self._active_frames.append(frame_id)
        try:
            self._run(callee)
            if len(callee.stack) != 1 \
                    or callee.stack[0].iec_type != (target.return_type or ""):
                raise IRExecutionError(
                    target.name, frame_id, len(target.code or []), None,
                    "FUNCTION 正常出口栈应恰为 [%s]，实为 %s"
                    % (target.return_type,
                       [tv.iec_type for tv in callee.stack]))
            ret = callee.stack.pop()
            self._write_out_bindings(
                ctx, ins.bindings,
                lambda formal: callee.cells[formal][1], pc, ins)
        finally:
            self._active_frames.pop()      # 异常路径同样出栈，frame 引用随作用域销毁

        # FUNCTION 返回边界（WP-005 收紧）：在 on_store 之前——
        # ① 出口栈恰一元素（上方已查）；② 返回值类型标签 = 定义返回类型
        #   （上方已查）且 = CallFunc.ret_type（调用前已查两者一致，此处
        #   再直接核对一次，防御中间态篡改）；③ 返回值的 Python 结构类型
        #   必须符合该 IEC 类型——被调体制造的"标签正确、结构错误"的
        #   TypedValue 不得经 on_store 洗白后返回调用方。
        if ret.iec_type != ins.ret_type:
            raise IRExecutionError(
                ctx.pou.name, ctx.where, pc, ins,
                "FUNCTION '%s' 返回值类型标签 %s 与 CallFunc.ret_type %s 不一致"
                % (target.name, ret.iec_type, ins.ret_type))
        value = self._checked_boundary(
            ctx, pc, ins, ret.value, ins.ret_type, self.mode.on_store,
            "FUNCTION '%s' 返回值" % target.name)
        ctx.stack.append(TypedValue(value, ins.ret_type))
        return None

    # ------------------------------------------------------------------
    # CALL_FB_INSTANCE
    # ------------------------------------------------------------------

    def _call_fb_instance(self, ctx: _Ctx, ins: CallFbInstance, pc) -> None:
        if ctx.path is None:
            raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                   "FUNCTION frame 内不可调用 FB 实例"
                                   "（FUNCTION 无实例，loader 应已拒绝）")
        full = "%s.%s" % (ctx.path, ins.instance_path)
        if full not in self._fb_paths:
            raise IRExecutionError(
                ctx.pou.name, ctx.where, pc, ins,
                "实例路径 '%s' 未由装载期展开（调用绝不创建实例，IR_SPEC §3）"
                % full)
        # 定位定义：沿相对路径逐段下钻
        target = self._fb_def_for(ctx, ins.instance_path, pc, ins)

        in_values = self._eval_in_bindings(ctx, ins.bindings, pc, ins)
        callee = self._make_ctx(target, full, ctx.prev, where=full)
        self._bind_aliases(ctx, callee, ins.bindings, pc, ins)
        # IN 拷入持久 VAR_INPUT（既有 Store 键；绝不新建）。
        # WP-005 Round 2：拷入前先核对目标持久键声明类型与绑定类型一致，
        # 再走共享边界通道（原始值结构检查先于 on_store）——装载后被篡改
        # 的绑定不得借 F1 量化把错误结构的值洗进 FB 持久 VAR_INPUT
        for formal, tv in in_values.items():
            key = persistent_key(full, formal)
            declared = self.store.declared_type(key)
            if declared != tv.iec_type:
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, ins,
                    "FB IN 形参 '%s'：绑定类型 %s 与持久键 '%s' 声明类型 %s "
                    "不一致（装载后篡改防御）"
                    % (formal, tv.iec_type, key, declared))
            self.store.write(key, self._checked_boundary(
                ctx, pc, ins, tv.value, tv.iec_type, self.mode.on_store,
                "FB IN 形参 '%s' 拷入值" % formal))

        self._active_frames.append(full)
        try:
            self._run(callee)
            if callee.stack:
                raise IRExecutionError(
                    target.name, full, len(target.code or []), None,
                    "FB 正常出口栈应为空，实为 %d 项" % len(callee.stack))
            self._write_out_bindings(
                ctx, ins.bindings,
                lambda formal: self.store.read(persistent_key(full, formal)),
                pc, ins)
        finally:
            self._active_frames.pop()
        return None

    def _fb_def_for(self, ctx: _Ctx, rel_path: str, pc, ins) -> POUDefinition:
        segments = rel_path.split(".")
        instances = ctx.instances
        target = None
        for seg in segments:
            decl = instances.get(seg)
            if decl is None or decl.kind != "user_fb":
                raise IRExecutionError(
                    ctx.pou.name, ctx.where, pc, ins,
                    "实例路径段 '%s'（于 '%s'）不是已声明的用户 FB 实例"
                    % (seg, rel_path))
            target = self.task.pou_lib.get(decl.block_type)
            if not isinstance(target, POUDefinition):
                raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                       "FB 定义 '%s' 不存在" % decl.block_type)
            instances = {d.name: d for d in target.instances
                         if isinstance(d, InstanceDecl)}
        return target

    # ------------------------------------------------------------------
    # CALL_STD / CALL_FB（注入边界）
    # ------------------------------------------------------------------

    def _call_std(self, ctx: _Ctx, ins: CallStd, pc) -> None:
        fn = self._std.get(ins.name)
        if fn is None:
            raise MissingStdFunctionError(
                ctx.pou.name, ctx.where, pc, ins,
                "标准函数 '%s' 无注入实现（本包不建完整 IEC 函数库）" % ins.name)
        args = []
        for t in reversed(ins.sig.param_types):
            args.append(self._pop(ctx, t, pc, ins, "CALL_STD 实参"))
        args.reverse()
        try:
            result = fn(*(tv.value for tv in args))
        except Exception as e:            # noqa: BLE001 —— 带上下文原样上抛
            raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                   "标准函数 '%s' 执行异常" % ins.name, cause=e)
        ret_t = ins.sig.return_type
        # WP-005 统一走共享边界通道：原始返回值结构检查（先于 on_result）
        # → 数值钩子 → 结果复检——与 LoadConst/Const 绑定/FUNCTION 返回同规则
        value = self._checked_boundary(
            ctx, pc, ins, result, ret_t, self.mode.on_result,
            "标准函数 '%s' 返回值" % ins.name)
        ctx.stack.append(TypedValue(value, ret_t))
        return None

    def _call_fb(self, ctx: _Ctx, ins: CallFb, pc) -> None:
        inst = ctx.instances.get(ins.instance)
        if inst is None or inst.kind != "library":
            raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                   "CALL_FB '%s' 不是已声明的库块实例"
                                   % ins.instance)
        full = "%s.%s" % (ctx.path, ins.instance)
        adapter = self._adapters.get(full)
        if adapter is None:
            raise MissingLibraryAdapterError(
                ctx.pou.name, ctx.where, pc, ins,
                "库块实例 '%s' 缺少注入的 library adapter（正式 L2 "
                "BlockSchema/RuntimeAdapter 注册表属独立工作包）" % full)
        try:
            adapter.step(self.task.cycle_ms)
        except Exception as e:            # noqa: BLE001 —— 不吞，带上下文上抛
            raise IRExecutionError(ctx.pou.name, ctx.where, pc, ins,
                                   "库块 adapter '%s' step 异常" % full, cause=e)
        return None


def _initial_of(d: VarDecl):
    """FUNCTION frame 变量的每次调用初值：声明 initial 优先，否则类型默认。"""
    return d.initial if d.initial is not None else default_value(d.iec_type)
