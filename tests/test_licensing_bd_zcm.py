"""BD_ZCM 注册码生成测试（任务书 §12.C）。

覆盖：同一 SerialText 多次一致 / 不同 SerialText 不同注册码 / 固定回归向量 /
失败时 ERROR=1000 且三组注册码为 0 / EN=False 保持上次输出。

回归向量说明：下方硬编码的 ZCM 是 **Python 迁移回归向量**（用于锁定本实现
跨改动的一致性），**不是**已由真实 CODESYS 对照验证的"黄金样本"
（见 ``docs/RISKS.md::LIC-PLATFORM-1``）。
"""

from __future__ import annotations

import unittest

from src.licensing.bd_zcm import BD_ZCM
from src.licensing.providers import StaticSerialTextProvider

# Python 迁移回归向量（非 CODESYS 黄金样本）。
REGRESSION_SERIAL = "PYPLC|TEST|MACHINE-0001"
REGRESSION_ZCM = (1159, 8702, 2216)


class TestBDZCMDeterminism(unittest.TestCase):
    def test_same_serial_repeatable(self):
        z = BD_ZCM(StaticSerialTextProvider(REGRESSION_SERIAL))
        out1 = z.step(True)
        out2 = z.step(True)
        self.assertEqual(
            (out1["ZCM1"], out1["ZCM2"], out1["ZCM3"]),
            (out2["ZCM1"], out2["ZCM2"], out2["ZCM3"]),
        )

    def test_different_serial_different_codes(self):
        a = BD_ZCM(StaticSerialTextProvider("PYPLC|TEST|AAA")).step(True)
        b = BD_ZCM(StaticSerialTextProvider("PYPLC|TEST|BBB")).step(True)
        self.assertNotEqual(
            (a["ZCM1"], a["ZCM2"], a["ZCM3"]),
            (b["ZCM1"], b["ZCM2"], b["ZCM3"]),
        )

    def test_regression_vector(self):
        z = BD_ZCM(StaticSerialTextProvider(REGRESSION_SERIAL))
        out = z.step(True)
        self.assertEqual((out["ZCM1"], out["ZCM2"], out["ZCM3"]), REGRESSION_ZCM)
        self.assertEqual(out["ERROR"], 0)

    def test_codes_in_range(self):
        z = BD_ZCM(StaticSerialTextProvider(REGRESSION_SERIAL)).step(True)
        for key in ("ZCM1", "ZCM2", "ZCM3"):
            self.assertTrue(0 <= z[key] <= 9999)


class TestBDZCMFailure(unittest.TestCase):
    def test_serial_unavailable_error_1000(self):
        z = BD_ZCM(StaticSerialTextProvider("", success=False))
        out = z.step(True)
        self.assertEqual(out["ERROR"], 1000)
        self.assertEqual((out["ZCM1"], out["ZCM2"], out["ZCM3"]), (0, 0, 0))

    def test_empty_serial_error_1000(self):
        z = BD_ZCM(StaticSerialTextProvider("", success=True))
        out = z.step(True)
        self.assertEqual(out["ERROR"], 1000)
        self.assertEqual((out["ZCM1"], out["ZCM2"], out["ZCM3"]), (0, 0, 0))


class TestBDZCMEnableGate(unittest.TestCase):
    def test_en_false_keeps_previous_output(self):
        z = BD_ZCM(StaticSerialTextProvider(REGRESSION_SERIAL))
        z.step(True)
        self.assertEqual((z.ZCM1, z.ZCM2, z.ZCM3), REGRESSION_ZCM)
        # EN=False 不刷新，即便切换为失败 Provider 也保留上次注册码。
        z._xtxx._provider = StaticSerialTextProvider("", success=False)
        out = z.step(False)
        self.assertEqual((out["ZCM1"], out["ZCM2"], out["ZCM3"]), REGRESSION_ZCM)
        self.assertEqual(out["ERROR"], 0)


if __name__ == "__main__":
    unittest.main()
