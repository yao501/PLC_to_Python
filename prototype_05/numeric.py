"""数值模式（E / F1-expr）与类型工具。

对应规格：IR_SPEC §5.3/§5.4/§8、TARGET_PROFILE §3/§4。
- E（engineering，默认）：Python float64 / int 不回绕，无量化。
- F1-expr：REAL 逐指令量化到 binary32（§5.3 全部边界）；整数在 STORE_VAR/CONVERT
  按声明类型截断（保证发生），BINOP/UNOP 出口按 int_intermediate_policy 决定
  （native_width=默认假设 / declared_width=逐步截断）——哪种匹配真机待黄金轨迹 #7 裁决。
- 有符号中间溢出按 native 位宽二进制补码回绕实现，属**待真机验证假设**（TARGET_PROFILE §3）。
- 模式是装载期配置，禁止热切换（IR_SPEC §8）：本原型每个 Engine 实例绑定一个模式，无切换接口。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

# IEC 整数族：类型 -> (位宽, 是否有符号)
INT_TYPES = {
    "SINT": (8, True), "INT": (16, True), "DINT": (32, True), "LINT": (64, True),
    "USINT": (8, False), "UINT": (16, False), "UDINT": (32, False), "ULINT": (64, False),
    "BYTE": (8, False), "WORD": (16, False), "DWORD": (32, False), "LWORD": (64, False),
}
REAL_TYPES = {"REAL", "LREAL"}
# TIME 项目内统一 int ms（00a R1），不参与回绕语义
SCALAR_TYPES = set(INT_TYPES) | REAL_TYPES | {"BOOL", "TIME", "STRING"}


def is_int_type(t: str) -> bool:
    return t in INT_TYPES


def wrap_int(value: int, bits: int, signed: bool) -> int:
    """模 2^bits 回绕；有符号按二进制补码表示解释。"""
    m = 1 << bits
    v = value % m
    if signed and v >= (1 << (bits - 1)):
        v -= m
    return v


def quantize_real32(v: float) -> float:
    """舍入到 IEEE 754 binary32 可编码值（round-to-nearest-even，由硬件转换保证）。"""
    return struct.unpack("<f", struct.pack("<f", v))[0]


def is_binary32_encodable(v: float) -> bool:
    return quantize_real32(v) == v


def default_value(iec_type: str):
    if iec_type == "BOOL":
        return False
    if iec_type in REAL_TYPES:
        return 0.0
    if is_int_type(iec_type) or iec_type == "TIME":
        return 0
    if iec_type == "STRING":
        return ""
    raise ValueError(f"未知 IEC 类型: {iec_type}")


@dataclass(frozen=True)
class NumericMode:
    """装载期数值模式配置（ScanContext.mode 的原型化身）。"""

    mode: str = "engineering"              # "engineering" / "fidelity_f1"
    int_native_width: int = 64             # 目标原生位宽（⬜ 待 TARGET_PROFILE 锁定 CPU）
    int_intermediate_policy: str = "native_width"  # "native_width" / "declared_width"

    def __post_init__(self):
        assert self.mode in ("engineering", "fidelity_f1")
        assert self.int_native_width in (32, 64)
        assert self.int_intermediate_policy in ("native_width", "declared_width")

    @property
    def is_fidelity(self) -> bool:
        return self.mode == "fidelity_f1"

    def on_store(self, value, iec_type: str):
        """STORE_VAR / CONVERT 出口 / 管脚边界（IR_SPEC §5.3 边界 1/3/4/5/6 + §5.4 保证截断点）。"""
        if not self.is_fidelity:
            return value
        if iec_type == "REAL":
            return quantize_real32(value)
        if is_int_type(iec_type):
            bits, signed = INT_TYPES[iec_type]
            return wrap_int(value, bits, signed)
        return value

    # LOAD_CONST（§5.3 边界 1）：常量按类型编码，与 on_store 同规则
    on_const = on_store

    def on_result(self, value, iec_type: str):
        """BINOP/UNOP/CALL_STD 出口（IR_SPEC §5.3 边界 2 + §5.4 中间位宽政策）。"""
        if not self.is_fidelity:
            return value
        if iec_type == "REAL":
            return quantize_real32(value)
        if is_int_type(iec_type):
            if self.int_intermediate_policy == "declared_width":
                bits, signed = INT_TYPES[iec_type]
                return wrap_int(value, bits, signed)
            # native_width：中间结果按原生位宽回绕（有符号补码，待真机裁决假设）
            return wrap_int(value, self.int_native_width, True)
        return value
