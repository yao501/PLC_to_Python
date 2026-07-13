"""授权模块 ``BD_ZCM``（注册码生成功能块）的 Python 迁移。

对应 CODESYS ST 功能块：``BD_ZCM``（``BD_ZCM.txt``）。

职责：``EN=True`` 时读取本机 ``SerialText``（经内嵌 :class:`XTXX`），用与
``BD_MMYZ`` **完全一致**的哈希核心（:func:`~src.licensing.hashcore.\
compute_registration_codes`）生成给用户展示的注册码 ``ZCM1~ZCM3``。

执行顺序严格保留 ST（``BD_ZCM.txt`` 第 29~83 行）：

1. ``XTXX(EN := TRUE)``；``SerialText := XTXX.SerialText``；``ERROR := 0``。
2. ``IF NOT XTXX.SerialOK THEN ERROR := ERROR + 1000``。
3. ``IF ERROR = 0`` → 计算 ``ZCM1~ZCM3``；``ELSE`` → 三组注册码清零。
4. ``EN=False`` 时整块不执行，保留上一拍输出（不清空）。

注册码生成只依赖 ``SerialText``（新版一机一码），不使用 PLCid/CPUtype/Version。
"""

from __future__ import annotations

from typing import TypedDict

from .hashcore import compute_registration_codes
from .providers import SerialTextProvider
from .xtxx import XTXX

# 序列号读取失败时叠加的错误码（ST: ERROR := ERROR + 1000）。
ERROR_SERIAL_UNAVAILABLE = 1000


class BDZCMOutput(TypedDict):
    ZCM1: int
    ZCM2: int
    ZCM3: int
    ERROR: int
    SerialText: str


class BD_ZCM:
    """注册码生成块。携带跨周期状态（输出 RETAIN 语义）。

    公开接口::

        step(EN: bool) -> BDZCMOutput

    构造时注入机器标识 Provider；内部自建一个 :class:`XTXX` 实例
    （对应 ST ``VAR XTXX : XTXX``）。**不**在本类直接读取真实机器信息。
    """

    def __init__(self, serial_provider: SerialTextProvider) -> None:
        self._xtxx = XTXX(serial_provider)

        self.ZCM1: int = 0
        self.ZCM2: int = 0
        self.ZCM3: int = 0
        self.ERROR: int = 0
        self.SerialText: str = ""

    def step(self, EN: bool) -> BDZCMOutput:
        if not EN:
            # ST: 整个块在 IF EN 内，EN=False 时不刷新，保留上次输出。
            return self._snapshot()

        self._xtxx.step(True)
        self.SerialText = self._xtxx.SerialText
        self.ERROR = 0

        if not self._xtxx.SerialOK:
            self.ERROR += ERROR_SERIAL_UNAVAILABLE

        if self.ERROR == 0:
            self.ZCM1, self.ZCM2, self.ZCM3 = compute_registration_codes(
                self._xtxx.SerialText
            )
        else:
            self.ZCM1 = 0
            self.ZCM2 = 0
            self.ZCM3 = 0

        return self._snapshot()

    def _snapshot(self) -> BDZCMOutput:
        return {
            "ZCM1": self.ZCM1,
            "ZCM2": self.ZCM2,
            "ZCM3": self.ZCM3,
            "ERROR": self.ERROR,
            "SerialText": self.SerialText,
        }
