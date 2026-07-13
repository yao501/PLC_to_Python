"""``LicenseContext``：授权相关全局变量的每实例容器。

对应 CODESYS 全局变量（GVL）中的授权字段：``BD_MM1~BD_MM4``、``KZQBDYZMK``
（``BD_MMYZ_ST`` 实例）、``BD_ERROR1~BD_ERROR9``。

**为什么用每实例 Context 而不是模块级单例**：Python 模块级全局会在多个
Runtime / 多个测试间相互污染。本类让每个 Runtime 实例独立持有自己的授权
全局状态（任务书 §11）。

职责边界（本轮严格限定）：

* 提供授权全局变量与明确的访问入口。
* ``KZQBDYZMK`` 每次验证**实时读取**本 Context 当前的 ``BD_MM1~BD_MM4``
  （通过 ``password_getter`` 闭包），不在构造时拷贝缓存。
* **不**实现 ``APCPIDZZD`` / ``APCPID`` 的错误累加逻辑——这里只提供
  ``BD_ERROR1~9`` 变量与门控接口；后续业务块自行使用：

  .. code-block:: python

      context.KZQBDYZMK.step(...)
      if context.KZQBDYZMK.OK % 10000 == 0:
          ...        # 授权通过
      else:
          context.BD_ERROR5 += 1   # APCPIDZZD 用 BD_ERROR5，APCPID 用 BD_ERROR1
"""

from __future__ import annotations

from typing import Tuple

from src.licensing import (
    BD_MMYZ,
    BD_MMYZ_ST,
    DateTimeProvider,
    SerialTextProvider,
    to_dword,
)

NUM_BD_ERRORS = 9


class LicenseContext:
    """每个 Runtime 实例独立拥有的授权全局状态。

    构造时注入机器标识 Provider 与时间 Provider；内部装配一个
    ``KZQBDYZMK = BD_MMYZ_ST(BD_MMYZ(provider, password_getter), dtp)``，
    且 ``password_getter`` 始终实时读取本实例的 ``BD_MM1~BD_MM4``。
    """

    def __init__(
        self,
        serial_provider: SerialTextProvider,
        datetime_provider: DateTimeProvider,
    ) -> None:
        # 密码全局变量（DWORD 语义），默认 0。
        self.BD_MM1: int = 0
        self.BD_MM2: int = 0
        self.BD_MM3: int = 0
        self.BD_MM4: int = 0

        # 错误统计全局变量（REAL），默认 0.0。
        self.BD_ERROR1: float = 0.0
        self.BD_ERROR2: float = 0.0
        self.BD_ERROR3: float = 0.0
        self.BD_ERROR4: float = 0.0
        self.BD_ERROR5: float = 0.0
        self.BD_ERROR6: float = 0.0
        self.BD_ERROR7: float = 0.0
        self.BD_ERROR8: float = 0.0
        self.BD_ERROR9: float = 0.0

        # 每次验证实时读取本实例当前密码（不在构造时拷贝缓存）。
        validator = BD_MMYZ(serial_provider, self._read_passwords)
        self.KZQBDYZMK = BD_MMYZ_ST(validator, datetime_provider)

    def _read_passwords(self) -> Tuple[int, int, int, int]:
        return (self.BD_MM1, self.BD_MM2, self.BD_MM3, self.BD_MM4)

    def set_passwords(self, mm1: int, mm2: int, mm3: int, mm4: int) -> None:
        """受控写入四个密码（按 DWORD 规约，且拒绝 bool 偷渡为数值）。

        这是推荐的密码写入入口；``to_dword`` 会拒绝 ``bool`` 输入
        （任务书 §4.5）。不引入配置文件 / 磁盘持久化 / UI。
        """
        self.BD_MM1 = to_dword(mm1)
        self.BD_MM2 = to_dword(mm2)
        self.BD_MM3 = to_dword(mm3)
        self.BD_MM4 = to_dword(mm4)
