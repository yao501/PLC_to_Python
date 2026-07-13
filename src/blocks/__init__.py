"""业务/组合功能块集合。

按阶段二《02-business-blocks》规则：每一个 FB 一个 Python 类，
严格复用 ``src.primitives`` 中已迁移的基础原语。
"""

from .apccd import APCCD
from .apcgcq import APCGCQ
from .apchsaccum import APCHSACCUM
from .apchsfop import APCHSFOP
from .apchshllim import APCHSHLLIM
from .apchsratelim import APCHSRATELIM
from .apchxhcl import APCHXHCL
from .apcm import APCM, RealRef
from .apcmautopara import APCMAUTOPARA
from .apcpid import APCPID
from .apcpidzzd import APCPIDZZD
from .apcrsfnautopara import APCRSFNAUTOPARA
from .apcspfinder import APCSPFINDER
from .apcstatistics import APCSTATISTICS

__all__ = [
    "APCCD",
    "APCGCQ",
    "APCHSACCUM",
    "APCHSFOP",
    "APCHSHLLIM",
    "APCHSRATELIM",
    "APCHXHCL",
    "APCM",
    "RealRef",
    "APCMAUTOPARA",
    "APCPID",
    "APCPIDZZD",
    "APCRSFNAUTOPARA",
    "APCSPFINDER",
    "APCSTATISTICS",
]
