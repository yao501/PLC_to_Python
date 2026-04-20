"""CODESYS / IEC 61131-3 兼容层。

定位：**无状态**的基础 helper 集合，用于承接 ST / CODESYS 的内建类型转换。
本目录下的函数**不是**功能块（无 ``step(...)``、无 ``self.*`` 状态），
只做一次性的值变换。

当前范围遵循《APCHXHCL_v2_最小转换层与风险收口任务书》§三.A：
只实现当前业务块立刻需要的最小闭包，不扩散到完整 IEC 转换全家桶。
"""

from .conversions import int_to_real, real_to_int, real_to_time_ms

__all__ = ["real_to_int", "real_to_time_ms", "int_to_real"]
