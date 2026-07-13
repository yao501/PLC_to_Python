"""LicenseContext 测试（任务书 §12.F）。

覆盖：默认全局值 / 两个 Context 彼此隔离 / KZQBDYZMK 每次读取最新 BD_MM1~4 /
BD_ERROR1~9 初值全部为 0.0 / 完整发放链路集成。
"""

from __future__ import annotations

import unittest

from src.globals import LicenseContext
from src.licensing.bd_mmyz_st import BD_MMYZ_ST
from src.licensing.bd_zcm import BD_ZCM
from src.licensing.issuer import derive_passwords_from_registration_codes
from src.licensing.providers import ManualDateTimeProvider, StaticSerialTextProvider

REGRESSION_SERIAL = "PYPLC|TEST|MACHINE-0001"
CORRECT_MM = (8781, 9291, 2193, 1078)
MS_SEC5 = 5000


def make_context(serial: str = REGRESSION_SERIAL) -> LicenseContext:
    return LicenseContext(
        StaticSerialTextProvider(serial),
        ManualDateTimeProvider(MS_SEC5),
    )


class TestDefaults(unittest.TestCase):
    def test_default_passwords_zero(self):
        ctx = make_context()
        self.assertEqual((ctx.BD_MM1, ctx.BD_MM2, ctx.BD_MM3, ctx.BD_MM4), (0, 0, 0, 0))

    def test_default_errors_are_zero_float(self):
        ctx = make_context()
        for i in range(1, 10):
            val = getattr(ctx, f"BD_ERROR{i}")
            self.assertEqual(val, 0.0)
            self.assertIsInstance(val, float)

    def test_kzqbdyzmk_is_bd_mmyz_st(self):
        ctx = make_context()
        self.assertIsInstance(ctx.KZQBDYZMK, BD_MMYZ_ST)
        self.assertEqual(ctx.KZQBDYZMK.OK, 9000)


class TestIsolation(unittest.TestCase):
    def test_two_contexts_isolated(self):
        a = make_context()
        b = make_context()
        a.set_passwords(*CORRECT_MM)
        a.BD_ERROR5 = 42.0
        self.assertEqual((b.BD_MM1, b.BD_MM2, b.BD_MM3, b.BD_MM4), (0, 0, 0, 0))
        self.assertEqual(b.BD_ERROR5, 0.0)
        self.assertIsNot(a.KZQBDYZMK, b.KZQBDYZMK)


class TestLivePasswordReading(unittest.TestCase):
    def test_kzqbdyzmk_reads_latest_passwords(self):
        """KZQBDYZMK 每次验证实时读取 Context 当前 BD_MM1~4。"""
        ctx = make_context()
        # 冷启动密码全 0 → 验证失败。
        out = ctx.KZQBDYZMK.step()
        self.assertNotEqual(out["ERR"], 0)
        self.assertFalse(out["YZTG"])
        # 写入正确密码后下一拍立即恢复（仍在失败态，每拍重验）。
        ctx.set_passwords(*CORRECT_MM)
        out = ctx.KZQBDYZMK.step()
        self.assertEqual(out["ERR"], 0)
        self.assertTrue(out["YZTG"])

    def test_set_passwords_rejects_bool(self):
        ctx = make_context()
        with self.assertRaises(TypeError):
            ctx.set_passwords(True, 0, 0, 0)


class TestEmptySerialEndToEnd(unittest.TestCase):
    def test_empty_serial_results_in_9000(self):
        """空序列号（底层读取成功）：SerialOK=False → KZQBDYZMK 最终 ERR=9000。"""
        ctx = make_context(serial="")
        out = ctx.KZQBDYZMK.step()
        self.assertEqual(out["OK"] % 10000, 9000)
        self.assertEqual(out["ERR"], 9000)
        self.assertFalse(out["YZTG"])


class TestFullIssueChainViaContext(unittest.TestCase):
    def test_zcm_derive_setpasswords_gate_ok(self):
        ctx = make_context()
        zcm = BD_ZCM(StaticSerialTextProvider(REGRESSION_SERIAL)).step(True)
        mm = derive_passwords_from_registration_codes(
            zcm["ZCM1"], zcm["ZCM2"], zcm["ZCM3"]
        )
        ctx.set_passwords(*mm)
        out = ctx.KZQBDYZMK.step()
        self.assertEqual(out["OK"] % 10000, 0)
        self.assertTrue(out["YZTG"])


if __name__ == "__main__":
    unittest.main()
