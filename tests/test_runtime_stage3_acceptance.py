"""Stage 3 ST directory acceptance for the provisional public compiler API."""
import ast
from pathlib import Path
import unittest
from unittest.mock import patch

import src.runtime as runtime
from src.blocks.apcgcq import APCGCQ
from src.blocks.apccd import APCCD
from src.blocks.apcspfinder import APCSPFINDER
from src.blocks.apcpid import APCPID
from src.blocks.apcpidzzd import APCPIDZZD
from src.blocks.apcrsfnautopara import APCRSFNAUTOPARA
from src.blocks.apcmautopara import APCMAUTOPARA
from src.blocks.apcm import APCM, RealRef
from src.blocks.apchshllim import APCHSHLLIM
from src.blocks.apchsfop import APCHSFOP
from src.blocks.apchsratelim import APCHSRATELIM
from src.blocks.apcstatistics import APCSTATISTICS
from src.blocks.apchsaccum import APCHSACCUM
from src.blocks.apchxhcl import APCHXHCL
from src.runtime import st_lexer, st_lowering, st_parser
from src.runtime.ir import LoadVar, StoreVar
from src.runtime.st_library_bindings import library_source_aliases, primitive_source_aliases
from src.globals import LicenseContext
from src.licensing.bd_zcm import BD_ZCM
from src.licensing.issuer import derive_passwords_from_registration_codes
from src.licensing.providers import ManualDateTimeProvider, StaticSerialTextProvider


def _pid_context(authorized=True):
    """Build a test-only context through the real Python licensing chain."""
    context = LicenseContext(
        StaticSerialTextProvider("PYPLC|TEST|MACHINE-0001"),
        ManualDateTimeProvider(5000),
    )
    if authorized:
        zcm = BD_ZCM(StaticSerialTextProvider("PYPLC|TEST|MACHINE-0001")).step(True)
        context.set_passwords(*derive_passwords_from_registration_codes(
            zcm["ZCM1"], zcm["ZCM2"], zcm["ZCM3"]))
    return context


_PUBLIC_ST = {
    "STLexDiagnostic": st_lexer.STLexDiagnostic,
    "STLexError": st_lexer.STLexError,
    "STParseDiagnostic": st_parser.STParseDiagnostic,
    "STParseError": st_parser.STParseError,
    "STCompileDiagnostic": st_lowering.STCompileDiagnostic,
    "STCompileError": st_lowering.STCompileError,
    "STCompileResult": st_lowering.STCompileResult,
    "STPOUCompileResult": st_lowering.STPOUCompileResult,
    "compile_st_task": st_lowering.compile_st_task,
    "compile_st_function": st_lowering.compile_st_function,
    "compile_st_function_block": st_lowering.compile_st_function_block,
}


_APCRSF_REQUIRED_INPUTS = (
    ("EN", "BOOL"), ("RESET", "BOOL"), ("CALC_NOW", "BOOL"),
    ("SP", "REAL"), ("PV", "REAL"), ("AV", "REAL"),
    ("TP", "REAL"), ("TS", "BOOL"), ("RSF_LEVEL", "REAL"),
    ("RSF_LOCK_LEVEL_IN", "REAL"), ("RSF_STEP", "REAL"),
)
_APCRSF_OPTIONAL_INPUTS = (
    ("CYCLE", "REAL", 0.5), ("COLLECT_MODE", "INT", 1),
    ("SP_MAN", "REAL", 0.0), ("SP_MAN_EN", "BOOL", False),
    ("SP_TAG_EN", "BOOL", True), ("SP_AUTO_EN", "BOOL", True),
    ("SP_AUTO_REPLACE_BAD_TAG", "BOOL", False),
    ("SP_STABLE_T", "REAL", 300.0), ("SP_CONF_T", "REAL", 900.0),
    ("SP_PV_STABLE_ABS", "REAL", 0.0),
    ("SP_AV_STABLE_ABS", "REAL", 0.0), ("MU", "REAL", 100.0),
    ("MD", "REAL", 0.0), ("PHY_RANGE_EN", "BOOL", False),
    ("PHY_MU", "REAL", 100.0), ("PHY_MD", "REAL", 0.0),
    ("WIN_T", "REAL", 7200.0), ("MIN_WIN_T", "REAL", 300.0),
    ("MIN_STORE_EVENT", "REAL", 1.0),
    ("MIN_VALID_EVENT", "REAL", 5.0), ("HISTORY_N", "INT", 24),
    ("FUSE_MIN_N", "REAL", 3.0), ("FUSE_MIN_WEIGHT", "REAL", 3.0),
    ("SIM_SP_K", "REAL", 0.05), ("SIM_PV_K", "REAL", 0.05),
    ("SIM_AV_K", "REAL", 0.1), ("SIM_ERR_K", "REAL", 0.05),
    ("SIM_SP_ABS", "REAL", 0.0), ("SIM_PV_ABS", "REAL", 0.0),
    ("SIM_AV_ABS", "REAL", 0.0), ("SIM_ERR_ABS", "REAL", 0.0),
    ("SIM_RELAX_K", "REAL", 2.0), ("MAN_AV_MIN", "REAL", 0.1),
    ("AO_GAIN_K", "REAL", 0.5), ("REC_BLEND", "REAL", 0.7),
    ("TL_IN", "REAL", 10.0), ("TL1_IN", "REAL", 60.0),
    ("TL2_IN", "REAL", 60.0), ("TL3_IN", "REAL", 60.0),
    ("TL4_IN", "REAL", 60.0), ("E1_IN", "REAL", 1.0),
    ("E2_IN", "REAL", 2.0), ("E3_IN", "REAL", 3.0),
    ("E4_IN", "REAL", 4.0), ("AO1_IN", "REAL", 1.0),
    ("AO2_IN", "REAL", 2.0), ("AO3_IN", "REAL", 3.0),
    ("AO4_IN", "REAL", 4.0), ("RSF_LOCK_T_IN", "REAL", 30.0),
    ("RSF_HYS_IN", "REAL", 0.8), ("RSF_FAST_HYS_IN", "REAL", 0.5),
    ("RSF_TLOUT_K_IN", "REAL", 0.5), ("ZF_K_IN", "REAL", 0.0),
)
_APCRSF_OUTPUTS = (
    ("RUNNING", "BOOL"), ("WINDOW_DONE", "BOOL"),
    ("FINAL_VALID", "BOOL"), ("FINAL_STRONG", "BOOL"),
    ("FINAL_WEAK", "BOOL"), ("MATCH_LEVEL", "INT"),
    ("DATA_REASON", "INT"), ("WINDOW_VALID", "BOOL"),
    ("SP_USE", "REAL"), ("SP_VALID", "BOOL"),
    ("SP_SOURCE", "INT"), ("SP_REASON", "INT"),
    ("SP_AUTO", "REAL"), ("SP_AUTO_OK", "BOOL"),
    ("SP_AUTO_CONF", "REAL"), ("SP_TAG_BAD", "BOOL"),
    ("SP_STABLE_T_OUT", "REAL"), ("HISTORY_COUNT", "REAL"),
    ("SIMILAR_COUNT", "REAL"), ("FUSE_WEIGHT", "REAL"),
    ("WINDOW_EVENT_N", "REAL"), ("WINDOW_T", "REAL"),
    ("AUTO_SAMPLE_T", "REAL"), ("MAN_EVENT_N", "REAL"),
    ("CROSS_COUNT", "REAL"), ("RSF_TRIGGER_N", "REAL"),
    ("RSF_LOCK_N", "REAL"), ("ERR_ABS_AVG", "REAL"),
    ("ERR_AREA_POS", "REAL"), ("ERR_AREA_NEG", "REAL"),
    ("ERR_PEAK_ABS", "REAL"), ("AVG_CROSS_T", "REAL"),
    ("PV_DELTA", "REAL"), ("AV_DELTA", "REAL"),
    ("NOISE_EST", "REAL"), ("PROCESS_GAIN", "REAL"),
    ("TL_REC", "REAL"), ("TL1_REC", "REAL"), ("TL2_REC", "REAL"),
    ("TL3_REC", "REAL"), ("TL4_REC", "REAL"), ("E1_REC", "REAL"),
    ("E2_REC", "REAL"), ("E3_REC", "REAL"), ("E4_REC", "REAL"),
    ("AO1_REC", "REAL"), ("AO2_REC", "REAL"), ("AO3_REC", "REAL"),
    ("AO4_REC", "REAL"), ("RSF_OK", "BOOL"),
    ("RSF_REASON", "INT"), ("RSF_LOCK_T_REC", "REAL"),
    ("RSF_HYS_REC", "REAL"), ("RSF_FAST_HYS_REC", "REAL"),
    ("RSF_TLOUT_K_REC", "REAL"), ("ZF_K_REC", "REAL"),
)


def _apcrsf_source(*, explicit_optional=False, instance_names=("R",)):
    declarations = []
    for name, iec_type in _APCRSF_REQUIRED_INPUTS:
        declarations.append(f"I_{name}:{iec_type};")
    for name, iec_type, _default in _APCRSF_OPTIONAL_INPUTS:
        declarations.append(f"I_{name}:{iec_type};")
    for name, iec_type in _APCRSF_OUTPUTS:
        declarations.append(f"O_{name}:{iec_type};")
    bindings = [f"{name}:=I_{name}" for name, _type in _APCRSF_REQUIRED_INPUTS]
    if explicit_optional:
        bindings.extend(
            f"{name}:=I_{name}" for name, _type, _default in _APCRSF_OPTIONAL_INPUTS)
    bindings.extend(f"{name}=>O_{name}" for name, _type in _APCRSF_OUTPUTS)
    return (
        "VAR_GLOBAL " + " ".join(declarations) + " END_VAR "
        f"VAR {','.join(instance_names)}:APCRSFNAUTOPARA; END_VAR "
        + " ".join(
            f"{instance_name}(" + ",".join(bindings) + ");"
            for instance_name in instance_names))


_APCCD_REQUIRED_INPUTS = (
    ("SP", "REAL"), ("PV", "REAL"), ("TS", "BOOL"), ("TC", "REAL"),
    ("TZ", "REAL"), ("CDH", "REAL"), ("CDL", "REAL"), ("TL", "REAL"),
)
_APCCD_OPTIONAL_INPUTS = (
    ("CD_K_J", "REAL", 1.0), ("CD_K_D", "REAL", 1.0),
    ("CD_K_FD", "REAL", 1.0), ("CD_GD", "REAL", 2.0),
    ("CD_K", "REAL", 0.5), ("AD", "BOOL", True),
)
_APCCD_OUTPUTS = (("AV", "REAL"), ("CD_BH", "REAL"))


def _apccd_source(*, explicit_optional=False):
    declarations = [
        "I_%s:%s;" % item for item in _APCCD_REQUIRED_INPUTS
    ] + [
        "I_%s:%s;" % (name, iec_type)
        for name, iec_type, _default in _APCCD_OPTIONAL_INPUTS
    ] + [
        "O_%s:%s;" % item for item in _APCCD_OUTPUTS
    ] + ["Z:REAL;"]
    bindings = [
        "%s:=I_%s" % (name, name) for name, _type in _APCCD_REQUIRED_INPUTS
    ]
    if explicit_optional:
        bindings.extend(
            "%s:=I_%s" % (name, name)
            for name, _type, _default in _APCCD_OPTIONAL_INPUTS)
    bindings.extend(("ZLOUT:=Z", "AV=>O_AV", "CD_BH=>O_CD_BH"))
    return (
        "VAR_GLOBAL " + " ".join(declarations) + " END_VAR "
        "VAR CD:APCCD; END_VAR CD(" + ",".join(bindings) + ");")


_APCM_REQUIRED_INPUTS = (
    ("SP", "REAL"), ("PV", "REAL"), ("OC", "REAL"),
    ("TS", "BOOL"), ("TP", "REAL"),
)
_APCM_OPTIONAL_INPUTS = (
    ("RM", "INT"), ("OUTT", "REAL"), ("OUTB", "REAL"),
    ("SADD", "BOOL"), ("SSUB", "BOOL"), ("ZLEN", "BOOL"),
    ("ZSYK", "REAL"),
)
_APCM_OUTPUTS = (
    ("AV", "REAL"), ("AV_P", "REAL"), ("AV_R", "REAL"),
    ("AV_GC", "REAL"), ("AV_J", "REAL"), ("AV_D", "REAL"),
    ("AV_C", "REAL"),
)


def _apcm_source(*, explicit_optional=False):
    declarations = [
        "I_%s:%s;" % item for item in _APCM_REQUIRED_INPUTS
    ] + [
        "I_%s:%s;" % item for item in _APCM_OPTIONAL_INPUTS
    ] + [
        "O_%s:%s;" % item for item in _APCM_OUTPUTS
    ] + ["Z:REAL;"]
    bindings = [
        "%s:=I_%s" % (name, name) for name, _type in _APCM_REQUIRED_INPUTS
    ]
    if explicit_optional:
        bindings.extend(
            "%s:=I_%s" % (name, name) for name, _type in _APCM_OPTIONAL_INPUTS)
    bindings.extend(
        ["ZLOUT:=Z"] + ["%s=>O_%s" % (name, name) for name, _type in _APCM_OUTPUTS])
    return (
        "VAR_GLOBAL " + " ".join(declarations) + " END_VAR "
        "VAR M:APCM; END_VAR M(" + ",".join(bindings) + ");")


_APCMAUTO_INPUTS = (
    ("EN", "BOOL", False), ("RESET", "BOOL", False), ("CALC_NOW", "BOOL", False),
    ("CYCLE", "REAL", 0.5), ("COLLECT_MODE", "INT", 1), ("SP", "REAL", 0.0),
    ("SP_MAN", "REAL", 0.0), ("SP_MAN_EN", "BOOL", False), ("SP_TAG_EN", "BOOL", True),
    ("SP_AUTO_EN", "BOOL", True), ("SP_AUTO_REPLACE_BAD_TAG", "BOOL", False), ("SP_STABLE_T", "REAL", 300.0),
    ("SP_CONF_T", "REAL", 900.0), ("SP_PV_STABLE_ABS", "REAL", 0.0), ("SP_AV_STABLE_ABS", "REAL", 0.0),
    ("PV", "REAL", 0.0), ("AV", "REAL", 0.0), ("RM", "INT", 1),
    ("TS", "BOOL", False), ("PVMU", "REAL", 100.0), ("PVMD", "REAL", 0.0),
    ("MU", "REAL", 100.0), ("MD", "REAL", 0.0), ("OUTT", "REAL", 100.0),
    ("OUTB", "REAL", 0.0), ("WIN_T", "REAL", 7200.0), ("MIN_WIN_T", "REAL", 300.0),
    ("MIN_STORE_EVENT", "REAL", 1.0), ("MIN_VALID_EVENT", "REAL", 5.0), ("HISTORY_N", "INT", 24),
    ("FUSE_MIN_N", "REAL", 3.0), ("FUSE_MIN_WEIGHT", "REAL", 3.0), ("SIM_SP_K", "REAL", 0.05),
    ("SIM_PV_K", "REAL", 0.05), ("SIM_AV_K", "REAL", 0.1), ("SIM_ERR_K", "REAL", 0.05),
    ("SIM_SP_ABS", "REAL", 0.0), ("SIM_PV_ABS", "REAL", 0.0), ("SIM_AV_ABS", "REAL", 0.0),
    ("SIM_ERR_ABS", "REAL", 0.0), ("SIM_RELAX_K", "REAL", 2.0), ("MAN_MERGE_T", "REAL", 10.0),
    ("MAN_RESP_T", "REAL", 60.0), ("MAN_RESP_T_MAX", "REAL", 7200.0), ("MAN_AV_MIN", "REAL", 0.1),
    ("PT_IN", "REAL", 300.0), ("TI_IN", "REAL", 50.0), ("TD_IN", "REAL", 0.0),
    ("DI_IN", "REAL", 0.0), ("SVH_IN", "REAL", 30.0), ("SVL_IN", "REAL", 0.0),
    ("PID_FORMULA_EN", "BOOL", True), ("PID_LAMBDA_K", "REAL", 1.5), ("PID_MODEL_L_K", "REAL", 0.2),
    ("PID_FORMULA_BLEND", "REAL", 0.8), ("TL_IN", "REAL", 10.0), ("TL1_IN", "REAL", 120.0),
    ("TL2_IN", "REAL", 120.0), ("TL3_IN", "REAL", 120.0), ("TL4_IN", "REAL", 120.0),
    ("E1_IN", "REAL", 1.0), ("E2_IN", "REAL", 2.0), ("E3_IN", "REAL", 3.0),
    ("E4_IN", "REAL", 4.0), ("AO1_IN", "REAL", 0.3), ("AO2_IN", "REAL", 0.4),
    ("AO3_IN", "REAL", 0.5), ("AO4_IN", "REAL", 0.6), ("RSF_LOCK_T_IN", "REAL", 30.0),
    ("TC_IN", "REAL", 10.0), ("TZ_IN", "REAL", 20.0), ("GC1_IN", "REAL", 1.0),
    ("GC2_IN", "REAL", 6.0), ("OUTH_IN", "REAL", 5.0), ("OUTL_IN", "REAL", -5.0),
    ("CD_GD_IN", "REAL", 0.0), ("CD_K_IN", "REAL", 0.5), ("CD_K_FD_IN", "REAL", 1.0),
    ("CD_K_J_IN", "REAL", 1.0), ("CD_K_D_IN", "REAL", 1.0), ("CDH_IN", "REAL", 5.0),
    ("CDL_IN", "REAL", -5.0), ("TC_CD_IN", "REAL", 10.0), ("TZ_CD_IN", "REAL", 20.0),
)
_APCMAUTO_OUTPUTS = (
    ("RUNNING", "BOOL"), ("WINDOW_DONE", "BOOL"), ("FINAL_VALID", "BOOL"),
    ("FINAL_STRONG", "BOOL"), ("FINAL_WEAK", "BOOL"), ("MATCH_LEVEL", "INT"),
    ("WINDOW_VALID", "BOOL"), ("DATA_REASON", "INT"), ("SP_USE", "REAL"),
    ("SP_AUTO", "REAL"), ("SP_VALID", "BOOL"), ("SP_AUTO_OK", "BOOL"),
    ("SP_TAG_BAD", "BOOL"), ("SP_SOURCE", "INT"), ("SP_REASON", "INT"),
    ("SP_AUTO_CONF", "REAL"), ("SP_STABLE_T_OUT", "REAL"), ("PID_OK", "BOOL"),
    ("RSF_OK", "BOOL"), ("GC_OK", "BOOL"), ("CD_OK", "BOOL"),
    ("PID_REASON", "INT"), ("RSF_REASON", "INT"), ("GC_REASON", "INT"),
    ("CD_REASON", "INT"), ("HISTORY_COUNT", "REAL"), ("SIMILAR_COUNT", "REAL"),
    ("FUSE_WEIGHT", "REAL"), ("WINDOW_EVENT_N", "REAL"), ("WINDOW_T", "REAL"),
    ("AUTO_SAMPLE_T", "REAL"), ("MAN_EVENT_N", "REAL"), ("MAN_RESP_T_AUTO", "REAL"),
    ("MAN_RESP_T_USE", "REAL"), ("CROSS_COUNT", "REAL"), ("ERR_ABS_AVG", "REAL"),
    ("ERR_AREA_POS", "REAL"), ("ERR_AREA_NEG", "REAL"), ("ERR_PEAK_ABS", "REAL"),
    ("AVG_CROSS_T", "REAL"), ("PV_DELTA", "REAL"), ("AV_DELTA", "REAL"),
    ("NOISE_EST", "REAL"), ("PROCESS_GAIN", "REAL"), ("PT_REC", "REAL"),
    ("TI_REC", "REAL"), ("TD_REC", "REAL"), ("DI_REC", "REAL"),
    ("SVH_REC", "REAL"), ("SVL_REC", "REAL"), ("PID_FORMULA_VALID", "BOOL"),
    ("PT_FORMULA_REC", "REAL"), ("TI_FORMULA_REC", "REAL"), ("PID_MODEL_GAIN_REC", "REAL"),
    ("PID_MODEL_T_REC", "REAL"), ("PID_MODEL_L_REC", "REAL"), ("PID_MODEL_LAMBDA_REC", "REAL"),
    ("PID_FORMULA_BLEND_REC", "REAL"), ("TL_REC", "REAL"), ("TL1_REC", "REAL"),
    ("TL2_REC", "REAL"), ("TL3_REC", "REAL"), ("TL4_REC", "REAL"),
    ("E1_REC", "REAL"), ("E2_REC", "REAL"), ("E3_REC", "REAL"),
    ("E4_REC", "REAL"), ("AO1_REC", "REAL"), ("AO2_REC", "REAL"),
    ("AO3_REC", "REAL"), ("AO4_REC", "REAL"), ("RSF_LOCK_T_REC", "REAL"),
    ("TC_REC", "REAL"), ("TZ_REC", "REAL"), ("GC1_REC", "REAL"),
    ("GC2_REC", "REAL"), ("OUTH_REC", "REAL"), ("OUTL_REC", "REAL"),
    ("CD_GD_REC", "REAL"), ("CD_K_REC", "REAL"), ("CD_K_FD_REC", "REAL"),
    ("CD_K_J_REC", "REAL"), ("CD_K_D_REC", "REAL"), ("CDH_REC", "REAL"),
    ("CDL_REC", "REAL"), ("TC_CD_REC", "REAL"), ("TZ_CD_REC", "REAL"),
)


def _apcmauto_source(*, explicit_inputs=False, instance_names=("A",)):
    declarations = [
        f"I_{name}:{iec_type};" for name, iec_type, _default in _APCMAUTO_INPUTS
    ] + [
        f"O_{name}:{iec_type};" for name, iec_type in _APCMAUTO_OUTPUTS
    ]
    bindings = []
    if explicit_inputs:
        bindings.extend(
            f"{name}:=I_{name}" for name, _type, _default in _APCMAUTO_INPUTS)
    bindings.extend(f"{name}=>O_{name}" for name, _type in _APCMAUTO_OUTPUTS)
    return (
        "VAR_GLOBAL " + " ".join(declarations) + " END_VAR "
        f"VAR {','.join(instance_names)}:APCMAUTOPARA; END_VAR "
        + " ".join(
            f"{instance_name}(" + ",".join(bindings) + ");"
            for instance_name in instance_names))



class Stage3PublicSurfaceTests(unittest.TestCase):
    def test_library_source_aliases_are_internal_fresh_and_explicit(self):
        aliases = library_source_aliases()
        self.assertEqual(tuple(aliases), (
            "TON", "TOF", "TP", "R_TRIG", "F_TRIG", "SR", "RS", "BLINK",
            "APCSTATISTICS", "APCCD", "APCM", "APCHSFOP", "APCHSRATELIM", "APCHSACCUM",
            "APCHXHCL", "APCHSHLLIM", "APCGCQ", "APCSPFINDER", "APCPIDZZD",
            "APCPID", "APCRSFNAUTOPARA", "APCMAUTOPARA"))
        self.assertEqual(aliases["APCSTATISTICS"], {
            "IN": "IN", "RESET": "RESET", "MN": "MN", "MX": "MX", "AVG": "AVG"})
        self.assertEqual(aliases["APCCD"], {
            name: name for name, _type in _APCCD_REQUIRED_INPUTS + _APCCD_OUTPUTS
        } | {
            name: name for name, _type, _default in _APCCD_OPTIONAL_INPUTS
        } | {"ZLOUT": "ZLOUT"})
        self.assertEqual(aliases["APCM"], {
            "SP": "SP", "PV": "PV", "OC": "OC", "TS": "TS", "TP": "TP",
            "RM": "RM", "OUTT": "OUTT", "OUTB": "OUTB", "SADD": "SADD",
            "SSUB": "SSUB", "ZLEN": "ZLEN", "ZSYK": "ZSYK",
            "ZLOUT": "ZLOUT", "AV": "AV", "AV_P": "AV_P", "AV_R": "AV_R",
            "AV_GC": "AV_GC", "AV_J": "AV_J", "AV_D": "AV_D", "AV_C": "AV_C"})
        self.assertEqual(aliases["APCHSFOP"], {
            "IN": "IN", "TC": "TC", "KG": "KG", "TB": "TB", "AV": "AV"})
        self.assertEqual(aliases["APCHSRATELIM"], {
            "IN": "IN", "HL": "HL", "LL": "LL", "AV": "AV"})
        self.assertEqual(aliases["APCHSACCUM"], {
            "I1": "I1", "RS": "RS", "AV": "AV", "SS": "SS"})
        self.assertEqual(aliases["APCHXHCL"], {
            "EN": "EN", "PV": "PV", "FV": "FV", "PVH": "PVH",
            "PVL": "PVL", "BHSLH": "BHSLH", "TL": "TL", "TC": "TC",
            "KG": "KG", "TB": "TB", "AV": "AV", "GZDV": "GZDV",
            "PV_AVG": "PV_AVG", "FV_AVG": "FV_AVG"})
        self.assertEqual(aliases["APCHSHLLIM"], {
            "IN": "IN", "HL": "HL", "LL": "LL", "AV": "AV"})
        self.assertEqual(aliases["APCGCQ"], {
            "IN": "IN", "TC": "TC", "TZ": "TZ", "K": "K", "INSP": "INSP",
            "GC1": "GC1", "GC2": "GC2", "OUTH": "OUTH", "OUTL": "OUTL",
            "OUTV": "OUTV", "GCAV": "GCAV", "JTAV": "JTAV", "DTAV": "DTAV"})
        self.assertEqual(aliases["APCSPFINDER"], {
            "EN": "EN", "RESET": "RESET", "CYCLE": "CYCLE", "SAMPLE_OK": "SAMPLE_OK",
            "SP_MAN": "SP_MAN", "SP_MAN_EN": "SP_MAN_EN", "SP_TAG": "SP_TAG",
            "SP_TAG_EN": "SP_TAG_EN", "SP_AUTO_EN": "SP_AUTO_EN",
            "SP_AUTO_REPLACE_BAD_TAG": "SP_AUTO_REPLACE_BAD_TAG", "PV": "PV", "AV": "AV",
            "PVMU": "PVMU", "PVMD": "PVMD", "OUTT": "OUTT", "OUTB": "OUTB",
            "SP_STABLE_T": "SP_STABLE_T", "SP_CONF_T": "SP_CONF_T",
            "PV_STABLE_K": "PV_STABLE_K", "AV_STABLE_K": "AV_STABLE_K",
            "PV_STABLE_ABS": "PV_STABLE_ABS", "AV_STABLE_ABS": "AV_STABLE_ABS",
            "SP_BAD_K": "SP_BAD_K", "SP_BAD_ABS": "SP_BAD_ABS", "SP_USE": "SP_USE",
            "SP_VALID": "SP_VALID", "SP_SOURCE": "SP_SOURCE", "SP_REASON": "SP_REASON",
            "SP_AUTO": "SP_AUTO", "SP_AUTO_OK": "SP_AUTO_OK", "SP_AUTO_CONF": "SP_AUTO_CONF",
            "SP_TAG_BAD": "SP_TAG_BAD", "SP_STABLE_T_OUT": "SP_STABLE_T_OUT",
            "SP_STABLE_PV_RANGE": "SP_STABLE_PV_RANGE"})
        self.assertEqual(aliases["APCPIDZZD"], {
            "AV": "AV", "SP": "SP", "PV": "PV", "PT": "PT", "TI": "TI",
            "RM": "RM", "PVMU": "PVMU", "PVMD": "PVMD", "MU": "MU", "MD": "MD",
            "SADD": "SADD", "SSUB": "SSUB", "PT1K": "PT1K", "TI1K": "TI1K",
            "PT1": "PT1", "TI1": "TI1"})
        self.assertEqual(aliases["APCPID"], {
            "SP": "SP", "PV": "PV", "IC": "IC", "OC": "OC", "TP": "TP",
            "TS": "TS", "RM": "RM", "OUTT": "OutT", "OUTB": "OutB",
            "SADD": "SADD", "SSUB": "SSUB", "PT": "PT", "TI": "TI",
            "KD": "KD", "TD": "TD", "AV": "AV"})
        self.assertEqual(aliases["APCRSFNAUTOPARA"], {
            name: name for name, _type in
            _APCRSF_REQUIRED_INPUTS + _APCRSF_OUTPUTS
        } | {
            name: name for name, _type, _default in _APCRSF_OPTIONAL_INPUTS
        })
        self.assertEqual(aliases["APCMAUTOPARA"], {
            name: name for name, _type, _default in _APCMAUTO_INPUTS
        } | {name: name for name, _type in _APCMAUTO_OUTPUTS})
        expected_pins = {
            "APCSTATISTICS": (
                ("IN", "VAR_INPUT", "REAL"), ("RESET", "VAR_INPUT", "BOOL"),
                ("MN", "VAR_OUTPUT", "REAL"), ("MX", "VAR_OUTPUT", "REAL"),
                ("AVG", "VAR_OUTPUT", "LREAL")),
            "APCCD": (
                ("SP", "VAR_INPUT", "REAL"), ("PV", "VAR_INPUT", "REAL"),
                ("TS", "VAR_INPUT", "BOOL"), ("TC", "VAR_INPUT", "REAL"),
                ("TZ", "VAR_INPUT", "REAL"), ("CDH", "VAR_INPUT", "REAL"),
                ("CDL", "VAR_INPUT", "REAL"), ("TL", "VAR_INPUT", "REAL"),
                ("CD_K_J", "VAR_INPUT", "REAL"),
                ("CD_K_D", "VAR_INPUT", "REAL"),
                ("CD_K_FD", "VAR_INPUT", "REAL"),
                ("CD_GD", "VAR_INPUT", "REAL"),
                ("CD_K", "VAR_INPUT", "REAL"), ("AD", "VAR_INPUT", "BOOL"),
                ("AV", "VAR_OUTPUT", "REAL"),
                ("CD_BH", "VAR_OUTPUT", "REAL")),
            "APCHSFOP": (
                ("IN", "VAR_INPUT", "REAL"), ("TC", "VAR_INPUT", "REAL"),
                ("KG", "VAR_INPUT", "REAL"), ("TB", "VAR_INPUT", "REAL"),
                ("AV", "VAR_OUTPUT", "REAL")),
            "APCHSRATELIM": (
                ("IN", "VAR_INPUT", "REAL"), ("HL", "VAR_INPUT", "REAL"),
                ("LL", "VAR_INPUT", "REAL"), ("AV", "VAR_OUTPUT", "REAL")),
            "APCHSACCUM": (
                ("I1", "VAR_INPUT", "REAL"), ("RS", "VAR_INPUT", "BOOL"),
                ("AV", "VAR_OUTPUT", "LREAL"), ("SS", "VAR_OUTPUT", "BOOL")),
            "APCHXHCL": (
                ("EN", "VAR_INPUT", "BOOL"), ("PV", "VAR_INPUT", "REAL"),
                ("FV", "VAR_INPUT", "REAL"), ("PVH", "VAR_INPUT", "REAL"),
                ("PVL", "VAR_INPUT", "REAL"), ("BHSLH", "VAR_INPUT", "REAL"),
                ("TL", "VAR_INPUT", "REAL"), ("TC", "VAR_INPUT", "REAL"),
                ("KG", "VAR_INPUT", "REAL"), ("TB", "VAR_INPUT", "REAL"),
                ("AV", "VAR_OUTPUT", "REAL"), ("GZDV", "VAR_OUTPUT", "BOOL"),
                ("PV_AVG", "VAR_OUTPUT", "REAL"), ("FV_AVG", "VAR_OUTPUT", "REAL")),
            "APCHSHLLIM": (
                ("IN", "VAR_INPUT", "REAL"), ("HL", "VAR_INPUT", "REAL"),
                ("LL", "VAR_INPUT", "REAL"), ("AV", "VAR_OUTPUT", "REAL")),
            "APCGCQ": (
                ("IN", "VAR_INPUT", "REAL"), ("TC", "VAR_INPUT", "REAL"),
                ("TZ", "VAR_INPUT", "REAL"), ("K", "VAR_INPUT", "REAL"),
                ("INSP", "VAR_INPUT", "REAL"), ("GC1", "VAR_INPUT", "REAL"),
                ("GC2", "VAR_INPUT", "REAL"), ("OUTH", "VAR_INPUT", "REAL"),
                ("OUTL", "VAR_INPUT", "REAL"), ("OUTV", "VAR_INPUT", "REAL"),
                ("GCAV", "VAR_OUTPUT", "REAL"), ("JTAV", "VAR_OUTPUT", "REAL"),
                ("DTAV", "VAR_OUTPUT", "REAL")),
            "APCSPFINDER": (
                ("EN", "VAR_INPUT", "BOOL"), ("RESET", "VAR_INPUT", "BOOL"),
                ("CYCLE", "VAR_INPUT", "REAL"), ("SAMPLE_OK", "VAR_INPUT", "BOOL"),
                ("SP_MAN", "VAR_INPUT", "REAL"), ("SP_MAN_EN", "VAR_INPUT", "BOOL"),
                ("SP_TAG", "VAR_INPUT", "REAL"), ("SP_TAG_EN", "VAR_INPUT", "BOOL"),
                ("SP_AUTO_EN", "VAR_INPUT", "BOOL"),
                ("SP_AUTO_REPLACE_BAD_TAG", "VAR_INPUT", "BOOL"),
                ("PV", "VAR_INPUT", "REAL"), ("AV", "VAR_INPUT", "REAL"),
                ("PVMU", "VAR_INPUT", "REAL"), ("PVMD", "VAR_INPUT", "REAL"),
                ("OUTT", "VAR_INPUT", "REAL"), ("OUTB", "VAR_INPUT", "REAL"),
                ("SP_STABLE_T", "VAR_INPUT", "REAL"), ("SP_CONF_T", "VAR_INPUT", "REAL"),
                ("PV_STABLE_K", "VAR_INPUT", "REAL"), ("AV_STABLE_K", "VAR_INPUT", "REAL"),
                ("PV_STABLE_ABS", "VAR_INPUT", "REAL"), ("AV_STABLE_ABS", "VAR_INPUT", "REAL"),
                ("SP_BAD_K", "VAR_INPUT", "REAL"), ("SP_BAD_ABS", "VAR_INPUT", "REAL"),
                ("SP_USE", "VAR_OUTPUT", "REAL"), ("SP_VALID", "VAR_OUTPUT", "BOOL"),
                ("SP_SOURCE", "VAR_OUTPUT", "INT"), ("SP_REASON", "VAR_OUTPUT", "INT"),
                ("SP_AUTO", "VAR_OUTPUT", "REAL"), ("SP_AUTO_OK", "VAR_OUTPUT", "BOOL"),
                ("SP_AUTO_CONF", "VAR_OUTPUT", "REAL"), ("SP_TAG_BAD", "VAR_OUTPUT", "BOOL"),
                ("SP_STABLE_T_OUT", "VAR_OUTPUT", "REAL"),
                ("SP_STABLE_PV_RANGE", "VAR_OUTPUT", "REAL")),
            "APCPIDZZD": (
                ("AV", "VAR_INPUT", "REAL"), ("SP", "VAR_INPUT", "REAL"),
                ("PV", "VAR_INPUT", "REAL"), ("PT", "VAR_INPUT", "REAL"),
                ("TI", "VAR_INPUT", "REAL"), ("RM", "VAR_INPUT", "INT"),
                ("PVMU", "VAR_INPUT", "REAL"), ("PVMD", "VAR_INPUT", "REAL"),
                ("MU", "VAR_INPUT", "REAL"), ("MD", "VAR_INPUT", "REAL"),
                ("SADD", "VAR_INPUT", "BOOL"), ("SSUB", "VAR_INPUT", "BOOL"),
                ("PT1K", "VAR_INPUT", "REAL"), ("TI1K", "VAR_INPUT", "REAL"),
                ("PT1", "VAR_OUTPUT", "REAL"), ("TI1", "VAR_OUTPUT", "REAL")),
            "APCPID": (
                ("SP", "VAR_INPUT", "REAL"), ("PV", "VAR_INPUT", "REAL"),
                ("IC", "VAR_INPUT", "REAL"), ("OC", "VAR_INPUT", "REAL"),
                ("TP", "VAR_INPUT", "REAL"), ("TS", "VAR_INPUT", "BOOL"),
                ("RM", "VAR_INPUT", "INT"), ("OutT", "VAR_INPUT", "REAL"),
                ("OutB", "VAR_INPUT", "REAL"), ("SADD", "VAR_INPUT", "BOOL"),
                ("SSUB", "VAR_INPUT", "BOOL"), ("PT", "VAR_INPUT", "REAL"),
                ("TI", "VAR_INPUT", "REAL"), ("KD", "VAR_INPUT", "REAL"),
                ("TD", "VAR_INPUT", "REAL"), ("AV", "VAR_OUTPUT", "REAL")),
        }
        registry = runtime.build_default_registry()
        for block_type, pins in expected_pins.items():
            with self.subTest(block_type=block_type):
                schema, _adapter = registry.resolve(block_type, "engineering")
                self.assertEqual(
                    tuple((pin.name, pin.kind, pin.iec_type)
                          for pin in tuple(schema.inputs) + tuple(schema.outputs)),
                    pins)
                expected_source_names = {
                    name.upper() if name in {"OutT", "OutB"} else name
                    for name, _kind, _type in pins}
                if block_type == "APCCD":
                    expected_source_names.add("ZLOUT")
                self.assertEqual(set(aliases[block_type]), expected_source_names)
                self.assertEqual(set(aliases[block_type].values()),
                                 {pin[0] for pin in pins} |
                                 ({"ZLOUT"} if block_type == "APCCD" else set()))
        apccd_schema, _adapter = registry.resolve("APCCD", "engineering")
        self.assertEqual(
            tuple((pin.name, pin.kind, pin.iec_type) for pin in apccd_schema.inouts),
            (("ZLOUT", "VAR_IN_OUT", "REAL"),))
        self.assertEqual(primitive_source_aliases(), {
            name: aliases[name] for name in primitive_source_aliases()})
        aliases["TON"]["IN"] = "BROKEN"
        self.assertEqual(library_source_aliases()["TON"]["IN"], "IN")
        self.assertFalse(hasattr(runtime, "library_source_aliases"))

    def test_apcrsfnautopara_schema_matches_explicit_source_contract(self):
        schema, adapter = runtime.build_default_registry().resolve(
            "APCRSFNAUTOPARA", "engineering")
        self.assertEqual(
            {pin.name: (pin.iec_type, pin.omit_policy,
                        type(pin.default), pin.default)
             for pin in schema.inputs},
            {name: (iec_type, "required", type(None), None)
             for name, iec_type in _APCRSF_REQUIRED_INPUTS} | {
                name: (iec_type, "use_default", type(default), default)
                for name, iec_type, default in _APCRSF_OPTIONAL_INPUTS})
        self.assertEqual(
            {pin.name: (pin.iec_type, pin.omit_policy,
                        type(pin.default), pin.default)
             for pin in schema.outputs},
            {name: (iec_type, "use_default", type(None), None)
             for name, iec_type in _APCRSF_OUTPUTS})
        self.assertEqual(schema.inouts, ())
        self.assertEqual(adapter.ctor_args, ())
        self.assertEqual((len(schema.inputs), len(schema.outputs)), (64, 56))

    def test_apcmautopara_schema_matches_explicit_source_contract(self):
        schema, adapter = runtime.build_default_registry().resolve(
            "APCMAUTOPARA", "engineering")
        self.assertEqual(
            {pin.name: (pin.iec_type, pin.omit_policy,
                        type(pin.default), pin.default)
             for pin in schema.inputs},
            {name: (iec_type, "use_default", type(default), default)
             for name, iec_type, default in _APCMAUTO_INPUTS})
        self.assertEqual(
            {pin.name: (pin.iec_type, pin.omit_policy,
                        type(pin.default), pin.default)
             for pin in schema.outputs},
            {name: (iec_type, "use_default", type(None), None)
             for name, iec_type in _APCMAUTO_OUTPUTS})
        self.assertEqual(schema.inouts, ())
        self.assertEqual(adapter.ctor_args, ())
        self.assertEqual((len(schema.inputs), len(schema.outputs)), (84, 87))
        self.assertEqual(len(schema.state_vars), 299)

    def test_apccd_schema_exposes_one_exact_inout_contract(self):
        schema, adapter = runtime.build_default_registry().resolve(
            "APCCD", "engineering")
        self.assertEqual(
            tuple((pin.name, pin.iec_type, pin.kind)
                  for pin in schema.inouts),
            (("ZLOUT", "REAL", "VAR_IN_OUT"),))
        self.assertEqual(adapter.ctor_args, ())
        self.assertEqual(
            (len(schema.inputs), len(schema.outputs), len(schema.inouts)),
            (14, 2, 1))

    def test_apcm_schema_exposes_shared_license_and_omit_contract(self):
        schema, adapter = runtime.build_default_registry().resolve(
            "APCM", "engineering")
        self.assertEqual(
            tuple((pin.name, pin.iec_type, pin.omit_policy)
                  for pin in schema.inputs),
            tuple((name, iec_type, "required")
                  for name, iec_type in _APCM_REQUIRED_INPUTS) + (
                ("RM", "INT", "none_means_no_write"),
                ("OUTT", "REAL", "none_means_no_write"),
                ("OUTB", "REAL", "none_means_no_write"),
                ("SADD", "BOOL", "none_means_no_write"),
                ("SSUB", "BOOL", "none_means_no_write"),
                ("ZLEN", "BOOL", "none_means_no_write"),
                ("ZSYK", "REAL", "keep_previous"),
            ))
        self.assertEqual(
            tuple((pin.name, pin.iec_type) for pin in schema.inouts),
            (("ZLOUT", "REAL"),))
        self.assertEqual(adapter.ctor_args, ("license_context",))

    def test_minimal_public_surface_and_definition_identity(self):
        for name, definition in _PUBLIC_ST.items():
            with self.subTest(name=name):
                self.assertIn(name, runtime.__all__)
                self.assertIs(getattr(runtime, name), definition)
        for internal in ("lex_st", "parse_st", "primitive_source_aliases"):
            with self.subTest(internal=internal):
                self.assertNotIn(internal, runtime.__all__)
                self.assertFalse(hasattr(runtime, internal))

    def test_public_error_layers_are_preserved(self):
        with self.assertRaises(runtime.STLexError) as lexed:
            runtime.compile_st_task("@")
        self.assertIsInstance(lexed.exception.errors[0], runtime.STLexDiagnostic)

        with self.assertRaises(runtime.STParseError) as parsed:
            runtime.compile_st_task("IF TRUE THEN")
        self.assertIsInstance(parsed.exception.errors[0], runtime.STParseDiagnostic)

        with self.assertRaises(runtime.STCompileError) as compiled:
            runtime.compile_st_task(
                "VAR_GLOBAL X:INT; END_VAR X:=TRUE;")
        self.assertIsInstance(
            compiled.exception.errors[0], runtime.STCompileDiagnostic)
        self.assertEqual(compiled.exception.errors[0].code, "TYPE_MISMATCH")

    def test_stage3_module_directory_and_internal_import_dag(self):
        root = Path(st_lowering.__file__).parent
        files = tuple(sorted(path.name for path in root.glob("st_*.py")))
        self.assertEqual(files, (
            "st_lexer.py", "st_library_bindings.py", "st_lowering.py",
            "st_parser.py"))
        graph = {name[:-3]: set() for name in files}
        for filename in files:
            module = filename[:-3]
            tree = ast.parse((root / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "src.runtime")
                    if node.module and node.module.startswith("src.runtime.st_"):
                        graph[module].add(node.module.rsplit(".", 1)[-1])
        self.assertEqual(graph, {
            "st_lexer": set(),
            "st_library_bindings": set(),
            "st_parser": {"st_lexer"},
            "st_lowering": {"st_lexer", "st_library_bindings", "st_parser"},
        })


class Stage3VerticalAcceptanceTests(unittest.TestCase):
    @staticmethod
    def _run(assembly, scans):
        trace = []
        previous = assembly.store.snapshot()
        for values in scans:
            for key in sorted(values):
                assembly.store.write(key, values[key])
            assembly.executor.execute_programs(previous)
            previous = assembly.store.snapshot()
            trace.append(previous.as_dict())
        return trace

    def test_apccd_inout_round_trips_and_matches_direct_block(self):
        compiled = runtime.compile_st_task(_apccd_source(explicit_optional=True))
        self.assertEqual(len(library_source_aliases()), 22)
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        direct = APCCD()
        values = {
            "SP": 0.0, "PV": 10.0, "TS": False, "TC": 100.0,
            "TZ": 1.0, "CDH": 10.0, "CDL": -10.0, "TL": 0.0,
            **{name: default for name, _type, default in _APCCD_OPTIONAL_INPUTS},
        }
        scans = (
            {**values, "TS": False},
            {**values, "TS": True},
            {**values, "TS": True},
            {**values, "TS": False, "PV": 0.0},
        )
        z_direct = 10.0
        previous = assembly.store.snapshot()
        assembly.store.write("Z", z_direct)
        for scan in scans:
            for name, value in sorted(scan.items()):
                assembly.store.write("I_" + name, value)
            assembly.executor.execute_programs(previous)
            previous = assembly.store.snapshot()
            out = direct.step(500, ZLOUT=z_direct, **scan)
            z_direct = out["ZLOUT"]
            self.assertEqual(
                (previous.as_dict()["O_AV"], previous.as_dict()["O_CD_BH"],
                 previous.as_dict()["Z"]),
                (out["AV"], out["CD_BH"], z_direct))
        self.assertNotEqual(z_direct, 10.0)

        other = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        self.assertIsNot(
            assembly.executor._adapters["PLC_PRG.CD"].instance,
            other.executor._adapters["PLC_PRG.CD"].instance)
        self.assertEqual(other.store.read("Z"), 0.0)

        failed = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        failed.store.write("Z", 12.0)
        for name, value in sorted(scans[0].items()):
            failed.store.write("I_" + name, value)
        before = failed.store.snapshot()
        with patch.object(APCCD, "step", side_effect=RuntimeError("injected")):
            with self.assertRaises(runtime.IRExecutionError):
                failed.executor.execute_programs(before)
        self.assertEqual(failed.store.read("Z"), 12.0)
        self.assertEqual(failed.store.read("O_AV"), 0.0)
        self.assertEqual(failed.store.read("O_CD_BH"), 0.0)

    def test_apcm_shared_license_inout_and_omission_match_direct(self):
        compiled = runtime.compile_st_task(_apcm_source())
        self.assertEqual(len(library_source_aliases()), 22)
        instance_decl = compiled.task.pou_lib["PLC_PRG"].instances[0]
        self.assertEqual(instance_decl.ctor_args, {})
        with self.assertRaises(runtime.StartupValidationError):
            runtime.build_runtime(compiled.task, runtime.build_default_registry())

        runtime_context = _pid_context(True)
        direct_context = _pid_context(True)
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry(),
            dependencies={"license_context": runtime_context})
        instance = assembly.executor._adapters["PLC_PRG.M"].instance
        self.assertIs(instance._ctx, runtime_context)
        self.assertIs(instance.PIDZZD1._ctx, runtime_context)

        direct = APCM(direct_context)
        direct_ref = RealRef(5.0)
        assembly.store.write("Z", 5.0)
        scans = (
            {"SP": 50.0, "PV": 45.0, "OC": 0.0, "TS": False, "TP": 0.0},
            {"SP": 50.0, "PV": 46.0, "OC": 1.0, "TS": False, "TP": 0.0},
            {"SP": 50.0, "PV": 47.0, "OC": 1.0, "TS": True, "TP": 2.0},
        )
        previous = assembly.store.snapshot()
        for scan in scans:
            for name, value in sorted(scan.items()):
                assembly.store.write("I_" + name, value)
            assembly.executor.execute_programs(previous)
            previous = assembly.store.snapshot()
            direct.step(500, zlout_ref=direct_ref, **scan)
            row = previous.as_dict()
            self.assertEqual(
                tuple(row["O_" + name] for name, _type in _APCM_OUTPUTS),
                tuple(getattr(direct, name) for name, _type in _APCM_OUTPUTS))
            self.assertEqual(row["Z"], direct_ref.value)
            self.assertEqual(runtime_context.BD_ERROR6, direct_context.BD_ERROR6)
        self.assertEqual(instance.ZSYK, direct.ZSYK)

        other_context = _pid_context(True)
        other = runtime.build_runtime(
            compiled.task, runtime.build_default_registry(),
            dependencies={"license_context": other_context})
        other_instance = other.executor._adapters["PLC_PRG.M"].instance
        self.assertIsNot(instance, other_instance)
        self.assertIsNot(instance.PIDZZD1, other_instance.PIDZZD1)
        self.assertIs(other_instance._ctx, other_context)

    def test_apcrsfnautopara_minimal_required_call_uses_schema_defaults(self):
        """The source surface opens only the 11 required inputs and 56 outputs."""
        compiled = runtime.compile_st_task(_apcrsf_source())
        self.assertEqual(len(library_source_aliases()), 22)
        self.assertIn(LoadVar("R.RUNNING", "BOOL"), compiled.code)
        self.assertNotIn(StoreVar("R.CYCLE", "REAL"), compiled.code)

        values = {
            "I_EN": True, "I_RESET": False, "I_CALC_NOW": False,
            "I_SP": 50.0, "I_PV": 45.0, "I_AV": 10.0, "I_TP": 0.0,
            "I_TS": False, "I_RSF_LEVEL": 0.0,
            "I_RSF_LOCK_LEVEL_IN": 0.0, "I_RSF_STEP": 0.0,
        }
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        row = self._run(assembly, (values,))[0]
        direct = APCRSFNAUTOPARA()
        direct.step(500, **{
            name: values["I_" + name] for name, _type in _APCRSF_REQUIRED_INPUTS})
        self.assertEqual(
            tuple(row["O_" + name] for name, _type in _APCRSF_OUTPUTS),
            tuple(getattr(direct, name) for name, _type in _APCRSF_OUTPUTS))

    def test_apcmautopara_zero_input_call_uses_all_schema_defaults(self):
        """All 84 inputs may be omitted, while all 87 outputs stay explicit."""
        compiled = runtime.compile_st_task(_apcmauto_source())
        self.assertEqual(len(library_source_aliases()), 22)
        self.assertNotIn(StoreVar("A.CYCLE", "REAL"), compiled.code)
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        row = self._run(assembly, ({},))[0]
        direct = APCMAUTOPARA()
        direct.step(500)
        self.assertEqual(
            tuple(row["O_" + name] for name, _type in _APCMAUTO_OUTPUTS),
            tuple(getattr(direct, name) for name, _type in _APCMAUTO_OUTPUTS))

    def test_apcmautopara_explicit_window_matches_direct_and_isolates_spf(self):
        compiled = runtime.compile_st_task(_apcmauto_source(explicit_inputs=True))
        left = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        right = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        left_instance = left.executor._adapters["PLC_PRG.A"].instance
        right_instance = right.executor._adapters["PLC_PRG.A"].instance
        self.assertIsNot(left_instance, right_instance)
        self.assertIsNot(left_instance.SPF1, right_instance.SPF1)
        self.assertIsNot(left_instance.H_VALID, right_instance.H_VALID)
        self.assertIsNot(left_instance.H_PT, right_instance.H_PT)

        values = {name: default for name, _type, default in _APCMAUTO_INPUTS}
        values.update({
            "CYCLE": 1.0, "COLLECT_MODE": 0, "WIN_T": 8.0,
            "MIN_WIN_T": 3.0, "MIN_STORE_EVENT": 1.0,
            "MIN_VALID_EVENT": 1.0, "HISTORY_N": 2,
            "FUSE_MIN_N": 1.0, "FUSE_MIN_WEIGHT": 0.0,
            "SP": 50.0, "AV": 10.0,
        })
        scans = []
        scans.append({**values, "EN": False, "PV": 50.0})
        for index in range(8):
            scans.append({
                **values, "EN": True, "PV": 55.0 if index % 2 == 0 else 45.0,
                "CALC_NOW": False})
        scans.append({**values, "EN": True, "PV": 55.0, "CALC_NOW": True})
        scans.append({
            **values, "EN": True, "RESET": True, "PV": 45.0,
            "CALC_NOW": False})

        direct = APCMAUTOPARA()
        previous = left.store.snapshot()
        trace = []
        critical = (
            "WIN_EVENT_N", "WIN_ELAPSED", "HISTORY_COUNT", "H_IDX",
            "CALC_OLD", "DATA_REASON", "MATCH_LEVEL", "MAN_RESP_ACTIVE")
        for scan in scans:
            runtime_values = {"I_" + name: value for name, value in scan.items()}
            for key in sorted(runtime_values):
                left.store.write(key, runtime_values[key])
            left.executor.execute_programs(previous)
            previous = left.store.snapshot()
            trace.append(previous.as_dict())
            direct.step(500, **scan)
            self.assertEqual(
                tuple(previous.as_dict()["O_" + name]
                      for name, _type in _APCMAUTO_OUTPUTS),
                tuple(getattr(direct, name) for name, _type in _APCMAUTO_OUTPUTS))
            self.assertEqual(
                tuple(getattr(left_instance, name) for name in critical),
                tuple(getattr(direct, name) for name in critical))
            self.assertEqual(left_instance.H_VALID, direct.H_VALID)
            self.assertEqual(left_instance.H_PT, direct.H_PT)
        self.assertTrue(trace[-3]["O_WINDOW_DONE"])
        self.assertGreaterEqual(trace[-3]["O_HISTORY_COUNT"], 1.0)
        self.assertTrue(trace[-1]["O_RUNNING"])
        self.assertGreaterEqual(left_instance.WIN_EVENT_N, 0)
        self.assertIs(left_instance.SPF1, left.executor._adapters["PLC_PRG.A"].instance.SPF1)
        self.assertEqual(right_instance.HISTORY_COUNT, 0.0)

    def test_apcmautopara_negative_real_clamp_inputs_commit_exact_floats(self):
        """Public ``compile_st_task -> build_runtime -> Executor`` proof that the
        17 REAL ``*_REC`` clamps commit exact ``float`` for negative and zero
        REAL inputs, and that an injected block failure rolls the whole scan back
        (batch-commit atomicity)."""
        clamp_inputs = (
            "TD_IN", "DI_IN", "SVH_IN", "SVL_IN", "TL_IN", "AO1_IN",
            "RSF_LOCK_T_IN", "TC_IN", "TZ_IN", "GC1_IN", "GC2_IN",
            "CD_GD_IN", "CD_K_FD_IN", "CD_K_J_IN", "CD_K_D_IN",
            "TC_CD_IN", "TZ_CD_IN")
        clamp_outputs = tuple(name[:-3] + "_REC" for name in clamp_inputs)
        compiled = runtime.compile_st_task(_apcmauto_source(explicit_inputs=True))
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        base = {"I_" + name: default for name, _type, default in _APCMAUTO_INPUTS}
        base["I_EN"] = True
        negative = dict(base, **{"I_" + name: -1.0 for name in clamp_inputs})
        zero = dict(base, **{"I_" + name: 0.0 for name in clamp_inputs})
        boundary = dict(base, **{"I_" + name: -1e-12 for name in clamp_inputs})
        for row in self._run(assembly, (negative, zero, boundary)):
            for name in clamp_outputs:
                self.assertIs(type(row["O_" + name]), float)
                self.assertEqual(row["O_" + name], 0.0)

        failed = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        for key, value in sorted(negative.items()):
            failed.store.write(key, value)
        before = failed.store.snapshot()
        with patch.object(APCMAUTOPARA, "step",
                          side_effect=RuntimeError("injected")):
            with self.assertRaises(runtime.IRExecutionError):
                failed.executor.execute_programs(before)
        for name in clamp_outputs:
            self.assertEqual(failed.store.read("O_" + name), 0.0)

    def test_apcmautopara_invalid_fuse_and_saturated_history_real_carriers(self):
        """Public-path proof for every APCMAUTO REAL output's FUSE fallback.

        The invalid-window candidate has a finite, exact-float input surface;
        ``MIN_STORE_EVENT`` prevents history admission, hence FUSE is zero.
        A failed Store conversion must publish none of the 87 output writes.
        """
        expected_recommendations = {
            "PT_REC": 330.0, "TI_REC": 55.00000000000001, "TD_REC": 0.0,
            "DI_REC": 5.0, "SVH_REC": 19.5, "SVL_REC": 5.0,
            "TL_REC": 10.0, "TL1_REC": 15.0, "TL2_REC": 22.5,
            "TL3_REC": 30.0, "TL4_REC": 37.5, "E1_REC": 5.0,
            "E2_REC": 10.5, "E3_REC": 19.5, "E4_REC": 30.0,
            "AO1_REC": 0.6, "AO2_REC": 0.8999999999999999,
            "AO3_REC": 1.2599999999999998, "AO4_REC": 1.6379999999999997,
            "RSF_LOCK_T_REC": 30.0,
            "TC_REC": 10.0, "TZ_REC": 5.0, "GC1_REC": 1.0,
            "GC2_REC": 0.0, "OUTH_REC": 10.0, "OUTL_REC": -10.0,
            "CD_GD_REC": 22.22222222222222, "CD_K_REC": 0.3,
            "CD_K_FD_REC": 0.5, "CD_K_J_REC": 1.0, "CD_K_D_REC": 0.2,
            "CDH_REC": 10.0, "CDL_REC": -10.0, "TC_CD_REC": 10.0,
            "TZ_CD_REC": 5.0,
        }
        negative_inputs = (
            "TL_IN", "TL1_IN", "TL2_IN", "TL3_IN", "TL4_IN", "E1_IN",
            "E2_IN", "E3_IN", "E4_IN", "AO1_IN", "AO2_IN", "AO3_IN",
            "AO4_IN", "RSF_LOCK_T_IN", "TC_IN", "TZ_IN", "GC1_IN",
            "GC2_IN", "OUTH_IN", "OUTL_IN", "CD_GD_IN", "CD_K_IN",
            "CD_K_FD_IN", "CD_K_J_IN", "CD_K_D_IN", "CDH_IN", "CDL_IN",
            "TC_CD_IN", "TZ_CD_IN")
        compiled = runtime.compile_st_task(_apcmauto_source(explicit_inputs=True))
        base = {"I_" + name: default
                for name, _type, default in _APCMAUTO_INPUTS}
        base.update({"I_EN": True, "I_COLLECT_MODE": 0, "I_CYCLE": 1.0,
                     "I_MIN_WIN_T": 3.0, "I_MIN_STORE_EVENT": 999.0,
                     **{"I_" + name: -1.0 for name in negative_inputs}})
        scans = tuple(
            dict(base, I_PV=55.0 if index % 2 == 0 else 45.0)
            for index in range(8)) + (dict(base, I_PV=55.0, I_CALC_NOW=True),)
        assembly = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        row = self._run(assembly, scans)[-1]
        for name, iec_type in _APCMAUTO_OUTPUTS:
            if iec_type == "REAL":
                self.assertIs(type(row["O_" + name]), float, name)
        for name, value in expected_recommendations.items():
            self.assertEqual(row["O_" + name], value, name)

        failed = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        sentinels = {
            "O_" + name: (17.25 if iec_type == "REAL" else
                            (True if iec_type == "BOOL" else 17))
            for name, iec_type in _APCMAUTO_OUTPUTS}
        for key, value in sorted(sentinels.items()):
            failed.store.write(key, value)
        previous = failed.store.snapshot()
        for scan in scans[:-1]:
            for key, value in sorted(scan.items()):
                failed.store.write(key, value)
            failed.executor.execute_programs(previous)
            previous = failed.store.snapshot()
        for key, value in sorted(scans[-1].items()):
            failed.store.write(key, value)
        before = failed.store.snapshot()
        outputs_before = {
            "O_" + name: before.as_dict()["O_" + name]
            for name, _type in _APCMAUTO_OUTPUTS}
        with patch.object(APCMAUTOPARA, "step",
                          side_effect=RuntimeError("injected after FUSE path")):
            with self.assertRaises(runtime.IRExecutionError):
                failed.executor.execute_programs(before)
        after = failed.store.snapshot().as_dict()
        self.assertEqual(
            {key: after[key] for key in outputs_before}, outputs_before)

        history = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        stable = {"I_" + name: default
                  for name, _type, default in _APCMAUTO_INPUTS}
        stable.update({"I_EN": True, "I_COLLECT_MODE": 0, "I_CYCLE": 1.0,
                       "I_MIN_STORE_EVENT": 1.0, "I_MIN_VALID_EVENT": 1.0,
                       "I_WIN_T": 8.0, "I_MIN_WIN_T": 3.0,
                       "I_HISTORY_N": 3, "I_FUSE_MIN_N": 1.0,
                       "I_FUSE_MIN_WEIGHT": 0.0, "I_SP": 50.0,
                       "I_AV": 10.0})
        history_scans = [dict(stable, I_EN=False, I_PV=50.0)]
        for _window in range(4):
            history_scans.extend(
                dict(stable, I_PV=55.0 if index % 2 == 0 else 45.0)
                for index in range(8))
            history_scans.append(dict(stable, I_PV=55.0, I_CALC_NOW=True))
        history_row = self._run(history, tuple(history_scans))[-1]
        self.assertIs(type(history_row["O_HISTORY_COUNT"]), float)
        self.assertEqual(history_row["O_HISTORY_COUNT"], 3.0)

    def test_apcrsfnautopara_real_recommendations_commit_exact_floats(self):
        """The public ST path preserves all 18 current REAL recommendations.

        Expected values are independently listed here.  RESET/default covers
        negative, zero, near-zero and upper-clamp inputs; the invalid-window
        scan reaches ``FUSE_SUM_W == 0`` and therefore the ``W_*`` fallback.
        An injected failure must leave all output Store cells unchanged.
        """
        recommendations = (
            ("TL_REC", 0.0),
            ("TL1_REC", 1.0), ("TL2_REC", 1.0),
            ("TL3_REC", 1.0), ("TL4_REC", 1.0),
            ("E1_REC", 0.001), ("E2_REC", 0.001),
            ("E3_REC", 0.001), ("E4_REC", 0.001),
            ("AO1_REC", 0.0), ("AO2_REC", 0.0),
            ("AO3_REC", 0.0), ("AO4_REC", 0.0),
            ("RSF_LOCK_T_REC", 0.0),
            ("RSF_HYS_REC", 1.0), ("RSF_FAST_HYS_REC", 1.0),
            ("RSF_TLOUT_K_REC", 1.0), ("ZF_K_REC", 1.0),
        )
        lower_inputs = (
            "TL_IN", "TL1_IN", "TL2_IN", "TL3_IN", "TL4_IN",
            "E1_IN", "E2_IN", "E3_IN", "E4_IN",
            "AO1_IN", "AO2_IN", "AO3_IN", "AO4_IN", "RSF_LOCK_T_IN")
        upper_inputs = ("RSF_HYS_IN", "RSF_FAST_HYS_IN",
                        "RSF_TLOUT_K_IN", "ZF_K_IN")
        lower = {"I_" + name: -1.0 for name in lower_inputs}
        lower.update({"I_" + name: 2.0 for name in upper_inputs})
        zero = {"I_" + name: 0.0 for name in lower_inputs + upper_inputs}
        near = {"I_" + name: -1e-12 for name in lower_inputs}
        near.update({"I_" + name: 1.0 + 1e-12 for name in upper_inputs})
        zero_expected = dict(recommendations)
        zero_expected.update({"RSF_HYS_REC": 0.1, "RSF_FAST_HYS_REC": 0.01,
                              "RSF_TLOUT_K_REC": 0.0, "ZF_K_REC": 0.0})
        compiled = runtime.compile_st_task(_apcrsf_source(explicit_optional=True))
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        base = {
            "I_" + name: default
            for name, _type, default in _APCRSF_OPTIONAL_INPUTS}
        base.update({
            "I_EN": True, "I_RESET": False, "I_CALC_NOW": False,
            "I_SP": 50.0, "I_PV": 45.0, "I_AV": 10.0, "I_TP": 0.0,
            "I_TS": False, "I_RSF_LEVEL": 0.0,
            "I_RSF_LOCK_LEVEL_IN": 0.0, "I_RSF_STEP": 0.0})
        for inputs, expected in ((lower, dict(recommendations)),
                                 (zero, zero_expected),
                                 (near, dict(recommendations))):
            row = self._run(assembly, (dict(base, I_RESET=True, **inputs),))[0]
            for name, value in recommendations:
                with self.subTest(path="reset", output=name, inputs=inputs):
                    self.assertIs(type(row["O_" + name]), float)
                    self.assertEqual(row["O_" + name], expected[name])

        invalid = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        reset = dict(base, I_RESET=True, **lower)
        collect = dict(base, I_RESET=False, I_CALC_NOW=False,
                       I_MIN_STORE_EVENT=999.0, I_MIN_WIN_T=3.0, **lower)
        settle = dict(collect, I_CALC_NOW=True)
        invalid_row = self._run(invalid, (reset,) + (collect,) * 5 + (settle,))[-1]
        self.assertIs(invalid_row["O_WINDOW_DONE"], True)
        self.assertIs(invalid_row["O_WINDOW_VALID"], False)
        for name, expected in recommendations:
            with self.subTest(path="invalid-window", output=name):
                self.assertIs(type(invalid_row["O_" + name]), float)
                self.assertEqual(invalid_row["O_" + name], expected)

        failed = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        for key, value in sorted(dict(base, **lower).items()):
            failed.store.write(key, value)
        for name, _expected in recommendations:
            failed.store.write("O_" + name, 17.25)
        before = failed.store.snapshot()
        with patch.object(APCRSFNAUTOPARA, "step",
                          side_effect=RuntimeError("injected")):
            with self.assertRaises(runtime.IRExecutionError):
                failed.executor.execute_programs(before)
        for name, _expected in recommendations:
            self.assertEqual(failed.store.read("O_" + name), 17.25)

        paired = runtime.compile_st_task(_apcmauto_source(
            explicit_inputs=True, instance_names=("A1", "A2")))
        paired_runtime = runtime.build_runtime(
            paired.task, runtime.build_default_registry())
        first = paired_runtime.executor._adapters["PLC_PRG.A1"].instance
        second = paired_runtime.executor._adapters["PLC_PRG.A2"].instance
        self.assertIsNot(first, second)
        self.assertIsNot(first.SPF1, second.SPF1)
        self.assertIsNot(first.H_VALID, second.H_VALID)
        self.assertIsNot(first.H_PT, second.H_PT)

    def test_apcrsfnautopara_explicit_multiscan_matches_direct_and_isolates_spf(self):
        compiled = runtime.compile_st_task(_apcrsf_source(explicit_optional=True))
        left = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        right = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        left_instance = left.executor._adapters["PLC_PRG.R"].instance
        right_instance = right.executor._adapters["PLC_PRG.R"].instance
        self.assertIsNot(left_instance, right_instance)
        self.assertIsNot(left_instance.SPF1, right_instance.SPF1)
        self.assertIsNot(left_instance.H_VALID, right_instance.H_VALID)
        self.assertIsNot(left_instance.H_TL, right_instance.H_TL)

        optionals = {
            name: default for name, _type, default in _APCRSF_OPTIONAL_INPUTS}
        optionals.update({
            "CYCLE": 1.0, "COLLECT_MODE": 0, "WIN_T": 3.0,
            "MIN_WIN_T": 1.0, "MIN_STORE_EVENT": 1.0,
            "MIN_VALID_EVENT": 1.0, "HISTORY_N": 2,
            "FUSE_MIN_N": 1.0, "FUSE_MIN_WEIGHT": 0.0,
            "SP_STABLE_T": 1.0, "SP_CONF_T": 2.0,
        })
        required_scans = (
            {"EN": False, "RESET": False, "CALC_NOW": False, "SP": 50.0,
             "PV": 45.0, "AV": 10.0, "TP": 0.0, "TS": False,
             "RSF_LEVEL": 0.0, "RSF_LOCK_LEVEL_IN": 0.0, "RSF_STEP": 0.0},
            {"EN": True, "RESET": False, "CALC_NOW": False, "SP": 50.0,
             "PV": 45.0, "AV": 10.0, "TP": 0.0, "TS": False,
             "RSF_LEVEL": 0.0, "RSF_LOCK_LEVEL_IN": 0.0, "RSF_STEP": 1.0},
            {"EN": True, "RESET": False, "CALC_NOW": False, "SP": 50.0,
             "PV": 44.0, "AV": 11.0, "TP": 0.0, "TS": False,
             "RSF_LEVEL": 1.0, "RSF_LOCK_LEVEL_IN": 1.0, "RSF_STEP": 1.0},
            {"EN": True, "RESET": False, "CALC_NOW": True, "SP": 50.0,
             "PV": 43.0, "AV": 12.0, "TP": 0.0, "TS": False,
             "RSF_LEVEL": 1.0, "RSF_LOCK_LEVEL_IN": 1.0, "RSF_STEP": 1.0},
            {"EN": True, "RESET": False, "CALC_NOW": False, "SP": 52.0,
             "PV": 46.0, "AV": 9.0, "TP": 0.0, "TS": True,
             "RSF_LEVEL": 0.0, "RSF_LOCK_LEVEL_IN": 0.0, "RSF_STEP": 0.0},
            {"EN": True, "RESET": True, "CALC_NOW": False, "SP": 50.0,
             "PV": 47.0, "AV": 8.0, "TP": 0.0, "TS": False,
             "RSF_LEVEL": 0.0, "RSF_LOCK_LEVEL_IN": 0.0, "RSF_STEP": 0.0},
        )
        runtime_scans = tuple({
            **{"I_" + name: value for name, value in required.items()},
            **{"I_" + name: value for name, value in optionals.items()},
        } for required in required_scans)
        direct = APCRSFNAUTOPARA()
        trace = []
        expected = []
        previous = left.store.snapshot()
        state_fields = (
            "WIN_N", "WIN_ELAPSED", "HISTORY_COUNT", "H_IDX",
            "CALC_OLD", "DATA_REASON", "MATCH_LEVEL")
        for required, values in zip(required_scans, runtime_scans):
            for key in sorted(values):
                left.store.write(key, values[key])
            left.executor.execute_programs(previous)
            previous = left.store.snapshot()
            trace.append(previous.as_dict())
            direct.step(500, **required, **optionals)
            expected.append(tuple(
                getattr(direct, name) for name, _type in _APCRSF_OUTPUTS))
            self.assertEqual(
                tuple(getattr(left_instance, name) for name in state_fields),
                tuple(getattr(direct, name) for name in state_fields))
            self.assertEqual(left_instance.H_VALID, direct.H_VALID)
            self.assertEqual(left_instance.H_WEIGHT, direct.H_WEIGHT)
        self.assertEqual(
            [tuple(row["O_" + name] for name, _type in _APCRSF_OUTPUTS)
             for row in trace], expected)
        self.assertEqual(
            tuple(trace[3]["O_" + name] for name in (
                "WINDOW_DONE", "WINDOW_VALID", "DATA_REASON", "HISTORY_COUNT",
                "MATCH_LEVEL", "FINAL_VALID", "RSF_OK")),
            (True, True, 1, 1.0, 1, True, True))
        self.assertEqual(
            tuple(trace[5]["O_" + name] for name in (
                "RUNNING", "WINDOW_DONE", "WINDOW_VALID", "DATA_REASON",
                "HISTORY_COUNT", "FINAL_VALID", "RSF_OK")),
            (True, False, False, 0, 0.0, False, False))
        self.assertIs(left_instance.SPF1, left.executor._adapters["PLC_PRG.R"].instance.SPF1)
        self.assertEqual(right_instance.HISTORY_COUNT, 0.0)

        paired = runtime.compile_st_task(_apcrsf_source(
            explicit_optional=True, instance_names=("R1", "R2")))
        paired_runtime = runtime.build_runtime(
            paired.task, runtime.build_default_registry())
        first = paired_runtime.executor._adapters["PLC_PRG.R1"].instance
        second = paired_runtime.executor._adapters["PLC_PRG.R2"].instance
        self.assertIsNot(first, second)
        self.assertIsNot(first.SPF1, second.SPF1)
        self.assertIsNot(first.H_VALID, second.H_VALID)
        self.assertIsNot(first.H_TL, second.H_TL)

    def test_representative_program_function_user_fb_and_ton(self):
        add_one = runtime.compile_st_function(
            "VAR_INPUT X:INT; END_VAR AddOne:=X+1;", "AddOne", "INT")
        accumulator = runtime.compile_st_function_block("""
            VAR_INPUT I:INT; END_VAR
            VAR_OUTPUT Q:INT; END_VAR
            VAR State:INT; END_VAR
            State:=State+I; Q:=State;
        """, "Accumulator")
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL Input:INT; Start:BOOL; FunctionOut:INT; Accum:INT;
                       Done:BOOL; Elapsed:TIME; Gate:BOOL; Count:INT; END_VAR
            VAR A:Accumulator; Timer:TON; END_VAR
            FunctionOut:=AddOne(ABS(Input));
            A(I:=FunctionOut,Q=>Accum);
            Timer(IN:=Start,PT:=T#1S,Q=>Done,ET=>Elapsed);
            Count:=0;
            WHILE Count<2 DO Count:=Count+1; END_WHILE;
            Gate:=SEL(Done,FALSE,Accum>0);
        """, functions=(add_one,), function_blocks=(accumulator,))
        left = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        right = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        left_trace = self._run(left, (
            {"INPUT": -3, "START": True},
            {"INPUT": -1, "START": True},
            {"INPUT": 2, "START": False},
        ))
        self.assertEqual(
            [(row["FUNCTIONOUT"], row["ACCUM"], row["DONE"],
              row["ELAPSED"], row["COUNT"], row["GATE"])
             for row in left_trace],
            [(4, 4, False, 500, 2, False),
             (2, 6, True, 1000, 2, True),
             (3, 9, False, 0, 2, False)])
        right_trace = self._run(right, ({"INPUT": -9, "START": False},))
        self.assertEqual(
            (right_trace[0]["FUNCTIONOUT"], right_trace[0]["ACCUM"],
             right_trace[0]["ELAPSED"]),
            (10, 10, 0))
        self.assertEqual(left.executor._active_frames, [])
        self.assertEqual(right.executor._active_frames, [])

    def test_apchsaccum_optional_inputs_can_be_omitted(self):
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL AccumAv:LREAL; AccumSs:BOOL; END_VAR
            VAR A:APCHSACCUM; END_VAR
            A(AV=>AccumAv,SS=>AccumSs);
        """)
        self.assertIn(LoadVar("A.AV", "LREAL"), compiled.code)
        self.assertNotIn(StoreVar("A.I1", "REAL"), compiled.code)
        self.assertNotIn(StoreVar("A.RS", "BOOL"), compiled.code)

        assembly = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        trace = self._run(assembly, ({},))
        self.assertEqual((trace[0]["ACCUMAV"], trace[0]["ACCUMSS"]), (0.0, False))

    def test_apcspfinder_minimal_required_call_uses_schema_defaults(self):
        """The new source alias must retain the descriptor's default-only pins."""
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL En:BOOL; Reset:BOOL; SampleOk:BOOL; Pv:REAL; Av:REAL;
                       SpUse:REAL; SpValid:BOOL; SpSource:INT; SpReason:INT;
                       SpAuto:REAL; SpAutoOk:BOOL; SpAutoConf:REAL; SpTagBad:BOOL;
                       SpStableT:REAL; SpStableRange:REAL; END_VAR
            VAR F:APCSPFINDER; END_VAR
            F(EN:=En,RESET:=Reset,SAMPLE_OK:=SampleOk,PV:=Pv,AV:=Av,
              SP_USE=>SpUse,SP_VALID=>SpValid,SP_SOURCE=>SpSource,
              SP_REASON=>SpReason,SP_AUTO=>SpAuto,SP_AUTO_OK=>SpAutoOk,
              SP_AUTO_CONF=>SpAutoConf,SP_TAG_BAD=>SpTagBad,
              SP_STABLE_T_OUT=>SpStableT,SP_STABLE_PV_RANGE=>SpStableRange);
        """)
        self.assertEqual(len(library_source_aliases()), 22)
        self.assertIn(LoadVar("F.SP_USE", "REAL"), compiled.code)
        self.assertNotIn(StoreVar("F.CYCLE", "REAL"), compiled.code)

        assembly = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        trace = self._run(assembly, ({"EN": True, "RESET": False,
                                      "SAMPLEOK": True, "PV": 42.0, "AV": 3.0},))
        direct = APCSPFINDER()
        direct.step(500, EN=True, RESET=False, SAMPLE_OK=True, PV=42.0, AV=3.0)
        self.assertEqual(
            tuple(trace[0][name] for name in (
                "SPUSE", "SPVALID", "SPSOURCE", "SPREASON", "SPAUTO", "SPAUTOOK",
                "SPAUTOCONF", "SPTAGBAD", "SPSTABLET", "SPSTABLERANGE")),
            (direct.SP_USE, direct.SP_VALID, direct.SP_SOURCE, direct.SP_REASON,
             direct.SP_AUTO, direct.SP_AUTO_OK, direct.SP_AUTO_CONF, direct.SP_TAG_BAD,
             direct.SP_STABLE_T_OUT, direct.SP_STABLE_PV_RANGE))

    def test_apcspfinder_default_and_explicit_multiscan_calls_are_isolated(self):
        """Omitted inputs reset to Schema defaults; explicit inputs retain source order."""
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL En1:BOOL; Reset1:BOOL; Sample1:BOOL; Pv1:REAL; Av1:REAL;
                       En2:BOOL; Reset2:BOOL; Sample2:BOOL; Pv2:REAL; Av2:REAL;
                       Man2:BOOL; Tag2:REAL; Replace2:BOOL;
                       Use1:REAL; Valid1:BOOL; Source1:INT; Reason1:INT; Auto1:REAL;
                       AutoOk1:BOOL; Conf1:REAL; TagBad1:BOOL; StableT1:REAL; Range1:REAL;
                       Use2:REAL; Valid2:BOOL; Source2:INT; Reason2:INT; Auto2:REAL;
                       AutoOk2:BOOL; Conf2:REAL; TagBad2:BOOL; StableT2:REAL; Range2:REAL;
                       END_VAR
            VAR F1,F2:APCSPFINDER; END_VAR
            F1(EN:=En1,RESET:=Reset1,SAMPLE_OK:=Sample1,PV:=Pv1,AV:=Av1,
               SP_USE=>Use1,SP_VALID=>Valid1,SP_SOURCE=>Source1,SP_REASON=>Reason1,
               SP_AUTO=>Auto1,SP_AUTO_OK=>AutoOk1,SP_AUTO_CONF=>Conf1,
               SP_TAG_BAD=>TagBad1,SP_STABLE_T_OUT=>StableT1,
               SP_STABLE_PV_RANGE=>Range1);
            F2(EN:=En2,RESET:=Reset2,CYCLE:=1.0,SAMPLE_OK:=Sample2,SP_MAN:=75.0,
               SP_MAN_EN:=Man2,SP_TAG:=Tag2,SP_TAG_EN:=TRUE,SP_AUTO_EN:=TRUE,
               SP_AUTO_REPLACE_BAD_TAG:=Replace2,PV:=Pv2,AV:=Av2,PVMU:=100.0,
               PVMD:=0.0,OUTT:=100.0,OUTB:=0.0,SP_STABLE_T:=1.0,SP_CONF_T:=4.0,
               PV_STABLE_K:=0.002,AV_STABLE_K:=0.001,PV_STABLE_ABS:=0.0,
               AV_STABLE_ABS:=0.0,SP_BAD_K:=0.05,SP_BAD_ABS:=0.0,
               SP_USE=>Use2,SP_VALID=>Valid2,SP_SOURCE=>Source2,SP_REASON=>Reason2,
               SP_AUTO=>Auto2,SP_AUTO_OK=>AutoOk2,SP_AUTO_CONF=>Conf2,
               SP_TAG_BAD=>TagBad2,SP_STABLE_T_OUT=>StableT2,
               SP_STABLE_PV_RANGE=>Range2);
        """)
        assembly = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        scans = (
            {"EN1": True, "RESET1": False, "SAMPLE1": True, "PV1": 5.0, "AV1": 1.0,
             "EN2": True, "RESET2": False, "SAMPLE2": True, "PV2": 50.0, "AV2": 10.0,
             "MAN2": False, "TAG2": 40.0, "REPLACE2": False},
            {"EN1": True, "RESET1": False, "SAMPLE1": True, "PV1": 7.0, "AV1": 2.0,
             "EN2": True, "RESET2": False, "SAMPLE2": True, "PV2": 50.0, "AV2": 10.0,
             "MAN2": False, "TAG2": 90.0, "REPLACE2": True},
            {"EN1": False, "RESET1": False, "SAMPLE1": False, "PV1": 99.0, "AV1": 9.0,
             "EN2": True, "RESET2": False, "SAMPLE2": True, "PV2": 50.0, "AV2": 10.0,
             "MAN2": True, "TAG2": 10.0, "REPLACE2": True},
            {"EN1": True, "RESET1": True, "SAMPLE1": True, "PV1": 8.0, "AV1": 1.0,
             "EN2": True, "RESET2": True, "SAMPLE2": True, "PV2": 60.0, "AV2": 10.0,
             "MAN2": False, "TAG2": 40.0, "REPLACE2": False},
            {"EN1": False, "RESET1": False, "SAMPLE1": False, "PV1": 3.0, "AV1": 0.0,
             "EN2": False, "RESET2": False, "SAMPLE2": False, "PV2": 70.0, "AV2": 10.0,
             "MAN2": False, "TAG2": 30.0, "REPLACE2": True},
        )
        trace = self._run(assembly, scans)
        direct_one, direct_two = APCSPFINDER(), APCSPFINDER()
        expected = []
        for values in scans:
            direct_one.step(500, EN=values["EN1"], RESET=values["RESET1"],
                            SAMPLE_OK=values["SAMPLE1"], PV=values["PV1"], AV=values["AV1"])
            direct_two.step(
                500, EN=values["EN2"], RESET=values["RESET2"], CYCLE=1.0,
                SAMPLE_OK=values["SAMPLE2"], SP_MAN=75.0, SP_MAN_EN=values["MAN2"],
                SP_TAG=values["TAG2"], SP_TAG_EN=True, SP_AUTO_EN=True,
                SP_AUTO_REPLACE_BAD_TAG=values["REPLACE2"], PV=values["PV2"], AV=values["AV2"],
                PVMU=100.0, PVMD=0.0, OUTT=100.0, OUTB=0.0, SP_STABLE_T=1.0,
                SP_CONF_T=4.0, PV_STABLE_K=0.002, AV_STABLE_K=0.001,
                PV_STABLE_ABS=0.0, AV_STABLE_ABS=0.0, SP_BAD_K=0.05, SP_BAD_ABS=0.0)
            expected.append(tuple(
                getattr(direct_one, field) for field in (
                    "SP_USE", "SP_VALID", "SP_SOURCE", "SP_REASON", "SP_AUTO", "SP_AUTO_OK",
                    "SP_AUTO_CONF", "SP_TAG_BAD", "SP_STABLE_T_OUT", "SP_STABLE_PV_RANGE")) + tuple(
                getattr(direct_two, field) for field in (
                    "SP_USE", "SP_VALID", "SP_SOURCE", "SP_REASON", "SP_AUTO", "SP_AUTO_OK",
                    "SP_AUTO_CONF", "SP_TAG_BAD", "SP_STABLE_T_OUT", "SP_STABLE_PV_RANGE")))
        actual = [tuple(row[name] for name in (
            "USE1", "VALID1", "SOURCE1", "REASON1", "AUTO1", "AUTOOK1", "CONF1", "TAGBAD1",
            "STABLET1", "RANGE1", "USE2", "VALID2", "SOURCE2", "REASON2", "AUTO2", "AUTOOK2",
            "CONF2", "TAGBAD2", "STABLET2", "RANGE2")) for row in trace]
        self.assertEqual(actual, expected)
        self.assertNotEqual([row[0] for row in actual], [row[10] for row in actual])

    def test_apcpidzzd_st_call_requires_injected_context_and_keeps_ctor_empty(self):
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL Av:REAL; Sp:REAL; Pv:REAL; Pt:REAL; Ti:REAL; Pvmu:REAL;
                       Pvmd:REAL; Mu:REAL; Md:REAL; Sadd:BOOL; Ssub:BOOL;
                       Pt1:REAL; Ti1:REAL; END_VAR
            VAR P:APCPIDZZD; END_VAR
            P(AV:=Av,SP:=Sp,PV:=Pv,PT:=Pt,TI:=Ti,PVMU:=Pvmu,PVMD:=Pvmd,
              MU:=Mu,MD:=Md,SADD:=Sadd,SSUB:=Ssub,PT1=>Pt1,TI1=>Ti1);
        """)
        instance = compiled.task.pou_lib["PLC_PRG"].instances[0]
        self.assertEqual(instance.ctor_args, {})
        registry = runtime.build_default_registry()
        dependencies = {}
        with self.assertRaises(runtime.StartupValidationError) as failed:
            runtime.build_runtime(compiled.task, registry, dependencies=dependencies)
        self.assertIn("缺共享构造依赖 'license_context'", failed.exception.errors[0])
        self.assertEqual(dependencies, {})
        self.assertEqual(registry.keys(), runtime.build_default_registry().keys())

    def test_apcpidzzd_runtime_matches_direct_and_contexts_are_isolated(self):
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL Av1:REAL; Sp1:REAL; Pv1:REAL; Rm1:INT;
                       Av2:REAL; Sp2:REAL; Pv2:REAL; Rm2:INT;
                       Pt1Out:REAL; Ti1Out:REAL; Pt2Out:REAL; Ti2Out:REAL;
                       END_VAR
            VAR P1,P2:APCPIDZZD; END_VAR
            P1(AV:=Av1,SP:=Sp1,PV:=Pv1,PT:=10.0,TI:=20.0,RM:=Rm1,
               PVMU:=100.0,PVMD:=0.0,MU:=100.0,MD:=0.0,SADD:=FALSE,SSUB:=FALSE,
               PT1=>Pt1Out,TI1=>Ti1Out);
            P2(AV:=Av2,SP:=Sp2,PV:=Pv2,PT:=10.0,TI:=20.0,RM:=Rm2,
               PVMU:=100.0,PVMD:=0.0,MU:=100.0,MD:=0.0,SADD:=FALSE,SSUB:=FALSE,
               PT1=>Pt2Out,TI1=>Ti2Out);
        """)
        context = _pid_context()
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry(),
            dependencies={"license_context": context})
        other_context = _pid_context()
        other = runtime.build_runtime(
            compiled.task, runtime.build_default_registry(),
            dependencies={"license_context": other_context})
        runtime_p1 = assembly.executor._adapters["PLC_PRG.P1"].instance
        runtime_p2 = assembly.executor._adapters["PLC_PRG.P2"].instance
        self.assertIs(runtime_p1._ctx, context)
        self.assertIs(runtime_p2._ctx, context)
        self.assertIsNot(other.executor._adapters["PLC_PRG.P1"].instance._ctx, context)

        scans = tuple(
            {"AV1": 50.0, "SP1": 50.0, "PV1": 60.0 if tick < 10 else 50.0,
             "RM1": 1 if tick != 10 else 0,
             "AV2": 45.0, "SP2": 50.0, "PV2": 40.0 if tick < 10 else 50.0,
             "RM2": 1}
            for tick in range(12))
        trace = self._run(assembly, scans)
        direct_context = _pid_context()
        direct_one = APCPIDZZD(direct_context)
        direct_two = APCPIDZZD(direct_context)
        expected = []
        for values in scans:
            common = dict(PT=10.0, TI=20.0, PVMU=100.0, PVMD=0.0,
                          MU=100.0, MD=0.0, SADD=False, SSUB=False)
            direct_one.step(500, AV=values["AV1"], SP=values["SP1"],
                            PV=values["PV1"], RM=values["RM1"], **common)
            direct_two.step(500, AV=values["AV2"], SP=values["SP2"],
                            PV=values["PV2"], RM=values["RM2"], **common)
            expected.append((direct_one.PT1, direct_one.TI1,
                             direct_two.PT1, direct_two.TI1))
        self.assertEqual(
            [(row["PT1OUT"], row["TI1OUT"], row["PT2OUT"], row["TI2OUT"])
             for row in trace], expected)
        self.assertEqual((runtime_p1.TON1.ET_ms, runtime_p1.JSSJ, runtime_p1.JS_Z),
                         (direct_one.TON1.ET_ms, direct_one.JSSJ, direct_one.JS_Z))
        self.assertEqual((runtime_p2.TON1.ET_ms, runtime_p2.JSSJ, runtime_p2.JS_F),
                         (direct_two.TON1.ET_ms, direct_two.JSSJ, direct_two.JS_F))
        self.assertIsNot(runtime_p1.TON1, runtime_p2.TON1)

    def test_apcpidzzd_authorization_failure_then_recovery_is_shared_and_safe(self):
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL Av:REAL; Sp:REAL; Pv:REAL; Pt1:REAL; Ti1:REAL; END_VAR
            VAR P:APCPIDZZD; END_VAR
            P(AV:=Av,SP:=Sp,PV:=Pv,PT:=10.0,TI:=20.0,PVMU:=100.0,PVMD:=0.0,
              MU:=100.0,MD:=0.0,SADD:=FALSE,SSUB:=FALSE,PT1=>Pt1,TI1=>Ti1);
        """)
        context = _pid_context(authorized=False)
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry(),
            dependencies={"license_context": context})
        instance = assembly.executor._adapters["PLC_PRG.P"].instance
        self._run(assembly, ({"AV": 50.0, "SP": 50.0, "PV": 60.0},))
        self.assertEqual(context.BD_ERROR5, 1.0)
        self.assertEqual(instance.TON1.ET_ms, 0)
        authorized = _pid_context()
        context.set_passwords(authorized.BD_MM1, authorized.BD_MM2,
                              authorized.BD_MM3, authorized.BD_MM4)
        self._run(assembly, ({"AV": 50.0, "SP": 50.0, "PV": 60.0},))
        self.assertEqual(context.BD_ERROR5, 1.0)
        self.assertEqual(instance.TON1.ET_ms, 500)

    def test_apcpid_st_call_injects_shared_context_and_matches_direct_multiscan(self):
        """APCPID has no ST-side ctor syntax; its real nested PIDZZD shares ctx."""
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL Sp1:REAL; Pv1:REAL; Tp1:REAL; Ts1:BOOL; Rm1:INT;
                       Outt1:REAL; Outb1:REAL; Sadd1:BOOL; Ssub1:BOOL;
                       Pt1:REAL; Ti1:REAL; Av1:REAL;
                       Sp2:REAL; Pv2:REAL; Ic2:REAL; Oc2:REAL; Tp2:REAL; Ts2:BOOL;
                       Rm2:INT; Outt2:REAL; Outb2:REAL; Sadd2:BOOL; Ssub2:BOOL;
                       Pt2:REAL; Ti2:REAL; Kd2:REAL; Td2:REAL; Av2:REAL;
                       END_VAR
            VAR P1,P2:APCPID; END_VAR
            P1(SP:=Sp1,PV:=Pv1,TP:=Tp1,TS:=Ts1,RM:=Rm1,OutT:=Outt1,OutB:=Outb1,
               SADD:=Sadd1,SSUB:=Ssub1,PT:=Pt1,TI:=Ti1,AV=>Av1);
            P2(SP:=Sp2,PV:=Pv2,IC:=Ic2,OC:=Oc2,TP:=Tp2,TS:=Ts2,RM:=Rm2,
               OutT:=Outt2,OutB:=Outb2,SADD:=Sadd2,SSUB:=Ssub2,PT:=Pt2,TI:=Ti2,
               KD:=Kd2,TD:=Td2,AV=>Av2);
        """)
        self.assertEqual(len(library_source_aliases()), 22)
        for optional in ("IC", "OC", "KD", "TD"):
            self.assertNotIn(StoreVar("P1." + optional, "REAL"), compiled.code)
        for instance in compiled.task.pou_lib["PLC_PRG"].instances:
            self.assertEqual(instance.ctor_args, {})

        registry = runtime.build_default_registry()
        dependencies = {}
        with self.assertRaises(runtime.StartupValidationError) as failed:
            runtime.build_runtime(compiled.task, registry, dependencies=dependencies)
        self.assertIn("缺共享构造依赖 'license_context'", failed.exception.errors[0])
        self.assertEqual(dependencies, {})
        self.assertEqual(registry.keys(), runtime.build_default_registry().keys())

        context = _pid_context()
        calls = []
        original_step = context.KZQBDYZMK.step

        def counted_step(dt_ms):
            calls.append(dt_ms)
            return original_step(dt_ms)

        context.KZQBDYZMK.step = counted_step
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry(),
            dependencies={"license_context": context})
        other_context = _pid_context()
        other = runtime.build_runtime(
            compiled.task, runtime.build_default_registry(),
            dependencies={"license_context": other_context})
        runtime_one = assembly.executor._adapters["PLC_PRG.P1"].instance
        runtime_two = assembly.executor._adapters["PLC_PRG.P2"].instance
        self.assertIs(runtime_one._ctx, context)
        self.assertIs(runtime_two._ctx, context)
        self.assertIs(runtime_one.PIDZZD1._ctx, context)
        self.assertIs(runtime_two.PIDZZD1._ctx, context)
        self.assertIsNot(runtime_one, runtime_two)
        self.assertIsNot(runtime_one.PIDZZD1, runtime_two.PIDZZD1)
        self.assertIsNot(runtime_one.PIDZZD1.TON1, runtime_two.PIDZZD1.TON1)
        self.assertIsNot(other.executor._adapters["PLC_PRG.P1"].instance._ctx, context)

        scans = (
            {"SP1": 50.0, "PV1": 45.0, "TP1": 10.0, "TS1": False, "RM1": 1,
             "OUTT1": 100.0, "OUTB1": 0.0, "SADD1": False, "SSUB1": False,
             "PT1": 10.0, "TI1": 20.0,
             "SP2": 50.0, "PV2": 55.0, "IC2": 1.0, "OC2": 0.0, "TP2": 10.0,
             "TS2": False, "RM2": 1, "OUTT2": 100.0, "OUTB2": 0.0,
             "SADD2": False, "SSUB2": False, "PT2": 10.0, "TI2": 20.0,
             "KD2": 1.0, "TD2": 0.0},
            {"SP1": 50.0, "PV1": 45.0, "TP1": 20.0, "TS1": True, "RM1": 3,
             "OUTT1": 100.0, "OUTB1": 0.0, "SADD1": True, "SSUB1": False,
             "PT1": 10.0, "TI1": 20.0,
             "SP2": 50.0, "PV2": 55.0, "IC2": 0.0, "OC2": 2.0, "TP2": 10.0,
             "TS2": False, "RM2": 4, "OUTT2": 100.0, "OUTB2": 0.0,
             "SADD2": False, "SSUB2": True, "PT2": 10.0, "TI2": 20.0,
             "KD2": 2.0, "TD2": 1.0},
            {"SP1": 50.0, "PV1": 45.0, "TP1": 20.0, "TS1": False, "RM1": 0,
             "OUTT1": 100.0, "OUTB1": 0.0, "SADD1": False, "SSUB1": False,
             "PT1": 10.0, "TI1": 20.0,
             "SP2": 50.0, "PV2": 55.0, "IC2": 0.0, "OC2": 0.0, "TP2": 10.0,
             "TS2": True, "RM2": 1, "OUTT2": 100.0, "OUTB2": 0.0,
             "SADD2": False, "SSUB2": False, "PT2": 10.0, "TI2": 20.0,
             "KD2": 1.0, "TD2": 0.0},
        )
        trace = []
        runtime_states = []
        previous = assembly.store.snapshot()
        for values in scans:
            for key in sorted(values):
                assembly.store.write(key, values[key])
            assembly.executor.execute_programs(previous)
            previous = assembly.store.snapshot()
            trace.append(previous.as_dict())
            runtime_states.append((
                runtime_one.AV, runtime_two.AV, context.BD_ERROR1, context.BD_ERROR5,
                runtime_one.PT1, runtime_one.TI1,
                runtime_two.PT1, runtime_two.TI1,
                runtime_one.PIDZZD1.TON1.ET_ms,
                runtime_two.PIDZZD1.TON1.ET_ms,
            ))
        direct_context = _pid_context()
        direct_one, direct_two = APCPID(direct_context), APCPID(direct_context)
        expected = []
        for values in scans:
            direct_one.step(500, SP=values["SP1"], PV=values["PV1"], TP=values["TP1"],
                            TS=values["TS1"], RM=values["RM1"], OutT=values["OUTT1"],
                            OutB=values["OUTB1"], SADD=values["SADD1"], SSUB=values["SSUB1"],
                            PT=values["PT1"], TI=values["TI1"])
            direct_two.step(500, SP=values["SP2"], PV=values["PV2"], IC=values["IC2"],
                            OC=values["OC2"], TP=values["TP2"], TS=values["TS2"],
                            RM=values["RM2"], OutT=values["OUTT2"], OutB=values["OUTB2"],
                            SADD=values["SADD2"], SSUB=values["SSUB2"], PT=values["PT2"],
                            TI=values["TI2"], KD=values["KD2"], TD=values["TD2"])
            expected.append((
                direct_one.AV, direct_two.AV,
                direct_context.BD_ERROR1, direct_context.BD_ERROR5,
                direct_one.PT1, direct_one.TI1,
                direct_two.PT1, direct_two.TI1,
                direct_one.PIDZZD1.TON1.ET_ms,
                direct_two.PIDZZD1.TON1.ET_ms,
            ))
        self.assertEqual([(row["AV1"], row["AV2"]) for row in trace],
                         [(row[0], row[1]) for row in expected])
        self.assertEqual(runtime_states, expected)
        self.assertEqual(calls, [500] * 12)
        self.assertIsNot(runtime_one.PIDZZD1.JS_Z, runtime_two.PIDZZD1.JS_Z)

    def test_apcpid_authorization_failure_then_recovery_does_not_step_nested_pidzzd(self):
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL Sp:REAL; Pv:REAL; Tp:REAL; Ts:BOOL; Rm:INT; Outt:REAL;
                       Outb:REAL; Sadd:BOOL; Ssub:BOOL; Pt:REAL; Ti:REAL; Av:REAL;
                       END_VAR
            VAR P:APCPID; END_VAR
            P(SP:=Sp,PV:=Pv,TP:=Tp,TS:=Ts,RM:=Rm,OutT:=Outt,OutB:=Outb,
              SADD:=Sadd,SSUB:=Ssub,PT:=Pt,TI:=Ti,AV=>Av);
        """)
        context = _pid_context(authorized=False)
        calls = []
        original_step = context.KZQBDYZMK.step

        def counted_step(dt_ms):
            calls.append(dt_ms)
            return original_step(dt_ms)

        context.KZQBDYZMK.step = counted_step
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry(),
            dependencies={"license_context": context})
        instance = assembly.executor._adapters["PLC_PRG.P"].instance
        values = {"SP": 50.0, "PV": 60.0, "TP": 10.0, "TS": False, "RM": 1,
                  "OUTT": 100.0, "OUTB": 0.0, "SADD": False, "SSUB": False,
                  "PT": 10.0, "TI": 20.0}
        self._run(assembly, (values,))
        self.assertEqual(calls, [500])
        self.assertEqual(context.BD_ERROR1, 1.0)
        self.assertEqual(instance.PIDZZD1.TON1.ET_ms, 0)
        authorized = _pid_context()
        context.set_passwords(authorized.BD_MM1, authorized.BD_MM2,
                              authorized.BD_MM3, authorized.BD_MM4)
        self._run(assembly, (values,))
        self.assertEqual(calls, [500, 500, 500])
        self.assertEqual(context.BD_ERROR1, 1.0)
        self.assertEqual(instance.PIDZZD1.TON1.ET_ms, 500)

    def test_use_default_business_blocks_match_direct_calls_and_isolate_instances(self):
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL Drive:BOOL; I1:REAL; I2:REAL; Rs:BOOL;
                       En:BOOL; Pv:REAL; Fv:REAL; Pvh:REAL; Pvl:REAL;
                       Bhslh:REAL; Tl:REAL; Tc:REAL; Kg:REAL; Tb:REAL;
                       AccumAv1:LREAL; AccumSs1:BOOL; AccumAv2:LREAL; AccumSs2:BOOL;
                       HclAv:REAL; HclGzdv:BOOL; HclPvAvg:REAL; HclFvAvg:REAL;
                       END_VAR
            VAR A1,A2:APCHSACCUM; H:APCHXHCL; END_VAR
            IF Drive THEN
                A1(I1:=I1,RS:=Rs,AV=>AccumAv1,SS=>AccumSs1);
                H(EN:=En,PV:=Pv,FV:=Fv,PVH:=Pvh,PVL:=Pvl,BHSLH:=Bhslh,
                  TL:=Tl,TC:=Tc,KG:=Kg,TB:=Tb,AV=>HclAv,GZDV=>HclGzdv,
                  PV_AVG=>HclPvAvg,FV_AVG=>HclFvAvg);
            ELSE
                A1(AV=>AccumAv1,SS=>AccumSs1);
                H(EN:=En,PV:=Pv,FV:=Fv,AV=>HclAv,GZDV=>HclGzdv,
                  PV_AVG=>HclPvAvg,FV_AVG=>HclFvAvg);
            END_IF;
            A2(I1:=I2,AV=>AccumAv2,SS=>AccumSs2);
        """)
        self.assertEqual(len(runtime.build_default_registry().keys()), 22)
        self.assertIn(LoadVar("A1.AV", "LREAL"), compiled.code)
        self.assertIn(StoreVar("ACCUMAV1", "LREAL"), compiled.code)

        scans = (
            {"DRIVE": True, "I1": 5.0, "I2": 1.5, "RS": False,
             "EN": True, "PV": 10.0, "FV": 2.0, "PVH": 50.0, "PVL": -50.0,
             "BHSLH": 25.0, "TL": 5.0, "TC": 2.0, "KG": 2.0, "TB": 1.0},
            {"DRIVE": False, "I1": 99.0, "I2": 2.0, "RS": True,
             "EN": True, "PV": 12.0, "FV": 4.0, "PVH": 1.0, "PVL": -1.0,
             "BHSLH": 1.0, "TL": 1.0, "TC": 9.0, "KG": 9.0, "TB": 9.0},
            {"DRIVE": True, "I1": 3.0, "I2": -1.0, "RS": True,
             "EN": True, "PV": 16.0, "FV": 6.0, "PVH": 100.0, "PVL": -100.0,
             "BHSLH": 40.0, "TL": 4.0, "TC": 1.0, "KG": 1.5, "TB": 0.5},
        )
        assembly = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        trace = self._run(assembly, scans)

        direct_a1 = APCHSACCUM()
        direct_a2 = APCHSACCUM()
        direct_hcl = APCHXHCL()
        expected = []
        for values in scans:
            if values["DRIVE"]:
                a1 = direct_a1.step(500, I1=values["I1"], RS=values["RS"])
                hcl = direct_hcl.step(
                    500, EN=values["EN"], PV=values["PV"], FV=values["FV"],
                    PVH=values["PVH"], PVL=values["PVL"], BHSLH=values["BHSLH"],
                    TL=values["TL"], TC=values["TC"], KG=values["KG"], TB=values["TB"])
            else:
                a1 = direct_a1.step(500)
                hcl = direct_hcl.step(
                    500, EN=values["EN"], PV=values["PV"], FV=values["FV"])
            a2 = direct_a2.step(500, I1=values["I2"])
            expected.append((a1["AV"], a1["SS"], a2["AV"], a2["SS"],
                             hcl["AV"], hcl["GZDV"], hcl["PV_AVG"], hcl["FV_AVG"]))
        self.assertEqual(
            [(row["ACCUMAV1"], row["ACCUMSS1"], row["ACCUMAV2"],
              row["ACCUMSS2"], row["HCLAV"], row["HCLGZDV"],
              row["HCLPVAVG"], row["HCLFVAVG"]) for row in trace], expected)
        self.assertEqual(trace[1]["ACCUMAV1"], 5.0)
        self.assertNotEqual(
            [row["ACCUMAV1"] for row in trace], [row["ACCUMAV2"] for row in trace])

    def test_three_required_only_business_blocks_match_direct_multicycle_calls(self):
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL StatIn:REAL; StatReset:BOOL; StatMn:REAL; StatMx:REAL;
                       StatAvg:LREAL; FopIn:REAL; FopTc:REAL; FopKg:REAL;
                       FopTb:REAL; FopAv:REAL; RateIn:REAL; RateIn2:REAL;
                       RateHl:REAL; RateLl:REAL; RateAv:REAL; RateAv2:REAL;
                       END_VAR
            VAR S:APCSTATISTICS; F:APCHSFOP; R1,R2:APCHSRATELIM; END_VAR
            S(IN:=StatIn,RESET:=StatReset,MN=>StatMn,MX=>StatMx,AVG=>StatAvg);
            F(IN:=FopIn,TC:=FopTc,KG:=FopKg,TB:=FopTb,AV=>FopAv);
            R1(IN:=RateIn,HL:=RateHl,LL:=RateLl,AV=>RateAv);
            R2(IN:=RateIn2,HL:=RateHl,LL:=RateLl,AV=>RateAv2);
        """)
        self.assertEqual(len(runtime.build_default_registry().keys()), 22)
        self.assertIn(LoadVar("S.AVG", "LREAL"), compiled.code)
        self.assertIn(StoreVar("STATAVG", "LREAL"), compiled.code)

        direct_stats = APCSTATISTICS()
        direct_fop = APCHSFOP()
        direct_rate_one = APCHSRATELIM()
        direct_rate_two = APCHSRATELIM()
        scans = (
            {"STATIN": 2.0, "STATRESET": True, "FOPIN": 10.0,
             "FOPTC": 2.0, "FOPKG": 1.5, "FOPTB": 0.5,
             "RATEIN": 4.0, "RATEIN2": -2.0, "RATEHL": 1.0, "RATELL": 0.5},
            {"STATIN": 8.0, "STATRESET": False, "FOPIN": 2.0,
             "FOPTC": 2.0, "FOPKG": 1.5, "FOPTB": 0.5,
             "RATEIN": 7.0, "RATEIN2": -4.0, "RATEHL": 1.0, "RATELL": 0.5},
            {"STATIN": -1.0, "STATRESET": False, "FOPIN": 6.0,
             "FOPTC": 1.0, "FOPKG": 2.0, "FOPTB": 0.5,
             "RATEIN": 1.0, "RATEIN2": 3.0, "RATEHL": 2.0, "RATELL": 1.0},
        )
        assembly = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        trace = self._run(assembly, scans)
        expected = []
        for values in scans:
            stats = direct_stats.step(500, IN=values["STATIN"],
                                      RESET=values["STATRESET"])
            fop = direct_fop.step(500, IN=values["FOPIN"], TC=values["FOPTC"],
                                  KG=values["FOPKG"], TB=values["FOPTB"])
            rate_one = direct_rate_one.step(
                500, IN=values["RATEIN"], HL=values["RATEHL"], LL=values["RATELL"])
            rate_two = direct_rate_two.step(
                500, IN=values["RATEIN2"], HL=values["RATEHL"], LL=values["RATELL"])
            expected.append((stats["MN"], stats["MX"], stats["AVG"], fop["AV"],
                             rate_one["AV"], rate_two["AV"]))
        self.assertEqual(
            [(row["STATMN"], row["STATMX"], row["STATAVG"], row["FOPAV"],
              row["RATEAV"], row["RATEAV2"]) for row in trace], expected)
        self.assertNotEqual(
            [row["RATEAV"] for row in trace], [row["RATEAV2"] for row in trace])

    def test_apchshllim_required_only_matches_direct_clamp_and_shrink(self):
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL In:REAL; Hl:REAL; Ll:REAL; Av:REAL; END_VAR
            VAR H:APCHSHLLIM; END_VAR
            H(IN:=In,HL:=Hl,LL:=Ll,AV=>Av);
        """)
        self.assertEqual(len(runtime.build_default_registry().keys()), 22)
        self.assertIn(LoadVar("H.AV", "REAL"), compiled.code)
        self.assertIn(StoreVar("AV", "REAL"), compiled.code)

        scans = (
            {"IN": 5.0, "HL": 10.0, "LL": 0.0},     # in-range passthrough
            {"IN": 15.0, "HL": 10.0, "LL": 0.0},    # upper clamp
            {"IN": -7.0, "HL": 5.0, "LL": -3.0},    # negative range lower clamp
            {"IN": 0.5, "HL": 1.0, "LL": 8.0},      # LL>HL silent in-block shrink
        )
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        trace = self._run(assembly, scans)

        direct = APCHSHLLIM()
        expected = [
            direct.step(500, IN=values["IN"], HL=values["HL"], LL=values["LL"])["AV"]
            for values in scans]
        self.assertEqual([row["AV"] for row in trace], expected)
        self.assertEqual(expected, [5.0, 10.0, -3.0, 1.0])

    def test_apcgcq_required_only_matches_direct_multicycle_and_isolates_instances(self):
        compiled = runtime.compile_st_task("""
            VAR_GLOBAL In1:REAL; In2:REAL; Tc:REAL; Tz:REAL; K:REAL; Insp:REAL;
                       Gc1:REAL; Gc2:REAL; Outh:REAL; Outl:REAL; Outv:REAL;
                       Gcav1:REAL; Jtav1:REAL; Dtav1:REAL;
                       Gcav2:REAL; Jtav2:REAL; Dtav2:REAL; END_VAR
            VAR G1,G2:APCGCQ; END_VAR
            G1(IN:=In1,TC:=Tc,TZ:=Tz,K:=K,INSP:=Insp,GC1:=Gc1,GC2:=Gc2,
               OUTH:=Outh,OUTL:=Outl,OUTV:=Outv,
               GCAV=>Gcav1,JTAV=>Jtav1,DTAV=>Dtav1);
            G2(IN:=In2,TC:=Tc,TZ:=Tz,K:=K,INSP:=Insp,GC1:=Gc1,GC2:=Gc2,
               OUTH:=Outh,OUTL:=Outl,OUTV:=Outv,
               GCAV=>Gcav2,JTAV=>Jtav2,DTAV=>Dtav2);
        """)
        self.assertEqual(len(runtime.build_default_registry().keys()), 22)
        self.assertIn(LoadVar("G1.GCAV", "REAL"), compiled.code)
        self.assertIn(LoadVar("G2.DTAV", "REAL"), compiled.code)
        self.assertIn(StoreVar("GCAV1", "REAL"), compiled.code)

        # Enough scans (dt_ms = cycle 500ms) to drive BLINK01 -> R_TRIG1 rising
        # edges and the STATISTICS window that feed the FOP01 delta chain, with
        # the two instances advanced by different IN sequences on the same tick.
        scans = (
            {"IN1": 20.0, "IN2": -5.0, "TC": 0.5, "TZ": 1.0, "K": 1.0,
             "INSP": 4.0, "GC1": 1.5, "GC2": 0.5, "OUTH": 30.0, "OUTL": -30.0,
             "OUTV": 6.0},
            {"IN1": 24.0, "IN2": -1.0, "TC": 0.5, "TZ": 1.0, "K": 1.0,
             "INSP": 4.0, "GC1": 1.5, "GC2": 0.5, "OUTH": 30.0, "OUTL": -30.0,
             "OUTV": 6.0},
            {"IN1": 12.0, "IN2": 9.0, "TC": 0.5, "TZ": 1.0, "K": 1.0,
             "INSP": 4.0, "GC1": 1.5, "GC2": 0.5, "OUTH": 30.0, "OUTL": -30.0,
             "OUTV": 6.0},
            {"IN1": 28.0, "IN2": 3.0, "TC": 0.5, "TZ": 1.0, "K": 1.0,
             "INSP": 4.0, "GC1": 1.5, "GC2": 0.5, "OUTH": 30.0, "OUTL": -30.0,
             "OUTV": 6.0},
            {"IN1": 16.0, "IN2": 7.0, "TC": 0.5, "TZ": 1.0, "K": 1.0,
             "INSP": 4.0, "GC1": 1.5, "GC2": 0.5, "OUTH": 30.0, "OUTL": -30.0,
             "OUTV": 6.0},
            {"IN1": 22.0, "IN2": -2.0, "TC": 0.5, "TZ": 1.0, "K": 1.0,
             "INSP": 4.0, "GC1": 1.5, "GC2": 0.5, "OUTH": 30.0, "OUTL": -30.0,
             "OUTV": 6.0},
        )
        assembly = runtime.build_runtime(
            compiled.task, runtime.build_default_registry())
        trace = self._run(assembly, scans)

        direct_one = APCGCQ()
        direct_two = APCGCQ()
        expected = []
        for values in scans:
            common = dict(
                TC=values["TC"], TZ=values["TZ"], K=values["K"],
                INSP=values["INSP"], GC1=values["GC1"], GC2=values["GC2"],
                OUTH=values["OUTH"], OUTL=values["OUTL"], OUTV=values["OUTV"])
            one = direct_one.step(500, IN=values["IN1"], **common)
            two = direct_two.step(500, IN=values["IN2"], **common)
            expected.append((
                one["GCAV"], one["JTAV"], one["DTAV"],
                two["GCAV"], two["JTAV"], two["DTAV"]))
        self.assertEqual(
            [(row["GCAV1"], row["JTAV1"], row["DTAV1"],
              row["GCAV2"], row["JTAV2"], row["DTAV2"]) for row in trace],
            expected)
        # The two same-type instances (top level and their nested
        # BLINK01/R_TRIG1/STAT01/FOP01/RLIM01/LIM01 sub-blocks) never share
        # state: distinct IN sequences produce distinct JTAV traces.
        self.assertNotEqual(
            [row["JTAV1"] for row in trace], [row["JTAV2"] for row in trace])


if __name__ == "__main__":
    unittest.main()
