"""契约级配置校验。

在主程序启动阶段（或功能块实例化处），调用 :func:`check_pt_ms` 对每个定
时器的 ``PT_ms`` 进行校验，以便及早暴露"周期不匹配"类的配置错误。

严格按 ``00a-runtime-contract`` 规则中 R6 第 7 条实现：

* ``PT_ms < cycle_ms``  → 该定时器在本项目下无实际意义；
* ``PT_ms`` 不是 ``cycle_ms`` 的整数倍 → 时间行为会被扫描周期量化。
"""

from __future__ import annotations

import warnings

from .config import CYCLE_MS


class PTMsConfigWarning(UserWarning):
    """``PT_ms`` 与扫描周期不匹配时抛出的警告。"""


def check_pt_ms(pt_ms: int, name: str = "", cycle_ms: int = CYCLE_MS) -> None:
    """校验一个 ``PT_ms`` 配置是否与当前扫描周期一致。

    Parameters
    ----------
    pt_ms:
        被校验的定时器阈值，单位毫秒。
    name:
        用于日志提示的定时器或业务块名字，可选。
    cycle_ms:
        生产模式的固定扫描周期，默认取自 :mod:`src.config`。

    Notes
    -----
    本函数不会抛异常，仅通过 :class:`PTMsConfigWarning` 发出 warning，
    便于在主程序启动阶段一次性暴露全部配置问题。
    """
    label = f"[{name}] " if name else ""

    if not isinstance(pt_ms, int):
        raise TypeError(
            f"{label}PT_ms 必须为 int 毫秒，收到 {type(pt_ms).__name__}"
        )
    if pt_ms < 0:
        raise ValueError(f"{label}PT_ms 不能为负，收到 {pt_ms}")

    if pt_ms < cycle_ms:
        warnings.warn(
            f"{label}PT_ms={pt_ms} 小于 cycle_ms={cycle_ms}，"
            f"在固定扫描周期下该定时器无实际意义。",
            PTMsConfigWarning,
            stacklevel=2,
        )
        return

    if pt_ms % cycle_ms != 0:
        warnings.warn(
            f"{label}PT_ms={pt_ms} 不是 cycle_ms={cycle_ms} 的整数倍，"
            f"时间行为将被扫描周期量化到 {(pt_ms // cycle_ms) * cycle_ms}"
            f" 或 {((pt_ms // cycle_ms) + 1) * cycle_ms} ms。",
            PTMsConfigWarning,
            stacklevel=2,
        )
