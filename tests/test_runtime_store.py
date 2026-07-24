"""WP-20260714-003：运行时 Store、实例状态与过程映像基础的测试。

对应工作包"最低测试要求"17 条逐条覆盖。注意：这些测试只验证 Python 侧
运行时内存底座的当前行为，**不构成与 CODESYS PLC 语义一致的证据**。
"""
from __future__ import annotations

import sys
import unittest

from src.runtime import (
    DuplicateStoreKeyError,
    InputImageError,
    InstanceDecl,
    InstanceLayoutError,
    IOMap,
    IRValidationError,
    LoadVar,
    OutputImageError,
    OutputPending,
    POUDefinition,
    ProgramInstance,
    Store,
    StoreTypeError,
    StoreVar,
    Task,
    UnknownStoreKeyError,
    VarDecl,
    build_default_registry,
    build_runtime_store,
    latch_inputs,
    make_prev_snapshot,
    persistent_key,
)


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------

def _gvl():
    return [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Level", "REAL", initial=1.5, section="VAR_GLOBAL"),
        VarDecl("Count", "INT", initial=7, section="VAR_GLOBAL",
                retain=True),
    ]


def _min_code():
    return [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]


def _fb_def(name="Filter", locals_extra=None):
    return POUDefinition(
        name=name, pou_kind="FUNCTION_BLOCK", language="ST",
        interface=[
            VarDecl("IN", "REAL", section="VAR_INPUT"),
            VarDecl("Q", "REAL", section="VAR_OUTPUT"),
        ],
        locals=[VarDecl("acc", "REAL", initial=0.0),
                VarDecl("tmp", "REAL", section="VAR_TEMP")]
               + (locals_extra or []),
        code=[],
    )


def _task(main_extra_instances=None, extra_pous=None, io_map=None,
          main_locals=None):
    main = POUDefinition(
        name="Main", pou_kind="PROGRAM", language="ST",
        locals=main_locals if main_locals is not None else [
            VarDecl("step_no", "INT", initial=0),
            VarDecl("scratch", "BOOL", section="VAR_TEMP"),
        ],
        instances=main_extra_instances or [],
        code=_min_code(),
    )
    pou_lib = {main.name: main}
    for p in (extra_pous or []):
        pou_lib[p.name] = p
    return Task(
        programs=[ProgramInstance(definition="Main", store_prefix="PLC_PRG")],
        gvl=_gvl(),
        io_map=io_map or [],
        pou_lib=pou_lib,
    )


# ---------------------------------------------------------------------------
# 1–3：Store 基础
# ---------------------------------------------------------------------------

class TestStoreBasics(unittest.TestCase):
    def test_declare_read_write(self):                       # 要求 1
        s = Store()
        s.declare("x", "INT", 3)
        self.assertEqual(s.read("x"), 3)
        s.write("x", 5)
        self.assertEqual(s.read("x"), 5)
        self.assertEqual(s.declared_type("x"), "INT")

    def test_default_initials(self):
        s = Store()
        for key, t, expect in [("b", "BOOL", False), ("i", "DINT", 0),
                               ("r", "REAL", 0.0), ("t", "TIME", 0),
                               ("s", "STRING", "")]:
            s.declare(key, t)
            self.assertEqual(s.read(key), expect)

    def test_unknown_key(self):                              # 要求 2
        s = Store()
        with self.assertRaises(UnknownStoreKeyError):
            s.read("ghost")
        with self.assertRaises(UnknownStoreKeyError):
            s.write("ghost", 1)          # 未声明键不得被静默创建
        self.assertNotIn("ghost", s)

    def test_duplicate_key(self):                            # 要求 2
        s = Store()
        s.declare("x", "INT")
        with self.assertRaises(DuplicateStoreKeyError):
            s.declare("x", "INT")

    def test_type_mismatch(self):                            # 要求 2
        s = Store()
        s.declare("x", "INT")
        with self.assertRaises(StoreTypeError):
            s.write("x", 1.5)
        with self.assertRaises(StoreTypeError):
            s.write("x", True)           # bool 不是合法 INT 值
        with self.assertRaises(StoreTypeError):
            s.declare("r", "REAL", initial=1)   # int 不隐式转 REAL
        with self.assertRaises(StoreTypeError):
            s.declare("bad", "NOT_A_TYPE")

    def test_snapshot_readonly_and_isolated(self):           # 要求 3
        s = Store()
        s.declare("x", "INT", 1)
        snap = s.snapshot()
        s.write("x", 2)
        self.assertEqual(snap.read("x"), 1)      # 后续写入不影响快照
        self.assertEqual(s.read("x"), 2)
        self.assertFalse(hasattr(snap, "write")) # 快照无写接口
        d = snap.as_dict()
        d["x"] = 99
        self.assertEqual(snap.read("x"), 1)      # 导出副本修改不回渗
        with self.assertRaises(UnknownStoreKeyError):
            snap.read("ghost")


# ---------------------------------------------------------------------------
# 4–10：实例布局
# ---------------------------------------------------------------------------

class TestRuntimeLayout(unittest.TestCase):
    def test_gvl_initialization(self):                       # 要求 4
        layout = build_runtime_store(_task())
        s = layout.store
        self.assertEqual(s.read("Start"), False)
        self.assertEqual(s.read("Level"), 1.5)
        self.assertEqual(s.read("Count"), 7)
        self.assertEqual(s.retain_flags("Count"), (True, False))

    def test_program_instance_created_once(self):            # 要求 5
        layout = build_runtime_store(_task())
        self.assertEqual(len(layout.programs), 1)
        key = persistent_key("PLC_PRG", "step_no")
        self.assertIn(key, layout.store)
        self.assertEqual(layout.store.read(key), 0)

    def test_two_fb_instances_isolated(self):                # 要求 6
        fb = _fb_def()
        task = _task(
            main_extra_instances=[
                InstanceDecl("F1", "Filter", kind="user_fb"),
                InstanceDecl("F2", "Filter", kind="user_fb"),
            ],
            extra_pous=[fb],
        )
        layout = build_runtime_store(task)
        s = layout.store
        k1 = persistent_key("PLC_PRG.F1", "acc")
        k2 = persistent_key("PLC_PRG.F2", "acc")
        s.write(k1, 9.9)
        self.assertEqual(s.read(k1), 9.9)
        self.assertEqual(s.read(k2), 0.0)        # 同型实例状态完全隔离
        self.assertEqual(set(layout.fb_paths()),
                         {"PLC_PRG.F1", "PLC_PRG.F2"})

    def test_nested_instance_paths(self):                    # 要求 7
        inner = _fb_def("Inner")
        outer = POUDefinition(
            name="Outer", pou_kind="FUNCTION_BLOCK", language="ST",
            locals=[VarDecl("state", "INT", initial=1)],
            instances=[InstanceDecl("Sub", "Inner", kind="user_fb")],
            code=[],
        )
        task = _task(
            main_extra_instances=[InstanceDecl("O1", "Outer", kind="user_fb"),
                                  InstanceDecl("O2", "Outer", kind="user_fb")],
            extra_pous=[outer, inner],
        )
        layout = build_runtime_store(task)
        s = layout.store
        self.assertEqual(set(layout.fb_paths()),
                         {"PLC_PRG.O1", "PLC_PRG.O1.Sub",
                          "PLC_PRG.O2", "PLC_PRG.O2.Sub"})
        s.write(persistent_key("PLC_PRG.O1.Sub", "acc"), 3.0)
        self.assertEqual(s.read(persistent_key("PLC_PRG.O2.Sub", "acc")), 0.0)

    def test_function_and_var_temp_not_persistent(self):     # 要求 8
        fn = POUDefinition(
            name="Calc", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("X", "REAL", section="VAR_INPUT")],
            locals=[VarDecl("local_v", "REAL")],
            return_type="REAL",
            code=[LoadVar("X", "REAL")],
        )
        task = _task(extra_pous=[fn])
        layout = build_runtime_store(task)
        keys = layout.store.keys()
        self.assertNotIn(persistent_key("PLC_PRG", "scratch"), keys)  # VAR_TEMP
        self.assertFalse(any("Calc" in k for k in keys))              # FUNCTION
        self.assertEqual(layout.fb_instances, [])

    def test_initial_and_init_overrides(self):               # 要求 9
        fb = _fb_def()
        task = _task(
            main_extra_instances=[
                InstanceDecl("F1", "Filter", kind="user_fb",
                             init_overrides={"acc": 5.5}),
                InstanceDecl("F2", "Filter", kind="user_fb"),
            ],
            extra_pous=[fb],
        )
        layout = build_runtime_store(task)
        self.assertEqual(layout.store.read(persistent_key("PLC_PRG.F1", "acc")), 5.5)
        self.assertEqual(layout.store.read(persistent_key("PLC_PRG.F2", "acc")), 0.0)

    def test_init_override_unknown_var_not_silent(self):     # 要求 9
        fb = _fb_def()
        task = _task(
            main_extra_instances=[
                InstanceDecl("F1", "Filter", kind="user_fb",
                             init_overrides={"ghost": 1.0}),
            ],
            extra_pous=[fb],
        )
        with self.assertRaises(InstanceLayoutError):
            build_runtime_store(task)

    def test_init_override_type_checked(self):
        fb = _fb_def()
        task = _task(
            main_extra_instances=[
                InstanceDecl("F1", "Filter", kind="user_fb",
                             init_overrides={"acc": "not-a-real"}),
            ],
            extra_pous=[fb],
        )
        with self.assertRaises(StoreTypeError):
            build_runtime_store(task)

    def test_retain_metadata_only(self):                     # 要求 10
        fb = _fb_def(locals_extra=[VarDecl("held", "REAL", retain=True)])
        task = _task(
            main_extra_instances=[
                InstanceDecl("F1", "Filter", kind="user_fb",
                             retain={"held"}),
            ],
            extra_pous=[fb],
        )
        layout = build_runtime_store(task)
        key = persistent_key("PLC_PRG.F1", "held")
        self.assertEqual(layout.store.retain_flags(key), (True, False))
        self.assertEqual(layout.fb_instances[0].retain, {"held"})
        # 只保留元数据,不伪造恢复行为:布局对象上没有任何 restore/persist API
        for attr in dir(layout.store):
            self.assertNotIn("restore", attr.lower())
            self.assertNotIn("persist_to", attr.lower())

    def test_library_instance_no_pin_guess(self):
        task = _task(main_extra_instances=[
            InstanceDecl("TON1", "TON", kind="library")])
        layout = build_runtime_store(task)
        self.assertEqual([p for p, _ in layout.library_instances],
                         ["PLC_PRG.TON1"])
        # 未接入 L2 描述符:不为库块分配任何管脚键
        self.assertFalse(any(k.startswith("PLC_PRG.TON1.")
                             for k in layout.store.keys()))


# ---------------------------------------------------------------------------
# 11–14：输入锁存
# ---------------------------------------------------------------------------

def _io_task():
    io_map = [
        IOMap("Start", "DI0", "IN"),
        IOMap("Level", "AI0", "IN"),
        IOMap("Motor", "DO0", "OUT", policy=object()),
    ]
    return _task(io_map=io_map)


class TestInputLatch(unittest.TestCase):
    def setUp(self):
        task = _io_task()
        self.task = task
        self.layout = build_runtime_store(task)
        self.store = self.layout.store

    def test_one_shot_latch(self):                           # 要求 11
        snap = latch_inputs(self.store, self.task.io_map,
                            {"DI0": True, "AI0": 2.5})
        self.assertEqual(self.store.read("Start"), True)
        self.assertEqual(self.store.read("Level"), 2.5)
        self.assertEqual(snap.read("Start"), True)
        self.assertEqual(snap.read("Level"), 2.5)

    def test_external_dict_mutation_isolated(self):          # 要求 12
        samples = {"DI0": True, "AI0": 2.5}
        snap = latch_inputs(self.store, self.task.io_map, samples)
        samples["AI0"] = 99.0                    # 锁存后外部字典再变
        samples["DI0"] = False
        self.assertEqual(snap.read("Level"), 2.5)   # 快照按变量名取值,不受影响
        self.assertEqual(snap.read("Start"), True)
        self.assertEqual(self.store.read("Level"), 2.5)

    def test_no_partial_update_on_invalid(self):             # 要求 13
        # AI0 类型非法;DI0 合法——两者都不得写入
        with self.assertRaises(InputImageError):
            latch_inputs(self.store, self.task.io_map,
                         {"DI0": True, "AI0": "bad"})
        self.assertEqual(self.store.read("Start"), False)    # 未部分更新
        self.assertEqual(self.store.read("Level"), 1.5)

    def test_unknown_missing_and_type_errors(self):          # 要求 14
        with self.assertRaises(InputImageError):             # 未知通道
            latch_inputs(self.store, self.task.io_map,
                         {"DI0": True, "AI0": 2.5, "DI9": False})
        with self.assertRaises(InputImageError):             # 缺失必要通道
            latch_inputs(self.store, self.task.io_map, {"DI0": True})
        with self.assertRaises(InputImageError):             # 类型不匹配
            latch_inputs(self.store, self.task.io_map,
                         {"DI0": 1, "AI0": 2.5})

    def test_duplicate_channel_mapping_rejected(self):
        io_map = [IOMap("Start", "DI0", "IN"), IOMap("Motor", "DI0", "IN")]
        with self.assertRaises(InputImageError):
            latch_inputs(self.store, io_map, {"DI0": True})
        # 同变量被两个通道映射同样拒绝
        io_map2 = [IOMap("Start", "DI0", "IN"), IOMap("Start", "DI1", "IN")]
        with self.assertRaises(InputImageError):
            latch_inputs(self.store, io_map2, {"DI0": True, "DI1": False})


# ---------------------------------------------------------------------------
# 15–16：输出待提交映像与 prev 快照
# ---------------------------------------------------------------------------

class TestOutputPendingAndPrev(unittest.TestCase):
    def setUp(self):
        task = _io_task()
        self.task = task
        self.store = build_runtime_store(task).store

    def test_output_pending_boundary(self):                  # 要求 15
        pending = OutputPending(self.store, self.task.io_map)
        self.assertEqual(pending.channels(), ("DO0",))
        self.assertEqual(pending.var_for("DO0"), "Motor")
        # 业务 Store 写入不自动进入待提交映像
        self.store.write("Motor", True)
        self.assertEqual(pending.staged(), {})
        # 暂存不改业务 Store,也不产生物理 I/O(容器无任何 commit/driver API)
        pending.stage("DO0", False)
        self.assertEqual(self.store.read("Motor"), True)
        self.assertEqual(pending.staged(), {"DO0": False})
        for attr in dir(pending):
            self.assertNotIn("commit", attr.lower())
            self.assertNotIn("driver", attr.lower())
        with self.assertRaises(OutputImageError):
            pending.stage("DO9", True)           # 未知通道
        with self.assertRaises(OutputImageError):
            pending.stage("DO0", 1.5)            # 类型不匹配
        d = pending.staged()
        d["DO0"] = True
        self.assertEqual(pending.staged(), {"DO0": False})   # 副本隔离

    def test_prev_snapshot_not_polluted(self):               # 要求 16
        prev = make_prev_snapshot(self.store)
        self.assertEqual(prev.read("Motor"), False)
        self.store.write("Motor", True)
        self.store.write("Level", 8.8)
        self.assertEqual(prev.read("Motor"), False)          # 不被后写污染
        self.assertEqual(prev.read("Level"), 1.5)
        self.assertEqual(self.store.read("Motor"), True)


# ---------------------------------------------------------------------------
# 17：正式代码不依赖 prototype_05
# ---------------------------------------------------------------------------

class TestNoPrototypeDependency(unittest.TestCase):
    def test_no_prototype_import(self):                      # 要求 17
        # 子进程内干净导入 src.runtime,检查导入链未触及 prototype_05。
        # (不能在本进程查 sys.modules:全仓 discovery 会同时加载原型测试,
        # 造成与被测事实无关的环境污染。)
        import pathlib
        import subprocess
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        probe = ("import sys, src.runtime; "
                 "bad = [m for m in sys.modules if m.startswith('prototype_05')]; "
                 "sys.exit(1 if bad else 0)")
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=str(repo_root),
            env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(repo_root)},
            capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         "正式代码导入链不得触及 prototype_05: %s"
                         % result.stderr.decode(errors="replace"))
        # 源码级双保险:runtime 包源文件中不存在 prototype_05 引用
        import src.runtime
        pkg = pathlib.Path(src.runtime.__file__).parent
        for py in pkg.glob("*.py"):
            text = py.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")):
                    self.assertNotIn("prototype_05", stripped,
                                     "%s 存在对原型的导入" % py.name)


# ---------------------------------------------------------------------------
# 接入 L2 注册表后的库块管脚一次性分配（WP-20260723-018）
# ---------------------------------------------------------------------------
#
# 传入 `build_default_registry()` 时，`build_runtime_store` 在**装载期一次性**
# 为每个库块实例按已注册 Schema 分配全部管脚键；初值取 init_overrides >
# 管脚 default > 类型默认；retain 取管脚在 InstanceDecl.retain / Schema
# retainable 的声明。运行期不新增键（Store 拒绝未声明键）。这些断言只验证
# Python 侧运行时内存底座，不构成与 CODESYS 语义一致的证据。

class TestRegistryLibraryLayout(unittest.TestCase):
    def _reg(self):
        return build_default_registry()

    def _lib_task(self, instances):
        return _task(main_extra_instances=instances)

    def test_library_pins_allocated_once(self):
        task = self._lib_task([InstanceDecl("TON1", "TON", kind="library")])
        layout = build_runtime_store(task, self._reg())
        for pin in ("IN", "PT_ms", "Q", "ET_ms"):
            self.assertIn(persistent_key("PLC_PRG.TON1", pin), layout.store)
        self.assertEqual([p for p, _ in layout.library_instances],
                         ["PLC_PRG.TON1"])

    def test_pin_default_and_type_default(self):
        task = self._lib_task([
            InstanceDecl("TON1", "TON", kind="library"),
            InstanceDecl("L1", "APCHSHLLIM", kind="library"),
        ])
        s = build_runtime_store(task, self._reg()).store
        # 管脚 default（TON IN=False、PT_ms=0）
        self.assertEqual(s.read(persistent_key("PLC_PRG.TON1", "IN")), False)
        self.assertEqual(s.read(persistent_key("PLC_PRG.TON1", "PT_ms")), 0)
        # 无 default 的输出/required 管脚 → 类型默认
        self.assertEqual(s.read(persistent_key("PLC_PRG.TON1", "Q")), False)
        self.assertEqual(s.read(persistent_key("PLC_PRG.L1", "IN")), 0.0)
        self.assertEqual(s.read(persistent_key("PLC_PRG.L1", "AV")), 0.0)

    def test_init_override_and_retain(self):
        task = self._lib_task([
            InstanceDecl("TON1", "TON", kind="library",
                         init_overrides={"PT_ms": 2000}, retain={"ET_ms"}),
        ])
        s = build_runtime_store(task, self._reg()).store
        self.assertEqual(s.read(persistent_key("PLC_PRG.TON1", "PT_ms")), 2000)
        self.assertEqual(s.retain_flags(persistent_key("PLC_PRG.TON1", "ET_ms")),
                         (True, False))
        # 未列入 retain 的管脚默认不 retain
        self.assertEqual(s.retain_flags(persistent_key("PLC_PRG.TON1", "IN")),
                         (False, False))

    def test_whole_instance_retain_star(self):
        task = self._lib_task([
            InstanceDecl("TON1", "TON", kind="library", retain={"*"}),
        ])
        s = build_runtime_store(task, self._reg()).store
        for pin in ("IN", "PT_ms", "Q", "ET_ms"):
            self.assertTrue(
                s.retain_flags(persistent_key("PLC_PRG.TON1", pin))[0], pin)

    def test_init_override_unknown_pin_rejected(self):
        task = self._lib_task([
            InstanceDecl("TON1", "TON", kind="library",
                         init_overrides={"GHOST": 1}),
        ])
        with self.assertRaises(InstanceLayoutError):
            build_runtime_store(task, self._reg())

    def test_retain_unknown_pin_rejected(self):
        task = self._lib_task([
            InstanceDecl("TON1", "TON", kind="library", retain={"GHOST"}),
        ])
        with self.assertRaises(InstanceLayoutError):
            build_runtime_store(task, self._reg())

    def test_init_override_pin_type_checked(self):
        # PT_ms 是 TIME（int 族）；float 初值不匹配 → Store 结构检查拒绝
        task = self._lib_task([
            InstanceDecl("TON1", "TON", kind="library",
                         init_overrides={"PT_ms": 1.5}),
        ])
        with self.assertRaises(StoreTypeError):
            build_runtime_store(task, self._reg())

    def test_unregistered_library_block_rejected(self):
        # build_runtime_store 先跑 validate_task(task, registry)：未注册库块
        # 类型在装载校验即失败关闭
        task = self._lib_task([InstanceDecl("G1", "GHOST", kind="library")])
        with self.assertRaises(IRValidationError):
            build_runtime_store(task, self._reg())

    def test_runtime_rejects_new_library_key(self):
        # 运行期不新增 Store 键：布局建立后写未声明的库块管脚键被拒
        task = self._lib_task([InstanceDecl("TON1", "TON", kind="library")])
        layout = build_runtime_store(task, self._reg())
        with self.assertRaises(UnknownStoreKeyError):
            layout.store.write(persistent_key("PLC_PRG.TON1", "GHOST"), True)

    def test_no_registry_allocates_no_pins(self):
        # 不传 registry 时保持历史诚实边界：不猜测/不分配任何管脚键
        task = self._lib_task([InstanceDecl("TON1", "TON", kind="library")])
        layout = build_runtime_store(task)
        self.assertFalse(any(k.startswith("PLC_PRG.TON1.")
                             for k in layout.store.keys()))
        self.assertEqual([p for p, _ in layout.library_instances],
                         ["PLC_PRG.TON1"])


if __name__ == "__main__":
    unittest.main()
