"""启动期参数装载核心与失败关闭装配入口（WP-20260728-041）。

本模块以**已冻结的正式 IR** ``InstanceDecl.ctor_args`` / ``init_overrides``
为唯一参数载体，建立一个公开、可测试的启动装配入口 :func:`build_runtime`：

1. **纯校验并汇总硬错误**：调用既有 ``validate_task``（IR/L2 静态闸门）、
   逐库块实例校验共享构造依赖齐备 / 单实例关键字构造配置授权与取值 /
   ``init_overrides`` 管脚初值结构 / 显式时间参数目录 / ``startup_inhibit_ms``；
2. **若有任何硬错误则一次性失败**（``StartupValidationError``，携带确定顺序的
   错误列表），**不**构建 Store/Executor；
3. 全部硬错误为零时才**构建布局/Store → 构造全部 library runtime → 一次性
   返回** :class:`RuntimeAssembly`。任何阶段失败都不返回半构造对象、不修改
   传入的参数/依赖映射、不在全局 Registry 留下注入或实例缓存；重试同一合法
   输入必得到全新的 Store、Executor 与块实例。

**两个同名概念永久区分**（COMPONENT_CONTRACT §3.1）：

- ``RuntimeAdapter.ctor_args: tuple[str, ...]`` 只表示从任务 ``dependencies``
  注入的**共享**构造依赖名（如 ``license_context``）；
- ``InstanceDecl.ctor_args: dict[str, value]`` 只表示**单实例关键字**构造配置，
  只能命中该实例 ``BlockSchema.init_overridable`` 且必须再过参数类型/值校验；
- ``InstanceDecl.init_overrides`` 继续表示 Store 管脚装载初值，不代表“该输入
  本拍已驱动”。三者不得互相覆盖、位置传递、静默丢弃或以 Python 签名猜测。

**诚实边界（不实现、不伪称）**：本包不发明 YAML/JSON/数据库/环境变量格式、
不读取外部配置文件、不做参数持久化；外部文件解析、参数来源优先级、HMI 在线
写、RETAIN/PERSISTENT 恢复均须另立工作包。``startup_inhibit_ms`` 只做启动配置
校验，**不**据此启动计时器、生成 ``system_ready``、写输出、引入 watchdog 或改变
五步扫描。只校验**实际出现在** ``init_overrides`` 或公开启动配置中的显式装载
值，不声称已验证由 IR 连线、上拍输出、HMI 或现场输入在运行期产生的动态值。
Python 结果不构成 PLC/CODESYS、HAL、真实 I/O、watchdog、执行机构或现场安全
一致性证明。
"""
from __future__ import annotations

import math
import warnings as _pywarnings
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from src.config import CYCLE_MS, STARTUP_INHIBIT_MS
from src.runtime.executor import Executor, check_ctor_value
from src.runtime.ir import (
    InstanceDecl,
    POUDefinition,
    ProgramInstance,
    Task,
)
from src.runtime.loader import IRValidationError, validate_task
from src.runtime.numeric import NumericMode
from src.runtime.standard_functions import default_standard_functions
from src.runtime.descriptors.registry import RegistryError
from src.runtime.store import (
    RuntimeLayout,
    build_runtime_store,
    check_value_type,
)
from src.validation import (
    PTMsConfigWarning,
    TBConfigWarning,
    check_pt_ms,
    check_tb_sample_n_integer,
)


# ---------------------------------------------------------------------------
# 专用异常与可收集告警（稳定、可检查、可聚合；不依赖捕获任意 Exception）
# ---------------------------------------------------------------------------

class StartupError(Exception):
    """启动装配层错误基类。"""


class StartupValidationError(StartupError):
    """启动纯校验阶段汇总的硬错误（一次性失败，不返回半构造运行时）。

    ``errors`` 为确定顺序的错误消息列表（每条含实例路径、块类型、参数名，
    便于启动日志定位）。
    """

    def __init__(self, errors):
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(
            "启动参数装载校验失败（%d 处）：\n  - %s" % (len(self.errors), joined))


@dataclass(frozen=True)
class StartupWarning:
    """可由调用方收集/检查的结构化启动告警（**绝不**升级为启动失败）。

    诊断顺序对相同输入稳定。字段：实例路径 / 块类型 / 参数名 / 原值 / 规则 /
    人类可读消息。
    """

    instance: str
    block_type: str
    field: str
    value: Any
    rule: str
    message: str


# ---------------------------------------------------------------------------
# 显式时间参数目录（必须显式列举，不做按名字后缀的全仓启发式扫描）
# ---------------------------------------------------------------------------

#: 毫秒整数且 >=0 的显式时间装载参数（软告警复用 ``check_pt_ms`` 周期口径）。
_MS_TIME_PARAMS = frozenset({
    ("TON", "PT_ms"), ("TOF", "PT_ms"), ("TP", "PT_ms"),
    ("BLINK", "TIMELOW_ms"), ("BLINK", "TIMEHIGH_ms"),
})

#: 秒制有限实数且 >=0 的显式时间装载参数（硬约束目录）。
_SEC_TIME_PARAMS = frozenset({
    ("APCCD", "TC"), ("APCCD", "TL"),
    ("APCGCQ", "TC"),
    ("APCHSFOP", "TC"), ("APCHSFOP", "TB"),
    ("APCHXHCL", "TL"), ("APCHXHCL", "TC"), ("APCHXHCL", "TB"),
})

#: 仅这两项对 ``TC*1000`` 非 ``cycle_ms`` 整数倍发周期量化 warning
#: （不 round/ceil/coerce）；其它秒制业务时间输入只做非负硬检查、不因名字
#: 相似自动获得未经规范确认的整除规则。
_SEC_CYCLE_MULT_WARN = frozenset({("APCCD", "TC"), ("APCGCQ", "TC")})

#: 仅 APCHXHCL.TB 复用 ``60/TB`` 整数性 warning，且仅在 ``TB > 0`` 时。
_TB_SAMPLE_WARN = ("APCHXHCL", "TB")


# ---------------------------------------------------------------------------
# 装配结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuntimeAssembly:
    """启动装配结果：仅在全部硬错误为零、校验通过后创建并返回。

    暴露通过校验后创建的布局/Store 与 Executor，以及本次收集到的结构化告警
    与已校验的 ``startup_inhibit_ms``。失败时调用方**得不到**本对象（不暴露
    半构造运行时）。
    """

    task: Task
    layout: RuntimeLayout
    executor: Executor
    startup_inhibit_ms: int
    warnings: tuple = field(default_factory=tuple)

    @property
    def store(self):
        """便捷访问：装配后的运行时 Store（= ``layout.store``）。"""
        return self.layout.store


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------

def build_runtime(task: Task, registry, *,
                  dependencies: Optional[Mapping[str, Any]] = None,
                  numeric_mode: Optional[NumericMode] = None,
                  startup_inhibit_ms: Optional[int] = None) -> RuntimeAssembly:
    """启动装配入口：先纯校验并汇总硬错误，全部通过后才构建并返回运行时。

    参数：
      - ``task``：已冻结正式 IR ``Task``（本入口调用 ``validate_task`` 复验）；
      - ``registry``：L2 组件 ``Registry``（``build_default_registry()``）；
      - ``dependencies``：任务级共享构造依赖映射（如 ``{"license_context": ...}``）；
      - ``numeric_mode``：数值模式（缺省 engineering）；
      - ``startup_inhibit_ms``：启动稳定窗口下限配置（缺省引用
        ``src.config.STARTUP_INHIBIT_MS``），只做校验、不驱动任何计时。

    返回 :class:`RuntimeAssembly`；任一硬错误抛 :class:`StartupValidationError`
    且不构建 Store/Executor、不修改传入映射、不污染 Registry。
    """
    errors: list = []
    warnings: list = []

    # (1) IR/L2 静态闸门：把 IR 错误纳入失败关闭（不得让未验证 Task 先进 Store）
    try:
        validate_task(task, registry)
    except IRValidationError as e:
        errors.extend("IR 装载校验：%s" % m for m in e.errors)

    # (2) startup_inhibit_ms：非 bool 整数且 >=0（默认引用 config），不驱动计时
    inhibit, inhibit_err = _validate_startup_inhibit(startup_inhibit_ms)
    if inhibit_err is not None:
        errors.append(inhibit_err)

    # (2b) numeric_mode：None 或 NumericMode 实例。非法类型纳入确定顺序失败
    #      关闭——**不**访问其 ``.mode`` 提前中断（否则字符串/普通对象会在
    #      Executor 构造期泄漏 AttributeError 且此时 Store 已建），退化为
    #      engineering 口径继续汇总同一输入的其它独立启动错误。
    mode_obj, mode_err = _validate_numeric_mode(numeric_mode)
    if mode_err is not None:
        errors.append(mode_err)

    # (3) 逐库块实例（确定顺序）：共享依赖 / ctor_args / init_overrides / 时间目录
    deps = dependencies if dependencies is not None else {}
    mode_str = getattr(mode_obj, "mode", "engineering")
    cycle_ms = task.cycle_ms if (isinstance(task.cycle_ms, int)
                                 and not isinstance(task.cycle_ms, bool)
                                 and task.cycle_ms > 0) else CYCLE_MS
    for path, inst in _iter_library_instances(task):
        _validate_instance(path, inst, registry, mode_str, deps, cycle_ms,
                           errors, warnings)

    if errors:
        # 一次性失败：不构建布局/Store/Executor，不修改任何传入映射，不污染 Registry
        raise StartupValidationError(errors)

    # (4) 构建：布局/Store → 构造全部 library runtime → 成功后一次性返回
    layout = build_runtime_store(task, registry)
    executor = Executor(
        task, layout, numeric_mode=numeric_mode,
        std_functions=default_standard_functions(),
        registry=registry, dependencies=deps)
    return RuntimeAssembly(task=task, layout=layout, executor=executor,
                           startup_inhibit_ms=inhibit,
                           warnings=tuple(warnings))


# ---------------------------------------------------------------------------
# 实例枚举（镜像 build_runtime_store 展开顺序；validate_task 已保证结构）
# ---------------------------------------------------------------------------

def _iter_library_instances(task: Task):
    """按装载展开顺序产出全部库块实例 ``(全路径, InstanceDecl)``。

    与 ``store._expand_instances`` 同一路径口径（PROGRAM 路径 = ``store_prefix``，
    子实例 = 父路径 + "." + 实例名），仅用 ``.get`` / isinstance 守卫，结构破损
    时不崩溃、尽力枚举（IR 错误已在 (1) 汇总）。

    **递归 user-FB 循环保护**：``seen`` 记录当前 DFS 路径上已展开的用户 FB
    定义名（``block_type``）。遇到已在路径上的定义即停止下钻，避免非法 IR
    （如 ``A → B → A``）在实例展开时泄漏 ``RecursionError``——此类循环已由
    ``validate_task``（步 (1)）以确定诊断汇总进 ``errors``，故本入口只需安全
    终止枚举，让 ``build_runtime`` 以 ``StartupValidationError`` 携原 IR 循环
    诊断失败，且不进入 (4) 构建（``build_runtime_store`` / ``Executor`` 均不被调用）。
    """
    pou_lib = task.pou_lib if isinstance(task.pou_lib, dict) else {}

    def walk(definition, path, seen):
        for inst in definition.instances:
            if not isinstance(inst, InstanceDecl):
                continue
            ipath = "%s.%s" % (path, inst.name)
            if inst.kind == "library":
                yield ipath, inst
            elif inst.block_type not in seen:
                sub = pou_lib.get(inst.block_type)
                if isinstance(sub, POUDefinition):
                    yield from walk(sub, ipath, seen | {inst.block_type})

    for prog in task.programs:
        if not isinstance(prog, ProgramInstance):
            continue
        definition = pou_lib.get(prog.definition)
        if isinstance(definition, POUDefinition):
            yield from walk(definition, prog.store_prefix,
                            frozenset({prog.definition}))


# ---------------------------------------------------------------------------
# 单实例校验
# ---------------------------------------------------------------------------

def _validate_instance(path, inst, registry, mode_str, deps, cycle_ms,
                       errors, warnings) -> None:
    try:
        schema, adapter = registry.resolve(inst.block_type, mode_str)
    except RegistryError as e:
        errors.append(
            "库块实例 '%s'：无法在 L2 注册表解析 block_type '%s'（模式 %s）：%s"
            % (path, inst.block_type, mode_str, e))
        return

    # (a) 共享构造依赖齐备（缺共享依赖 fail-closed）
    for dep_name in adapter.ctor_args:
        if dep_name not in deps:
            errors.append(
                "库块实例 '%s'（%s）缺共享构造依赖 '%s'（未注入 dependencies）"
                % (path, inst.block_type, dep_name))

    # (b) 单实例关键字构造配置：授权（⊆ init_overridable，非共享依赖名）+ 取值
    shared = set(adapter.ctor_args)
    for key in sorted(inst.ctor_args):
        if key in shared:
            errors.append(
                "库块实例 '%s'（%s）构造配置 '%s' 与共享构造依赖同名，"
                "不能遮蔽任务依赖" % (path, inst.block_type, key))
            continue
        if key not in schema.init_overridable:
            errors.append(
                "库块实例 '%s'（%s）构造配置 '%s' 未被 Schema init_overridable "
                "授权（未声明的构造覆盖一律拒绝，不臆测块构造签名）"
                % (path, inst.block_type, key))
            continue
        ok, why = check_ctor_value(inst.ctor_args[key])
        if not ok:
            errors.append(
                "库块实例 '%s'（%s）构造配置 '%s' %s"
                % (path, inst.block_type, key, why))

    # (c) init_overrides 管脚初值：结构类型（与 Store 同口径）+ 显式时间目录
    pin_by_name = {p.name: p for p in schema.all_pins()}
    for key in sorted(inst.init_overrides):
        pin = pin_by_name.get(key)
        if pin is None:
            errors.append(
                "库块实例 '%s'（%s）init_overrides 指向不存在的管脚 '%s'"
                % (path, inst.block_type, key))
            continue
        value = inst.init_overrides[key]
        if not check_value_type(pin.iec_type, value):
            errors.append(
                "库块实例 '%s'（%s）init_overrides '%s' 值 %r 与管脚类型 %s "
                "结构不匹配（不做隐式转换；bool 不放宽 IEC 类型）"
                % (path, inst.block_type, key, value, pin.iec_type))
            continue
        _validate_time_param(path, inst.block_type, key, value, cycle_ms,
                             errors, warnings)


# ---------------------------------------------------------------------------
# 取值/时间目录校验
# ---------------------------------------------------------------------------

def _validate_time_param(path, block_type, field, value, cycle_ms,
                         errors, warnings) -> None:
    """显式时间参数目录：硬约束 + 结构化周期/整除 warning（warning 不升级为
    失败）。值已通过管脚 IEC 结构类型检查（毫秒项 = int 非 bool；秒制项 = float）。
    """
    key = (block_type, field)
    if key in _MS_TIME_PARAMS:
        # 毫秒整数且 >=0：负值是硬错误；< cycle_ms / 非整数倍复用 check_pt_ms 只发 warning
        if value < 0:
            errors.append("库块实例 '%s'（%s）时间参数 '%s'=%r 必须 >= 0（毫秒整数）"
                          % (path, block_type, field, value))
            return
        _collect_pt_ms_warning(path, block_type, field, value, cycle_ms, warnings)
        return
    if key in _SEC_TIME_PARAMS:
        # 秒制有限实数且 >=0：NaN/±Inf 与负值是硬错误
        if not math.isfinite(value):
            errors.append("库块实例 '%s'（%s）时间参数 '%s'=%r 必须是有限实数"
                          % (path, block_type, field, value))
            return
        if value < 0:
            errors.append("库块实例 '%s'（%s）时间参数 '%s'=%r 必须 >= 0（秒制实数）"
                          % (path, block_type, field, value))
            return
        if key in _SEC_CYCLE_MULT_WARN:
            _collect_tc_cycle_warning(path, block_type, field, value, cycle_ms,
                                      warnings)
        elif key == _TB_SAMPLE_WARN and value > 0:
            # TB=0 是非负合法值：按冻结合同跳过 60/TB warning，不悄悄升级为
            # TB>0 硬错误，也不除零（check_tb_sample_n_integer 的正数前置保持）。
            _collect_tb_warning(path, block_type, field, value, warnings)


def _collect_pt_ms_warning(path, block_type, field, value, cycle_ms, warnings):
    name = "%s.%s" % (path, field)
    with _pywarnings.catch_warnings(record=True) as caught:
        _pywarnings.simplefilter("always")
        check_pt_ms(value, name=name, cycle_ms=cycle_ms)   # value 已 int>=0，不抛
    for w in caught:
        if issubclass(w.category, PTMsConfigWarning):
            warnings.append(StartupWarning(
                instance=path, block_type=block_type, field=field,
                value=value, rule="pt_ms_cycle", message=str(w.message)))


def _collect_tb_warning(path, block_type, field, value, warnings):
    name = "%s.%s" % (path, field)
    with _pywarnings.catch_warnings(record=True) as caught:
        _pywarnings.simplefilter("always")
        check_tb_sample_n_integer(value, name=name)        # value > 0，不抛
    for w in caught:
        if issubclass(w.category, TBConfigWarning):
            warnings.append(StartupWarning(
                instance=path, block_type=block_type, field=field,
                value=value, rule="tb_sample_n", message=str(w.message)))


def _collect_tc_cycle_warning(path, block_type, field, value, cycle_ms,
                              warnings):
    ms = value * 1000.0
    ratio = ms / cycle_ms
    nearest = round(ratio)
    if abs(ratio - nearest) > 1e-9:
        warnings.append(StartupWarning(
            instance=path, block_type=block_type, field=field, value=value,
            rule="tc_cycle_multiple",
            message=("[%s.%s] TC*1000=%s ms 不是 cycle_ms=%s 的整数倍，时间行为"
                     "将被扫描周期量化（只发 warning，不 round/ceil/coerce）"
                     % (path, field, ms, cycle_ms))))


def _validate_startup_inhibit(raw):
    value = STARTUP_INHIBIT_MS if raw is None else raw
    if isinstance(value, bool) or not isinstance(value, int):
        return None, ("startup_inhibit_ms 必须是非 bool 的整数（毫秒），得到 %r"
                      % (raw,))
    if value < 0:
        return None, "startup_inhibit_ms 必须 >= 0，得到 %r" % (value,)
    return value, None


def _validate_numeric_mode(raw):
    """``numeric_mode`` 必须是 ``None`` 或 :class:`NumericMode` 实例。

    返回 ``(mode_obj, err)``：合法时 ``mode_obj`` 为可用的 ``NumericMode``
    （``None`` 退化为默认 engineering），``err`` 为 ``None``；非法类型返回
    ``(None, 稳定错误串)``——调用方据此汇总失败并退化为 engineering 口径继续
    枚举其它错误，**不**访问非法对象的 ``.mode``（避免 ``AttributeError`` 在
    Executor 构造期泄漏、且此时 Store 已建）。"""
    if raw is None:
        return NumericMode(), None
    if isinstance(raw, NumericMode):
        return raw, None
    return None, ("numeric_mode 必须是 NumericMode 实例或 None（不臆测其 mode），"
                  "得到 %r（类型 %s）" % (raw, type(raw).__name__))


__all__ = [
    "build_runtime",
    "RuntimeAssembly",
    "StartupWarning",
    "StartupError",
    "StartupValidationError",
]
