"""业务块 APCCD：重叠控制（动态项 + 静态项叠加）。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/APCCD.txt``。

业务定位（源 ST 注释原文）：
    "重叠控制，根据动态项和静态项的输出之和与拐点比较，大于拐点时，输出
    静态动态之和的 ``CD_K_FD`` 倍，再小于拐点或者切除时，返回拐点的
    ``CD_K`` 倍。"

    具体语义：
    * ``BLINK1 + R_TRIG1`` 形成周期采样节拍；
    * ``STAT1`` 在每个采样窗口内对 ``PV`` 做运行均值，采样事件拍 RESET 开新窗口；
    * ``JZ_ZUP2 - JZ_ZUP3`` 取相邻两个完整窗口的均值差，经 ``FOP1`` 一阶低通
      滤波得到"动态项"；
    * 动态项与静态项 ``(PV-SP)`` 叠加并乘正反作用符号，得专家输出 ``CD_BH``；
    * ``|CD_BH| >= CD_GD`` 持续 ``TL`` 秒后 ``TON1.Q`` 为真（延时进入），在
      未跟踪（``TS=False``）时把 ``CD_BH*CD_K_FD`` 钳幅后写入 ``AV_TEMP``，
      并记录方向 ``FLG``；
    * 退出（``|CD_BH| < CD_GD``）或进入跟踪（``TS=True``）且当前 ``AV_TEMP != 0``
      时，由 ``R_TRIG2`` 触发一次"回补"：把拐点的 ``CD_K`` 倍累加到 ``ZLOUT``，
      随后清零 ``AV_TEMP``；
    * ``TS=True`` 时强制 ``AV_TEMP = 0``（跟踪切除）。
    * ``AV`` 在周期末尾等于最终 ``AV_TEMP``。

依赖（严格复用，不重写）：

* :class:`~src.primitives.BLINK` —— 周期方波
* :class:`~src.primitives.R_TRIG` —— 上升沿检测（``R_TRIG1`` 采样、``R_TRIG2`` 回补）
* :class:`~src.primitives.TON` —— 接通延时（延时进入）
* :class:`~src.blocks.APCSTATISTICS` —— 运行统计（ST 实例名 ``STATISTICS_REAL``）
* :class:`~src.blocks.APCHSFOP` —— 一阶 IIR 低通滤波
* :func:`~src.compat.real_to_time_ms` —— 承接 ``REAL_TO_TIME(... *1000)``

------------------------------------------------------------------
ST 执行顺序锁定（**关键，逐字保留，不允许重排 / 合并 / 优化**）：

源 ST 顺序::

    BLINK1(ENABLE:=TRUE, TIMELOW:=REAL_TO_TIME(TC*1000), TIMEHIGH:=T#300MS);
    R_TRIG1(CLK:=BLINK1.OUT);
    JZ_ZUP3 := SEL(R_TRIG1.Q, JZ_ZUP3, JZ_ZUP2);   (* Q=True 时取旧 JZ_ZUP2 *)
    JZ_ZUP2 := SEL(R_TRIG1.Q, JZ_ZUP2, JZ_Z1);     (* Q=True 时取旧 JZ_Z1 *)
    STAT1(IN:=PV, RESET:=R_TRIG1.Q);               (* 之后才 RESET *)
    JZ_Z1 := STAT1.AVG;                            (* 采样事件拍 = 0.0 *)
    FOP1(IN:=JZ_ZUP2-JZ_ZUP3, TC:=TZ*2, KG:=1);
    CD_BH := ((PV-SP)*CD_K_J + FOP1.AV*CD_K_D) * SEL(AD,1,-1);
    TON1(IN:=ABS(CD_BH)>=CD_GD, PT:=REAL_TO_TIME(TL*1000));
    IF TON1.Q AND TS=0 THEN ... 更新 AV_TEMP / FLG ... END_IF
    R_TRIG2(CLK:=(ABS(CD_BH)<CD_GD OR TS=TRUE) AND AV_TEMP<>0);
    IF R_TRIG2.Q THEN ... 回补 ZLOUT，AV_TEMP:=0 ... END_IF
    IF TS=TRUE THEN AV_TEMP:=0; END_IF
    AV := AV_TEMP;

* **核心不变量（与 APCGCQ-GG1 同构）**：窗口历史移位（``JZ_ZUP3/JZ_ZUP2``）
  必须发生在 ``STAT1.step(RESET=...)`` **之前**。采样事件拍 ``JZ_ZUP2`` 取到
  的是**旧** ``JZ_Z1``（上一个完整窗口的均值），而不是 RESET 后的 ``0``。
  错误顺序会把当拍 RESET 后的 ``0`` 写入历史链，破坏"相邻完整窗口均值差"语义。
* ``if TS: AV_TEMP=0`` 必须在 ``R_TRIG2`` 回补之后执行：``TS`` 进入拍若已有
  非零 ``AV_TEMP``，允许先经 ``R_TRIG2`` 回补一次再清零（见 CD5）。

------------------------------------------------------------------
``ZLOUT`` 的 Python 适配（``VAR_IN_OUT``）：

ST 中 ``ZLOUT`` 是 ``VAR_IN_OUT``（读-改-写引用管脚）。Python 不用引用传参，
改为"**入参 + 返回值**"模式：

* 每拍以 ``step(ZLOUT=...)`` 入参为当拍真值来源（不在块内缓存为唯一真值）；
* 仅在 ``R_TRIG2.Q`` 回补拍对入参做加法；
* 通过 ``out["ZLOUT"]`` 返回更新值，**调用方必须把它回灌到下一拍**::

      zlout = 0.0
      while scanning:
          out = cd.step(dt_ms, SP=..., PV=..., TS=..., ZLOUT=zlout, ...)
          zlout = out["ZLOUT"]          # 下一拍回灌

------------------------------------------------------------------
时间语义（严格按 00a 契约 R7 条）：

* ``TC / TZ / TL`` 是 ST 显式输入脚，单位**秒**，**不等于** ``dt_ms/1000``，
  也不被 runtime 自动替代：
    - ``TC`` 经 ``REAL_TO_TIME(TC*1000)`` 转成 ``BLINK1.TIMELOW_ms``；
    - ``TZ`` 经 ``TZ*2`` 进入 ``FOP1.TC``（仍是秒）；
    - ``TL`` 经 ``REAL_TO_TIME(TL*1000)`` 转成 ``TON1.PT_ms``。
* ``dt_ms`` **仅**用于推进 ``BLINK1`` 的相位累积与 ``TON1`` 的 ``ET_ms`` 累加，
  不参与任何业务公式，也不替代任何显式时间输入脚。
* ``FOP1`` 在 ST 中未连接 ``TB``，按 R7 输入脚语义使用 ``APCHSFOP`` 声明默认值
  ``0.5`` 秒（模块级常量 :data:`FOP1_DEFAULT_TB_SEC`）。

------------------------------------------------------------------
项目工程约定 / 边界：

1. **BLINK1.TIMEHIGH = 500 ms（量化到任务周期，忠实复现）**：源 ST 写
   ``T#300MS``，但原工程任务周期 = 500ms，``R_TRIG1`` 只在任务边界采样，
   亚周期脉宽（300ms < 500ms）不可分辨——真实 PLC 中 300 与 500 等价。
   本项目 ``BLINK`` 为余数保留实现，取 300 会在 500ms 扫描下吞脉冲/抖动，
   故端口量化到 ``cycle_ms = 500``，与 ``APCGCQ`` 一致。采样窗口周期 =
   ``TC*1000 + 500`` ms。详见 :data:`BLINK_TIMEHIGH_MS` 与 ``APCCD-CD2``。
2. **不暴露 EN / RESET / system_ready / output_enable / safety_ok /
   interlock_ok**：本块只产出逻辑结果（``AV / CD_BH / ZLOUT``）。物理输出
   安全门控属于后续 Runtime / MainProgram（见 ``RUNTIME-GATE``）。
3. **块内不做参数合法化**：``CDH/CDL`` 钳幅严格按源码 ``MIN(MAX(x,CDL),CDH)``，
   即便 ``CDL>CDH`` 也按原表达式自然执行，不交换 / 不纠正；``TC/TZ/TL`` 等的
   非负 / 范围校验由配置装载层（``RUNTIME-PARAM-VALIDATION``）兜底。

参考 ``docs/RISKS.md`` 的 ``APCCD-*`` 条目。
"""

from __future__ import annotations

from typing import TypedDict

from src.blocks.apchsfop import APCHSFOP
from src.blocks.apcstatistics import APCSTATISTICS
from src.compat import real_to_time_ms
from src.primitives import BLINK, R_TRIG, TON


BLINK_TIMEHIGH_MS: int = 500
"""``BLINK1.TIMEHIGH``：**源 ST 值 = ``T#300MS``；端口有效值 = 500 ms**
（= 任务扫描周期 ``cycle_ms``）。这是"量化复现"而非"笔误修正"。

（双层留痕：源 ST 写的就是 300ms，端口故意取 500ms——不要误以为源 ST 本来
就是 500ms。理由见下，对应 ``APCCD-CD2`` / 契约 ``00a R8`` / ``SAMPLING-PATTERN``。）

* 原工程任务周期确凿为 **500ms**（用户确认），``R_TRIG1`` 只能在任务边界
  （每 500ms）观察 ``BLINK1.OUT``。因此任何 **≤ 500ms 的高电平宽度都不可
  分辨**，在真实 PLC 里被采成"一个任务周期宽"的脉冲——**在"同任务、OUT
  仅经 R_TRIG 取边沿"这一场景的可观察层面**，``TIMEHIGH=300`` 与 ``500``
  **等价**（高电平占 1 拍，采样周期 = ``TC*1000 + 500ms``）。
  **该等价是有条件的**：若 OUT 被更快任务读取 / 被直接用于业务判断或物理
  输出 / 同实例跨任务消费 / 在非固定周期仿真模式下，则 300 与 500 不再等价
  （见 ``00a R8`` 第 1 条）。
* 本项目的 ``BLINK`` 采用**余数保留**实现（``BLINK-B2``），仅当
  ``TIMEHIGH_ms >= dt_ms`` 时才与"任务边界采样"一致；若取源值 300（< dt=500），
  高脉冲会在同一拍内 True→False 被吞掉，导致采样事件丢失 + 节拍抖动。
* 故端口把这个**内部脉宽常量**量化到 ``cycle_ms = 500``，与 ``APCGCQ`` 一致。

**关于 ``TIMELOW`` (= ``TC*1000``)**：它由业务输入 ``TC`` 驱动，按契约 R7
**不在块内静默变换**。为得到整齐采样节拍，**建议**在配置层把 ``TC`` 配成
``cycle_ms/1000`` 的整数倍（如 0.5/1.0/2.0…）——这是工程建议，**非硬性校验**。
**非整数倍属合法配置**（原 PLC 可能确有 ``TC=1.1/1.25``），允许使用。配置校验
（warning）**目前尚未实现**——当前仅在契约 ``00a R8`` / ``docs/RISKS.md`` / 测试中
记录"拍间隔可能抖动"，待接入 ``RUNTIME-PARAM-VALIDATION`` 再统一提供；无论如何
**不阻断运行、不改写参数、不做 ceil/round 静默量化**（见 ``00a R8`` 第 3 条）。
非整数倍时本端口余数保留 BLINK 表现为"逐次抖动 + 长期平均准确"（``TC=1.1`` →
间隔 ``{3,4}`` 拍）；该"长期平均准确"仅在"固定 ``dt_ms=500`` + 整数毫秒 + 余数
保留"下成立（含义=保持源参数对应均值），``{3,4}`` 是**本实现行为**，与真实
CODESYS 是否一致**未在真机验证**。
"""

FOP1_DEFAULT_TB_SEC: float = 0.5
"""``FOP1`` 在 ST 中未传 ``TB``，使用 ``APCHSFOP`` 的 VAR_INPUT 声明默认值（秒）。

与 ``src/blocks/apchsfop.py`` 的 ``TB=0.5`` 默认值同步；如未来 ST 源头修改
``APCHSFOP`` 默认值，本常量必须**同步更新**。
"""

FOP1_KG: float = 1.0
"""``FOP1.KG`` 在 ST 中固定为常量 1（不是参数）。"""


class APCCDOutput(TypedDict):
    AV: float
    CD_BH: float
    ZLOUT: float


class APCCD:
    """重叠控制业务块。

    跨周期状态（全部 ``self.*``，保留 ST 变量名）：

    * 直接持有的 ST VAR / VAR_OUTPUT：
        - ``AV``      —— 模型输出（VAR_OUTPUT，等于周期末 ``AV_TEMP``）
        - ``CD_BH``   —— 专家输出（VAR_OUTPUT）
        - ``JZ_ZUP3 / JZ_ZUP2 / JZ_Z1`` —— 窗口均值延迟链
        - ``AV_TEMP`` —— 中间输出保持量
        - ``FLG``     —— 方向标志（冷启动 0.0，对应 ST 未显式赋初值的默认零值）

    * 嵌套的 FB 实例（保留 ST 实例名）：
        - ``BLINK1 / R_TRIG1 / TON1 / STAT1 / FOP1 / R_TRIG2``
        - 其中 ``STAT1`` 是 :class:`APCSTATISTICS`（ST 实例名 ``STATISTICS_REAL``）、
          ``FOP1`` 是 :class:`APCHSFOP`

    公开接口::

        step(dt_ms, *, SP, PV, TS, ZLOUT, TC, TZ, CDH, CDL, TL,
             CD_K_J=1.0, CD_K_D=1.0, CD_K_FD=1.0,
             CD_GD=2.0, CD_K=0.5, AD=True)
            -> {"AV": ..., "CD_BH": ..., "ZLOUT": ...}
    """

    def __init__(self) -> None:
        self.AV: float = 0.0
        self.CD_BH: float = 0.0

        self.JZ_ZUP3: float = 0.0
        self.JZ_ZUP2: float = 0.0
        self.JZ_Z1: float = 0.0

        self.AV_TEMP: float = 0.0
        self.FLG: float = 0.0

        self.BLINK1 = BLINK()
        self.R_TRIG1 = R_TRIG()
        self.TON1 = TON()
        self.STAT1 = APCSTATISTICS()
        self.FOP1 = APCHSFOP()
        self.R_TRIG2 = R_TRIG()

    def step(
        self,
        dt_ms: int,
        *,
        SP: float,
        PV: float,
        TS: bool,
        ZLOUT: float,
        TC: float,
        TZ: float,
        CDH: float,
        CDL: float,
        TL: float,
        CD_K_J: float = 1.0,
        CD_K_D: float = 1.0,
        CD_K_FD: float = 1.0,
        CD_GD: float = 2.0,
        CD_K: float = 0.5,
        AD: bool = True,
    ) -> APCCDOutput:
        # 1) BLINK1 + R_TRIG1：周期采样节拍
        #    TC 是业务显式参数（秒），仅在此转成 TIMELOW_ms。
        timelow_ms = real_to_time_ms(TC * 1000.0)
        blink_out = self.BLINK1.step(
            dt_ms,
            ENABLE=True,
            TIMELOW_ms=timelow_ms,
            TIMEHIGH_ms=BLINK_TIMEHIGH_MS,
        )
        rtrig1_q = self.R_TRIG1.step(blink_out)

        # 2) 窗口历史移位：必须在 STAT1.step(RESET=...) 之前（核心不变量）
        #    SEL(Q, IN0, IN1): Q=True 取 IN1，故仅 Q=True 时移位。
        if rtrig1_q:
            self.JZ_ZUP3 = self.JZ_ZUP2
            self.JZ_ZUP2 = self.JZ_Z1

        # 3) STAT1 统计 + JZ_Z1 更新（采样事件拍 RESET，AVG=0.0）
        stat_out = self.STAT1.step(dt_ms, IN=PV, RESET=rtrig1_q)
        self.JZ_Z1 = stat_out["AVG"]

        # 4) FOP1 动态项 + CD_BH（专家输出）
        #    TZ 是秒；FOP1.TC=TZ*2 仍是秒。TB 用 FB 声明默认值 0.5 秒。
        fop_out = self.FOP1.step(
            dt_ms,
            IN=self.JZ_ZUP2 - self.JZ_ZUP3,
            TC=TZ * 2.0,
            KG=FOP1_KG,
            TB=FOP1_DEFAULT_TB_SEC,
        )
        # SEL(AD, 1, -1): AD=False -> +1（正作用），AD=True -> -1（反作用）
        self.CD_BH = (
            (PV - SP) * CD_K_J + fop_out["AV"] * CD_K_D
        ) * (-1.0 if AD else 1.0)

        # 5) TON1 延时进入（保留 >= 比较；TL 秒 -> PT_ms）
        pt_ms = real_to_time_ms(TL * 1000.0)
        ton_q, _ = self.TON1.step(
            dt_ms,
            IN=abs(self.CD_BH) >= CD_GD,
            PT_ms=pt_ms,
        )

        # 6) TON.Q 且未跟踪：更新 AV_TEMP 与方向 FLG
        if ton_q and not TS:
            self.AV_TEMP = min(max(self.CD_BH * CD_K_FD, CDL), CDH)
            # SEL(CD_BH>0, SEL(CD_BH<0, FLG, -1), 1)
            if self.CD_BH > 0:
                self.FLG = 1.0
            elif self.CD_BH < 0:
                self.FLG = -1.0
            # CD_BH == 0 时 FLG 保持前值（严禁写成 sign()）

        # 7) R_TRIG2：退出拐点或进入跟踪时回补 ZLOUT（仅一次）
        rtrig2_clk = (
            (abs(self.CD_BH) < CD_GD or TS) and self.AV_TEMP != 0.0
        )
        rtrig2_q = self.R_TRIG2.step(rtrig2_clk)

        zlout_out = ZLOUT
        if rtrig2_q:
            self.AV_TEMP = min(max(CD_GD * CD_K_FD * CD_K * self.FLG, CDL), CDH)
            zlout_out = zlout_out + self.AV_TEMP
            self.AV_TEMP = 0.0

        # 8) 跟踪切除（必须在 R_TRIG2 回补之后）
        if TS:
            self.AV_TEMP = 0.0

        self.AV = self.AV_TEMP
        return {"AV": self.AV, "CD_BH": self.CD_BH, "ZLOUT": zlout_out}
