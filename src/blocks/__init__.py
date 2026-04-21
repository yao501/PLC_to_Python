"""业务/组合功能块集合。

按阶段二《02-business-blocks》规则：每一个 FB 一个 Python 类，
严格复用 ``src.primitives`` 中已迁移的基础原语。
"""

from .apchsfop import APCHSFOP
from .apchxhcl import APCHXHCL
from .apcstatistics import APCSTATISTICS

__all__ = ["APCHSFOP", "APCHXHCL", "APCSTATISTICS"]
