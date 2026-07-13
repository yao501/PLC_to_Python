"""授权模块 ``BD_MMYZ``（密码验证函数）的 Python 迁移。

对应 CODESYS ST 对象：``FUNCTION BD_MMYZ : WORD``（``BD_MMYZ .txt``）。

语义（``BD_MMYZ.txt`` 第 27~107 行，逐条保留）：

1. ``XTXX(EN := TRUE)`` 读取本机序列号。
2. ``IF NOT XTXX.SerialOK THEN BD_MMYZ := 9000; RETURN``。
3. 否则用与 ``BD_ZCM`` **完全一致**的哈希重新计算本机 ``ZCM1~ZCM3``，
   再推算四个正确密码 ``CheckMM1~4``。
4. 按 ST 顺序累计错误码：
   ``BD_MM1!=CheckMM1 → +1000``、``+100``、``+10``、``BD_MM4!=CheckMM4 → +1``。
5. 返回总错误码（``WORD``）。

**Python 适配**：原 ST 函数读取全局 ``BD_MM1~BD_MM4``。这里改为可调用对象，
构造时注入 ``password_getter``，且 **每次调用** 都通过它实时读取最新
``BD_MM1~BD_MM4``（任务书 §8）——**不**在构造时拷贝后永久缓存，确保用户更新
密码后下一次验证立即生效。
"""

from __future__ import annotations

from typing import Callable, Tuple

from .dword import to_word
from .hashcore import compute_check_passwords, compute_registration_codes
from .providers import SerialTextProvider
from .xtxx import XTXX

# 序列号不可用时的返回码（ST: BD_MMYZ := 9000）。
RESULT_SERIAL_UNAVAILABLE = 9000

PasswordGetter = Callable[[], Tuple[int, int, int, int]]


class BD_MMYZ:
    """密码验证可调用对象（语义对应 ST ``FUNCTION BD_MMYZ : WORD``）。

    用法::

        validator = BD_MMYZ(serial_provider, password_getter)
        result = validator()   # 返回 WORD 错误码（0 表示通过）

    其中 ``password_getter()`` 每次返回当前 ``(BD_MM1, BD_MM2, BD_MM3, BD_MM4)``。
    """

    def __init__(
        self,
        serial_provider: SerialTextProvider,
        password_getter: PasswordGetter,
    ) -> None:
        self._xtxx = XTXX(serial_provider)
        self._password_getter = password_getter

    def __call__(self) -> int:
        self._xtxx.step(True)

        if not self._xtxx.SerialOK:
            return RESULT_SERIAL_UNAVAILABLE

        zcm1, zcm2, zcm3 = compute_registration_codes(self._xtxx.SerialText)
        check1, check2, check3, check4 = compute_check_passwords(zcm1, zcm2, zcm3)

        mm1, mm2, mm3, mm4 = self._password_getter()

        error_code = 0
        if mm1 != check1:
            error_code += 1000
        if mm2 != check2:
            error_code += 100
        if mm3 != check3:
            error_code += 10
        if mm4 != check4:
            error_code += 1

        return to_word(error_code)
