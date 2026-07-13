"""PLC ``DWORD`` / ``WORD`` 无符号整数兼容辅助（授权模块最小闭包）。

承接 CODESYS ST 中授权代码所依赖的整数语义：

* ``DWORD``：32 位无符号，算术按 ``mod 2**32`` 回绕。
* ``WORD``：16 位无符号，算术按 ``mod 2**16`` 回绕。

设计原则（对齐 ``src/compat/conversions.py`` 风格）：

1. **纯函数、无状态**：集中承载 ST 整数边界语义，不让业务代码散落
   ``& 0xFFFFFFFF``。
2. **只在需要处回绕**：哈希算法里"会真正越过 32 位的乘法 / 加法"才调用
   :func:`to_dword`；对数学上不可能溢出 32 位的中间式（如 ``CheckMM`` 系列，
   其输入 ``ZCM ∈ [0, 9999]``）不强行套用（见 ``docs/RISKS.md::LIC-SEC-LEGACY-1``）。
3. **bool 不得被静默当作数值**：Python ``bool`` 是 ``int`` 子类，若放任会把
   ``True/False`` 当成 ``1/0`` 偷渡进密码 / 哈希输入。本模块所有入口显式拒绝
   ``bool``（对齐 ``int_to_real`` 的做法）。

本模块**不**实现任何加密 / 签名 / 联网逻辑（属阶段 2，见 RISKS）。
"""

from __future__ import annotations

DWORD_MASK = 0xFFFFFFFF
WORD_MASK = 0xFFFF


def _check_int(value: int, *, name: str) -> int:
    """拒绝 bool 与非 int 输入，返回原值。"""
    if isinstance(value, bool):
        raise TypeError(f"{name} does not accept bool (got {value!r})")
    if not isinstance(value, int):
        raise TypeError(f"{name} expects int, got {type(value).__name__}")
    return value


def to_dword(value: int) -> int:
    """把任意整数规约到 32 位无符号 ``DWORD`` 值域 ``[0, 2**32-1]``。"""
    _check_int(value, name="to_dword")
    return value & DWORD_MASK


def to_word(value: int) -> int:
    """把任意整数规约到 16 位无符号 ``WORD`` 值域 ``[0, 2**16-1]``。"""
    _check_int(value, name="to_word")
    return value & WORD_MASK


def dword_add(*values: int) -> int:
    """多个值按 ``DWORD`` 语义相加（结果回绕到 32 位无符号）。"""
    total = 0
    for v in values:
        total += to_dword(v)
    return total & DWORD_MASK


def dword_mul(a: int, b: int) -> int:
    """两个值按 ``DWORD`` 语义相乘（结果回绕到 32 位无符号）。"""
    return (to_dword(a) * to_dword(b)) & DWORD_MASK


def dword_xor(a: int, b: int) -> int:
    """两个值按 ``DWORD`` 语义异或（输入先规约到 32 位无符号）。"""
    return (to_dword(a) ^ to_dword(b)) & DWORD_MASK
