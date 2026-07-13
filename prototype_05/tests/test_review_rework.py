"""一轮定向返修的反证测试（对应 Codex 审核意见 1–5，逐条建立回归锁）。"""
import unittest

from prototype_05 import frontends as fe
from prototype_05 import programs
from prototype_05.engine import Engine, MemoryDriver
from prototype_05.ir import (Binding, IOMap, OutputPolicy, ProgramInstance,
                             Task, VarDecl)
from prototype_05.loader import LoadError
from prototype_05.numeric import NumericMode


class RaisingDriver(MemoryDriver):
    """在指定通道抛异常（而非返回 False）的驱动。"""

    def __init__(self):
        super().__init__()
        self.raise_channels: set = set()

    def write(self, channel, value):
        if channel in self.raise_channels:
            raise TimeoutError(f"bus timeout on {channel}")
        return super().write(channel, value)


class TestReview1DriverExceptionIsolation(unittest.TestCase):
    """意见 1：驱动抛异常 = 写失败，不得破坏逐通道隔离（ENGINE_SCAN_SPEC §4.4-4）。"""

    def test_exception_treated_as_commit_fault_and_other_channels_committed(self):
        driver = RaisingDriver()
        driver.raise_channels.add("DO1")
        eng = Engine(programs.output_paths_task(), driver=driver)
        eng.run_scan({"InAV": 50.0, "InMotor": True})   # 异常不得逃出 run_scan
        self.assertEqual(driver.values.get("AO1"), 15.0)          # AO1 照常提交
        st = eng.output_states["DO1"]
        self.assertTrue(st.commit_fault)
        self.assertIsNone(st.last_physical_committed)
        kinds = [a[0] for a in eng.alarms]
        self.assertIn("commit_exception", kinds)
        self.assertIn("commit_fault", kinds)

    def test_exception_follows_same_retry_escalation_path(self):
        driver = RaisingDriver()
        driver.raise_channels.add("DO1")
        eng = Engine(programs.output_paths_task(), driver=driver)
        for _ in range(3):
            eng.run_scan({"InMotor": True})
        self.assertIn(("channel_fault_escalated", "DO1"), eng.alarms)
        driver.raise_channels.clear()
        eng.run_scan({"InMotor": True})                            # 恢复：写 safe 成功
        self.assertIn(("commit_recovered", "DO1"), eng.alarms)
        self.assertEqual(eng.output_states["DO1"].last_physical_committed, False)


class TestReview2BindingActualTypeChecked(unittest.TestCase):
    """意见 2：绑定 actual 变量的声明类型必须与形参类型严格相等（全类型化闭环）。"""

    def _task_with_total_type(self, iec_type):
        task = programs.var_in_out_task()
        task.gvl = [VarDecl("Total", iec_type,
                            False if iec_type == "BOOL" else 0.0,
                            section="VAR_GLOBAL")]
        return task

    def test_real_inout_bound_to_bool_var_rejected(self):
        with self.assertRaisesRegex(LoadError, "声明类型 BOOL.*要求 REAL"):
            Engine(self._task_with_total_type("BOOL"))

    def test_out_binding_type_mismatch_rejected(self):
        task = programs.isolation_task()
        # CntA 改声明为 REAL，与 CounterFB.CNT(INT) 的 OUT 绑定不再匹配
        for v in task.gvl:
            if v.name == "CntA":
                v.iec_type, v.initial = "REAL", 0.0
        with self.assertRaisesRegex(LoadError, "声明类型 REAL.*要求 INT"):
            Engine(task)


class TestReview3PolicyValidationComplete(unittest.TestCase):
    """意见 3：OutputPolicy 是安全配置，非法字段一律加载期硬拒绝。"""

    def _engine_with(self, **kw):
        policy = OutputPolicy(**{**dict(var="B1", iec_type="BOOL", safe_value=False), **kw})
        task = Task(programs=[ProgramInstance("P", [])],
                    gvl=[VarDecl("B1", "BOOL", section="VAR_GLOBAL")],
                    io_map=[IOMap("B1", "DO", "OUT", policy)])
        return Engine(task)

    def test_unknown_fault_action_rejected(self):
        with self.assertRaisesRegex(LoadError, "不是合法 FaultAction"):
            self._engine_with(on_operator_disable="banana")

    def test_negative_rate_limit_rejected(self):
        with self.assertRaisesRegex(LoadError, "rate_limit"):
            self._engine_with(rate_limit=-10)

    def test_zero_retry_n_rejected(self):
        with self.assertRaisesRegex(LoadError, "commit_fault_retry_n"):
            self._engine_with(commit_fault_retry_n=0)

    def test_safe_value_type_mismatch_rejected(self):
        with self.assertRaisesRegex(LoadError, "safe_value"):
            self._engine_with(safe_value=5.0)          # BOOL 通道配浮点安全值
        with self.assertRaisesRegex(LoadError, "safe_value"):
            self._engine_with(iec_type="REAL", var="B1", safe_value=True)


class TestReview4ColdShadowToLiveBaseline(unittest.TestCase):
    """意见 4：冷启动直接 shadow → 切实写且无 LPC 时，基准 = safe_value（原型约定②，
    暂定语义，待冻结评审裁决，登记 PLATFORM-OUTPUT-BASELINE-1）。"""

    def test_first_live_scan_rate_limits_from_safe_value(self):
        eng = Engine(programs.output_paths_task())
        for _ in range(3):
            eng.run_scan({"InAV": 50.0}, shadow=True)   # le 爬到 35，lpc 仍 None
        self.assertEqual(eng.output_states["AO1"].last_effective, 35.0)
        eng.run_scan({"InAV": 50.0})                    # 首次实写
        # 修复前：基准取 le=35 → 直接写 45（无物理基准的跳变）；现从 safe=5 限速 → 15
        self.assertEqual(eng.driver.values["AO1"], 15.0)


class TestReview5PureIntegerDivMod(unittest.TestCase):
    """意见 5：整数 DIV/MOD 纯整数向零截断，大整数不经 float 失精度。"""

    LINT_MAX = 2 ** 63 - 1

    def _run(self, code, gvl, mode):
        eng = Engine(Task(programs=[ProgramInstance("P", code)], gvl=gvl), mode=mode)
        eng.run_scan()
        return eng

    def test_lint_max_div_1_exact_in_both_modes(self):
        code = fe.lower_st([fe.Assign("Y", "LINT",
                                      fe.Bin("DIV", fe.Var("X", "LINT"),
                                             fe.Const(1, "LINT"), "LINT"))])
        gvl = [VarDecl("X", "LINT", self.LINT_MAX, section="VAR_GLOBAL"),
               VarDecl("Y", "LINT", section="VAR_GLOBAL")]
        for mode in (NumericMode(), NumericMode(mode="fidelity_f1")):
            eng = self._run(code, gvl, mode)
            self.assertEqual(eng.store["Y"], self.LINT_MAX, mode.mode)

    def test_div_mod_truncate_toward_zero_with_signs(self):
        """(-7) DIV 2 = -3（非 floor 的 -4）；(-7) MOD 2 = -1（符号随被除数）。"""
        code = fe.lower_st([
            fe.Assign("Q", "INT", fe.Bin("DIV", fe.Var("A", "INT"),
                                         fe.Const(2, "INT"), "INT")),
            fe.Assign("R", "INT", fe.Bin("MOD", fe.Var("A", "INT"),
                                         fe.Const(2, "INT"), "INT")),
        ])
        gvl = [VarDecl("A", "INT", -7, section="VAR_GLOBAL"),
               VarDecl("Q", "INT", section="VAR_GLOBAL"),
               VarDecl("R", "INT", section="VAR_GLOBAL")]
        eng = self._run(code, gvl, NumericMode())
        self.assertEqual(eng.store["Q"], -3)
        self.assertEqual(eng.store["R"], -1)

    def test_lint_max_mod_exact(self):
        code = fe.lower_st([fe.Assign("Y", "LINT",
                                      fe.Bin("MOD", fe.Var("X", "LINT"),
                                             fe.Const(10, "LINT"), "LINT"))])
        gvl = [VarDecl("X", "LINT", self.LINT_MAX, section="VAR_GLOBAL"),
               VarDecl("Y", "LINT", section="VAR_GLOBAL")]
        eng = self._run(code, gvl, NumericMode())
        self.assertEqual(eng.store["Y"], self.LINT_MAX % 10)   # = 7


class TestReview2Round2BindingStructure(unittest.TestCase):
    """二轮意见 1：Binding 表结构非法必须加载期拒绝（不得留到运行期）。"""

    def _task_with_bindings(self, bindings):
        from prototype_05.ir import CALL_FB_INSTANCE
        task = programs.var_in_out_task()
        task.programs[0].code = [CALL_FB_INSTANCE("ACC1", bindings)]
        return task

    def test_duplicate_formal_binding_rejected(self):
        with self.assertRaisesRegex(LoadError, "重复绑定"):
            Engine(self._task_with_bindings([
                Binding("INC", "IN", "const", 2.5, "REAL"),
                Binding("INC", "IN", "const", 9.9, "REAL"),   # 后者原会静默覆盖
                Binding("ACC", "INOUT", "var", "Total", "REAL"),
            ]))

    def test_illegal_actual_kind_rejected(self):
        with self.assertRaisesRegex(LoadError, "actual_kind"):
            Engine(self._task_with_bindings([
                Binding("INC", "IN", "banana", 2.5, "REAL"),
                Binding("ACC", "INOUT", "var", "Total", "REAL"),
            ]))

    def test_const_value_type_mismatch_rejected(self):
        with self.assertRaisesRegex(LoadError, "const 实参"):
            Engine(self._task_with_bindings([
                Binding("INC", "IN", "const", "not-a-real", "REAL"),
                Binding("ACC", "INOUT", "var", "Total", "REAL"),
            ]))

    def test_load_const_value_type_mismatch_rejected(self):
        """同一原则覆盖 LOAD_CONST：常量值与指令类型不匹配加载期拒绝。"""
        from prototype_05.ir import LOAD_CONST, STORE_VAR
        task = Task(programs=[ProgramInstance("P", [
            LOAD_CONST("not-a-real", "REAL"), STORE_VAR("R1", "REAL")])],
            gvl=[VarDecl("R1", "REAL", section="VAR_GLOBAL")])
        with self.assertRaisesRegex(LoadError, "LOAD_CONST 常量"):
            Engine(task)


class TestReview3Round2PolicyFiniteAndRange(unittest.TestCase):
    """二轮意见 2：安全配置必须是有限数；整数安全值须在 IEC 类型范围内。"""

    def _engine_with(self, gvl_type="BOOL", **kw):
        init = {"BOOL": False, "REAL": 0.0, "UINT": 0}[gvl_type]
        defaults = dict(var="V1", iec_type=gvl_type,
                        safe_value={"BOOL": False, "REAL": 5.0, "UINT": 0}[gvl_type])
        policy = OutputPolicy(**{**defaults, **kw})
        task = Task(programs=[ProgramInstance("P", [])],
                    gvl=[VarDecl("V1", gvl_type, init, section="VAR_GLOBAL")],
                    io_map=[IOMap("V1", "CH", "OUT", policy)])
        return Engine(task)

    def test_rate_limit_nan_rejected(self):
        with self.assertRaisesRegex(LoadError, "rate_limit"):
            self._engine_with("REAL", rate_limit=float("nan"))

    def test_rate_limit_infinity_rejected(self):
        """Infinity 若表示不限速，应使用 None。"""
        with self.assertRaisesRegex(LoadError, "rate_limit"):
            self._engine_with("REAL", rate_limit=float("inf"))

    def test_safe_value_nan_rejected(self):
        with self.assertRaisesRegex(LoadError, "safe_value"):
            self._engine_with("REAL", safe_value=float("nan"))

    def test_uint_safe_value_negative_rejected(self):
        with self.assertRaisesRegex(LoadError, "safe_value"):
            self._engine_with("UINT", safe_value=-1)


if __name__ == "__main__":
    unittest.main()
