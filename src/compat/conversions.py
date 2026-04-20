"""ST / CODESYS 基础类型转换 helper（最小闭包）。

范围（按《APCHXHCL_v2_最小转换层与风险收口任务书》§三.A）：

* :func:`real_to_int` —— 承接 ST ``REAL_TO_INT(...)``
* :func:`real_to_time_ms` —— 承接 ST ``REAL_TO_TIME(...)``，项目内部统一 ``int ms``
* :func:`int_to_real` —— 基础反向转换，方便后续业务块复用

设计原则：

1. **无状态**：纯函数，无副作用，无 ``self``；
2. **集中决策**：所有 ST→Python 的取整 / 单位 / 边界策略集中在此；
3. **保守兼容**：当前选择与 IEC 61131-3 默认语义一致的实现；
   后续若发现真实 CODESYS 运行时差异，只需改本模块一处；
4. **最小范围**：严禁为"以后也许会用到"扩展新转换，新的 ``XXX_TO_YYY``
   必须在新的任务书中明确要求后再补。
"""

from __future__ import annotations

import math


def real_to_int(x: float) -> int:
    """承接 ST ``REAL_TO_INT(x)``。

    当前约定：**round to nearest, ties to even（银行家舍入）**。

    选择依据：

    * IEC 61131-3 标准默认定义即为 "round to nearest even"；
    * Python 内置 :func:`round` 的舍入策略正好一致；
    * 对 APCHXHCL 的 ``60/TB`` 场景更稳健：
      ``TB=0.3`` 时 ``60/0.3 ≈ 199.9999...`` 浮点误差下，
      ``int(...)`` 截断会得到 ``199`` 少一帧，``round(...)`` 得到 ``200`` 正确。

    参数
    ----
    x : float
        任意实数。

    返回
    ----
    int
        按银行家舍入得到的整数。对 ``NaN`` / ``±Inf`` 抛 ``ValueError``，
        与 CODESYS 在异常浮点值下的行为一致地"拒绝静默吞掉"。

    示例
    ----
    >>> real_to_int(120.0)
    120
    >>> real_to_int(0.5)
    0
    >>> real_to_int(1.5)
    2
    >>> real_to_int(2.5)
    2
    >>> real_to_int(-0.5)
    0
    >>> real_to_int(60 / 0.3)  # 浮点误差下仍得到业务期望 200
    200
    """
    if not isinstance(x, (int, float)):
        raise TypeError(f"real_to_int expects int/float, got {type(x).__name__}")
    if isinstance(x, float):
        if math.isnan(x):
            raise ValueError("real_to_int: NaN is not convertible to INT")
        if math.isinf(x):
            raise ValueError("real_to_int: ±Inf is not convertible to INT")
    return int(round(x))


def real_to_time_ms(x_ms: float) -> int:
    """承接 ST ``REAL_TO_TIME(x)``，项目内部统一到 ``int ms``。

    语义契约：

    * 输入 ``x_ms`` 的**单位是毫秒**，允许带小数（如 ``TL*1000`` 这类表达式）。
    * 输出为 ``int`` 毫秒，与定时器 :class:`~src.primitives.TOF` 的
      ``PT_ms`` 接口完全一致，不引入 :class:`datetime.timedelta`，
      也不与 Python 真实时钟耦合。
    * 负值作为非法输入（CODESYS ``TIME`` 非负）抛 ``ValueError``；
      ``NaN`` / ``±Inf`` 同样拒绝。

    为什么显式提供这个 helper（而不是复用 :func:`real_to_int`）：

    * 即使内部实现相似，**语义领域不同**：一个是"算术取整"，一个是
      "时间类型转换"。分开命名让业务代码一眼能看出意图，并为未来
      可能的单位策略差异（如 ``TIME_OF_DAY``、纳秒等）留出扩展点。

    示例
    ----
    >>> real_to_time_ms(60_000.0)   # 60 秒
    60000
    >>> real_to_time_ms(0.0)
    0
    >>> real_to_time_ms(1500.4)
    1500
    """
    if not isinstance(x_ms, (int, float)):
        raise TypeError(
            f"real_to_time_ms expects int/float, got {type(x_ms).__name__}"
        )
    if isinstance(x_ms, float):
        if math.isnan(x_ms):
            raise ValueError("real_to_time_ms: NaN is not a valid TIME value")
        if math.isinf(x_ms):
            raise ValueError("real_to_time_ms: ±Inf is not a valid TIME value")
    if x_ms < 0:
        raise ValueError(
            f"real_to_time_ms: negative duration not allowed (got {x_ms})"
        )
    return int(round(x_ms))


def int_to_real(x: int) -> float:
    """承接 ST ``INT_TO_REAL(x)``。

    Python 的 ``int`` 与 ``float`` 互转极其朴素，但仍提供该 helper 用于：

    * 集中承载单一职责，不让业务块直接 ``float(x)`` 散落；
    * 拒绝非整数输入（如 ``bool`` 会被 ``isinstance(int)`` 误判，特判掉）。

    示例
    ----
    >>> int_to_real(3)
    3.0
    >>> int_to_real(-5)
    -5.0
    >>> int_to_real(0)
    0.0
    """
    if isinstance(x, bool):
        raise TypeError("int_to_real does not accept bool")
    if not isinstance(x, int):
        raise TypeError(f"int_to_real expects int, got {type(x).__name__}")
    return float(x)
