"""项目级运行契约的默认常量。

这些常量描述 **当前项目** 的默认配置，不是通用工业常量。
主程序可以在启动阶段覆盖它们。
"""

from __future__ import annotations

CYCLE_MS: int = 500
"""生产模式的固定扫描周期，单位毫秒。主程序每 cycle_ms 调用一次所有 step()。"""

STARTUP_INHIBIT_MS: int = 500
"""上电稳定窗口下限，单位毫秒。

仅作为时间维度的必要条件。主程序最终释放 ``system_ready`` 时应叠加
``io_ready / bus_ready / comm_ready / safety_ok`` 等业务 ready 条件。
"""
