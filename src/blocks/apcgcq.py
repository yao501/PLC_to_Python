"""业务块 APCGCQ：观测器（MMYZ）。

对应 CODESYS CFC 源：``/Users/guangyaosun/Desktop/GCQ.docx``。
对应 ST 转换稿：``/Users/guangyaosun/Desktop/CGCQ1.txt``（用户基于 CFC 转化）。

业务定位：
    控制器中的"观测器"组合块。两路相邻完整窗口的均值差经一阶低通滤波得
    "动态观测分量"，再叠加"静态观测分量"，经速率限幅与幅值限幅后输出
    控制量 ``GCAV``。同时单独输出 ``JTAV / DTAV`` 用于上层观察。

源 ST 顺序（**严格保留**，不允许重排，详见下方 §"ST 执行顺序锁定"）::

    1.  BLINK01(ENABLE:=TRUE,
                TIMELOW:=REAL_TO_TIME(TC*1000),
                TIMEHIGH:=T#500MS);
    2.  R_TRIG1(CLK:=BLINK01.OUT);
    3.  JZ_ZUP1 := SEL(R_TRIG1.Q, JZ_ZUP1, JZ_ZUP);
    4.  JZ_ZUP  := SEL(R_TRIG1.Q, JZ_ZUP,  JZ_Z);
    5.  STAT01(IN:=IN, RESET:=R_TRIG1.Q);
    6.  JZ_Z := STAT01.AVG;
    7.  FOP01(IN:=JZ_ZUP-JZ_ZUP1, TC:=TZ*2, KG:=1);   (* TB 不传，用 FB 声明默认值 0.5 *)
    8.  AV := FOP01.AV;
    9.  JTAV := (IN - INSP) * GC1;
    10. DTAV := AV * GC2;
    11. RLIM01(IN := SEL(IN<INSP AND IN>INSP, (JTAV+DTAV)*K, 0),
              HL := OUTV, LL := OUTV);
    12. LIM01(IN := RLIM01.AV, HL := OUTH, LL := OUTL);
    13. GCAV := LIM01.AV;

依赖（严格复用，不重写）：

* :class:`~src.primitives.BLINK` —— 周期方波
* :class:`~src.primitives.R_TRIG` —— 上升沿检测
* :class:`~src.blocks.APCSTATISTICS` —— 运行统计（ST 实例名 ``STATISTICS_REAL``）
* :class:`~src.blocks.APCHSFOP` —— 一阶 IIR 滤波
* :class:`~src.blocks.APCHSRATELIM` —— 速率限幅（每拍变化量正幅值）
* :class:`~src.blocks.APCHSHLLIM` —— 幅值限幅
* :func:`~src.compat.real_to_time_ms` —— 承接 ``REAL_TO_TIME(TC*1000)``

------------------------------------------------------------------
ST 执行顺序锁定（**关键，逐字保留**）：

* 第 4 步 ``JZ_ZUP := SEL(Q, JZ_ZUP, JZ_Z)`` 中的 ``JZ_Z`` 是**上一拍**
  ``STAT01.AVG`` 的旧值（因为这一拍 STAT01 还没调用）。
* 第 5 步才执行 ``STAT01(RESET:=Q)``，第 6 步把 ``JZ_Z`` 更新为新一拍
  ``STAT01.AVG``。
* 在采样事件那一拍（``Q=True``），``STAT01`` 因 ``RESET=True`` 当拍不采样
  且 ``AVG=0``——但 ``JZ_ZUP`` 已在第 4 步取到了旧的 ``JZ_Z``（即上一个
  完整窗口的均值），不会被这一拍 RESET 后的 0 污染。
* 这是经典的 "采样旧值 → 重置统计 → 开新窗口" 的延迟一拍模式，在 Python
  实现中**必须严格按 ST 顺序**写，不能合并 / 不能优化。
  锁死测试：``TestSTOrderingLocked``。

------------------------------------------------------------------
项目工程约定（与 BLINK 行动单 / R7.7 / 02 规则对齐）：

1. **BLINK.TIMEHIGH** 固定 ``T#500MS``（用户已在 GCQ 任务确认；原 TXT
   写 ``T#300MS`` 是笔误）。本块以 ``500`` 字面量传入，不暴露为 GCQ 输入。
2. **BLINK.ENABLE** 固定 ``TRUE``（用户确认：原 CFC 是密码验证段才置
   TRUE，本块版本直通；密码验证由 Runtime 阶段实现）。
3. **死区条件 ``IN<INSP AND IN>INSP`` 恒为 FALSE**：用户确认是故意设计
   （让 SEL 永远走 IN0 = ``(JTAV+DTAV)*K``）。**按 02 规则不擅自化简**，
   原样保留 SEL 表达式作为未来恢复死区功能的扩展钩子。
4. **RLIM01 对称速率限幅 ``HL=LL=OUTV``**：用户确认是对称速率限。
   按 ``APCHSRATELIM-RL1``，``HL/LL`` 都是正幅值，不是上下区间。
5. **FOP01 的 TB 不传**：使用 ``APCHSFOP`` 的 ST VAR_INPUT 声明默认值
   ``0.5`` 秒（R7.7："输入脚无外部赋值用 FB 声明默认值"）。本块通过模块
   级常量 :data:`FOP01_DEFAULT_TB_SEC` 显式传入，与 ST 默认值同步。
6. **本块不暴露 ``EN / RESET / auth_ok``**：冷启动门控、安全使能、密码
   验证由 Runtime 主程序（``RUNTIME-GATE`` / ``RUNTIME-STARTUP-INHIBIT``）
   闭环承担。

时间语义（严格按 00a 契约 R7 条）：

* ``TC / TZ`` 是 ST 显式输入脚（单位**秒**），不等于 ``dt_ms/1000``，也
  不被 runtime 自动替代。``TC`` 通过 ``REAL_TO_TIME(TC*1000)`` 转成 BLINK
  的 ``TIMELOW_ms``；``TZ`` 通过 ``TZ*2`` 进入 FOP01.TC（单位秒）。
* ``dt_ms`` 用于驱动 ``BLINK`` 的相位累积；其他依赖块按各自语义使用
  ``dt_ms``（``R_TRIG`` 不用、``APCSTATISTICS`` / ``APCHSRATELIM`` /
  ``APCHSHLLIM`` 都不用、``APCHSFOP`` 不参与公式但保留契约签名）。

参考 ``docs/RISKS.md`` 的 ``APCGCQ-*`` 条目。
"""

from __future__ import annotations

from typing import TypedDict

from src.blocks.apchsfop import APCHSFOP
from src.blocks.apchshllim import APCHSHLLIM
from src.blocks.apchsratelim import APCHSRATELIM
from src.blocks.apcstatistics import APCSTATISTICS
from src.compat import real_to_time_ms
from src.primitives import BLINK, R_TRIG


BLINK_TIMEHIGH_MS: int = 500
"""``BLINK01.TIMEHIGH`` 固定 500 ms（用户在 GCQ 任务中确认）。"""

FOP01_DEFAULT_TB_SEC: float = 0.5
"""``FOP01`` 在 ST 中未传 TB，使用 APCHSFOP 的 VAR_INPUT 声明默认值（秒）。

与 ``src/blocks/apchsfop.py`` 的 ``TB=0.5`` 默认值同步；如未来 ST 源头
修改 APCHSFOP 默认值，本常量必须**同步更新**。
"""

FOP01_KG: float = 1.0
"""``FOP01.KG`` 在 ST 中固定为常量 1（不是参数）。"""


class APCGCQOutput(TypedDict):
    GCAV: float
    JTAV: float
    DTAV: float


class APCGCQ:
    """观测器（MMYZ）业务块。

    跨周期状态（全部 ``self.*``，保留 ST 变量名）：

    * 直接持有的 ST VAR / VAR_OUTPUT：
        - ``GCAV`` —— 公开输出（带 RETAIN 语义）
        - ``JTAV`` —— 静态观测分量
        - ``DTAV`` —— 动态观测分量
        - ``AV``   —— FOP01 输出
        - ``JZ_ZUP1 / JZ_ZUP / JZ_Z`` —— 三拍延迟链（详见 ST 执行顺序锁定）

    * 嵌套的 FB 实例（保留 ST 实例名）：
        - ``BLINK01 / R_TRIG1 / STAT01 / FOP01 / RLIM01 / LIM01``

    公开接口::

        step(dt_ms, *, IN, TC, TZ, K, INSP, GC1, GC2, OUTH, OUTL, OUTV)
            -> {"GCAV": ..., "JTAV": ..., "DTAV": ...}
    """

    def __init__(self) -> None:
        self.GCAV: float = 0.0
        self.JTAV: float = 0.0
        self.DTAV: float = 0.0
        self.AV: float = 0.0

        self.JZ_ZUP1: float = 0.0
        self.JZ_ZUP: float = 0.0
        self.JZ_Z: float = 0.0

        self.BLINK01 = BLINK()
        self.R_TRIG1 = R_TRIG()
        self.STAT01 = APCSTATISTICS()
        self.FOP01 = APCHSFOP()
        self.RLIM01 = APCHSRATELIM()
        self.LIM01 = APCHSHLLIM()

    def step(
        self,
        dt_ms: int,
        *,
        IN: float,
        TC: float,
        TZ: float,
        K: float,
        INSP: float,
        GC1: float,
        GC2: float,
        OUTH: float,
        OUTL: float,
        OUTV: float,
    ) -> APCGCQOutput:
        timelow_ms = real_to_time_ms(TC * 1000.0)
        blink_out = self.BLINK01.step(
            dt_ms,
            ENABLE=True,
            TIMELOW_ms=timelow_ms,
            TIMEHIGH_ms=BLINK_TIMEHIGH_MS,
        )

        rtrig_q = self.R_TRIG1.step(blink_out)

        if rtrig_q:
            self.JZ_ZUP1 = self.JZ_ZUP
            self.JZ_ZUP = self.JZ_Z

        stat_out = self.STAT01.step(dt_ms, IN=IN, RESET=rtrig_q)

        self.JZ_Z = stat_out["AVG"]

        fop_out = self.FOP01.step(
            dt_ms,
            IN=self.JZ_ZUP - self.JZ_ZUP1,
            TC=TZ * 2.0,
            KG=FOP01_KG,
            TB=FOP01_DEFAULT_TB_SEC,
        )
        self.AV = fop_out["AV"]

        self.JTAV = (IN - INSP) * GC1
        self.DTAV = self.AV * GC2

        cond = (IN < INSP) and (IN > INSP)
        if cond:
            rl_in = 0.0
        else:
            rl_in = (self.JTAV + self.DTAV) * K

        rl_out = self.RLIM01.step(dt_ms, IN=rl_in, HL=OUTV, LL=OUTV)
        lim_out = self.LIM01.step(dt_ms, IN=rl_out["AV"], HL=OUTH, LL=OUTL)

        self.GCAV = lim_out["AV"]
        return {"GCAV": self.GCAV, "JTAV": self.JTAV, "DTAV": self.DTAV}
