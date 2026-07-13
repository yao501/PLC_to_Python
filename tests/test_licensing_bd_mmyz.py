"""BD_MMYZ 密码验证测试（任务书 §12.D）。

覆盖：正确四组密码返回 0 / 单密码错误对应错误码 / 组合错误码 /
序列号不可用返回 9000 / 更新密码后下一次验证立即使用新密码 /
注册码发放链路（BD_ZCM → derive → 写入 → BD_MMYZ=0，任务书 §9.4）。
"""

from __future__ import annotations

import unittest

from src.licensing.bd_mmyz import BD_MMYZ
from src.licensing.bd_zcm import BD_ZCM
from src.licensing.issuer import derive_passwords_from_registration_codes
from src.licensing.providers import StaticSerialTextProvider

REGRESSION_SERIAL = "PYPLC|TEST|MACHINE-0001"
# 与 REGRESSION_SERIAL 对应的正确密码（Python 迁移回归向量）。
CORRECT_MM = (8781, 9291, 2193, 1078)


def make_validator(serial: str, passwords: list):
    provider = StaticSerialTextProvider(serial)
    return BD_MMYZ(provider, lambda: tuple(passwords))


class TestBDMMYZPasswordCheck(unittest.TestCase):
    def test_all_correct_returns_zero(self):
        v = make_validator(REGRESSION_SERIAL, list(CORRECT_MM))
        self.assertEqual(v(), 0)

    def test_mm1_wrong_returns_1000(self):
        pw = list(CORRECT_MM)
        pw[0] = (pw[0] + 1) % 10000
        self.assertEqual(make_validator(REGRESSION_SERIAL, pw)(), 1000)

    def test_mm2_wrong_returns_100(self):
        pw = list(CORRECT_MM)
        pw[1] = (pw[1] + 1) % 10000
        self.assertEqual(make_validator(REGRESSION_SERIAL, pw)(), 100)

    def test_mm3_wrong_returns_10(self):
        pw = list(CORRECT_MM)
        pw[2] = (pw[2] + 1) % 10000
        self.assertEqual(make_validator(REGRESSION_SERIAL, pw)(), 10)

    def test_mm4_wrong_returns_1(self):
        pw = list(CORRECT_MM)
        pw[3] = (pw[3] + 1) % 10000
        self.assertEqual(make_validator(REGRESSION_SERIAL, pw)(), 1)

    def test_combo_1_3_4_wrong_returns_1011(self):
        pw = list(CORRECT_MM)
        pw[0] = (pw[0] + 1) % 10000
        pw[2] = (pw[2] + 1) % 10000
        pw[3] = (pw[3] + 1) % 10000
        self.assertEqual(make_validator(REGRESSION_SERIAL, pw)(), 1011)

    def test_all_wrong_returns_1111(self):
        pw = [(c + 1) % 10000 for c in CORRECT_MM]
        self.assertEqual(make_validator(REGRESSION_SERIAL, pw)(), 1111)


class TestBDMMYZSerialUnavailable(unittest.TestCase):
    def test_read_failure_returns_9000(self):
        v = BD_MMYZ(
            StaticSerialTextProvider("", success=False),
            lambda: CORRECT_MM,
        )
        self.assertEqual(v(), 9000)

    def test_empty_serial_returns_9000(self):
        v = BD_MMYZ(StaticSerialTextProvider("", success=True), lambda: CORRECT_MM)
        self.assertEqual(v(), 9000)


class TestBDMMYZLivePasswords(unittest.TestCase):
    def test_password_update_takes_effect_next_call(self):
        """password_getter 每次实时读取：更新密码后下一次验证立即使用新值。"""
        pw = [0, 0, 0, 0]
        v = make_validator(REGRESSION_SERIAL, pw)
        self.assertNotEqual(v(), 0)  # 全错
        pw[0], pw[1], pw[2], pw[3] = CORRECT_MM
        self.assertEqual(v(), 0)  # 立即生效


class TestRegistrationIssueChain(unittest.TestCase):
    def test_zcm_to_passwords_to_mmyz_zero(self):
        """BD_ZCM 生成注册码 → derive 密码 → 写入 → BD_MMYZ 返回 0。"""
        provider = StaticSerialTextProvider(REGRESSION_SERIAL)
        zcm = BD_ZCM(provider).step(True)
        passwords = list(
            derive_passwords_from_registration_codes(
                zcm["ZCM1"], zcm["ZCM2"], zcm["ZCM3"]
            )
        )
        self.assertEqual(tuple(passwords), CORRECT_MM)
        v = BD_MMYZ(StaticSerialTextProvider(REGRESSION_SERIAL), lambda: tuple(passwords))
        self.assertEqual(v(), 0)


if __name__ == "__main__":
    unittest.main()
