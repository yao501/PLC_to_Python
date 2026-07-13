"""可注入的机器标识 / 时间提供者（平台适配层）。

本模块承担两类"运行时输入源"的**可注入抽象**，使授权逻辑既能在测试中用
固定值复现，又能在生产中接 PC / 服务器平台：

* :class:`SerialTextProvider`：替代 CODESYS ``SysTargetGetSerialNumber``，
  给出稳定机器标识（见 ``docs/RISKS.md::LIC-PLATFORM-1 / LIC-PLATFORM-2``）。
* :class:`DateTimeProvider`：替代 ST ``DateTimeProvider.GetDateTime()``，
  给出整数毫秒时间，供 ``BD_MMYZ_ST`` 周期复验使用
  （见 ``docs/RISKS.md::LIC-CLOCK-1``）。

强约束：

1. 机器标识读取失败 **不得** 偷偷改用 MAC / ``uuid.getnode()`` / 主机名 /
   随机值 / 本地新建 ID（任务书 §0.9、§3.3）。失败就如实返回失败。
2. 时间来源 **不得** 读取 Python 系统时钟（``time.time`` / ``time.monotonic`` /
   ``datetime.now``）；生产时间由未来 Runtime 注入。
3. 仅用标准库，无第三方依赖。
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# 机器标识读取结果码（喂给 XTXX.Serial_result；0 = 成功，非 0 = 失败原因）。
SERIAL_RESULT_OK = 0
SERIAL_RESULT_READ_FAILED = 0x1001
SERIAL_RESULT_EMPTY = 0x1002
SERIAL_RESULT_ENCODING = 0x1003
SERIAL_RESULT_TOO_LONG = 0x1004

# 平台标识前缀（任务书 §3.1）：PYPLC|<OS>|<原始ID>。
SERIAL_PREFIX = "PYPLC"
MAX_SERIAL_BYTES = 255


@dataclass(frozen=True)
class SerialReadResult:
    """机器标识读取结果。

    属性
    ----
    success : bool
        是否成功读取到一个**可用候选**机器标识（编码/长度合法性由
        :class:`~src.licensing.xtxx.XTXX` 再次校验）。
    serial_text : str
        规范化后的机器标识文本；失败时为空串。
    result_code : int
        结果码，0 表示成功，非 0 表示失败原因（见本模块 ``SERIAL_RESULT_*``）。
    """

    success: bool
    serial_text: str
    result_code: int


@runtime_checkable
class SerialTextProvider(Protocol):
    """机器标识提供者协议。"""

    def read_serial(self) -> SerialReadResult:  # pragma: no cover - 协议声明
        ...


@runtime_checkable
class DateTimeProvider(Protocol):
    """时间提供者协议：返回整数毫秒（自某固定纪元的累计毫秒）。"""

    def get_datetime_ms(self) -> int:  # pragma: no cover - 协议声明
        ...


class StaticSerialTextProvider:
    """固定机器标识提供者（测试 / 仿真用）。

    不读取任何真实机器信息，便于回归测试不依赖开发机（任务书 §3、§14.6）。
    """

    def __init__(
        self,
        serial_text: str,
        *,
        success: bool = True,
        result_code: int = SERIAL_RESULT_OK,
    ) -> None:
        self._serial_text = serial_text
        self._success = success
        self._result_code = result_code

    def read_serial(self) -> SerialReadResult:
        return SerialReadResult(
            success=self._success,
            serial_text=self._serial_text,
            result_code=self._result_code,
        )


class ManualDateTimeProvider:
    """可手动设置 / 推进的时间提供者（测试用）。

    时间以整数毫秒保存。``set_ms`` 绝对设置、``advance_ms`` 相对推进。
    **不**读取系统时钟，调用 :meth:`get_datetime_ms` 本身不会推进时间——
    这正是验证"同一扫描周期内多次调用看到同一时间"所需的语义。
    """

    def __init__(self, now_ms: int = 0) -> None:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int):
            raise TypeError("ManualDateTimeProvider now_ms must be int")
        self._now_ms = now_ms

    def set_ms(self, now_ms: int) -> None:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int):
            raise TypeError("set_ms expects int")
        self._now_ms = now_ms

    def advance_ms(self, delta_ms: int) -> None:
        if isinstance(delta_ms, bool) or not isinstance(delta_ms, int):
            raise TypeError("advance_ms expects int")
        self._now_ms += delta_ms

    def get_datetime_ms(self) -> int:
        return self._now_ms


class PlatformSerialTextProvider:
    """生产默认机器标识提供者（按操作系统读取稳定 ID）。

    来源（任务书 §3）：

    * Windows：注册表 ``HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid``
    * Linux：``/etc/machine-id``（或 ``/var/lib/dbus/machine-id``）
    * macOS：``IOPlatformUUID``（``ioreg`` 解析）

    规范化为 ``PYPLC|<OS>|<原始ID>`` 文本（ASCII）。读取失败一律返回
    ``success=False``，**不**降级到 MAC / 主机名 / 随机值（§3.3）。

    .. note::
       本类在单元测试中**不**被使用（测试用 :class:`StaticSerialTextProvider`），
       因此其平台分支默认不进入覆盖统计。它是平台适配边界，**不**等价于
       CODESYS ``SysTargetGetSerialNumber`` 的逐字节复刻（``LIC-PLATFORM-1``）。
    """

    @staticmethod
    def normalize(os_tag: str, raw: str) -> str:
        """把操作系统标记与原始机器 ID 组合成稳定文本 ``PYPLC|<OS>|<raw>``。

        抽成纯函数便于在不依赖真实机器的情况下对"规范化输出逻辑"做单元测试
        （任务书 §3.1、§12.B）。
        """
        return f"{SERIAL_PREFIX}|{os_tag}|{raw.strip()}"

    def read_serial(self) -> SerialReadResult:  # pragma: no cover - 平台相关
        system = platform.system()
        try:
            if system == "Windows":
                raw = self._read_windows_machine_guid()
                os_tag = "WINDOWS"
            elif system == "Linux":
                raw = self._read_linux_machine_id()
                os_tag = "LINUX"
            elif system == "Darwin":
                raw = self._read_macos_platform_uuid()
                os_tag = "MACOS"
            else:
                return SerialReadResult(False, "", SERIAL_RESULT_READ_FAILED)
        except Exception:
            return SerialReadResult(False, "", SERIAL_RESULT_READ_FAILED)

        if not raw:
            return SerialReadResult(False, "", SERIAL_RESULT_EMPTY)

        return SerialReadResult(True, self.normalize(os_tag, raw), SERIAL_RESULT_OK)

    @staticmethod
    def _read_windows_machine_guid() -> str:  # pragma: no cover - 平台相关
        import winreg  # type: ignore[import-not-found]

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
        finally:
            winreg.CloseKey(key)
        return str(value)

    @staticmethod
    def _read_linux_machine_id() -> str:  # pragma: no cover - 平台相关
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                with open(path, "r", encoding="ascii") as fh:
                    text = fh.read().strip()
                if text:
                    return text
            except OSError:
                continue
        return ""

    @staticmethod
    def _read_macos_platform_uuid() -> str:  # pragma: no cover - 平台相关
        out = subprocess.check_output(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            text=True,
        )
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                return line.split("=", 1)[1].strip().strip('"')
        return ""
