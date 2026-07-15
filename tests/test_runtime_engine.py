"""WP-20260716-006：五步扫描编排骨架与确定性单拍执行测试。

对应任务书"最低测试要求"10 条逐条对号（注释标注"要求 N"）。

诚实边界：本文件注入的 fake policy / fake committer **只是**用来证明五步
顺序、调用次数与失败边界的测试替身，**不是**生产安全实现——真实
``OutputPolicy``（safe_value / rate_limit / 故障优先级）、shadow、watchdog
与驱动提交属后续工作包。这些 Python 测试锁定的是当前编排行为，不构成与
CODESYS PLC 语义一致的证据。
"""
from __future__ import annotations

import threading
import unittest

from src.runtime import (
    CallFb,
    InputImageError,
    InstanceDecl,
    IOMap,
    IRExecutionError,
    LoadConst,
    LoadPrev,
    LoadVar,
    OutputImageError,
    OutputStagingError,
    POUDefinition,
    ProgramInstance,
    ScanConfigError,
    ScanEngine,
    ScanReentryError,
    StoreVar,
    Task,
    VarDecl,
    Executor,
    build_runtime_store,
)


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------

def _gvl():
    return [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Level", "INT", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),     # request
        VarDecl("PrevMotor", "BOOL", section="VAR_GLOBAL"),  # 上一拍观察位
        VarDecl("Trace", "INT", section="VAR_GLOBAL"),
    ]


def _io_map():
    return [
        IOMap("Start", "DI0", "IN"),
        IOMap("Level", "AI0", "IN"),
        IOMap("Motor", "DO0", "OUT", policy=object()),   # policy 仅占位
    ]


def _prog(name, code, locals_=None, instances=None):
    return POUDefinition(name=name, pou_kind="PROGRAM", language="ST",
                         locals=locals_ or [], instances=instances or [],
                         code=code)


#: 默认业务：Motor := Start（只写 request 变量，不碰物理输出）
_MAIN_CODE = [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]


def _task(main_code=None, pous=None, programs=None, gvl=None, io_map=None,
          cycle_ms=500):
    pou_lib = {}
    if main_code is not None:
        pou_lib["Main"] = _prog("Main", main_code)
    for p in (pous or []):
        pou_lib[p.name] = p
    return Task(
        cycle_ms=cycle_ms,
        programs=programs or [ProgramInstance("Main", "PLC_PRG")],
        gvl=_gvl() if gvl is None else gvl,
        io_map=_io_map() if io_map is None else io_map,
        pou_lib=pou_lib,
    )


class _Trace:
    """五步事件轨迹记录器（要求 1）。"""

    def __init__(self):
        self.events = []


class _FakePolicy:
    """测试替身：把 Store 中的 request 变量 stage 到对应通道。

    **不是** OutputPolicy 实现——不含 safe_value / rate_limit / 故障优先级。
    """

    def __init__(self, trace, fail=False, skip_channels=(), stage_extra=None,
                 hook=None):
        self.trace = trace
        self.fail = fail
        self.skip_channels = set(skip_channels)
        self.stage_extra = stage_extra
        self.hook = hook
        self.calls = 0
        self.seen_prev = []

    def stage_outputs(self, pending, store, inputs, prev):
        self.calls += 1
        self.trace.events.append("policy")
        self.seen_prev.append(prev)
        if self.hook is not None:
            self.hook()
        if self.fail:
            raise RuntimeError("policy boom")
        for ch in pending.channels():
            if ch in self.skip_channels:
                continue
            pending.stage(ch, store.read(pending.var_for(ch)))
        if self.stage_extra is not None:
            pending.stage(*self.stage_extra)


class _FakeCommitter:
    """测试替身：记录被提交的输出。**不连接任何真实驱动/HAL**。"""

    def __init__(self, trace, fail=False, mutate=False):
        self.trace = trace
        self.fail = fail
        self.mutate = mutate
        self.calls = 0
        self.received = []

    def commit(self, outputs):
        self.calls += 1
        self.trace.events.append("commit")
        self.received.append(dict(outputs))
        if self.mutate:
            outputs["DO0"] = "polluted"
            outputs["INJECTED"] = 1
        if self.fail:
            raise RuntimeError("commit boom")


class _TracingExecutor(Executor):
    """在真实执行器外记录"IR 执行"事件与收到的 prev（不改变执行语义）。"""

    def __init__(self, *args, trace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.trace = trace
        self.seen_prev = []

    def execute_programs(self, prev_snapshot):
        if self.trace is not None:
            self.trace.events.append("ir")
        self.seen_prev.append(prev_snapshot)
        return super().execute_programs(prev_snapshot)


def _build(task, trace=None, policy=None, committer=None, adapters=None,
           executor=None):
    layout = build_runtime_store(task)
    trace = trace if trace is not None else _Trace()
    if executor is None:
        executor = _TracingExecutor(task, layout, trace=trace,
                                    library_adapters=adapters)
    policy = policy if policy is not None else _FakePolicy(trace)
    committer = committer if committer is not None else _FakeCommitter(trace)
    engine = ScanEngine(task, layout, executor, policy, committer)
    return engine, layout, trace, policy, committer


# ---------------------------------------------------------------------------
# 要求 1：五步顺序 + 策略/提交各恰一次
# ---------------------------------------------------------------------------

class TestFiveStepOrder(unittest.TestCase):

    def test_event_trace_is_exactly_five_step_order(self):
        task = _task(_MAIN_CODE)
        engine, layout, trace, policy, committer = _build(task)

        # 输入锁存无独立事件源，用"锁存已生效"作为第 1 步已发生的证据：
        # IR 事件发生时 Store 中的 Start 必须已是本拍采样值。
        result = engine.scan({"DI0": True, "AI0": 7})

        self.assertEqual(trace.events, ["ir", "policy", "commit"])
        self.assertEqual(policy.calls, 1)
        self.assertEqual(committer.calls, 1)
        self.assertEqual(layout.store.read("Start"), True)      # 第 1 步已锁存
        self.assertEqual(layout.store.read("Level"), 7)
        self.assertEqual(result.outputs(), {"DO0": True})        # 第 4/5 步
        self.assertEqual(committer.received, [{"DO0": True}])

    def test_latch_precedes_ir_execution(self):
        # 第 1 步先于第 2+3 步：IR 读到的必须是本拍锁存值，不是上一拍
        task = _task(_MAIN_CODE)
        engine, layout, trace, policy, committer = _build(task)
        engine.scan({"DI0": True, "AI0": 1})
        result = engine.scan({"DI0": False, "AI0": 2})
        self.assertEqual(result.outputs(), {"DO0": False})

    def test_result_views_are_readonly_snapshots(self):
        # 生命周期纪律：返回值可只读观察输入 / 待提交输出 / 提交后 prev
        task = _task(_MAIN_CODE)
        engine, layout, _, _, _ = _build(task)
        result = engine.scan({"DI0": True, "AI0": 3})

        self.assertEqual(result.inputs.read("Start"), True)
        self.assertEqual(result.inputs.read("Level"), 3)
        self.assertEqual(result.prev.read("Motor"), True)

        # 调用方修改副本不污染引擎内部状态
        out = result.outputs()
        out["DO0"] = "polluted"
        self.assertEqual(result.outputs(), {"DO0": True})
        result.inputs.as_dict()["Start"] = "polluted"
        self.assertEqual(result.inputs.read("Start"), True)


# ---------------------------------------------------------------------------
# 要求 2：两拍 LOAD_PREV 回归
# ---------------------------------------------------------------------------

class TestLoadPrevAcrossScans(unittest.TestCase):

    def test_second_scan_reads_value_committed_in_first_scan(self):
        # PrevMotor := PREV(Motor); Motor := Start
        # 注意顺序：先读上一拍 Motor，再用本拍 Start 覆写 Motor
        code = [
            LoadPrev("Motor", "BOOL"), StoreVar("PrevMotor", "BOOL"),
            LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL"),
        ]
        task = _task(code)
        engine, layout, _, _, _ = _build(task)

        # 第 1 拍：冷启动 prev（Motor 初值 False），Motor 变 True
        r1 = engine.scan({"DI0": True, "AI0": 0})
        self.assertEqual(layout.store.read("PrevMotor"), False)
        self.assertEqual(r1.outputs(), {"DO0": True})

        # 第 2 拍：读到第 1 拍**成功提交后**的 Motor=True，而非本拍新值 False
        r2 = engine.scan({"DI0": False, "AI0": 0})
        self.assertEqual(layout.store.read("PrevMotor"), True)
        self.assertEqual(layout.store.read("Motor"), False)   # 本拍新值
        self.assertEqual(r2.outputs(), {"DO0": False})

    def test_prev_snapshot_is_taken_after_commit_not_before(self):
        task = _task(_MAIN_CODE)
        engine, layout, trace, policy, committer = _build(task)
        engine.scan({"DI0": True, "AI0": 0})
        # 提交时策略/执行器看到的 prev 仍是上一拍的；提交成功后才前移
        self.assertEqual(engine.prev.read("Motor"), True)


# ---------------------------------------------------------------------------
# 要求 3：输入锁存失败
# ---------------------------------------------------------------------------

class TestLatchFailure(unittest.TestCase):

    def test_latch_failure_leaves_no_partial_input_and_skips_all_stages(self):
        task = _task(_MAIN_CODE)
        engine, layout, trace, policy, committer = _build(task)

        # AI0 类型不匹配（INT 变量收到 BOOL 之外的非法值）→ 两阶段校验拒绝
        with self.assertRaises(InputImageError):
            engine.scan({"DI0": True, "AI0": "not-an-int"})

        self.assertEqual(layout.store.read("Start"), False)  # 无部分更新
        self.assertEqual(layout.store.read("Level"), 0)
        self.assertEqual(trace.events, [])                   # 后续三段未执行
        self.assertEqual(policy.calls, 0)
        self.assertEqual(committer.calls, 0)

    def test_missing_channel_is_rejected_before_any_stage(self):
        task = _task(_MAIN_CODE)
        engine, layout, trace, policy, committer = _build(task)
        with self.assertRaises(InputImageError):
            engine.scan({"DI0": True})           # 缺 AI0
        self.assertEqual(trace.events, [])
        self.assertEqual(committer.calls, 0)


# ---------------------------------------------------------------------------
# 要求 4：三类失败路径均不更新 prev、不继续后续步骤、不伪造安全输出
# ---------------------------------------------------------------------------

class TestFailurePathsDoNotAdvancePrev(unittest.TestCase):

    def _prev_of(self, engine):
        return engine.prev.read("Motor")

    def test_ir_failure_stops_scan_and_keeps_prev(self):
        # 装载校验通过后篡改 code，构造运行期 StoreVar 类型不匹配
        # （与 test_runtime_executor 同法：证明失败发生在第 2+3 步执行期）
        task = _task(_MAIN_CODE)
        engine, layout, trace, policy, committer = _build(task)
        task.pou_lib["Main"].code = [LoadConst(5, "INT"),
                                     StoreVar("Motor", "BOOL")]

        with self.assertRaises(IRExecutionError):
            engine.scan({"DI0": True, "AI0": 0})

        self.assertEqual(trace.events, ["ir"])      # 未进策略/提交
        self.assertEqual(policy.calls, 0)
        self.assertEqual(committer.calls, 0)
        self.assertEqual(self._prev_of(engine), False)   # prev 未前移

    def test_policy_failure_stops_scan_and_keeps_prev(self):
        task = _task(_MAIN_CODE)
        trace = _Trace()
        policy = _FakePolicy(trace, fail=True)
        engine, layout, trace, policy, committer = _build(
            task, trace=trace, policy=policy)

        # 异常原样传播，不被伪装成安全输出
        with self.assertRaises(RuntimeError) as ctx:
            engine.scan({"DI0": True, "AI0": 0})
        self.assertEqual(str(ctx.exception), "policy boom")

        self.assertEqual(trace.events, ["ir", "policy"])
        self.assertEqual(committer.calls, 0)             # 未提交
        self.assertEqual(self._prev_of(engine), False)

    def test_commit_failure_keeps_prev(self):
        task = _task(_MAIN_CODE)
        trace = _Trace()
        committer = _FakeCommitter(trace, fail=True)
        engine, layout, trace, policy, committer = _build(
            task, trace=trace, committer=committer)

        with self.assertRaises(RuntimeError) as ctx:
            engine.scan({"DI0": True, "AI0": 0})
        self.assertEqual(str(ctx.exception), "commit boom")

        self.assertEqual(trace.events, ["ir", "policy", "commit"])
        self.assertEqual(committer.calls, 1)
        self.assertEqual(self._prev_of(engine), False)   # 提交失败 → prev 不前移

    def test_failures_do_not_fabricate_safe_outputs(self):
        # 编排层不代替 OutputPolicy 落安全值（§4.3 属外层 runner）
        task = _task(_MAIN_CODE)
        trace = _Trace()
        committer = _FakeCommitter(trace, fail=True)
        engine, _, trace, policy, committer = _build(
            task, trace=trace, committer=committer)
        with self.assertRaises(RuntimeError):
            engine.scan({"DI0": True, "AI0": 0})
        # 只有业务值被提交过一次（且失败），没有额外的"安全值补提交"
        self.assertEqual(committer.received, [{"DO0": True}])


# ---------------------------------------------------------------------------
# 要求 5：提交失败后可恢复调用；无半拍残留
# ---------------------------------------------------------------------------

class TestRecoveryAfterCommitFailure(unittest.TestCase):

    def test_next_scan_uses_last_successfully_committed_prev(self):
        code = [
            LoadPrev("Motor", "BOOL"), StoreVar("PrevMotor", "BOOL"),
            LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL"),
        ]
        task = _task(code)
        trace = _Trace()
        committer = _FakeCommitter(trace)
        engine, layout, trace, policy, committer = _build(
            task, trace=trace, committer=committer)

        # 第 1 拍成功：prev.Motor = True
        engine.scan({"DI0": True, "AI0": 0})

        # 第 2 拍提交失败：prev 停留在第 1 拍
        committer.fail = True
        with self.assertRaises(RuntimeError):
            engine.scan({"DI0": False, "AI0": 0})

        # 第 3 拍恢复：仍以上次**成功提交**的 prev（Motor=True）为准
        committer.fail = False
        engine.scan({"DI0": False, "AI0": 0})
        self.assertEqual(layout.store.read("PrevMotor"), True)

    def test_no_half_scan_pending_residue_after_failure(self):
        task = _task(_MAIN_CODE)
        trace = _Trace()
        committer = _FakeCommitter(trace, fail=True)
        engine, layout, trace, policy, committer = _build(
            task, trace=trace, committer=committer)

        with self.assertRaises(RuntimeError):
            engine.scan({"DI0": True, "AI0": 0})

        # 下一拍（策略只 stage False）不得看到上一拍残留的 True
        committer.fail = False
        result = engine.scan({"DI0": False, "AI0": 0})
        self.assertEqual(result.outputs(), {"DO0": False})
        self.assertEqual(committer.received[-1], {"DO0": False})


# ---------------------------------------------------------------------------
# 要求 6：通道集完整性
# ---------------------------------------------------------------------------

class TestOutputChannelCompleteness(unittest.TestCase):

    def test_missing_staged_channel_is_rejected_before_commit(self):
        task = _task(_MAIN_CODE)
        trace = _Trace()
        policy = _FakePolicy(trace, skip_channels={"DO0"})
        engine, _, trace, policy, committer = _build(
            task, trace=trace, policy=policy)

        with self.assertRaises(OutputStagingError):
            engine.scan({"DI0": True, "AI0": 0})

        self.assertEqual(committer.calls, 0)          # 提交前拒绝
        self.assertEqual(engine.prev.read("Motor"), False)

    def test_unknown_channel_is_rejected_by_pending(self):
        task = _task(_MAIN_CODE)
        trace = _Trace()
        policy = _FakePolicy(trace, stage_extra=("DO_UNKNOWN", True))
        engine, _, trace, policy, committer = _build(
            task, trace=trace, policy=policy)

        with self.assertRaises(OutputImageError):
            engine.scan({"DI0": True, "AI0": 0})
        self.assertEqual(committer.calls, 0)

    def test_zero_out_channel_task_commits_empty_snapshot(self):
        io_map = [IOMap("Start", "DI0", "IN"), IOMap("Level", "AI0", "IN")]
        task = _task(_MAIN_CODE, io_map=io_map)
        engine, _, trace, policy, committer = _build(task)

        result = engine.scan({"DI0": True, "AI0": 0})
        self.assertEqual(result.outputs(), {})
        self.assertEqual(committer.received, [{}])    # 合法：提交空快照
        self.assertEqual(trace.events, ["ir", "policy", "commit"])


# ---------------------------------------------------------------------------
# 要求 7：request 不绕过策略；提交方修改不污染
# ---------------------------------------------------------------------------

class TestRequestDoesNotBypassPolicy(unittest.TestCase):

    def test_store_writes_do_not_auto_enter_pending(self):
        # 策略"什么都不 stage" → 即便 Store 里 Motor=True，也不得有输出
        task = _task(_MAIN_CODE)
        trace = _Trace()
        policy = _FakePolicy(trace, skip_channels={"DO0"})
        engine, layout, trace, policy, committer = _build(
            task, trace=trace, policy=policy)

        with self.assertRaises(OutputStagingError):
            engine.scan({"DI0": True, "AI0": 0})

        self.assertEqual(layout.store.read("Motor"), True)   # 业务确实写了
        self.assertEqual(committer.calls, 0)                 # 但没有绕过策略

    def test_committer_mutation_pollutes_neither_result_nor_next_scan(self):
        task = _task(_MAIN_CODE)
        trace = _Trace()
        committer = _FakeCommitter(trace, mutate=True)
        engine, _, trace, policy, committer = _build(
            task, trace=trace, committer=committer)

        result = engine.scan({"DI0": True, "AI0": 0})
        self.assertEqual(result.outputs(), {"DO0": True})    # 结果未被污染

        result2 = engine.scan({"DI0": False, "AI0": 0})
        self.assertEqual(result2.outputs(), {"DO0": False})  # 下一拍未被污染


# ---------------------------------------------------------------------------
# 要求 8：多 PROGRAM 顺序 + cycle_ms 原样到达 adapter
# ---------------------------------------------------------------------------

class _FakeAdapter:
    """测试替身：记录收到的 dt_ms。**不是**任何真实库块实现。"""

    def __init__(self, trace, name):
        self.trace = trace
        self.name = name
        self.dt_ms_seen = []

    def step(self, dt_ms):
        self.trace.events.append("adapter:%s" % self.name)
        self.dt_ms_seen.append(dt_ms)


class TestProgramOrderAndDtMs(unittest.TestCase):

    def test_multiple_programs_run_in_explicit_list_order(self):
        # P1 写 Trace:=1，P2 写 Trace:=2 → 列表顺序决定最终值
        p1 = _prog("P1", [LoadConst(1, "INT"), StoreVar("Trace", "INT"),
                          LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")])
        p2 = _prog("P2", [LoadConst(2, "INT"), StoreVar("Trace", "INT")])
        task = _task(None, pous=[p1, p2],
                     programs=[ProgramInstance("P1", "A"),
                               ProgramInstance("P2", "B")])
        engine, layout, _, _, _ = _build(task)
        engine.scan({"DI0": True, "AI0": 0})
        self.assertEqual(layout.store.read("Trace"), 2)

        # 反序即得反序结果（证明顺序来自显式列表，不是字典/名称顺序）
        task_r = _task(None, pous=[p1, p2],
                       programs=[ProgramInstance("P2", "B"),
                                 ProgramInstance("P1", "A")])
        engine_r, layout_r, _, _, _ = _build(task_r)
        engine_r.scan({"DI0": True, "AI0": 0})
        self.assertEqual(layout_r.store.read("Trace"), 1)

    def test_cycle_ms_reaches_library_adapter_unchanged(self):
        main = _prog("Main", [LoadVar("Start", "BOOL"),
                              StoreVar("Motor", "BOOL"),
                              CallFb("Blk")],
                     instances=[InstanceDecl("Blk", "TON", kind="library")])
        task = _task(None, pous=[main], cycle_ms=500)
        trace = _Trace()
        adapter = _FakeAdapter(trace, "Blk")
        engine, _, trace, _, _ = _build(
            task, trace=trace, adapters={"PLC_PRG.Blk": adapter})

        engine.scan({"DI0": True, "AI0": 0})
        engine.scan({"DI0": True, "AI0": 0})

        # dt_ms 恒为任务配置的 cycle_ms——不随 Python 实际耗时/墙钟变化
        self.assertEqual(adapter.dt_ms_seen, [500, 500])
        self.assertEqual(engine.cycle_ms, 500)

    def test_dt_ms_comes_from_task_config_not_wall_clock(self):
        # dt_ms 的唯一来源是 Task.cycle_ms：拍与拍之间无论真实耗时多少，
        # adapter 收到的值恒定（本测试不依赖墙钟，也不 sleep）。
        # 注：当前阶段 loader 把 cycle_ms 冻结为 500（IR_SPEC §3），故此处
        # 不构造其他周期值——多周期属后续扩展点。
        main = _prog("Main", [LoadVar("Start", "BOOL"),
                              StoreVar("Motor", "BOOL"),
                              CallFb("Blk")],
                     instances=[InstanceDecl("Blk", "TON", kind="library")])
        task = _task(None, pous=[main])
        trace = _Trace()
        adapter = _FakeAdapter(trace, "Blk")
        engine, _, _, _, _ = _build(
            task, trace=trace, adapters={"PLC_PRG.Blk": adapter})
        for _ in range(5):
            engine.scan({"DI0": True, "AI0": 0})
        self.assertEqual(adapter.dt_ms_seen, [task.cycle_ms] * 5)


# ---------------------------------------------------------------------------
# 要求 9：重入失败关闭且可恢复
# ---------------------------------------------------------------------------

class TestReentrancy(unittest.TestCase):

    def test_recursive_scan_fails_closed(self):
        task = _task(_MAIN_CODE)
        trace = _Trace()
        box = {}

        def _recurse():
            with self.assertRaises(ScanReentryError):
                box["engine"].scan({"DI0": True, "AI0": 0})
            box["recursed"] = True

        policy = _FakePolicy(trace, hook=_recurse)
        engine, _, trace, policy, committer = _build(
            task, trace=trace, policy=policy)
        box["engine"] = engine

        result = engine.scan({"DI0": True, "AI0": 0})
        self.assertTrue(box["recursed"])
        # 递归拍被拒绝，外层拍正常完成：没有两拍交错
        self.assertEqual(trace.events, ["ir", "policy", "commit"])
        self.assertEqual(committer.calls, 1)
        self.assertEqual(result.outputs(), {"DO0": True})

    def test_concurrent_scan_fails_closed(self):
        task = _task(_MAIN_CODE)
        trace = _Trace()
        started = threading.Event()
        release = threading.Event()
        errors = []

        def _hold():
            started.set()
            release.wait(timeout=5)

        policy = _FakePolicy(trace, hook=_hold)
        engine, _, trace, policy, committer = _build(
            task, trace=trace, policy=policy)

        def _other():
            started.wait(timeout=5)
            try:
                engine.scan({"DI0": True, "AI0": 0})
            except ScanReentryError as e:
                errors.append(e)
            finally:
                release.set()

        t = threading.Thread(target=_other)
        t.start()
        engine.scan({"DI0": True, "AI0": 0})
        t.join(timeout=5)

        self.assertEqual(len(errors), 1)          # 并发拍失败关闭
        self.assertEqual(committer.calls, 1)      # 只有一拍真正提交

    def test_lock_recovers_after_failed_scan(self):
        task = _task(_MAIN_CODE)
        trace = _Trace()
        policy = _FakePolicy(trace, fail=True)
        engine, _, trace, policy, committer = _build(
            task, trace=trace, policy=policy)

        with self.assertRaises(RuntimeError):
            engine.scan({"DI0": True, "AI0": 0})

        # 失败不得永久卡死：修好后下一拍仍可执行
        policy.fail = False
        result = engine.scan({"DI0": True, "AI0": 0})
        self.assertEqual(result.outputs(), {"DO0": True})


# ---------------------------------------------------------------------------
# 要求 10：包边界与装配契约
# ---------------------------------------------------------------------------

class TestPackageBoundaryAndConfig(unittest.TestCase):

    def test_public_api_is_exported(self):
        import src.runtime as rt
        for name in ("ScanEngine", "ScanResult", "ScanError", "ScanConfigError",
                     "OutputStagingError", "ScanReentryError"):
            self.assertIn(name, rt.__all__)
            self.assertTrue(hasattr(rt, name))

    def test_engine_does_not_import_prototype_or_wall_clock(self):
        import src.runtime.engine as eng
        with open(eng.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("prototype_05", source)
        # dt 只来自 Task.cycle_ms：不得读墙钟、不得 sleep（匹配实际调用形态，
        # 不误伤 docstring 中"不 sleep"这类边界声明文字）
        self.assertNotIn("import time", source)
        self.assertNotIn("time.time(", source)
        self.assertNotIn("time.monotonic(", source)
        self.assertNotIn("sleep(", source)

    def test_executor_bound_to_other_task_is_rejected(self):
        task_a = _task(_MAIN_CODE)
        task_b = _task(_MAIN_CODE)
        layout_a = build_runtime_store(task_a)
        layout_b = build_runtime_store(task_b)
        trace = _Trace()
        executor_b = Executor(task_b, layout_b)

        with self.assertRaises(ScanConfigError):
            ScanEngine(task_a, layout_a, executor_b, _FakePolicy(trace),
                       _FakeCommitter(trace))

    def test_ports_missing_contract_methods_are_rejected(self):
        task = _task(_MAIN_CODE)
        layout = build_runtime_store(task)
        executor = Executor(task, layout)
        trace = _Trace()

        with self.assertRaises(ScanConfigError):
            ScanEngine(task, layout, executor, object(), _FakeCommitter(trace))
        with self.assertRaises(ScanConfigError):
            ScanEngine(task, layout, executor, _FakePolicy(trace), object())


if __name__ == "__main__":
    unittest.main()
