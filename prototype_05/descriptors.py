"""块描述符与注册表（COMPONENT_CONTRACT v2.1 的原型子集）。

- 键 = (block_type, variant)，重复注册显式报错（§5）。
- 标准库块源码零改动（D1）：本原型只读复用 src/primitives/timers.TON。
- 原型只实现 omit_policy="use_default" 语义；其余枚举值保留字段、阶段 1 实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from src.primitives.timers import TON

OMIT_POLICIES = ("use_default", "required", "keep_previous", "none_means_no_write")


@dataclass
class Pin:
    name: str
    iec_type: str
    default: Any = None
    kind: str = "VAR_INPUT"
    omit_policy: str = "use_default"


@dataclass
class BlockDescriptor:
    block_type: str
    cls: type
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    inouts: list = field(default_factory=list)
    variant: str = "engineering"
    descriptor_version: str = "0.5-proto"
    output_access: dict = field(default_factory=dict)   # 管脚名 -> 'attr:NAME' | 'return:KEY'
    call_adapter: Optional[Callable] = None  # (instance, dt_ms, resolved_inputs, inout_refs) -> outputs


class DuplicateDescriptorError(Exception):
    pass


REGISTRY: dict = {}   # (block_type, variant) -> BlockDescriptor


def register(desc: BlockDescriptor) -> None:
    key = (desc.block_type, desc.variant)
    if key in REGISTRY:
        raise DuplicateDescriptorError(key)
    REGISTRY[key] = desc


def resolve(block_type: str, mode: str) -> BlockDescriptor:
    # E / fidelity_f1 共用 engineering 变体（COMPONENT_CONTRACT §5）；原型无 f2
    key = (block_type, "engineering")
    if key not in REGISTRY:
        raise KeyError(f"未注册的库块: {key}")
    return REGISTRY[key]


def collect_outputs(desc: BlockDescriptor, instance, ret) -> dict:
    """按 output_access 收集输出（'attr:NAME' 读 self 属性；'return:KEY' 取返回 dict 键）。"""
    out = {}
    for pin in desc.outputs:
        rule = desc.output_access[pin.name]
        how, name = rule.split(":", 1)
        if how == "attr":
            out[pin.name] = getattr(instance, name)
        elif how == "return":
            out[pin.name] = ret[name]
        else:
            raise ValueError(rule)
    return out


# ---------------------------------------------------------------- TON 描述符

def _ton_adapter(instance: TON, dt_ms: int, ins: dict, inout_refs: dict) -> dict:
    ret = instance.step(dt_ms=dt_ms, IN=ins["IN"], PT_ms=ins["PT"])
    return collect_outputs(TON_DESCRIPTOR, instance, ret)


TON_DESCRIPTOR = BlockDescriptor(
    block_type="TON",
    cls=TON,
    inputs=[
        Pin("IN", "BOOL", default=False),
        Pin("PT", "TIME", default=0),      # 管脚名 PT，适配到 step kwarg PT_ms
    ],
    outputs=[
        Pin("Q", "BOOL", kind="VAR_OUTPUT"),
        Pin("ET", "TIME", kind="VAR_OUTPUT"),
    ],
    output_access={"Q": "attr:Q", "ET": "attr:ET_ms"},
    call_adapter=_ton_adapter,
)

register(TON_DESCRIPTOR)
