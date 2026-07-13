"""一机一码哈希核心（``BD_ZCM`` / ``BD_MMYZ`` 共用的唯一实现）。

对应 CODESYS ST 对象：``BD_ZCM`` 的注册码生成段（FOR 循环 + 最终 MOD）
与 ``BD_MMYZ`` 内部"重新计算 ZCM + 推算 CheckMM"段。

**为什么抽成共享纯函数**（见任务书 §14.3）：源 ST 中 ``BD_ZCM`` 与
``BD_MMYZ`` 各写了一遍**完全相同**的四路哈希；若 Python 也复制两份，任何一处
改动都会让"用户看到的注册码"与"内部校验用的注册码"漂移，导致授权全盘失效。
故此处只保留一份严格的 ``DWORD`` 算法，二者都调用它；但二者的**外层执行顺序、
错误码、状态行为**仍各自保留在各自模块（``bd_zcm.py`` / ``bd_mmyz.py``）。

字节语义（见任务书 §5、``docs/RISKS.md::LIC-PLATFORM-1``）：

* ``SerialText`` 先按 **Latin-1** 编码为字节序列；每个字节值 ∈ ``[0, 255]``
  即 ST 的 ``CharValue := BYTE_TO_DWORD(SerialText[i])``。
* 索引 **1-based**，严格对应 ST ``FOR i := 1 TO nLen DO``。

所有标注 ``DWORD`` 的中间步骤按 32 位无符号回绕（``& DWORD_MASK``）。
"""

from __future__ import annotations

from .dword import DWORD_MASK

_M = DWORD_MASK

# 哈希初值（对应 ST DWORD#16#... 字面量）。
_HASH1_INIT = 0x811C9DC5
_HASH2_INIT = 0xA5A5A5A5
_HASH3_INIT = 0x5A5A5A5A
_HASH4_INIT = 0x9E3779B1


def serial_text_to_char_values(serial_text: str) -> list[int]:
    """把 ``SerialText`` 规范化为 1-based 遍历用的字节值列表（Latin-1）。

    与 ST ``BYTE_TO_DWORD(SerialText[i])`` 对齐：返回的列表元素就是各字节值，
    调用方以 ``enumerate(..., start=1)`` 还原 ST 的 ``i := 1 TO nLen``。

    若 ``serial_text`` 不能用 Latin-1 编码，抛 :class:`UnicodeEncodeError`
    （由上层 :class:`~src.licensing.xtxx.XTXX` 在读取阶段拦截为序列号不可用）。
    """
    return list(serial_text.encode("latin-1"))


def compute_registration_codes(serial_text: str) -> tuple[int, int, int]:
    """根据 ``SerialText`` 计算 ``(ZCM1, ZCM2, ZCM3)``，每个 ∈ ``[0, 9999]``。

    严格复刻 ST::

        Hash1 := (Hash1 XOR CharValue) * 16777619
        Hash2 := (Hash2 XOR (CharValue + i*131)) * 16777619
        Hash3 := ((Hash3 + CharValue + i*17) * 1103515245) + 12345
        Hash4 := (Hash4 XOR (Hash1 + Hash2 + Hash3 + CharValue)) * 0x9E3779B1
        ...
        ZCM1 := ((Hash1 XOR Hash3) + 1359) MOD 10000
        ZCM2 := ((Hash2 XOR Hash4) + 2671) MOD 10000
        ZCM3 := ((Hash1 + Hash2 + Hash3 + Hash4) + 8273) MOD 10000

    注意 ``MOD 10000`` 之前的加法仍是 ``DWORD`` 加法（先回绕 32 位再取模）。
    """
    h1 = _HASH1_INIT
    h2 = _HASH2_INIT
    h3 = _HASH3_INIT
    h4 = _HASH4_INIT

    for i, cv in enumerate(serial_text_to_char_values(serial_text), start=1):
        h1 = ((h1 ^ cv) * 16777619) & _M
        h2 = ((h2 ^ ((cv + (i * 131)) & _M)) * 16777619) & _M
        h3 = ((((h3 + cv + (i * 17)) & _M) * 1103515245) + 12345) & _M
        h4 = ((h4 ^ ((h1 + h2 + h3 + cv) & _M)) * 0x9E3779B1) & _M

    zcm1 = (((h1 ^ h3) + 1359) & _M) % 10000
    zcm2 = (((h2 ^ h4) + 2671) & _M) % 10000
    zcm3 = (((h1 + h2 + h3 + h4) + 8273) & _M) % 10000
    return zcm1, zcm2, zcm3


def compute_check_passwords(
    zcm1: int, zcm2: int, zcm3: int
) -> tuple[int, int, int, int]:
    """根据注册码计算四个正确密码 ``(CheckMM1..4)``，每个 ∈ ``[0, 9999]``。

    严格复刻 ST::

        CheckMM1 := ((((ZCM1+ZCM2) MOD 10000) * 6219) XOR (ZCM3+1234)) MOD 10000
        CheckMM2 := ((((ZCM2+ZCM3) MOD 10000) * 4157) XOR (ZCM1+5678)) MOD 10000
        CheckMM3 := ((((ZCM3+ZCM1) MOD 10000) * 2957) XOR (ZCM2+9012)) MOD 10000
        CheckMM4 := ((ZCM1*1739) XOR (ZCM2*2791) XOR (ZCM3*3571) XOR 4321) MOD 10000

    输入 ``ZCM ∈ [0, 9999]``，所有中间乘积（最大 ``9999*3571 ≈ 3.57e7``）均 **远
    小于 2**32**，不会发生 32 位回绕，因此此处不套 ``& DWORD_MASK``（遵循
    "只在需要处回绕"原则，见 ``dword`` docstring）。该判断由本函数的注释与
    测试共同锁定。
    """
    check1 = ((((zcm1 + zcm2) % 10000) * 6219) ^ (zcm3 + 1234)) % 10000
    check2 = ((((zcm2 + zcm3) % 10000) * 4157) ^ (zcm1 + 5678)) % 10000
    check3 = ((((zcm3 + zcm1) % 10000) * 2957) ^ (zcm2 + 9012)) % 10000
    check4 = ((zcm1 * 1739) ^ (zcm2 * 2791) ^ (zcm3 * 3571) ^ 4321) % 10000
    return check1, check2, check3, check4
