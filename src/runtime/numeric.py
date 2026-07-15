"""正式运行时数值执行策略（WP-20260714-004；IR_SPEC §5.3/§5.4/§8、TARGET_PROFILE §3/§4）。

模式（装载期绑定，**禁止运行中热切换**——本模块与 Executor 均不提供切换接口）：

- **engineering（E，默认）**：Python float64 / int，整数中间结果**不回绕**，
  无量化；不做任何隐式类型提升——一切提升必须来自显式 ``Convert``。
- **fidelity_f1（F1-expr）**：REAL 在 IR_SPEC §5.3 的全部边界量化为 IEEE 754
  binary32；整数按 ``int_native_width``（32|64）与 ``int_intermediate_policy``
  （``native_width`` / ``declared_width``）处理，Store/Convert 按声明类型截断。
- **fidelity_f2**：本包**不支持**——构造 ``NumericMode(mode="fidelity_f2")``
  明确抛 ``UnsupportedNumericModeError``（缺少 F2 块变体与位级语义实现），
  绝不静默降级到 E/F1。

**诚实边界（必须区分候选行为与已证实事实）**：

- ``int_native_width=64`` 来自样本工程（Control Win V3 x64）——**候选值**，
  真机对拍前不作定论，保持可配置（TARGET_PROFILE §2）。
- 有符号中间溢出按 native/declared 位宽二进制补码回绕、越界 CONVERT 截断，
  均为**当前候选行为 / 待真机验证假设**（TARGET_PROFILE §3、IR_SPEC §5.4
  ``int_overflow_convert_policy=TBD``），黄金轨迹 #7 裁决前不得表述为
  CODESYS 已证实语义。
- F1 只保证表示层（值 binary32 可编码）与当前候选策略，**不承诺 bit-exact**
  （TARGET_PROFILE §4.1）。
- 位串/整数的按位 NOT/AND/OR/XOR 依类型位宽解释（位运算天然依赖位宽，两种
  模式行为一致）——这是**项目工程约定**的确定性实现，非官方语义结论。
- 不支持或尚未裁决的转换组合一律抛 ``UnsupportedConversionError``，
  不得用 Python ``bool()``/``int()``/``str()`` 猜测 IEC 语义。

REAL_TO_INT / INT_TO_REAL 分别走 ``src.compat.conversions.real_to_int``
（银行家舍入，NaN/Inf 拒绝）与 ``int_to_real``——转换策略集中一处。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from src.compat.conversions import int_to_real, real_to_int
from src.runtime.ir import BIT_TYPES, REAL_TYPES, SIGNED_INT_TYPES, UNSIGNED_INT_TYPES

# 类型 -> (位宽, 是否有符号)
INT_WIDTHS = {
    "SINT": (8, True), "INT": (16, True), "DINT": (32, True), "LINT": (64, True),
    "USINT": (8, False), "UINT": (16, False), "UDINT": (32, False), "ULINT": (64, False),
    "BYTE": (8, False), "WORD": (16, False), "DWORD": (32, False), "LWORD": (64, False),
}
_INT_FAMILY = frozenset(INT_WIDTHS)
assert _INT_FAMILY == (SIGNED_INT_TYPES | UNSIGNED_INT_TYPES | BIT_TYPES)


class NumericError(Exception):
    """数值执行层错误基类。"""


class UnsupportedNumericModeError(NumericError):
    """不支持的数值模式（如 F2：缺块级 float32 变体与位级语义实现）。"""


class UnsupportedConversionError(NumericError):
    """不支持或尚未裁决的 CONVERT 组合——显式拒绝，不猜测。"""


class IECMathError(NumericError):
    """运算域错误（如除零）——显式传播，不静默兜底。"""


def is_int_type(t: str) -> bool:
    return t in _INT_FAMILY


def wrap_int(value: int, bits: int, signed: bool) -> int:
    """模 2^bits 回绕；有符号按二进制补码解释（候选行为，待真机裁决）。"""
    m = 1 << bits
    v = value % m
    if signed and v >= (1 << (bits - 1)):
        v -= m
    return v


def quantize_real32(v: float) -> float:
    """舍入到 IEEE 754 binary32 可编码值（round-to-nearest-even）。"""
    return struct.unpack("<f", struct.pack("<f", v))[0]


def default_value(iec_type: str):
    """各 IEC 类型的工程默认初值（VAR_TEMP 清零 / frame 初始化用）。"""
    if iec_type == "BOOL":
        return False
    if iec_type in REAL_TYPES:
        return 0.0
    if is_int_type(iec_type) or iec_type == "TIME":
        return 0
    if iec_type == "STRING":
        return ""
    raise NumericError("未知 IEC 类型：%r" % (iec_type,))


def trunc_div(a: int, b: int) -> int:
    """IEC 整数 DIV：纯整数算法、商向零截断（不经 float）。除零抛 IECMathError。"""
    if b == 0:
        raise IECMathError("整数 DIV 除零")
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def iec_mod(a: int, b: int) -> int:
    """IEC MOD：余数符号随被除数（a - trunc_div(a,b)*b），不直接用 Python %。"""
    if b == 0:
        raise IECMathError("整数 MOD 除零")
    return a - trunc_div(a, b) * b


@dataclass(frozen=True)
class NumericMode:
    """装载期数值模式配置（构造时绑定，无热切换接口）。"""

    mode: str = "engineering"                       # "engineering" / "fidelity_f1"
    int_native_width: int = 64                      # 32|64；64 为样本工程候选值
    int_intermediate_policy: str = "native_width"   # "native_width" / "declared_width"

    def __post_init__(self):
        if self.mode == "fidelity_f2":
            raise UnsupportedNumericModeError(
                "fidelity_f2 尚未支持：缺少 F2 块级 float32 变体与位级语义实现"
                "（TARGET_PROFILE §4，需真机证明；不静默降级到 engineering/F1）")
        if self.mode not in ("engineering", "fidelity_f1"):
            raise UnsupportedNumericModeError("未知数值模式：%r" % (self.mode,))
        if self.int_native_width not in (32, 64):
            raise UnsupportedNumericModeError(
                "int_native_width 须为 32|64，得到 %r" % (self.int_native_width,))
        if self.int_intermediate_policy not in ("native_width", "declared_width"):
            raise UnsupportedNumericModeError(
                "int_intermediate_policy 须为 native_width|declared_width，得到 %r"
                % (self.int_intermediate_policy,))

    @property
    def is_fidelity(self) -> bool:
        return self.mode == "fidelity_f1"

    # ---- IR_SPEC §5.3 边界钩子 ----

    def on_store(self, value, iec_type: str):
        """STORE_VAR / CONVERT 出口 / 管脚与调用边界（§5.3 边界 1/3/4/5/6）。"""
        if not self.is_fidelity:
            return value
        if iec_type == "REAL":
            return quantize_real32(value)
        if is_int_type(iec_type):
            bits, signed = INT_WIDTHS[iec_type]
            return wrap_int(value, bits, signed)
        return value

    on_const = on_store       # LOAD_CONST（§5.3 边界 1）同规则

    def on_result(self, value, iec_type: str):
        """BINOP/UNOP/CALL_STD 出口（§5.3 边界 2 + §5.4 中间位宽政策）。"""
        if not self.is_fidelity:
            return value
        if iec_type == "REAL":
            return quantize_real32(value)
        if is_int_type(iec_type):
            if self.int_intermediate_policy == "declared_width":
                bits, signed = INT_WIDTHS[iec_type]
                return wrap_int(value, bits, signed)
            # native_width：中间结果按原生位宽有符号补码回绕（候选，待真机裁决）
            return wrap_int(value, self.int_native_width, True)
        return value

    # ---- 位运算（类型位宽解释；两模式一致的确定性工程约定） ----

    def bitwise_not(self, value, iec_type: str):
        if iec_type == "BOOL":
            return not value
        if is_int_type(iec_type):
            bits, signed = INT_WIDTHS[iec_type]
            mask = (1 << bits) - 1
            raw = (~value) & mask
            return wrap_int(raw, bits, signed)
        raise UnsupportedConversionError("NOT 不支持类型 %s" % iec_type)

    # ---- CONVERT（显式转换的唯一实现点；组合白名单，其余明确拒绝） ----

    def convert(self, value, from_type: str, to_type: str):
        """支持组合：整数族↔整数族、整数族→REAL/LREAL、REAL/LREAL→整数族、
        REAL↔LREAL、同类型恒等（TIME→TIME 等）。其余组合（含 BOOL/STRING/
        TIME 与他类互转）尚未裁决，显式拒绝。越界整数转换：E 模式保持原值
        （不回绕，IR_SPEC §8）；F1 按声明类型截断（候选行为，
        ``int_overflow_convert_policy=TBD``）。"""
        if from_type == to_type:
            return self.on_store(value, to_type)
        if is_int_type(from_type) and is_int_type(to_type):
            return self.on_store(value, to_type)
        if is_int_type(from_type) and to_type in REAL_TYPES:
            return self.on_store(int_to_real(value), to_type)
        if from_type in REAL_TYPES and is_int_type(to_type):
            return self.on_store(real_to_int(value), to_type)   # 银行家舍入；NaN/Inf 拒绝
        if from_type in REAL_TYPES and to_type in REAL_TYPES:
            return self.on_store(float(value), to_type)
        raise UnsupportedConversionError(
            "CONVERT %s -> %s 尚未裁决/不支持（不得用 Python 强转猜测 IEC 语义）"
            % (from_type, to_type))
