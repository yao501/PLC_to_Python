"""授权模块 ``BD_MMYZ_ST``（带周期复验的密码验证功能块）的 Python 迁移。

对应 CODESYS ST 功能块：``BD_MMYZ_ST``（``BD_MMYZ_ST.txt``）。

状态字段（初值严格对应 ST ``VAR_OUTPUT``）：

* ``OK : WORD := 9000``  —— 验证结果。``OK MOD 10000 = 0`` 表示通过；
  ``> 10000`` 表示"本轮强制复验已执行"的标志位；初值 9000 = 未验证。
* ``YZTG : BOOL := FALSE`` —— 验证通过标志。
* ``ERR : WORD := 0`` —— 当前错误码 ``= OK MOD 10000``。
* ``ERR_N : REAL := 0.0`` —— 累计错误次数。

核心语义（``BD_MMYZ_ST.txt`` 第 29~70 行，逐条保留）::

    totalSeconds := ULINT_TO_WORD((GetDateTime() / 1000) MOD 65536);
    IF (OK MOD 10000) <> 0 THEN
        OK := BD_MMYZ();                       (* 失败：每拍都重验 *)
    ELSE
        IF totalSeconds MOD 10 = 7 THEN
            IF OK < 10000 THEN
                OK := BD_MMYZ(); OK := OK + 10000;   (* 时段内首次强制复验 *)
            END_IF
        ELSE
            OK := OK MOD 10000;                (* 离开时段，去除标志 *)
        END_IF
    END_IF
    ERR := OK MOD 10000;
    IF ERR = 0 THEN YZTG := TRUE;
    ELSE YZTG := FALSE; ERR_N := ERR_N + 1;
         IF ERR_N > 999999999 THEN ERR_N := 100000000; END_IF
    END_IF

**时间来源（关键）**：``totalSeconds`` 来自注入的
:class:`~src.licensing.providers.DateTimeProvider`，**不**读取 Python 系统时钟
（见 ``docs/RISKS.md::LIC-CLOCK-1``）。``step(dt_ms)`` 保留 ``dt_ms`` 仅为统一
功能块接口，**不**用它自行累计/推进时间——同一扫描周期内多次调用必须看到
**同一个**当前时间（由 Runtime 统一推进时间 Provider 保证）。
"""

from __future__ import annotations

from typing import Callable, TypedDict

from .providers import DateTimeProvider

# BD_MMYZ() 验证器：无参，返回 WORD 错误码。
Validator = Callable[[], int]


class BDMMYZSTOutput(TypedDict):
    OK: int
    YZTG: bool
    ERR: int
    ERR_N: float


class BD_MMYZ_ST:
    """周期复验功能块。携带跨周期状态。

    公开接口::

        step(dt_ms: int = 0) -> BDMMYZSTOutput

    构造时注入：``validator``（一个 :class:`~src.licensing.bd_mmyz.BD_MMYZ`
    可调用对象）与 ``datetime_provider``。
    """

    def __init__(
        self,
        validator: Validator,
        datetime_provider: DateTimeProvider,
    ) -> None:
        self._validator = validator
        self._dtp = datetime_provider

        self.OK: int = 9000
        self.YZTG: bool = False
        self.ERR: int = 0
        self.ERR_N: float = 0.0

    def step(self, dt_ms: int = 0) -> BDMMYZSTOutput:
        # dt_ms 仅为统一接口保留；时间来自注入 Provider，不用 dt_ms 推进。
        del dt_ms

        total_seconds = (self._dtp.get_datetime_ms() // 1000) % 65536

        if (self.OK % 10000) != 0:
            # 上次失败：每个扫描周期都重新验证。
            self.OK = self._validator()
        else:
            if total_seconds % 10 == 7:
                if self.OK < 10000:
                    self.OK = self._validator()
                    self.OK = self.OK + 10000
            else:
                self.OK = self.OK % 10000

        self.ERR = self.OK % 10000

        if self.ERR == 0:
            self.YZTG = True
        else:
            self.YZTG = False
            self.ERR_N += 1
            if self.ERR_N > 999999999:
                self.ERR_N = 100000000.0

        return {
            "OK": self.OK,
            "YZTG": self.YZTG,
            "ERR": self.ERR,
            "ERR_N": self.ERR_N,
        }
