"""L2 组件模型：``Pin`` / ``BlockSchema``（纯数据）与 ``RuntimeAdapter``
（进程内绑定）拆分（``docs/COMPONENT_CONTRACT.md`` v2.1 §3/§3.1）。

拆分方向（v2.1 §3.1）严格落实：

- ``Pin`` / ``BlockSchema`` **只含可序列化纯数据**——管脚名、IEC 类型、
  默认值、pin kind、省略语义枚举、状态变量、版本、``output_access`` 字符串
  规则。它们不持有 ``class`` / ``callable`` / 实例 / 锁 / 运行能力，可稳定
  ``to_json()`` 成仅含 JSON 基本类型的结构；文档 / 导入导出工具只依赖
  ``BlockSchema``。
- ``RuntimeAdapter`` 单独承载 ``cls`` / ``ctor_args`` 构造依赖 /
  ``call_adapter`` 调用约定 / 可选 ``serializer``。Schema 序列化不依赖它。

**隔离与不可变**：``BlockSchema`` 在构造时把管脚列表规范化为 ``tuple``、
把集合规范化为 ``frozenset``、把 ``output_access`` 规范化为不可变映射；因此
调用方事后修改传入的 dict / list **不会**让已注册的 Schema 漂移
（COMPONENT_CONTRACT §5 "解析结果不得因调用方修改输入字典/列表而漂移"）。

**诚实边界**：本模块只定义模型与加载期结构校验，不实现块行为、不做数值
语义、不接触真机；``call_adapter`` 的调用正确性由代表性 adapter 的对照
测试证明（§7），Schema 通过 ≠ 与 CODESYS 语义一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from src.runtime.ir import IEC_TYPES

# ---------------------------------------------------------------------------
# 枚举常量
# ---------------------------------------------------------------------------

#: 管脚方向（与 IR_SPEC §3 接口区段一致）。
PIN_KINDS = frozenset({"VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT"})

#: 输入省略语义枚举（COMPONENT_CONTRACT v2.1 §3 ``OmitPolicy``）。
OMIT_POLICIES = frozenset({
    "use_default",         # 未连线：用 default（= 块源码声明默认）
    "required",            # 必须连线，否则加载期报错
    "keep_previous",       # 首拍用 default，后续省略保持该管脚上次值
    "none_means_no_write",  # 省略/None = 本拍不覆盖块内该值
})

#: 数值变体（COMPONENT_CONTRACT v2.1 §2.x / §5）：engineering（64 位，E/F1
#: 共用）与 fidelity_f2（块级 float32 版，与"零改动"互斥、按需立项）。
NUMERIC_VARIANTS = frozenset({"engineering", "fidelity_f2"})


# ---------------------------------------------------------------------------
# 专用异常
# ---------------------------------------------------------------------------

class DescriptorError(Exception):
    """L2 描述符层错误基类。"""


class SchemaValidationError(DescriptorError):
    """``BlockSchema`` 结构非法（名称/类型/kind/omit/管脚冲突/output_access）。"""


class AdapterBindingError(DescriptorError):
    """``RuntimeAdapter`` 绑定非法（cls/call_adapter/ctor_args/serializer）。"""


# ---------------------------------------------------------------------------
# Pin（纯数据）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pin:
    """单个管脚的可序列化元数据。

    ``name`` = 该块 ``step`` 的 kwarg 名 / 输出键 / ``self`` 属性名；
    ``default`` 只允许 JSON 基本类型或 ``None``（纯数据约束——结构性 IEC
    类型闭环由 Store 声明期核验，不在此复制数值语义）。
    """

    name: str
    iec_type: str
    kind: str = "VAR_INPUT"
    default: Any = None
    omit_policy: str = "use_default"

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise SchemaValidationError("管脚名非法：%r" % (self.name,))
        if self.iec_type not in IEC_TYPES:
            raise SchemaValidationError(
                "管脚 '%s' 的 IEC 类型非法：%r" % (self.name, self.iec_type))
        if self.kind not in PIN_KINDS:
            raise SchemaValidationError(
                "管脚 '%s' 的 kind 非法：%r（应为 %s）"
                % (self.name, self.kind, "/".join(sorted(PIN_KINDS))))
        if self.omit_policy not in OMIT_POLICIES:
            raise SchemaValidationError(
                "管脚 '%s' 的 omit_policy 非法：%r（应为 %s）"
                % (self.name, self.omit_policy, "/".join(sorted(OMIT_POLICIES))))
        if self.default is not None and not isinstance(
                self.default, (bool, int, float, str)):
            raise SchemaValidationError(
                "管脚 '%s' 的 default 必须是 JSON 基本类型或 None，得到 %r"
                "（Schema 须可序列化，不得持有非数据对象）"
                % (self.name, self.default))

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "iec_type": self.iec_type,
            "kind": self.kind,
            "default": self.default,
            "omit_policy": self.omit_policy,
        }


# ---------------------------------------------------------------------------
# output_access 规则解析
# ---------------------------------------------------------------------------

def parse_output_access(rule: str):
    """解析 ``output_access`` 规则字符串，返回 ``(kind, key)``。

    合法形态（COMPONENT_CONTRACT §3）：``'return:KEY'``（从 ``step`` 返回值
    取键——KEY 为纯数字时按 tuple/list 下标，否则按 dict 键）与
    ``'attr:NAME'``（从 ``self.NAME`` 读）。其余一律拒绝。
    """
    if not isinstance(rule, str) or ":" not in rule:
        raise SchemaValidationError(
            "output_access 规则非法：%r（应为 'return:KEY' 或 'attr:NAME'）"
            % (rule,))
    kind, key = rule.split(":", 1)
    if kind not in ("return", "attr") or not key:
        raise SchemaValidationError(
            "output_access 规则非法：%r（前缀须为 return/attr 且键非空）"
            % (rule,))
    return kind, key


def collect_outputs(output_access: Mapping[str, str], instance: Any,
                    return_value: Any) -> dict:
    """按 ``output_access`` 从块实例 / ``step`` 返回值收集输出值字典。

    这是 ``call_adapter`` 收尾"按 output_access 收集输出"的唯一共享实现
    （COMPONENT_CONTRACT §3）：``attr:`` 读 ``self.<NAME>``；``return:`` 读
    返回值——纯数字键按下标（tuple 输出回收），否则按 dict 键。
    """
    outputs: dict = {}
    for pin, rule in output_access.items():
        kind, key = parse_output_access(rule)
        if kind == "attr":
            outputs[pin] = getattr(instance, key)
        else:                                       # return:
            if key.isdigit():
                outputs[pin] = return_value[int(key)]
            else:
                outputs[pin] = return_value[key]
    return outputs


# ---------------------------------------------------------------------------
# BlockSchema（纯数据，可 JSON 序列化）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BlockSchema:
    """块的可序列化 Schema（COMPONENT_CONTRACT v2.1 §3.1 ``BlockSchema``）。

    构造时完成结构校验并把容器规范化为不可变形态；无默认字段在前，含默认
    字段在后（dataclass 约束）。``output_access`` 必须覆盖全部 VAR_OUTPUT
    管脚（否则输出无从回收），键不得指向非输出管脚。
    """

    block_type: str
    inputs: tuple = ()
    outputs: tuple = ()
    inouts: tuple = ()
    variant: str = "engineering"
    descriptor_version: str = "1.0"
    state_vars: frozenset = frozenset()
    retainable: frozenset = frozenset()
    init_overridable: frozenset = frozenset()
    hmi_writable: frozenset = frozenset()
    output_access: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.block_type or not isinstance(self.block_type, str):
            raise SchemaValidationError("block_type 非法：%r" % (self.block_type,))
        if self.variant not in NUMERIC_VARIANTS:
            raise SchemaValidationError(
                "variant 非法：%r（应为 %s）"
                % (self.variant, "/".join(sorted(NUMERIC_VARIANTS))))
        if not self.descriptor_version or not isinstance(
                self.descriptor_version, str):
            raise SchemaValidationError(
                "descriptor_version 非法：%r" % (self.descriptor_version,))

        # 规范化为不可变 tuple，并核对各集合的 kind 一致（跨集合方向闭环）
        inputs = self._as_pins(self.inputs, "VAR_INPUT")
        outputs = self._as_pins(self.outputs, "VAR_OUTPUT")
        inouts = self._as_pins(self.inouts, "VAR_IN_OUT")
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "outputs", outputs)
        object.__setattr__(self, "inouts", inouts)

        # 管脚名：集合内不得重复，跨集合不得冲突
        seen: dict = {}
        for coll_name, coll in (("inputs", inputs), ("outputs", outputs),
                                ("inouts", inouts)):
            local: set = set()
            for pin in coll:
                if pin.name in local:
                    raise SchemaValidationError(
                        "%s 内管脚名重复：'%s'" % (coll_name, pin.name))
                local.add(pin.name)
                if pin.name in seen:
                    raise SchemaValidationError(
                        "管脚 '%s' 跨集合冲突（%s 与 %s）"
                        % (pin.name, seen[pin.name], coll_name))
                seen[pin.name] = coll_name

        # required 只对输入有意义（输出/inout 是块产出，不谈"必须连线"）
        for pin in outputs + inouts:
            if pin.omit_policy != "use_default":
                raise SchemaValidationError(
                    "管脚 '%s'（%s）不应声明输入省略语义 %r"
                    % (pin.name, pin.kind, pin.omit_policy))

        # 规范化集合字段
        for name in ("state_vars", "retainable", "init_overridable",
                     "hmi_writable"):
            value = getattr(self, name)
            if not all(isinstance(s, str) for s in value):
                raise SchemaValidationError("%s 必须是字符串集合" % name)
            object.__setattr__(self, name, frozenset(value))
        if not self.retainable <= self.state_vars:
            raise SchemaValidationError(
                "retainable 必须是 state_vars 的子集，越界项：%s"
                % sorted(self.retainable - self.state_vars))
        # init_overridable（COMPONENT_CONTRACT §3.1，WP-20260728-041）：仅“上电/
        # 装载时允许覆盖的实例状态字段”，必须是 state_vars 的子集——普通 step
        # 输入管脚或未声明状态不得冒充装载配置。与 hmi_writable 正交（后者为
        # 运行期在线写候选，本包保持空集、不实现运行期写入）：两集合是相互
        # 独立的分类轴，不共用授权，也不因一方声明而放宽另一方。
        if not self.init_overridable <= self.state_vars:
            raise SchemaValidationError(
                "init_overridable 必须是 state_vars 的子集（仅上电/装载时可覆盖的"
                "实例状态字段，与 hmi_writable 正交），越界项：%s"
                % sorted(self.init_overridable - self.state_vars))

        # output_access：键须为已声明输出管脚，规则合法，且覆盖全部输出
        out_names = {p.name for p in outputs}
        access = dict(self.output_access)
        for pin_name, rule in access.items():
            if pin_name not in out_names:
                raise SchemaValidationError(
                    "output_access 键 '%s' 不是已声明的 VAR_OUTPUT 管脚"
                    % pin_name)
            parse_output_access(rule)              # 规则形态校验
        missing = out_names - set(access)
        if missing:
            raise SchemaValidationError(
                "output_access 未覆盖输出管脚：%s（输出无从回收）"
                % sorted(missing))
        object.__setattr__(self, "output_access", MappingProxyType(dict(access)))

    @staticmethod
    def _as_pins(coll, expect_kind: str) -> tuple:
        pins = []
        for pin in coll:
            if not isinstance(pin, Pin):
                raise SchemaValidationError(
                    "%s 集合含非 Pin 项：%r" % (expect_kind, pin))
            if pin.kind != expect_kind:
                raise SchemaValidationError(
                    "管脚 '%s' 声明 kind=%s，却置于 %s 集合"
                    % (pin.name, pin.kind, expect_kind))
            pins.append(pin)
        return tuple(pins)

    # ---- 查询 ----

    def all_pins(self) -> tuple:
        return self.inputs + self.outputs + self.inouts

    def pin(self, name: str) -> Optional[Pin]:
        for p in self.all_pins():
            if p.name == name:
                return p
        return None

    def to_json(self) -> dict:
        """导出仅含 JSON 基本类型的结构（可落盘 / 跨进程 / 差异评审）。"""
        return {
            "block_type": self.block_type,
            "variant": self.variant,
            "descriptor_version": self.descriptor_version,
            "inputs": [p.to_json() for p in self.inputs],
            "outputs": [p.to_json() for p in self.outputs],
            "inouts": [p.to_json() for p in self.inouts],
            "state_vars": sorted(self.state_vars),
            "retainable": sorted(self.retainable),
            "init_overridable": sorted(self.init_overridable),
            "hmi_writable": sorted(self.hmi_writable),
            "output_access": dict(self.output_access),
        }


# ---------------------------------------------------------------------------
# RuntimeAdapter（进程内绑定）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuntimeAdapter:
    """块的进程内运行绑定（COMPONENT_CONTRACT v2.1 §3.1 ``RuntimeAdapter``）。

    - ``cls``：已迁移的 Python 块类（**零改动**，D1）。
    - ``ctor_args``：**共享构造依赖名** tuple（如 ``("license_context",)``）；
      由运行时从任务注入的共享依赖按声明顺序**位置**解析，保持"同任务内共享
      同一 context"语义。**注意**与 ``InstanceDecl.ctor_args``（单实例关键字
      构造配置 dict）是两个不同概念，二者不得互相遮蔽（``construct`` 对名称
      冲突 fail-closed）。
    - ``call_adapter(instance, dt_ms, resolved_inputs, inout_refs) ->
      outputs_dict``：唯一与块打交道的函数——按真实签名调用 ``step``、注入
      ``VAR_IN_OUT`` 引用、按 ``output_access`` 收集输出。
    - ``serializer``：可选实例状态 <-> dict（阶段 8 快照/恢复），本包不实现。
    """

    cls: type
    call_adapter: Callable
    ctor_args: tuple = ()
    serializer: Optional[Callable] = None

    def __post_init__(self) -> None:
        if not isinstance(self.cls, type):
            raise AdapterBindingError("cls 必须是类，得到 %r" % (self.cls,))
        if not callable(self.call_adapter):
            raise AdapterBindingError("call_adapter 必须可调用")
        if not isinstance(self.ctor_args, tuple) \
                or not all(isinstance(a, str) and a for a in self.ctor_args):
            raise AdapterBindingError(
                "ctor_args 必须是非空字符串的 tuple，得到 %r" % (self.ctor_args,))
        if self.serializer is not None and not callable(self.serializer):
            raise AdapterBindingError("serializer 若提供必须可调用")

    def construct(self, dependencies: Mapping[str, Any],
                  ctor_kwargs: Optional[Mapping[str, Any]] = None) -> Any:
        """构造块实例：共享依赖按 ``ctor_args`` 位置注入 + 单实例关键字配置。

        - ``dependencies``：任务级共享构造依赖映射（如 ``license_context``），
          按 ``self.ctor_args`` 声明顺序**位置**送入；缺依赖 fail-closed。
        - ``ctor_kwargs``（可选，来自 IR ``InstanceDecl.ctor_args``）：单实例
          **关键字**构造配置，只按关键字送入块构造器；与共享依赖名称冲突时
          fail-closed（不得位置/关键字遮蔽任务依赖）。``None`` 时退化为旧的
          纯共享依赖构造，保持既有无参配置调用兼容。授权（键是否属该块
          ``BlockSchema.init_overridable``）与值类型/范围校验属启动装配层
          （``src/runtime/parameters.py``），本方法只做依赖解析、名称冲突
          闸门并把关键字透传给块构造器（未知关键字由块构造器自身拒绝）。
        """
        args = []
        for name in self.ctor_args:
            if name not in dependencies:
                raise AdapterBindingError(
                    "构造依赖 '%s' 未注入（%s 无法实例化）"
                    % (name, self.cls.__name__))
            args.append(dependencies[name])
        kwargs = dict(ctor_kwargs) if ctor_kwargs else {}
        conflict = set(kwargs) & set(self.ctor_args)
        if conflict:
            raise AdapterBindingError(
                "实例构造配置 %s 与共享构造依赖同名，不能遮蔽任务依赖"
                "（fail-closed）" % sorted(conflict))
        return self.cls(*args, **kwargs)
