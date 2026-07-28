"""WP-20260728-041：启动期参数装载核心 / APCHSACCUM 构造覆盖 / 失败关闭装配
测试。

覆盖 `src/runtime/parameters.py::build_runtime` 的纵向闭环：

* 逐拍等价——APCHSACCUM 默认构造经 Registry→Loader→Store→Executor 与基线一致；
  自定义 `IV/MS/MC` 与直接 `APCHSACCUM(IV=..,MS=..,MC=..)` 至少五拍对照
  （正常积算 / 单次回绕 / 下一拍 IV 恢复 / RS 上升沿 / 非零 IV 冷启动 AV=0）；
* 隔离与失败原子性——同任务多实例状态独立；重试得到全新 Store/Executor/块实例；
  中途失败不返回半构造对象、不改传入映射、不污染 Registry；
* 两个同名 `ctor_args` 概念永久区分——`RuntimeAdapter.ctor_args`（共享依赖名
  tuple）vs `InstanceDecl.ctor_args`（单实例关键字配置 dict）；APCM/APCPID/
  APCPIDZZD 共享 `LicenseContext`，实例配置不得遮蔽；
* 授权与取值反证——未知构造键 / 未授权但真实存在于 Python 签名的键 / bool 冒充
  数值 / NaN・Inf / 未知 init_overrides 管脚 / IEC 类型错误 / 缺共享依赖 / 非法
  startup inhibit / 非法 IR / 多错误确定顺序汇总；
* 显式时间参数目录与 warning 语义——毫秒周期 warning、BLINK 两持续时间、
  APCCD/APCGCQ 的 `TC*1000` 周期整除 warning、APCHXHCL.TB>0 采样 warning 与
  `TB=0` 跳过（不除零、不升级为硬错误），warning 可收集、稳定顺序、绝不失败关闭；
* `startup_inhibit_ms` 只做启动配置校验（非 bool 整数且 >=0），不驱动计时。

诚实边界：这些 Python 测试只锁定当前实现，**不**构成与 CODESYS/PLC、HAL、真实
I/O、watchdog、执行机构或现场安全一致性的证明。只校验实际出现在 `init_overrides`
或公开启动配置中的显式装载值，不声称已验证运行期动态连线/上拍/HMI/现场输入。
"""
from __future__ import annotations

import inspect
import math
import unittest
from unittest import mock

from src.runtime import (
    BlockSchema,
    CallFb,
    Executor,
    InstanceDecl,
    LibraryRuntimeError,
    LoadVar,
    NumericMode,
    Pin,
    POUDefinition,
    ProgramInstance,
    Registry,
    RuntimeAdapter,
    StoreVar,
    Task,
    VarDecl,
    build_default_registry,
    build_runtime_store,
    collect_outputs,
    persistent_key,
)
from src.runtime.parameters import (
    RuntimeAssembly,
    StartupError,
    StartupValidationError,
    StartupWarning,
    build_runtime,
)
from src.blocks.apchsaccum import APCHSACCUM
from src.blocks.apcm import APCM
from src.globals import LicenseContext
from src.licensing.bd_zcm import BD_ZCM
from src.licensing.issuer import derive_passwords_from_registration_codes
from src.licensing.providers import (
    ManualDateTimeProvider,
    StaticSerialTextProvider,
)


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------

_APCM_SERIAL = "PYPLC|TEST|MACHINE-0001"


def _make_license_ctx() -> LicenseContext:
    """已授权、彼此隔离的 LicenseContext（与 test_runtime_executor 同口径）。"""
    ctx = LicenseContext(
        StaticSerialTextProvider(_APCM_SERIAL),
        ManualDateTimeProvider(5000),
    )
    zcm = BD_ZCM(StaticSerialTextProvider(_APCM_SERIAL)).step(True)
    ctx.set_passwords(
        *derive_passwords_from_registration_codes(
            zcm["ZCM1"], zcm["ZCM2"], zcm["ZCM3"]))
    return ctx


def _prog(name, code, instances=None):
    return POUDefinition(name=name, pou_kind="PROGRAM", language="ST",
                         instances=instances or [], code=code)


def _task(pous, gvl=None, programs=None):
    pou_lib = {p.name: p for p in pous}
    progs = programs or [ProgramInstance("Main", "PLC_PRG")]
    return Task(programs=progs, gvl=gvl or [], pou_lib=pou_lib)


def _accum_task(ctor_args=None, name="A1"):
    """PROGRAM：GVL 驱动 I1/RS → 调用一个 APCHSACCUM 实例（可带 ctor 覆盖）。"""
    inst = InstanceDecl(name, "APCHSACCUM", kind="library",
                        ctor_args=dict(ctor_args or {}))
    code = [LoadVar("I1_in", "REAL"), StoreVar("%s.I1" % name, "REAL"),
            LoadVar("RS_in", "BOOL"), StoreVar("%s.RS" % name, "BOOL"),
            CallFb(name)]
    main = _prog("Main", code, instances=[inst])
    gvl = [VarDecl("I1_in", "REAL", section="VAR_GLOBAL"),
           VarDecl("RS_in", "BOOL", section="VAR_GLOBAL")]
    return _task([main], gvl=gvl)


def _empty_prog_task(instances):
    """空代码 PROGRAM（仅声明库块实例）——只需触发装配校验/构造，不驱动扫描。"""
    return _task([_prog("Main", [], instances=instances)])


def _drive(assembly, i1, rs):
    assembly.store.write("I1_in", i1)
    assembly.store.write("RS_in", rs)
    assembly.executor.execute_programs(assembly.store.snapshot())


# APCHSACCUM 源码冻结默认值（不得被本包私自增改）。
_MS_DEFAULT = 1.797693134862e38


# ---------------------------------------------------------------------------
# 逐拍等价：默认构造 + 自定义 IV/MS/MC
# ---------------------------------------------------------------------------

class TestApchsaccumDefaultConstructBaseline(unittest.TestCase):
    def test_default_construct_matches_direct_five_ticks(self):
        reg = build_default_registry()
        asm = build_runtime(_accum_task(), reg)
        self.assertIsInstance(asm, RuntimeAssembly)
        ref = APCHSACCUM()
        avk = persistent_key("PLC_PRG.A1", "AV")
        ssk = persistent_key("PLC_PRG.A1", "SS")
        for i1, rs in [(1.0, False), (2.0, False), (3.0 * _MS_DEFAULT, False),
                       (0.0, False), (5.0, False)]:
            _drive(asm, i1, rs)
            out = ref.step(500, I1=i1, RS=rs)
            self.assertEqual(asm.store.read(avk), out["AV"], (i1, rs))
            self.assertIs(asm.store.read(ssk), out["SS"], (i1, rs))

    def test_default_ctor_params_are_source_frozen_defaults(self):
        # 默认构造后，实例 IV/MS/MC 必须是源码冻结默认，AV 冷启动为 0.0。
        reg = build_default_registry()
        asm = build_runtime(_accum_task(), reg)
        rt = asm.executor._adapters["PLC_PRG.A1"].instance
        self.assertEqual(rt.IV, 0.0)
        self.assertEqual(rt.MS, _MS_DEFAULT)
        self.assertEqual(rt.MC, 1.0)
        self.assertEqual(rt.AV, 0.0)


class TestApchsaccumCtorOverrides(unittest.TestCase):
    def test_custom_iv_ms_mc_matches_direct_over_ticks(self):
        # 覆盖：正常积算 / 单次回绕(SS=True) / 下一拍 IV 恢复 / RS 上升沿(LR) /
        # 非零 IV 冷启动 AV=0。逐拍对照直接 APCHSACCUM(IV,MS,MC)。
        reg = build_default_registry()
        cfg = {"IV": 7.0, "MS": 100.0, "MC": 1.0}
        asm = build_runtime(_accum_task(ctor_args=cfg), reg)
        rt = asm.executor._adapters["PLC_PRG.A1"].instance
        # 非零 IV 冷启动：构造后 AV 仍为 0.0（不取 IV），配置已写入实例。
        self.assertEqual(rt.IV, 7.0)
        self.assertEqual(rt.MS, 100.0)
        self.assertEqual(rt.MC, 1.0)
        self.assertEqual(rt.AV, 0.0)

        ref = APCHSACCUM(IV=7.0, MS=100.0, MC=1.0)
        avk = persistent_key("PLC_PRG.A1", "AV")
        ssk = persistent_key("PLC_PRG.A1", "SS")
        seq = [(5.0, False),    # 冷启动积算：AV=5（非 IV=7、非 12）
               (50.0, False),   # 正常积算：AV=55
               (200.0, False),  # 单次回绕：AV=155>=MS，SS=True
               (0.0, False),    # 下一拍开头 AV>=MS → 恢复 IV=7
               (3.0, True),     # 本拍积算(10)后 RS 上升沿 → LR=10, AV=IV=7
               (0.0, False)]    # 电平不复位（非上升沿）→ 保持 7
        avs, sss = [], []
        for i1, rs in seq:
            _drive(asm, i1, rs)
            out = ref.step(500, I1=i1, RS=rs)
            self.assertEqual(asm.store.read(avk), out["AV"], (i1, rs))
            self.assertIs(asm.store.read(ssk), out["SS"], (i1, rs))
            avs.append(asm.store.read(avk))
            sss.append(asm.store.read(ssk))
        self.assertEqual(avs[0], 5.0)       # 冷启动 AV=0 起算，非 IV
        self.assertTrue(sss[2])             # 单次回绕拍 SS=True
        self.assertEqual(avs[3], 7.0)       # 下一拍开头恢复 IV=7
        self.assertEqual(rt.LR, 10.0)       # RS 上升沿保存本拍积算后的 AV
        self.assertEqual(avs[4], 7.0)       # 上升沿复位到 IV=7

    def test_two_instances_isolated_state(self):
        # 同任务两个 APCHSACCUM 配置不同且状态隔离。
        reg = build_default_registry()
        a1 = InstanceDecl("A1", "APCHSACCUM", kind="library",
                          ctor_args={"IV": 7.0, "MS": 100.0, "MC": 1.0})
        a2 = InstanceDecl("A2", "APCHSACCUM", kind="library",
                          ctor_args={"IV": 0.0, "MS": 1.0e9, "MC": 2.0})
        code = [LoadVar("I1_in", "REAL"), StoreVar("A1.I1", "REAL"),
                LoadVar("RS_in", "BOOL"), StoreVar("A1.RS", "BOOL"),
                CallFb("A1"),
                LoadVar("I1_in", "REAL"), StoreVar("A2.I1", "REAL"),
                LoadVar("RS_in", "BOOL"), StoreVar("A2.RS", "BOOL"),
                CallFb("A2")]
        main = _prog("Main", code, instances=[a1, a2])
        gvl = [VarDecl("I1_in", "REAL", section="VAR_GLOBAL"),
               VarDecl("RS_in", "BOOL", section="VAR_GLOBAL")]
        asm = build_runtime(_task([main], gvl=gvl), reg)
        r1 = asm.executor._adapters["PLC_PRG.A1"].instance
        r2 = asm.executor._adapters["PLC_PRG.A2"].instance
        self.assertIsNot(r1, r2)
        self.assertEqual(r1.MC, 1.0)
        self.assertEqual(r2.MC, 2.0)
        ref1 = APCHSACCUM(IV=7.0, MS=100.0, MC=1.0)
        ref2 = APCHSACCUM(IV=0.0, MS=1.0e9, MC=2.0)
        for i1, rs in [(4.0, False), (6.0, False), (5.0, False)]:
            _drive(asm, i1, rs)
            o1 = ref1.step(500, I1=i1, RS=rs)
            o2 = ref2.step(500, I1=i1, RS=rs)
            self.assertEqual(asm.store.read(persistent_key("PLC_PRG.A1", "AV")),
                             o1["AV"])
            self.assertEqual(asm.store.read(persistent_key("PLC_PRG.A2", "AV")),
                             o2["AV"])
        # 因子不同 → 累计不同 → 两实例 AV 独立
        self.assertNotEqual(asm.store.read(persistent_key("PLC_PRG.A1", "AV")),
                            asm.store.read(persistent_key("PLC_PRG.A2", "AV")))


# ---------------------------------------------------------------------------
# 隔离、重试与失败原子性
# ---------------------------------------------------------------------------

class TestIsolationAndAtomicity(unittest.TestCase):
    def test_retry_same_input_fresh_store_executor_instances(self):
        reg = build_default_registry()
        task = _accum_task(ctor_args={"IV": 1.0, "MS": 10.0, "MC": 1.0})
        a = build_runtime(task, reg)
        b = build_runtime(task, reg)
        self.assertIsNot(a.store, b.store)
        self.assertIsNot(a.executor, b.executor)
        self.assertIsNot(a.layout, b.layout)
        self.assertIsNot(a.executor._adapters["PLC_PRG.A1"].instance,
                         b.executor._adapters["PLC_PRG.A1"].instance)

    def test_two_task_builds_do_not_share_state(self):
        reg = build_default_registry()
        a = build_runtime(_accum_task(ctor_args={"MC": 1.0}), reg)
        b = build_runtime(_accum_task(ctor_args={"MC": 1.0}), reg)
        _drive(a, 5.0, False)                 # 只推进 a
        self.assertEqual(a.store.read(persistent_key("PLC_PRG.A1", "AV")), 5.0)
        self.assertEqual(b.store.read(persistent_key("PLC_PRG.A1", "AV")), 0.0)
        self.assertEqual(b.executor._adapters["PLC_PRG.A1"].instance.AV, 0.0)

    def test_failed_build_then_fixed_retry_does_not_inherit_state(self):
        reg = build_default_registry()
        before = reg.keys()
        # 失败构建（未知构造键）：不返回任何 Store/Executor。
        with self.assertRaises(StartupValidationError):
            build_runtime(_accum_task(ctor_args={"NOPE": 1.0}), reg)
        # 修正配置重试：全新实例、AV/SS/LR/preRS 均为冷启动零态。
        asm = build_runtime(_accum_task(ctor_args={"IV": 9.0, "MC": 1.0}), reg)
        rt = asm.executor._adapters["PLC_PRG.A1"].instance
        self.assertEqual((rt.AV, rt.SS, rt.LR, rt.preRS), (0.0, False, 0.0, False))
        self.assertEqual(rt.IV, 9.0)
        # Registry 未被污染（键集合不变，仍可解析）。
        self.assertEqual(reg.keys(), before)
        self.assertTrue(reg.has("APCHSACCUM", "engineering"))

    def test_failure_does_not_mutate_caller_dependencies(self):
        reg = build_default_registry()
        ctx = _make_license_ctx()
        deps = {"license_context": ctx}
        # 失败构建（APCHSACCUM 未知键）——传入 deps 不得被改写。
        with self.assertRaises(StartupValidationError):
            build_runtime(_accum_task(ctor_args={"ZZ": 1.0}), reg,
                          dependencies=deps)
        self.assertEqual(list(deps), ["license_context"])
        self.assertIs(deps["license_context"], ctx)

    def test_success_does_not_mutate_caller_dependencies(self):
        reg = build_default_registry()
        ctx = _make_license_ctx()
        deps = {"license_context": ctx}
        build_runtime(_empty_prog_task([
            InstanceDecl("M1", "APCM", kind="library")]), reg, dependencies=deps)
        self.assertEqual(list(deps), ["license_context"])
        self.assertIs(deps["license_context"], ctx)


# ---------------------------------------------------------------------------
# 共享构造依赖（APCM/APCPID/APCPIDZZD）：不遮蔽、不共享跨图
# ---------------------------------------------------------------------------

class TestSharedLicenseDependency(unittest.TestCase):
    _LICENSE_BLOCKS = ("APCM", "APCPID", "APCPIDZZD")

    def test_same_graph_shares_single_context(self):
        for bt in self._LICENSE_BLOCKS:
            reg = build_default_registry()
            ctx = _make_license_ctx()
            asm = build_runtime(_empty_prog_task([
                InstanceDecl("B1", bt, kind="library"),
                InstanceDecl("B2", bt, kind="library"),
            ]), reg, dependencies={"license_context": ctx})
            i1 = asm.executor._adapters["PLC_PRG.B1"].instance
            i2 = asm.executor._adapters["PLC_PRG.B2"].instance
            self.assertIs(i1._ctx, i2._ctx, bt)     # 同图共享
            self.assertIs(i1._ctx, ctx, bt)

    def test_different_graphs_do_not_share_context(self):
        reg = build_default_registry()
        ctx_a, ctx_b = _make_license_ctx(), _make_license_ctx()
        a = build_runtime(_empty_prog_task([
            InstanceDecl("M1", "APCM", kind="library")]), reg,
            dependencies={"license_context": ctx_a})
        b = build_runtime(_empty_prog_task([
            InstanceDecl("M1", "APCM", kind="library")]), reg,
            dependencies={"license_context": ctx_b})
        self.assertIsNot(a.executor._adapters["PLC_PRG.M1"].instance._ctx,
                         b.executor._adapters["PLC_PRG.M1"].instance._ctx)

    def test_instance_ctor_args_cannot_shadow_shared_dependency(self):
        # InstanceDecl.ctor_args={"license_context": ...} 必须被拒绝，
        # 不能遮蔽任务级共享依赖（两个同名 ctor_args 概念永久区分）。
        for bt in self._LICENSE_BLOCKS:
            reg = build_default_registry()
            ctx = _make_license_ctx()
            with self.assertRaises(StartupValidationError) as cm:
                build_runtime(_empty_prog_task([
                    InstanceDecl("B1", bt, kind="library",
                                 ctor_args={"license_context": object()})]),
                    reg, dependencies={"license_context": ctx})
            self.assertTrue(any("遮蔽" in e or "共享构造依赖" in e
                                for e in cm.exception.errors), bt)

    def test_missing_shared_dependency_fails_closed(self):
        reg = build_default_registry()
        with self.assertRaises(StartupValidationError) as cm:
            build_runtime(_empty_prog_task([
                InstanceDecl("M1", "APCM", kind="library")]), reg)
        self.assertTrue(any("缺共享构造依赖" in e for e in cm.exception.errors))


# ---------------------------------------------------------------------------
# 授权与取值反证（失败关闭）
# ---------------------------------------------------------------------------

# 一个真实拥有多个 ctor 参数、但 Schema 只授权其中之一的测试块——用于证明
# 授权只看 Schema.init_overridable，绝不因参数真实存在于 Python 签名而放开。
class _TwoCtorBlock:
    def __init__(self, *, A: float = 1.0, B: float = 2.0) -> None:
        self.A = A
        self.B = B

    def step(self, dt_ms):
        del dt_ms
        return {}


_TWOCTOR_SCHEMA = BlockSchema(
    block_type="_TWOCTOR",
    state_vars=frozenset({"A", "B"}),
    init_overridable=frozenset({"A"}),      # 只授权 A；B 真实存在于签名但未授权
)
_TWOCTOR_ADAPTER = RuntimeAdapter(
    cls=_TwoCtorBlock,
    call_adapter=lambda inst, dt, ins, io: collect_outputs({}, inst, {}))


def _registry_with_twoctor():
    reg = build_default_registry()
    reg.register(_TWOCTOR_SCHEMA, _TWOCTOR_ADAPTER)
    return reg


class TestFailClosedCounterExamples(unittest.TestCase):
    def _expect_fail(self, task, registry=None, **kw):
        reg = registry or build_default_registry()
        with self.assertRaises(StartupValidationError) as cm:
            build_runtime(task, reg, **kw)
        return cm.exception

    def test_unknown_construct_key_rejected(self):
        exc = self._expect_fail(_accum_task(ctor_args={"NOPE": 1.0}))
        self.assertTrue(any("未被 Schema init_overridable" in e
                            for e in exc.errors))

    def test_unauthorized_but_real_signature_key_rejected(self):
        # B 真实存在于 _TwoCtorBlock.__init__ 签名，却不在 init_overridable →
        # 必须拒绝（不臆测块构造签名自动开放参数）。
        self.assertIn("B", inspect.signature(_TwoCtorBlock.__init__).parameters)
        reg = _registry_with_twoctor()
        exc = self._expect_fail(_empty_prog_task([
            InstanceDecl("T1", "_TWOCTOR", kind="library",
                         ctor_args={"B": 5.0})]), registry=reg)
        self.assertTrue(any("未被 Schema init_overridable" in e
                            for e in exc.errors))
        # 授权键 A 则可通过并透传到构造器。
        asm = build_runtime(_empty_prog_task([
            InstanceDecl("T1", "_TWOCTOR", kind="library",
                         ctor_args={"A": 5.0})]), reg)
        self.assertEqual(asm.executor._adapters["PLC_PRG.T1"].instance.A, 5.0)

    def test_bool_masquerading_as_number_rejected(self):
        for bad in (True, False):
            exc = self._expect_fail(_accum_task(ctor_args={"IV": bad}))
            self.assertTrue(any("bool" in e for e in exc.errors), bad)

    def test_nan_and_inf_ctor_value_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            exc = self._expect_fail(_accum_task(ctor_args={"MS": bad}))
            self.assertTrue(any("有限" in e or "NaN" in e for e in exc.errors))

    def test_string_ctor_value_rejected(self):
        exc = self._expect_fail(_accum_task(ctor_args={"MC": "1.0"}))
        self.assertTrue(any("int/float" in e for e in exc.errors))

    def test_large_finite_int_ctor_value_accepted(self):
        # 任务书声明合法：有限大 Python int（如 10**1000）。Python 任意精度 int
        # 恒为有限、无 NaN/±Inf；启动装配层不得因对其调用 math.isfinite（先转 C
        # double）而泄漏 OverflowError，须原样接受并透传到实例，AV 冷启动=0.0。
        reg = build_default_registry()
        big = 10 ** 1000
        asm = build_runtime(_accum_task(ctor_args={"IV": big}), reg)
        rt = asm.executor._adapters["PLC_PRG.A1"].instance
        self.assertEqual(rt.IV, big)            # 原样接受（未转 float、未截断）
        self.assertIsInstance(rt.IV, int)
        self.assertEqual(rt.AV, 0.0)            # 非零 IV 冷启动 AV=0.0 不变

    def test_unknown_init_override_pin_rejected(self):
        task = _empty_prog_task([
            InstanceDecl("A1", "APCHSACCUM", kind="library",
                         init_overrides={"NOPE": 1.0})])
        exc = self._expect_fail(task)
        self.assertTrue(any("不存在的管脚" in e for e in exc.errors))

    def test_init_override_iec_type_error_rejected(self):
        # TON.PT_ms 是 TIME(int)；给 float → 结构类型不匹配，失败关闭。
        task = _empty_prog_task([
            InstanceDecl("T1", "TON", kind="library",
                         init_overrides={"PT_ms": 3.5})])
        exc = self._expect_fail(task)
        self.assertTrue(any("结构不匹配" in e for e in exc.errors))

    def test_unregistered_block_type_fails_closed(self):
        # block_type 无法在 L2 注册表解析（缺变体/未注册的等价失败关闭路径）。
        task = _empty_prog_task([
            InstanceDecl("X1", "NOSUCHBLOCK", kind="library")])
        exc = self._expect_fail(task)
        self.assertTrue(any("未在 L2 注册表登记" in e or "无法在 L2 注册表解析" in e
                            for e in exc.errors))

    def test_illegal_startup_inhibit_rejected(self):
        for bad in (-1, True, 1.5, "0"):
            exc = self._expect_fail(_accum_task(), startup_inhibit_ms=bad)
            self.assertTrue(any("startup_inhibit_ms" in e for e in exc.errors),
                            bad)

    def test_illegal_ir_rejected(self):
        # cycle_ms != 500 → validate_task 失败，纳入启动失败关闭。
        task = _accum_task()
        task.cycle_ms = 250
        exc = self._expect_fail(task)
        self.assertTrue(any("IR 装载校验" in e for e in exc.errors))
        self.assertTrue(any("cycle_ms" in e for e in exc.errors))

    def test_multiple_errors_aggregated_deterministic_order(self):
        # 两个独立错误（两个实例各一未知键）确定顺序汇总，便于启动日志定位。
        reg = build_default_registry()
        a1 = InstanceDecl("A1", "APCHSACCUM", kind="library",
                          ctor_args={"BAD1": 1.0})
        a2 = InstanceDecl("A2", "APCHSACCUM", kind="library",
                          ctor_args={"BAD2": 1.0})
        task = _empty_prog_task([a1, a2])
        exc1 = self._expect_fail(task, registry=reg)
        exc2 = self._expect_fail(task, registry=build_default_registry())
        self.assertGreaterEqual(len(exc1.errors), 2)
        self.assertEqual(exc1.errors, exc2.errors)      # 确定顺序（可复现）
        self.assertTrue(any("PLC_PRG.A1" in e for e in exc1.errors))
        self.assertTrue(any("PLC_PRG.A2" in e for e in exc1.errors))

    def test_executor_gate_independently_rejects_unauthorized_ctor(self):
        # 防御纵深：即便绕过启动装配层，Executor 构造闸门也独立拒绝未授权键。
        reg = build_default_registry()
        task = _accum_task(ctor_args={"NOPE": 1.0})
        layout = build_runtime_store(task, reg)
        with self.assertRaises(LibraryRuntimeError):
            Executor(task, layout, registry=reg)


# ---------------------------------------------------------------------------
# 显式时间参数目录与 warning 语义
# ---------------------------------------------------------------------------

class TestTimeParamCatalog(unittest.TestCase):
    def _build(self, block, overrides, ctor=None):
        reg = build_default_registry()
        inst = InstanceDecl("B1", block, kind="library",
                            init_overrides=dict(overrides),
                            ctor_args=dict(ctor or {}))
        return build_runtime(_empty_prog_task([inst]), reg)

    def _warn_fields(self, asm):
        for w in asm.warnings:
            self.assertIsInstance(w, StartupWarning)
        return {(w.block_type, w.field, w.rule) for w in asm.warnings}

    def test_pt_ms_sub_cycle_warns_not_fails(self):
        asm = self._build("TON", {"PT_ms": 300})       # 300 < cycle 500
        self.assertIn(("TON", "PT_ms", "pt_ms_cycle"), self._warn_fields(asm))

    def test_pt_ms_non_multiple_warns(self):
        asm = self._build("TOF", {"PT_ms": 700})        # 非 500 整数倍
        self.assertIn(("TOF", "PT_ms", "pt_ms_cycle"), self._warn_fields(asm))

    def test_pt_ms_exact_multiple_no_warn(self):
        asm = self._build("TP", {"PT_ms": 1000})        # 恰整数倍 → 无 warning
        self.assertEqual(self._warn_fields(asm), set())

    def test_blink_two_durations_each_get_cycle_warning(self):
        asm = self._build("BLINK", {"TIMELOW_ms": 300, "TIMEHIGH_ms": 700})
        fields = self._warn_fields(asm)
        self.assertIn(("BLINK", "TIMELOW_ms", "pt_ms_cycle"), fields)
        self.assertIn(("BLINK", "TIMEHIGH_ms", "pt_ms_cycle"), fields)

    def test_negative_ms_is_hard_error(self):
        reg = build_default_registry()
        with self.assertRaises(StartupValidationError) as cm:
            build_runtime(_empty_prog_task([
                InstanceDecl("B1", "TON", kind="library",
                             init_overrides={"PT_ms": -100})]), reg)
        self.assertTrue(any(">= 0" in e for e in cm.exception.errors))

    def test_apccd_tc_non_cycle_multiple_warns(self):
        # APCCD.TC*1000 非 cycle_ms 整数倍 → 只发 warning，不 round/coerce。
        asm = self._build("APCCD", {"TC": 0.3})         # 300ms 非 500 整数倍
        self.assertIn(("APCCD", "TC", "tc_cycle_multiple"), self._warn_fields(asm))

    def test_apccd_tc_exact_multiple_no_tc_warn(self):
        asm = self._build("APCCD", {"TC": 0.5})         # 500ms == cycle → 无 TC warn
        self.assertNotIn(("APCCD", "TC", "tc_cycle_multiple"),
                         self._warn_fields(asm))

    def test_apcgcq_tc_non_cycle_multiple_warns(self):
        asm = self._build("APCGCQ", {"TC": 0.75})       # 750ms 非整数倍
        self.assertIn(("APCGCQ", "TC", "tc_cycle_multiple"),
                      self._warn_fields(asm))

    def test_apchxhcl_tb_positive_sample_warns(self):
        asm = self._build("APCHXHCL", {"TB": 0.7})      # 60/0.7 非整数 → warning
        self.assertIn(("APCHXHCL", "TB", "tb_sample_n"), self._warn_fields(asm))

    def test_apchxhcl_tb_zero_skips_warning_no_divzero(self):
        # TB=0 是非负合法值：跳过 60/TB warning（不除零），不升级为硬错误。
        asm = self._build("APCHXHCL", {"TB": 0.0})
        self.assertNotIn(("APCHXHCL", "TB", "tb_sample_n"),
                         self._warn_fields(asm))
        # 构建成功、无与 TB 相关的硬失败。
        self.assertIsInstance(asm, RuntimeAssembly)

    def test_negative_sec_time_is_hard_error(self):
        reg = build_default_registry()
        with self.assertRaises(StartupValidationError) as cm:
            build_runtime(_empty_prog_task([
                InstanceDecl("B1", "APCCD", kind="library",
                             init_overrides={"TC": -1.0})]), reg)
        self.assertTrue(any(">= 0" in e for e in cm.exception.errors))

    def test_nonfinite_sec_time_is_hard_error(self):
        reg = build_default_registry()
        with self.assertRaises(StartupValidationError) as cm:
            build_runtime(_empty_prog_task([
                InstanceDecl("B1", "APCGCQ", kind="library",
                             init_overrides={"TC": float("inf")})]), reg)
        self.assertTrue(any("有限" in e for e in cm.exception.errors))

    def test_warnings_stable_order_for_same_input(self):
        a = self._build("BLINK", {"TIMELOW_ms": 300, "TIMEHIGH_ms": 700})
        b = self._build("BLINK", {"TIMELOW_ms": 300, "TIMEHIGH_ms": 700})
        self.assertEqual([(w.field, w.rule) for w in a.warnings],
                         [(w.field, w.rule) for w in b.warnings])

    def test_warning_never_upgraded_to_failure(self):
        # 大量周期不匹配 warning 也不失败关闭：返回可用装配 + 可检查 warning。
        asm = self._build("TON", {"PT_ms": 1})
        self.assertIsInstance(asm, RuntimeAssembly)
        self.assertTrue(len(asm.warnings) >= 1)


# ---------------------------------------------------------------------------
# startup_inhibit_ms 校验（只校验、不驱动计时）
# ---------------------------------------------------------------------------

class TestStartupInhibit(unittest.TestCase):
    def test_default_from_config(self):
        from src.config import STARTUP_INHIBIT_MS
        asm = build_runtime(_accum_task(), build_default_registry())
        self.assertEqual(asm.startup_inhibit_ms, STARTUP_INHIBIT_MS)

    def test_explicit_zero_and_positive_accepted(self):
        for v in (0, 1000):
            asm = build_runtime(_accum_task(), build_default_registry(),
                                startup_inhibit_ms=v)
            self.assertEqual(asm.startup_inhibit_ms, v)

    def test_bool_rejected_even_though_int_subclass(self):
        reg = build_default_registry()
        with self.assertRaises(StartupValidationError) as cm:
            build_runtime(_accum_task(), reg, startup_inhibit_ms=True)
        self.assertTrue(any("startup_inhibit_ms" in e for e in cm.exception.errors))


# ---------------------------------------------------------------------------
# 公开导出与异常层级
# ---------------------------------------------------------------------------

class TestPublicSurface(unittest.TestCase):
    def test_exports_available_from_src_runtime(self):
        import src.runtime as rt
        for name in ("build_runtime", "RuntimeAssembly", "StartupWarning",
                     "StartupError", "StartupValidationError"):
            self.assertTrue(hasattr(rt, name), name)

    def test_validation_error_is_startup_error_subclass(self):
        self.assertTrue(issubclass(StartupValidationError, StartupError))
        exc = None
        try:
            build_runtime(_accum_task(ctor_args={"NOPE": 1.0}),
                          build_default_registry())
        except StartupError as e:      # 基类可捕获
            exc = e
        self.assertIsInstance(exc, StartupValidationError)
        self.assertTrue(len(exc.errors) >= 1)


# ---------------------------------------------------------------------------
# 递归 user-FB 非法 IR：稳定失败关闭（不泄漏 RecursionError；不建 Store/Executor）
# ---------------------------------------------------------------------------

class TestRecursiveFBFailsClosed(unittest.TestCase):
    """WP-042：递归 user-FB 非法 IR（A → B → A）在 build_runtime 后续实例展开
    中不得泄漏 RecursionError，必须稳定、可聚合地抛 StartupValidationError 并
    携原 IR 循环诊断，且不创建/暴露 Store/Executor。"""

    def _recursive_task(self):
        # 合法 IR 数据对象构造 user-FB 循环：FB_A 含实例 b:FB_B，FB_B 含实例
        # a:FB_A，PROGRAM 引用 root:FB_A。循环性是唯一非法处（validate_task 的
        # DFS 三色标记会汇总"递归实例声明循环"，build_runtime 实例展开若无循环
        # 保护则会无限递归）。
        fb_a = POUDefinition(
            name="FB_A", pou_kind="FUNCTION_BLOCK", language="ST",
            instances=[InstanceDecl("b", "FB_B", kind="user_fb")], code=[])
        fb_b = POUDefinition(
            name="FB_B", pou_kind="FUNCTION_BLOCK", language="ST",
            instances=[InstanceDecl("a", "FB_A", kind="user_fb")], code=[])
        main = _prog("Main", [],
                     instances=[InstanceDecl("root", "FB_A", kind="user_fb")])
        return _task([main, fb_a, fb_b])

    def test_recursive_fb_raises_startup_validation_not_recursionerror(self):
        reg = build_default_registry()
        task = self._recursive_task()
        # patch 证明未触及构建阶段（build_runtime_store / Executor 均不被调用）。
        with mock.patch("src.runtime.parameters.build_runtime_store") as m_store, \
                mock.patch("src.runtime.parameters.Executor") as m_exec:
            with self.assertRaises(StartupValidationError) as cm:
                build_runtime(task, reg)   # RecursionError 若泄漏则本断言失败
        errs = cm.exception.errors
        # 携原 IR 循环诊断（而非 Python 递归栈错误）
        self.assertTrue(any("循环" in e for e in errs), errs)
        self.assertTrue(any("FB_A" in e and "FB_B" in e for e in errs), errs)
        # 未创建或暴露 Store/Executor
        m_store.assert_not_called()
        m_exec.assert_not_called()

    def test_recursive_fb_errors_are_deterministic(self):
        # 同一非法输入两次构建得到相同确定顺序的错误列表（可聚合、可复现）。
        task = self._recursive_task()
        e1 = None
        e2 = None
        try:
            build_runtime(task, build_default_registry())
        except StartupValidationError as exc:
            e1 = exc.errors
        try:
            build_runtime(task, build_default_registry())
        except StartupValidationError as exc:
            e2 = exc.errors
        self.assertIsNotNone(e1)
        self.assertEqual(e1, e2)


# ---------------------------------------------------------------------------
# 非法 numeric_mode 类型：确定顺序汇总，不泄漏 AttributeError、不建 Store/Executor
# ---------------------------------------------------------------------------

class TestIllegalNumericModeFailsClosed(unittest.TestCase):
    """WP-042：build_runtime(..., numeric_mode=<非法类型>) 不得泄漏
    AttributeError；须把非法类型纳入确定顺序 StartupValidationError，且不
    创建/暴露 Store/Executor。至少覆盖字符串与普通对象。"""

    def test_string_numeric_mode_fails_closed_no_store_executor(self):
        reg = build_default_registry()
        with mock.patch("src.runtime.parameters.build_runtime_store") as m_store, \
                mock.patch("src.runtime.parameters.Executor") as m_exec:
            with self.assertRaises(StartupValidationError) as cm:
                build_runtime(_accum_task(), reg, numeric_mode="engineering")
        self.assertTrue(any("numeric_mode" in e for e in cm.exception.errors),
                        cm.exception.errors)
        m_store.assert_not_called()
        m_exec.assert_not_called()

    def test_plain_object_numeric_mode_rejected(self):
        reg = build_default_registry()
        with self.assertRaises(StartupValidationError) as cm:
            build_runtime(_accum_task(), reg, numeric_mode=object())
        self.assertTrue(any("numeric_mode" in e for e in cm.exception.errors))

    def test_illegal_numeric_mode_coaggregates_not_short_circuit(self):
        # 关键：非法 numeric_mode 与同一输入中另一独立启动错误共同汇总，
        # 不因访问 .mode 提前中断（回落 engineering 口径继续枚举实例错误）。
        reg = build_default_registry()
        task = _accum_task(ctor_args={"NOPE": 1.0})    # 独立的未授权构造键错误
        with self.assertRaises(StartupValidationError) as cm:
            build_runtime(task, reg, numeric_mode="engineering")
        errs = cm.exception.errors
        self.assertTrue(any("numeric_mode" in e for e in errs), errs)
        self.assertTrue(any("未被 Schema init_overridable" in e for e in errs),
                        errs)

    def test_none_and_numericmode_instance_accepted(self):
        reg = build_default_registry()
        self.assertIsInstance(
            build_runtime(_accum_task(), reg, numeric_mode=None),
            RuntimeAssembly)
        self.assertIsInstance(
            build_runtime(_accum_task(), reg, numeric_mode=NumericMode()),
            RuntimeAssembly)


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
