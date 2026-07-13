"""案例④：shadow / 扫描异常 / 提交失败三条输出路径（ENGINE_SCAN_SPEC §4.1/§4.3/§4.4）
+ OutputPolicy 正常路径/故障优先级/hold 退化。"""
import unittest

from prototype_05 import programs
from prototype_05.engine import Engine, FlakyDriver, MemoryDriver, ScanFlags


def _engine(driver=None):
    return Engine(programs.output_paths_task(), driver=driver or MemoryDriver())


class TestShadowMode(unittest.TestCase):
    def test_shadow_computes_but_never_writes(self):
        """shadow：last_effective 连续更新（限速可模拟），lpc 不更新，驱动零写入（§4.1）。"""
        eng = _engine()
        for _ in range(3):
            eng.run_scan({"InAV": 50.0, "InMotor": True}, shadow=True)
        st = eng.output_states["AO1"]
        # 冷启动基准 safe=5.0，每拍限速 +10：15 → 25 → 35
        self.assertEqual(st.last_effective, 35.0)
        self.assertIsNone(st.last_physical_committed)
        self.assertEqual(eng.driver.written, [])                 # 只算不写

    def test_shadow_to_live_aligns_from_last_physical_committed(self):
        """shadow→实写切换首拍：限速基准 = last_physical_committed（§4.1 对齐规则）。"""
        eng = _engine()
        eng.run_scan({"InAV": 50.0})                             # 实写 1 拍：final 15，lpc=15
        self.assertEqual(eng.driver.values["AO1"], 15.0)
        for _ in range(3):                                       # shadow 3 拍：le 爬到 45
            eng.run_scan({"InAV": 50.0}, shadow=True)
        self.assertEqual(eng.output_states["AO1"].last_effective, 45.0)
        self.assertEqual(eng.output_states["AO1"].last_physical_committed, 15.0)
        eng.run_scan({"InAV": 50.0})                             # 切回实写
        # 若基准取 le=45 会写 50（跳变）；按 lpc=15 渐进 → 25
        self.assertEqual(eng.driver.values["AO1"], 25.0)


class TestScanFault(unittest.TestCase):
    def test_scan_exception_forces_all_outputs_safe_and_still_commits(self):
        """扫描中途抛异常 → runner 在扫描逻辑外按 on_scan_fault（强制 safe）提交（§4.3）。"""
        eng = _engine()
        eng.run_scan({"InAV": 50.0, "InMotor": True})            # 正常拍：15 / True
        self.assertEqual(eng.driver.values, {"AO1": 15.0, "DO1": True})
        pending = eng.run_scan({"InAV": 50.0, "InMotor": True, "FaultTrig": True})  # 除零
        self.assertEqual(pending, {"AO1": 5.0, "DO1": False})    # 全部落安全值
        self.assertEqual(eng.driver.values, {"AO1": 5.0, "DO1": False})  # 且确实提交
        self.assertIn("scan_fault", [a[0] for a in eng.alarms])
        # 故障落 safe 不受 rate_limit：15 → 5 一步到位（§4.2）

    def test_recovery_after_scan_fault(self):
        eng = _engine()
        eng.run_scan({"InAV": 50.0, "FaultTrig": True})
        self.assertEqual(eng.driver.values["AO1"], 5.0)
        # 输入映像保持上一拍锁存值：必须显式撤除故障触发
        eng.run_scan({"InAV": 50.0, "FaultTrig": False})         # 恢复：基准 le=5 → 15
        self.assertEqual(eng.driver.values["AO1"], 15.0)


class TestPolicyPaths(unittest.TestCase):
    def test_cold_start_hold_degrades_to_safe(self):
        """冷启动无历史值时 hold 退化为 safe_value（§4.1）。"""
        eng = _engine()
        pending = eng.run_scan({"InAV": 50.0},
                               flags=ScanFlags(output_enable=False))   # AO1 配 hold
        self.assertEqual(pending["AO1"], 5.0)

    def test_hold_uses_last_effective(self):
        eng = _engine()
        eng.run_scan({"InAV": 50.0})                             # le=15
        pending = eng.run_scan({"InAV": 50.0},
                               flags=ScanFlags(output_enable=False))
        self.assertEqual(pending["AO1"], 15.0)                   # hold = last_effective

    def test_multi_fault_priority_forces_safe(self):
        """safety_trip 与 operator_disable 并发：取最严者 → 强制 safe（§4 约束 2）。"""
        eng = _engine()
        eng.run_scan({"InAV": 50.0})                             # le=15（有历史，hold 本可用）
        pending = eng.run_scan({"InAV": 50.0},
                               flags=ScanFlags(safety_ok=False, output_enable=False))
        self.assertEqual(pending["AO1"], 5.0)

    def test_rate_limit_normal_path(self):
        eng = _engine()
        written = [eng.run_scan({"InAV": 50.0})["AO1"] for _ in range(6)]
        self.assertEqual(written, [15.0, 25.0, 35.0, 45.0, 50.0, 50.0])  # 每拍 ≤10，到位后跟随


class TestCommitFault(unittest.TestCase):
    def test_commit_fault_full_lifecycle(self):
        """写失败固定行为（§4.4）：告警→持续写 safe→连续 N 拍升级→恢复首拍基准=lpc。"""
        driver = FlakyDriver()
        eng = _engine(driver)
        eng.run_scan({"InAV": 50.0})                             # 拍1 正常：写 15，lpc=15
        self.assertEqual(eng.output_states["AO1"].last_physical_committed, 15.0)

        driver.fail_channels.add("AO1")
        eng.run_scan({"InAV": 50.0})                             # 拍2：写 25 失败
        st = eng.output_states["AO1"]
        self.assertTrue(st.commit_fault)
        self.assertEqual(st.last_physical_committed, 15.0)       # lpc 不更新（§4.4-1）
        self.assertIn(("commit_fault", "AO1"), eng.alarms)
        self.assertEqual(st.last_effective, 25.0)                # 策略层照常算（§4.4-5）

        eng.run_scan({"InAV": 50.0})                             # 拍3：改写 safe=5，仍失败
        eng.run_scan({"InAV": 50.0})                             # 拍4：连续 3 拍 → 升级
        self.assertIn(("channel_fault_escalated", "AO1"), eng.alarms)
        self.assertTrue(st.channel_fault)
        # 失败期间写的是 safe_value 而非业务值（§4.4-2）
        fails = [w for w in driver.written if len(w) == 3 and w[0] == "AO1"]
        self.assertEqual([w[1] for w in fails], [25.0, 5.0, 5.0])
        self.assertEqual(st.last_effective, 45.0)                # 逻辑生效值保持连续

        driver.fail_channels.clear()
        eng.run_scan({"InAV": 50.0})                             # 拍5：写 safe 成功 → 恢复
        self.assertFalse(st.commit_fault)
        self.assertEqual(st.last_physical_committed, 5.0)
        self.assertIn(("commit_recovered", "AO1"), eng.alarms)

        eng.run_scan({"InAV": 50.0})                             # 拍6：恢复首拍基准 = lpc=5 → 15
        self.assertEqual(driver.values["AO1"], 15.0)

    def test_commit_fault_isolated_per_channel(self):
        """写失败不影响其他通道提交（§4.4-4）。"""
        driver = FlakyDriver()
        driver.fail_channels.add("AO1")
        eng = _engine(driver)
        for _ in range(3):
            eng.run_scan({"InAV": 50.0, "InMotor": True})
        self.assertEqual(driver.values.get("DO1"), True)         # DO1 每拍提交成功
        self.assertNotIn("AO1", driver.values)
        self.assertFalse(eng.output_states["DO1"].commit_fault)
        self.assertTrue(eng.output_states["AO1"].channel_fault)


if __name__ == "__main__":
    unittest.main()
