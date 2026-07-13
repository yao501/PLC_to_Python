"""Python 端注册码 → 密码发放辅助（授权工具侧，非 PLC 业务块）。

原 ST 注释（``BD_MMYZ .txt`` 第 74~77 行）说明：``BD_ZCM`` 把 ``ZCM1~ZCM3``
展示给用户，外部授权工具据此生成 ``MM1~MM4`` 密码，写回 ``BD_MM1~BD_MM4``。

本模块就是那个"外部授权工具"在 Python 端的最小实现：它**只**复用
``BD_MMYZ`` 内部已经出现的四个 ``CheckMM`` 公式
（:func:`~src.licensing.hashcore.compute_check_passwords`），**不**新增任何
原 ST 没有的业务逻辑，也**不**伪装成原 ST 的功能块。

典型链路（也是单元测试要锁定的链路）::

    BD_ZCM(EN=True) → (ZCM1, ZCM2, ZCM3)
        → derive_passwords_from_registration_codes(...)
        → 写入 BD_MM1~4
        → BD_MMYZ() 返回 0
"""

from __future__ import annotations

from typing import Tuple

from .hashcore import compute_check_passwords


def derive_passwords_from_registration_codes(
    zcm1: int, zcm2: int, zcm3: int
) -> Tuple[int, int, int, int]:
    """由注册码 ``(ZCM1, ZCM2, ZCM3)`` 推算四个正确密码 ``(MM1, MM2, MM3, MM4)``。

    返回的四元组可直接写入 ``LicenseContext.BD_MM1~BD_MM4``。结果与
    ``BD_MMYZ`` 内部 ``CheckMM1~4`` 公式一致，因此写回后 ``BD_MMYZ()`` 在
    同一台机器上返回 0。
    """
    return compute_check_passwords(zcm1, zcm2, zcm3)
