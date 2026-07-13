"""XTXX / Serial Provider 测试（任务书 §12.B）。

覆盖：固定 Provider 成功读取 / 空序列号失败 / 读取失败 / 非 Latin-1 或超长失败 /
EN=False 保持上次输出 / 平台 Provider 规范化逻辑 / 不依赖当前机器真实标识。
"""

from __future__ import annotations

import unittest

from src.licensing.providers import (
    SERIAL_RESULT_OK,
    PlatformSerialTextProvider,
    StaticSerialTextProvider,
)
from src.licensing.xtxx import RESULT_NOT_IMPLEMENTED, XTXX


class TestXTXXSuccess(unittest.TestCase):
    def test_static_provider_success(self):
        x = XTXX(StaticSerialTextProvider("PYPLC|TEST|MACHINE-0001"))
        out = x.step(True)
        self.assertTrue(out["SerialOK"])
        self.assertEqual(out["SerialText"], "PYPLC|TEST|MACHINE-0001")
        self.assertEqual(out["SerialLen"], len("PYPLC|TEST|MACHINE-0001"))
        self.assertEqual(out["Serial_result"], SERIAL_RESULT_OK)

    def test_aux_fields_are_not_implemented_placeholders(self):
        """PLCid/CPUtype/Version 不伪造 CODESYS 数据，保留 0 + 未实现结果码。"""
        x = XTXX(StaticSerialTextProvider("PYPLC|TEST|X"))
        out = x.step(True)
        self.assertEqual(out["PLCid"], 0)
        self.assertEqual(out["CPUtype"], 0)
        self.assertEqual(out["Version"], 0)
        self.assertEqual(out["PLCid_result"], RESULT_NOT_IMPLEMENTED)
        self.assertEqual(out["CPU_result"], RESULT_NOT_IMPLEMENTED)
        self.assertEqual(out["Version_result"], RESULT_NOT_IMPLEMENTED)


class TestXTXXFailureModes(unittest.TestCase):
    def test_read_failure_fails(self):
        """场景 A：底层读取失败 → Serial_result != 0、SerialOK=False。"""
        x = XTXX(StaticSerialTextProvider("ignored", success=False, result_code=0x1001))
        out = x.step(True)
        self.assertNotEqual(out["Serial_result"], SERIAL_RESULT_OK)
        self.assertEqual(out["SerialText"], "")
        self.assertEqual(out["SerialLen"], 0)
        self.assertFalse(out["SerialOK"])

    def test_read_success_empty_serial_keeps_result_zero(self):
        """场景 B（严格 ST 语义）：底层读取成功但序列号为空。

        源 ST：``Serial_result`` 仅反映 ``SysTargetGetSerialNumber`` 的返回值，
        空串只让 ``LEN>0`` 不成立 → ``SerialOK=False``，``Serial_result`` 仍为 0。
        """
        x = XTXX(StaticSerialTextProvider("", success=True))
        out = x.step(True)
        self.assertEqual(out["Serial_result"], 0)
        self.assertEqual(out["SerialText"], "")
        self.assertEqual(out["SerialLen"], 0)
        self.assertFalse(out["SerialOK"])
        # 同时锁定实例属性（任务书 §测试要求 2）。
        self.assertEqual(x.Serial_result, 0)
        self.assertEqual(x.SerialText, "")
        self.assertEqual(x.SerialLen, 0)
        self.assertFalse(x.SerialOK)

    def test_non_latin1_unusable_serial_ok_false(self):
        """非 Latin-1 是 Python 平台适配层的不可用判定：仅 SerialOK=False，
        Serial_result 仍透传底层（此处 Provider 成功 → 0），不被 XTXX 改写。"""
        x = XTXX(StaticSerialTextProvider("PYPLC|TEST|中文ID"))
        out = x.step(True)
        self.assertFalse(out["SerialOK"])
        self.assertEqual(out["SerialText"], "")
        self.assertEqual(out["SerialLen"], 0)
        self.assertEqual(out["Serial_result"], 0)

    def test_over_255_bytes_unusable_serial_ok_false(self):
        x = XTXX(StaticSerialTextProvider("A" * 256))
        out = x.step(True)
        self.assertFalse(out["SerialOK"])
        self.assertEqual(out["SerialText"], "")
        self.assertEqual(out["SerialLen"], 0)
        self.assertEqual(out["Serial_result"], 0)

    def test_exactly_255_bytes_ok(self):
        x = XTXX(StaticSerialTextProvider("A" * 255))
        out = x.step(True)
        self.assertTrue(out["SerialOK"])
        self.assertEqual(out["SerialLen"], 255)


class TestXTXXEnableGate(unittest.TestCase):
    def test_en_false_keeps_previous_output(self):
        x = XTXX(StaticSerialTextProvider("PYPLC|TEST|MACHINE-0001"))
        x.step(True)
        self.assertTrue(x.SerialOK)
        # 切换 Provider 不会被读到，因为 EN=False 不刷新。
        x._provider = StaticSerialTextProvider("", success=False)
        out = x.step(False)
        self.assertTrue(out["SerialOK"])
        self.assertEqual(out["SerialText"], "PYPLC|TEST|MACHINE-0001")


class TestPlatformProviderNormalization(unittest.TestCase):
    """平台 Provider 的规范化逻辑（不依赖真实机器标识）。"""

    def test_normalize_format(self):
        self.assertEqual(
            PlatformSerialTextProvider.normalize("LINUX", "abc123"),
            "PYPLC|LINUX|abc123",
        )

    def test_normalize_strips_whitespace(self):
        self.assertEqual(
            PlatformSerialTextProvider.normalize("MACOS", "  UUID-X  \n"),
            "PYPLC|MACOS|UUID-X",
        )

    def test_normalized_output_flows_through_xtxx(self):
        serial = PlatformSerialTextProvider.normalize("WINDOWS", "GUID-1")
        x = XTXX(StaticSerialTextProvider(serial))
        out = x.step(True)
        self.assertTrue(out["SerialOK"])
        self.assertEqual(out["SerialText"], "PYPLC|WINDOWS|GUID-1")


if __name__ == "__main__":
    unittest.main()
