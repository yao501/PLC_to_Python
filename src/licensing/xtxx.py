"""授权模块 ``XTXX``（系统信息）的 Python 平台适配迁移。

对应 CODESYS ST 功能块：``XTXX``（``XTXX .txt``）。

ST 原块通过 ``SysTargetGetId / SysTargetGetType / SysTargetGetVersion /
SysTargetGetSerialNumber`` 读取软 PLC 系统信息。授权算法**只依赖**：
``SerialText / Serial_result / SerialLen / SerialOK``。

**平台适配边界（重要）**：Python 部署目标是 PC / 服务器，不是 SoftPLC。
本类通过可注入的 :class:`~src.licensing.providers.SerialTextProvider`
取得稳定机器标识，**不**逐字节复刻 ``SysTargetGetSerialNumber``
（见 ``docs/RISKS.md::LIC-PLATFORM-1 / LIC-PLATFORM-2``）。
``PLCid / CPUtype / Version`` 不是授权核心，保留默认 ``0`` 与"未实现"结果码，
**不伪造** CODESYS 系统调用数据。

执行语义（保留 ST ``IF EN`` 门控）：

* ``EN=False``：不刷新任何输出，保留上一拍状态（FB RETAIN 语义）。
* ``EN=True``：读取 Provider，并按"成功且非空且 Latin-1 可编码且 ≤255 字节"
  判定 ``SerialOK``。
"""

from __future__ import annotations

from typing import TypedDict

from .providers import (
    MAX_SERIAL_BYTES,
    SERIAL_RESULT_OK,
    SerialTextProvider,
)

# PLCid / CPUtype / Version 在 Python 平台不实现，用此结果码显式标注"不可用"。
RESULT_NOT_IMPLEMENTED = 0x0F00


class XTXXOutput(TypedDict):
    SerialText: str
    SerialLen: int
    SerialOK: bool
    Serial_result: int
    PLCid: int
    PLCid_result: int
    CPUtype: int
    CPU_result: int
    Version: int
    Version_result: int


class XTXX:
    """系统信息读取块（仅 SerialText 通路有效，其余为平台适配占位）。

    公开接口::

        step(EN: bool) -> XTXXOutput

    需在构造时注入机器标识提供者（Provider）；测试用
    :class:`~src.licensing.providers.StaticSerialTextProvider`，
    生产用 :class:`~src.licensing.providers.PlatformSerialTextProvider`。
    """

    def __init__(self, serial_provider: SerialTextProvider) -> None:
        self._provider = serial_provider

        # 授权核心输出。
        self.SerialText: str = ""
        self.SerialLen: int = 0
        self.SerialOK: bool = False
        self.Serial_result: int = SERIAL_RESULT_OK

        # 平台适配占位输出（非授权核心，不伪造 CODESYS 数据）。
        self.PLCid: int = 0
        self.PLCid_result: int = RESULT_NOT_IMPLEMENTED
        self.CPUtype: int = 0
        self.CPU_result: int = RESULT_NOT_IMPLEMENTED
        self.Version: int = 0
        self.Version_result: int = RESULT_NOT_IMPLEMENTED

    def step(self, EN: bool) -> XTXXOutput:
        if not EN:
            # EN=False：保留上一拍输出，不刷新（ST IF EN 门控）。
            return self._snapshot()

        # 平台占位输出每次 EN=True 时复位为"未实现"（不伪造）。
        self.PLCid = 0
        self.PLCid_result = RESULT_NOT_IMPLEMENTED
        self.CPUtype = 0
        self.CPU_result = RESULT_NOT_IMPLEMENTED
        self.Version = 0
        self.Version_result = RESULT_NOT_IMPLEMENTED

        result = self._provider.read_serial()

        # Serial_result 严格透传 Provider 的“底层读取结果”，**不**被 XTXX 内部的
        # 可用性判定（空 / 编码 / 超长）覆盖——对齐源 ST：读取成功但 SerialText
        # 为空时 Serial_result 仍为 0，仅 SerialOK 因 LEN>0 不满足而为 False
        # （见 docs/RISKS.md::LIC-PLATFORM-1）。
        self.Serial_result = result.result_code

        # 可用性判定（决定 SerialOK）：底层读取成功 且 非空 且 Latin-1 可编码
        # 且 字节长度 ≤ 255。任一不满足 → 序列号不可用，但不改写 Serial_result。
        text = result.serial_text
        encoded_len = 0
        serial_ok = False
        if result.success and text:
            try:
                encoded = text.encode("latin-1")
            except UnicodeEncodeError:
                serial_ok = False
            else:
                if len(encoded) <= MAX_SERIAL_BYTES:
                    serial_ok = True
                    encoded_len = len(encoded)

        if serial_ok:
            # 序列号可用。SerialLen 按字节长度（Latin-1 下 == 字符数）。
            self.SerialText = text
            self.SerialLen = encoded_len
            self.SerialOK = True
        else:
            self.SerialText = ""
            self.SerialLen = 0
            self.SerialOK = False

        return self._snapshot()

    def _snapshot(self) -> XTXXOutput:
        return {
            "SerialText": self.SerialText,
            "SerialLen": self.SerialLen,
            "SerialOK": self.SerialOK,
            "Serial_result": self.Serial_result,
            "PLCid": self.PLCid,
            "PLCid_result": self.PLCid_result,
            "CPUtype": self.CPUtype,
            "CPU_result": self.CPU_result,
            "Version": self.Version,
            "Version_result": self.Version_result,
        }
