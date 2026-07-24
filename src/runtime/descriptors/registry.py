"""L2 库块注册表（``docs/COMPONENT_CONTRACT.md`` v2.1 §5）。

唯一注册键为 ``(block_type, variant)``（v2.1 P0 修正：修复多变体互相
覆盖）。注册表存 ``(schema, adapter)`` 对；``resolve(block_type, mode)``
按数值模式选变体：

- ``engineering`` 与 ``fidelity_f1`` 均解析 ``engineering`` 变体；
- ``fidelity_f2`` 必须解析 ``fidelity_f2`` 变体，**缺失即加载期明确失败，
  绝不静默回退 engineering**（否则"位级保真"名存实亡）。

一切非法情形（重复键、Schema/Adapter 类型不符、未知 block、缺变体）都
**显式报错**，不静默覆盖或猜测。错误诊断只用注册键（``block_type`` /
``variant`` 字符串），不对不可信对象做危险字符串化。
"""
from __future__ import annotations

from typing import Optional, Tuple

from src.runtime.descriptors.model import (
    BlockSchema,
    DescriptorError,
    RuntimeAdapter,
)


class RegistryError(DescriptorError):
    """注册表层错误基类。"""


class DuplicateDescriptorError(RegistryError):
    """同 ``(block_type, variant)`` 重复注册（= 工程错误，显式报错）。"""


class UnknownBlockError(RegistryError):
    """解析未注册的 ``block_type``。"""


class MissingVariantError(RegistryError):
    """缺目标变体（如 fidelity_f2 无 f2 变体）——绝不静默降级。"""


def variant_for_mode(mode: str) -> str:
    """数值模式 → 变体：engineering/fidelity_f1 → engineering；
    fidelity_f2 → fidelity_f2。其余模式视为需 f2 之外的 engineering。"""
    return "fidelity_f2" if mode == "fidelity_f2" else "engineering"


class Registry:
    """``(block_type, variant) -> (BlockSchema, RuntimeAdapter)`` 注册表。"""

    def __init__(self) -> None:
        self._entries: dict = {}

    # ---- 注册 ----

    def register(self, schema: BlockSchema, adapter: RuntimeAdapter) -> None:
        if not isinstance(schema, BlockSchema):
            raise RegistryError("register 需要 BlockSchema，得到 %r" % (schema,))
        if not isinstance(adapter, RuntimeAdapter):
            raise RegistryError(
                "block_type '%s' 的 adapter 不是 RuntimeAdapter（Schema/Adapter "
                "不匹配）" % schema.block_type)
        key = (schema.block_type, schema.variant)
        if key in self._entries:
            raise DuplicateDescriptorError(
                "同键重复注册：block_type='%s' variant='%s'"
                % (schema.block_type, schema.variant))
        self._entries[key] = (schema, adapter)

    # ---- 解析 ----

    def resolve(self, block_type: str, mode: str) -> Tuple[BlockSchema,
                                                           RuntimeAdapter]:
        """按数值模式解析 ``(schema, adapter)``。"""
        variant = variant_for_mode(mode)
        entry = self._entries.get((block_type, variant))
        if entry is not None:
            return entry
        if variant == "fidelity_f2":
            raise MissingVariantError(
                "block_type '%s' 缺 fidelity_f2 变体：加载期失败，绝不静默降级到 "
                "engineering（COMPONENT_CONTRACT §5）" % block_type)
        raise UnknownBlockError("block_type '%s' 未注册（engineering 变体）"
                                % block_type)

    def resolve_schema(self, block_type: str, mode: str) -> BlockSchema:
        return self.resolve(block_type, mode)[0]

    def has(self, block_type: str, variant: str = "engineering") -> bool:
        return (block_type, variant) in self._entries

    def keys(self):
        return tuple(sorted(self._entries.keys()))

    def block_types(self):
        return tuple(sorted({bt for bt, _ in self._entries}))
