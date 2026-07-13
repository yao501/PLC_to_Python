"""DWORD / WORD 兼容辅助测试（任务书 §12.A）。

覆盖：32 位回绕 / 16 位回绕 / bool 不得被静默当作数值。
"""

from __future__ import annotations

import unittest

from src.licensing.dword import (
    DWORD_MASK,
    WORD_MASK,
    dword_add,
    dword_mul,
    dword_xor,
    to_dword,
    to_word,
)


class TestDwordWrap(unittest.TestCase):
    def test_to_dword_basic(self):
        self.assertEqual(to_dword(0), 0)
        self.assertEqual(to_dword(DWORD_MASK), DWORD_MASK)

    def test_to_dword_wraps_32bit(self):
        self.assertEqual(to_dword(DWORD_MASK + 1), 0)
        self.assertEqual(to_dword(0x1_0000_0001), 1)

    def test_to_dword_negative_wraps_unsigned(self):
        self.assertEqual(to_dword(-1), DWORD_MASK)

    def test_dword_mul_wraps(self):
        # 0xFFFFFFFF * 2 = 0x1_FFFFFFFE → 回绕到 0xFFFFFFFE
        self.assertEqual(dword_mul(DWORD_MASK, 2), 0xFFFFFFFE)

    def test_dword_add_wraps(self):
        self.assertEqual(dword_add(DWORD_MASK, 2), 1)

    def test_dword_xor(self):
        self.assertEqual(dword_xor(0xFF00, 0x00FF), 0xFFFF)


class TestWordWrap(unittest.TestCase):
    def test_to_word_basic(self):
        self.assertEqual(to_word(0), 0)
        self.assertEqual(to_word(WORD_MASK), WORD_MASK)

    def test_to_word_wraps_16bit(self):
        self.assertEqual(to_word(WORD_MASK + 1), 0)
        self.assertEqual(to_word(0x1_0007), 7)


class TestBoolRejected(unittest.TestCase):
    """bool 是 int 子类，必须显式拒绝，不能被当成 1/0 偷渡为密码 / 数值。"""

    def test_to_dword_rejects_bool(self):
        with self.assertRaises(TypeError):
            to_dword(True)
        with self.assertRaises(TypeError):
            to_dword(False)

    def test_to_word_rejects_bool(self):
        with self.assertRaises(TypeError):
            to_word(True)

    def test_dword_ops_reject_bool(self):
        with self.assertRaises(TypeError):
            dword_add(1, True)
        with self.assertRaises(TypeError):
            dword_mul(True, 2)
        with self.assertRaises(TypeError):
            dword_xor(True, 1)

    def test_non_int_rejected(self):
        with self.assertRaises(TypeError):
            to_dword(1.0)
        with self.assertRaises(TypeError):
            to_word("1")


if __name__ == "__main__":
    unittest.main()
