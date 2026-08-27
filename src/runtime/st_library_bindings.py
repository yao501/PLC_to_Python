"""Explicit Stage 3 ST aliases for 22 allow-listed blocks: eight primitives
and APCSTATISTICS, APCCD, APCHSFOP, APCHSRATELIM, APCHSACCUM, APCHXHCL,
APCHSHLLIM, APCGCQ, APCSPFINDER, APCPIDZZD, APCPID, APCRSFNAUTOPARA, and
APCMAUTOPARA, plus the APCM composite block.

The runtime engineering descriptors deliberately expose millisecond-suffixed
Python pins.  ST source uses the CODESYS names, so those differences are listed
one by one here.  This module never derives aliases by stripping suffixes.
"""


_PRIMITIVE_SOURCE_PIN_ALIASES = (
    ("TON", (
        ("IN", "IN"), ("PT", "PT_ms"), ("Q", "Q"), ("ET", "ET_ms"))),
    ("TOF", (
        ("IN", "IN"), ("PT", "PT_ms"), ("Q", "Q"), ("ET", "ET_ms"))),
    ("TP", (
        ("IN", "IN"), ("PT", "PT_ms"), ("Q", "Q"), ("ET", "ET_ms"))),
    ("R_TRIG", (("CLK", "CLK"), ("Q", "Q"))),
    ("F_TRIG", (("CLK", "CLK"), ("Q", "Q"))),
    ("SR", (("SET1", "SET1"), ("RESET", "RESET"), ("Q1", "Q1"))),
    ("RS", (("SET", "SET"), ("RESET1", "RESET1"), ("Q1", "Q1"))),
    ("BLINK", (
        ("ENABLE", "ENABLE"), ("TIMELOW", "TIMELOW_ms"),
        ("TIMEHIGH", "TIMEHIGH_ms"), ("OUT", "OUT"))),
)


# These business blocks are deliberately an allow-list, not a projection of the
# Registry.  The source and engineering spellings happen to match today, but
# each mapping remains explicit so a future descriptor rename cannot silently
# change the supported ST surface.
_LIBRARY_SOURCE_PIN_ALIASES = _PRIMITIVE_SOURCE_PIN_ALIASES + (
    ("APCSTATISTICS", (
        ("IN", "IN"), ("RESET", "RESET"),
        ("MN", "MN"), ("MX", "MX"), ("AVG", "AVG"))),
    ("APCCD", (
        ("SP", "SP"), ("PV", "PV"), ("TS", "TS"), ("TC", "TC"),
        ("TZ", "TZ"), ("CDH", "CDH"), ("CDL", "CDL"), ("TL", "TL"),
        ("CD_K_J", "CD_K_J"), ("CD_K_D", "CD_K_D"),
        ("CD_K_FD", "CD_K_FD"), ("CD_GD", "CD_GD"),
        ("CD_K", "CD_K"), ("AD", "AD"), ("ZLOUT", "ZLOUT"),
        ("AV", "AV"), ("CD_BH", "CD_BH"))),
    ("APCM", (
        ("SP", "SP"), ("PV", "PV"), ("OC", "OC"), ("TS", "TS"),
        ("TP", "TP"), ("RM", "RM"), ("OUTT", "OUTT"),
        ("OUTB", "OUTB"), ("SADD", "SADD"), ("SSUB", "SSUB"),
        ("ZLEN", "ZLEN"), ("ZSYK", "ZSYK"), ("ZLOUT", "ZLOUT"),
        ("AV", "AV"), ("AV_P", "AV_P"), ("AV_R", "AV_R"),
        ("AV_GC", "AV_GC"), ("AV_J", "AV_J"), ("AV_D", "AV_D"),
        ("AV_C", "AV_C"))),
    ("APCHSFOP", (
        ("IN", "IN"), ("TC", "TC"), ("KG", "KG"), ("TB", "TB"),
        ("AV", "AV"))),
    ("APCHSRATELIM", (
        ("IN", "IN"), ("HL", "HL"), ("LL", "LL"), ("AV", "AV"))),
    ("APCHSACCUM", (
        ("I1", "I1"), ("RS", "RS"), ("AV", "AV"), ("SS", "SS"))),
    ("APCHXHCL", (
        ("EN", "EN"), ("PV", "PV"), ("FV", "FV"), ("PVH", "PVH"),
        ("PVL", "PVL"), ("BHSLH", "BHSLH"), ("TL", "TL"), ("TC", "TC"),
        ("KG", "KG"), ("TB", "TB"), ("AV", "AV"), ("GZDV", "GZDV"),
        ("PV_AVG", "PV_AVG"), ("FV_AVG", "FV_AVG"))),
    ("APCHSHLLIM", (
        ("IN", "IN"), ("HL", "HL"), ("LL", "LL"), ("AV", "AV"))),
    ("APCGCQ", (
        ("IN", "IN"), ("TC", "TC"), ("TZ", "TZ"), ("K", "K"),
        ("INSP", "INSP"), ("GC1", "GC1"), ("GC2", "GC2"), ("OUTH", "OUTH"),
        ("OUTL", "OUTL"), ("OUTV", "OUTV"), ("GCAV", "GCAV"),
        ("JTAV", "JTAV"), ("DTAV", "DTAV"))),
    ("APCSPFINDER", (
        ("EN", "EN"), ("RESET", "RESET"), ("CYCLE", "CYCLE"),
        ("SAMPLE_OK", "SAMPLE_OK"), ("SP_MAN", "SP_MAN"),
        ("SP_MAN_EN", "SP_MAN_EN"), ("SP_TAG", "SP_TAG"),
        ("SP_TAG_EN", "SP_TAG_EN"), ("SP_AUTO_EN", "SP_AUTO_EN"),
        ("SP_AUTO_REPLACE_BAD_TAG", "SP_AUTO_REPLACE_BAD_TAG"), ("PV", "PV"),
        ("AV", "AV"), ("PVMU", "PVMU"), ("PVMD", "PVMD"),
        ("OUTT", "OUTT"), ("OUTB", "OUTB"),
        ("SP_STABLE_T", "SP_STABLE_T"), ("SP_CONF_T", "SP_CONF_T"),
        ("PV_STABLE_K", "PV_STABLE_K"), ("AV_STABLE_K", "AV_STABLE_K"),
        ("PV_STABLE_ABS", "PV_STABLE_ABS"), ("AV_STABLE_ABS", "AV_STABLE_ABS"),
        ("SP_BAD_K", "SP_BAD_K"), ("SP_BAD_ABS", "SP_BAD_ABS"),
        ("SP_USE", "SP_USE"), ("SP_VALID", "SP_VALID"),
        ("SP_SOURCE", "SP_SOURCE"), ("SP_REASON", "SP_REASON"),
        ("SP_AUTO", "SP_AUTO"), ("SP_AUTO_OK", "SP_AUTO_OK"),
        ("SP_AUTO_CONF", "SP_AUTO_CONF"), ("SP_TAG_BAD", "SP_TAG_BAD"),
        ("SP_STABLE_T_OUT", "SP_STABLE_T_OUT"),
        ("SP_STABLE_PV_RANGE", "SP_STABLE_PV_RANGE"))),
    ("APCPIDZZD", (
        ("AV", "AV"), ("SP", "SP"), ("PV", "PV"), ("PT", "PT"),
        ("TI", "TI"), ("RM", "RM"), ("PVMU", "PVMU"), ("PVMD", "PVMD"),
        ("MU", "MU"), ("MD", "MD"), ("SADD", "SADD"), ("SSUB", "SSUB"),
        ("PT1K", "PT1K"), ("TI1K", "TI1K"), ("PT1", "PT1"), ("TI1", "TI1"))),
    ("APCPID", (
        ("SP", "SP"), ("PV", "PV"), ("IC", "IC"), ("OC", "OC"),
        ("TP", "TP"), ("TS", "TS"), ("RM", "RM"), ("OUTT", "OutT"),
        ("OUTB", "OutB"), ("SADD", "SADD"), ("SSUB", "SSUB"),
        ("PT", "PT"), ("TI", "TI"), ("KD", "KD"), ("TD", "TD"),
        ("AV", "AV"))),
    ("APCRSFNAUTOPARA", (
        ("EN", "EN"), ("RESET", "RESET"), ("CALC_NOW", "CALC_NOW"),
        ("CYCLE", "CYCLE"), ("COLLECT_MODE", "COLLECT_MODE"),
        ("SP", "SP"), ("SP_MAN", "SP_MAN"), ("SP_MAN_EN", "SP_MAN_EN"),
        ("SP_TAG_EN", "SP_TAG_EN"), ("SP_AUTO_EN", "SP_AUTO_EN"),
        ("SP_AUTO_REPLACE_BAD_TAG", "SP_AUTO_REPLACE_BAD_TAG"),
        ("SP_STABLE_T", "SP_STABLE_T"), ("SP_CONF_T", "SP_CONF_T"),
        ("SP_PV_STABLE_ABS", "SP_PV_STABLE_ABS"),
        ("SP_AV_STABLE_ABS", "SP_AV_STABLE_ABS"),
        ("PV", "PV"), ("AV", "AV"), ("TP", "TP"), ("TS", "TS"),
        ("MU", "MU"), ("MD", "MD"),
        ("PHY_RANGE_EN", "PHY_RANGE_EN"), ("PHY_MU", "PHY_MU"),
        ("PHY_MD", "PHY_MD"), ("RSF_LEVEL", "RSF_LEVEL"),
        ("RSF_LOCK_LEVEL_IN", "RSF_LOCK_LEVEL_IN"),
        ("RSF_STEP", "RSF_STEP"), ("WIN_T", "WIN_T"),
        ("MIN_WIN_T", "MIN_WIN_T"),
        ("MIN_STORE_EVENT", "MIN_STORE_EVENT"),
        ("MIN_VALID_EVENT", "MIN_VALID_EVENT"), ("HISTORY_N", "HISTORY_N"),
        ("FUSE_MIN_N", "FUSE_MIN_N"),
        ("FUSE_MIN_WEIGHT", "FUSE_MIN_WEIGHT"),
        ("SIM_SP_K", "SIM_SP_K"), ("SIM_PV_K", "SIM_PV_K"),
        ("SIM_AV_K", "SIM_AV_K"), ("SIM_ERR_K", "SIM_ERR_K"),
        ("SIM_SP_ABS", "SIM_SP_ABS"), ("SIM_PV_ABS", "SIM_PV_ABS"),
        ("SIM_AV_ABS", "SIM_AV_ABS"), ("SIM_ERR_ABS", "SIM_ERR_ABS"),
        ("SIM_RELAX_K", "SIM_RELAX_K"), ("MAN_AV_MIN", "MAN_AV_MIN"),
        ("AO_GAIN_K", "AO_GAIN_K"), ("REC_BLEND", "REC_BLEND"),
        ("TL_IN", "TL_IN"), ("TL1_IN", "TL1_IN"),
        ("TL2_IN", "TL2_IN"), ("TL3_IN", "TL3_IN"),
        ("TL4_IN", "TL4_IN"), ("E1_IN", "E1_IN"),
        ("E2_IN", "E2_IN"), ("E3_IN", "E3_IN"),
        ("E4_IN", "E4_IN"), ("AO1_IN", "AO1_IN"),
        ("AO2_IN", "AO2_IN"), ("AO3_IN", "AO3_IN"),
        ("AO4_IN", "AO4_IN"), ("RSF_LOCK_T_IN", "RSF_LOCK_T_IN"),
        ("RSF_HYS_IN", "RSF_HYS_IN"),
        ("RSF_FAST_HYS_IN", "RSF_FAST_HYS_IN"),
        ("RSF_TLOUT_K_IN", "RSF_TLOUT_K_IN"), ("ZF_K_IN", "ZF_K_IN"),
        ("RUNNING", "RUNNING"), ("WINDOW_DONE", "WINDOW_DONE"),
        ("FINAL_VALID", "FINAL_VALID"), ("FINAL_STRONG", "FINAL_STRONG"),
        ("FINAL_WEAK", "FINAL_WEAK"), ("MATCH_LEVEL", "MATCH_LEVEL"),
        ("WINDOW_VALID", "WINDOW_VALID"), ("DATA_REASON", "DATA_REASON"),
        ("SP_USE", "SP_USE"), ("SP_AUTO", "SP_AUTO"),
        ("SP_VALID", "SP_VALID"), ("SP_AUTO_OK", "SP_AUTO_OK"),
        ("SP_TAG_BAD", "SP_TAG_BAD"), ("SP_SOURCE", "SP_SOURCE"),
        ("SP_REASON", "SP_REASON"), ("SP_AUTO_CONF", "SP_AUTO_CONF"),
        ("SP_STABLE_T_OUT", "SP_STABLE_T_OUT"), ("RSF_OK", "RSF_OK"),
        ("RSF_REASON", "RSF_REASON"), ("HISTORY_COUNT", "HISTORY_COUNT"),
        ("SIMILAR_COUNT", "SIMILAR_COUNT"), ("FUSE_WEIGHT", "FUSE_WEIGHT"),
        ("WINDOW_EVENT_N", "WINDOW_EVENT_N"), ("WINDOW_T", "WINDOW_T"),
        ("AUTO_SAMPLE_T", "AUTO_SAMPLE_T"), ("MAN_EVENT_N", "MAN_EVENT_N"),
        ("CROSS_COUNT", "CROSS_COUNT"), ("RSF_TRIGGER_N", "RSF_TRIGGER_N"),
        ("RSF_LOCK_N", "RSF_LOCK_N"), ("ERR_ABS_AVG", "ERR_ABS_AVG"),
        ("ERR_AREA_POS", "ERR_AREA_POS"), ("ERR_AREA_NEG", "ERR_AREA_NEG"),
        ("ERR_PEAK_ABS", "ERR_PEAK_ABS"), ("AVG_CROSS_T", "AVG_CROSS_T"),
        ("PV_DELTA", "PV_DELTA"), ("AV_DELTA", "AV_DELTA"),
        ("NOISE_EST", "NOISE_EST"), ("PROCESS_GAIN", "PROCESS_GAIN"),
        ("TL_REC", "TL_REC"), ("TL1_REC", "TL1_REC"),
        ("TL2_REC", "TL2_REC"), ("TL3_REC", "TL3_REC"),
        ("TL4_REC", "TL4_REC"), ("E1_REC", "E1_REC"),
        ("E2_REC", "E2_REC"), ("E3_REC", "E3_REC"),
        ("E4_REC", "E4_REC"), ("AO1_REC", "AO1_REC"),
        ("AO2_REC", "AO2_REC"), ("AO3_REC", "AO3_REC"),
        ("AO4_REC", "AO4_REC"), ("RSF_LOCK_T_REC", "RSF_LOCK_T_REC"),
        ("RSF_HYS_REC", "RSF_HYS_REC"),
        ("RSF_FAST_HYS_REC", "RSF_FAST_HYS_REC"),
        ("RSF_TLOUT_K_REC", "RSF_TLOUT_K_REC"), ("ZF_K_REC", "ZF_K_REC"))),
    ("APCMAUTOPARA", (
        ("EN", "EN"), ("RESET", "RESET"), ("CALC_NOW", "CALC_NOW"),
        ("CYCLE", "CYCLE"), ("COLLECT_MODE", "COLLECT_MODE"), ("SP", "SP"),
        ("SP_MAN", "SP_MAN"), ("SP_MAN_EN", "SP_MAN_EN"), ("SP_TAG_EN", "SP_TAG_EN"),
        ("SP_AUTO_EN", "SP_AUTO_EN"), ("SP_AUTO_REPLACE_BAD_TAG", "SP_AUTO_REPLACE_BAD_TAG"), ("SP_STABLE_T", "SP_STABLE_T"),
        ("SP_CONF_T", "SP_CONF_T"), ("SP_PV_STABLE_ABS", "SP_PV_STABLE_ABS"), ("SP_AV_STABLE_ABS", "SP_AV_STABLE_ABS"),
        ("PV", "PV"), ("AV", "AV"), ("RM", "RM"),
        ("TS", "TS"), ("PVMU", "PVMU"), ("PVMD", "PVMD"),
        ("MU", "MU"), ("MD", "MD"), ("OUTT", "OUTT"),
        ("OUTB", "OUTB"), ("WIN_T", "WIN_T"), ("MIN_WIN_T", "MIN_WIN_T"),
        ("MIN_STORE_EVENT", "MIN_STORE_EVENT"), ("MIN_VALID_EVENT", "MIN_VALID_EVENT"), ("HISTORY_N", "HISTORY_N"),
        ("FUSE_MIN_N", "FUSE_MIN_N"), ("FUSE_MIN_WEIGHT", "FUSE_MIN_WEIGHT"), ("SIM_SP_K", "SIM_SP_K"),
        ("SIM_PV_K", "SIM_PV_K"), ("SIM_AV_K", "SIM_AV_K"), ("SIM_ERR_K", "SIM_ERR_K"),
        ("SIM_SP_ABS", "SIM_SP_ABS"), ("SIM_PV_ABS", "SIM_PV_ABS"), ("SIM_AV_ABS", "SIM_AV_ABS"),
        ("SIM_ERR_ABS", "SIM_ERR_ABS"), ("SIM_RELAX_K", "SIM_RELAX_K"), ("MAN_MERGE_T", "MAN_MERGE_T"),
        ("MAN_RESP_T", "MAN_RESP_T"), ("MAN_RESP_T_MAX", "MAN_RESP_T_MAX"), ("MAN_AV_MIN", "MAN_AV_MIN"),
        ("PT_IN", "PT_IN"), ("TI_IN", "TI_IN"), ("TD_IN", "TD_IN"),
        ("DI_IN", "DI_IN"), ("SVH_IN", "SVH_IN"), ("SVL_IN", "SVL_IN"),
        ("PID_FORMULA_EN", "PID_FORMULA_EN"), ("PID_LAMBDA_K", "PID_LAMBDA_K"), ("PID_MODEL_L_K", "PID_MODEL_L_K"),
        ("PID_FORMULA_BLEND", "PID_FORMULA_BLEND"), ("TL_IN", "TL_IN"), ("TL1_IN", "TL1_IN"),
        ("TL2_IN", "TL2_IN"), ("TL3_IN", "TL3_IN"), ("TL4_IN", "TL4_IN"),
        ("E1_IN", "E1_IN"), ("E2_IN", "E2_IN"), ("E3_IN", "E3_IN"),
        ("E4_IN", "E4_IN"), ("AO1_IN", "AO1_IN"), ("AO2_IN", "AO2_IN"),
        ("AO3_IN", "AO3_IN"), ("AO4_IN", "AO4_IN"), ("RSF_LOCK_T_IN", "RSF_LOCK_T_IN"),
        ("TC_IN", "TC_IN"), ("TZ_IN", "TZ_IN"), ("GC1_IN", "GC1_IN"),
        ("GC2_IN", "GC2_IN"), ("OUTH_IN", "OUTH_IN"), ("OUTL_IN", "OUTL_IN"),
        ("CD_GD_IN", "CD_GD_IN"), ("CD_K_IN", "CD_K_IN"), ("CD_K_FD_IN", "CD_K_FD_IN"),
        ("CD_K_J_IN", "CD_K_J_IN"), ("CD_K_D_IN", "CD_K_D_IN"), ("CDH_IN", "CDH_IN"),
        ("CDL_IN", "CDL_IN"), ("TC_CD_IN", "TC_CD_IN"), ("TZ_CD_IN", "TZ_CD_IN"),
        ("RUNNING", "RUNNING"), ("WINDOW_DONE", "WINDOW_DONE"), ("FINAL_VALID", "FINAL_VALID"),
        ("FINAL_STRONG", "FINAL_STRONG"), ("FINAL_WEAK", "FINAL_WEAK"), ("MATCH_LEVEL", "MATCH_LEVEL"),
        ("WINDOW_VALID", "WINDOW_VALID"), ("DATA_REASON", "DATA_REASON"), ("SP_USE", "SP_USE"),
        ("SP_AUTO", "SP_AUTO"), ("SP_VALID", "SP_VALID"), ("SP_AUTO_OK", "SP_AUTO_OK"),
        ("SP_TAG_BAD", "SP_TAG_BAD"), ("SP_SOURCE", "SP_SOURCE"), ("SP_REASON", "SP_REASON"),
        ("SP_AUTO_CONF", "SP_AUTO_CONF"), ("SP_STABLE_T_OUT", "SP_STABLE_T_OUT"), ("PID_OK", "PID_OK"),
        ("RSF_OK", "RSF_OK"), ("GC_OK", "GC_OK"), ("CD_OK", "CD_OK"),
        ("PID_REASON", "PID_REASON"), ("RSF_REASON", "RSF_REASON"), ("GC_REASON", "GC_REASON"),
        ("CD_REASON", "CD_REASON"), ("HISTORY_COUNT", "HISTORY_COUNT"), ("SIMILAR_COUNT", "SIMILAR_COUNT"),
        ("FUSE_WEIGHT", "FUSE_WEIGHT"), ("WINDOW_EVENT_N", "WINDOW_EVENT_N"), ("WINDOW_T", "WINDOW_T"),
        ("AUTO_SAMPLE_T", "AUTO_SAMPLE_T"), ("MAN_EVENT_N", "MAN_EVENT_N"), ("MAN_RESP_T_AUTO", "MAN_RESP_T_AUTO"),
        ("MAN_RESP_T_USE", "MAN_RESP_T_USE"), ("CROSS_COUNT", "CROSS_COUNT"), ("ERR_ABS_AVG", "ERR_ABS_AVG"),
        ("ERR_AREA_POS", "ERR_AREA_POS"), ("ERR_AREA_NEG", "ERR_AREA_NEG"), ("ERR_PEAK_ABS", "ERR_PEAK_ABS"),
        ("AVG_CROSS_T", "AVG_CROSS_T"), ("PV_DELTA", "PV_DELTA"), ("AV_DELTA", "AV_DELTA"),
        ("NOISE_EST", "NOISE_EST"), ("PROCESS_GAIN", "PROCESS_GAIN"), ("PT_REC", "PT_REC"),
        ("TI_REC", "TI_REC"), ("TD_REC", "TD_REC"), ("DI_REC", "DI_REC"),
        ("SVH_REC", "SVH_REC"), ("SVL_REC", "SVL_REC"), ("PID_FORMULA_VALID", "PID_FORMULA_VALID"),
        ("PT_FORMULA_REC", "PT_FORMULA_REC"), ("TI_FORMULA_REC", "TI_FORMULA_REC"), ("PID_MODEL_GAIN_REC", "PID_MODEL_GAIN_REC"),
        ("PID_MODEL_T_REC", "PID_MODEL_T_REC"), ("PID_MODEL_L_REC", "PID_MODEL_L_REC"), ("PID_MODEL_LAMBDA_REC", "PID_MODEL_LAMBDA_REC"),
        ("PID_FORMULA_BLEND_REC", "PID_FORMULA_BLEND_REC"), ("TL_REC", "TL_REC"), ("TL1_REC", "TL1_REC"),
        ("TL2_REC", "TL2_REC"), ("TL3_REC", "TL3_REC"), ("TL4_REC", "TL4_REC"),
        ("E1_REC", "E1_REC"), ("E2_REC", "E2_REC"), ("E3_REC", "E3_REC"),
        ("E4_REC", "E4_REC"), ("AO1_REC", "AO1_REC"), ("AO2_REC", "AO2_REC"),
        ("AO3_REC", "AO3_REC"), ("AO4_REC", "AO4_REC"), ("RSF_LOCK_T_REC", "RSF_LOCK_T_REC"),
        ("TC_REC", "TC_REC"), ("TZ_REC", "TZ_REC"), ("GC1_REC", "GC1_REC"),
        ("GC2_REC", "GC2_REC"), ("OUTH_REC", "OUTH_REC"), ("OUTL_REC", "OUTL_REC"),
        ("CD_GD_REC", "CD_GD_REC"), ("CD_K_REC", "CD_K_REC"), ("CD_K_FD_REC", "CD_K_FD_REC"),
        ("CD_K_J_REC", "CD_K_J_REC"), ("CD_K_D_REC", "CD_K_D_REC"), ("CDH_REC", "CDH_REC"),
        ("CDL_REC", "CDL_REC"), ("TC_CD_REC", "TC_CD_REC"), ("TZ_CD_REC", "TZ_CD_REC"),
    )),
)


def library_source_aliases():
    """Return fresh explicit source-to-engineering aliases for ST libraries.

    This is an internal Stage 3 allow-list; registered blocks not present here
    remain unavailable to source ST until a separately reviewed mapping exists.
    """

    return {
        block_type: dict(pairs)
        for block_type, pairs in _LIBRARY_SOURCE_PIN_ALIASES
    }


def primitive_source_aliases():
    """Return a fresh, canonical source-name mapping for every primitive."""

    return {
        block_type: dict(pairs)
        for block_type, pairs in _PRIMITIVE_SOURCE_PIN_ALIASES
    }


__all__ = ["library_source_aliases", "primitive_source_aliases"]
