"""授权模块（阶段 1：一机一码 + 关键功能块门控）。

迁移自 CODESYS SoftPLC 授权代码（``XTXX`` / ``BD_ZCM`` / ``BD_MMYZ`` /
``BD_MMYZ_ST``），并对 PC / 服务器平台做机器标识适配。

**阶段定位（重要）**：本模块的注册码与密码算法是**可逆 / 可复现的确定性
算法**，用于"一机一码 + 复制阻断 + 关键模块门控"的原有业务结构，**不**构成
强密码学保护。离线签名 / 公私钥 / License 文件 / 联网验证属阶段 2
（见 ``docs/RISKS.md::LIC-SEC-LEGACY-1`` 与阶段 2 待办条目）。
"""

from __future__ import annotations

from .bd_mmyz import BD_MMYZ
from .bd_mmyz_st import BD_MMYZ_ST
from .bd_zcm import BD_ZCM
from .dword import (
    DWORD_MASK,
    WORD_MASK,
    dword_add,
    dword_mul,
    dword_xor,
    to_dword,
    to_word,
)
from .hashcore import compute_check_passwords, compute_registration_codes
from .issuer import derive_passwords_from_registration_codes
from .providers import (
    DateTimeProvider,
    ManualDateTimeProvider,
    PlatformSerialTextProvider,
    SerialReadResult,
    SerialTextProvider,
    StaticSerialTextProvider,
)
from .xtxx import XTXX

__all__ = [
    "BD_MMYZ",
    "BD_MMYZ_ST",
    "BD_ZCM",
    "XTXX",
    "DWORD_MASK",
    "WORD_MASK",
    "to_dword",
    "to_word",
    "dword_add",
    "dword_mul",
    "dword_xor",
    "compute_registration_codes",
    "compute_check_passwords",
    "derive_passwords_from_registration_codes",
    "DateTimeProvider",
    "SerialTextProvider",
    "SerialReadResult",
    "StaticSerialTextProvider",
    "ManualDateTimeProvider",
    "PlatformSerialTextProvider",
]
