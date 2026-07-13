"""测试用程序构造器（双前端合流程序 + 5 个语义敏感案例的任务装配）。"""
from __future__ import annotations

from . import frontends as fe
from .ir import (Binding, InstanceDecl, IOMap, OutputPolicy, POUDefinition,
                 ProgramInstance, Task, VarDecl)

CYCLE_MS = 500


# ---------------------------------------------------------------- 双前端合流程序（ENGINE_SCAN_SPEC §8 蓝本）
# 逻辑：Start 经 5 秒延时且未 Stop 则 Motor 置位。

def motor_st_instrs() -> list:
    """ST 源（概念）：TON1(IN := Start, PT := T#5s); Motor := TON1.Q AND NOT Stop;"""
    return fe.lower_st([
        fe.FBCall("TON1", [
            ("IN", "BOOL", fe.Var("Start", "BOOL")),
            ("PT", "TIME", fe.Const(5000, "TIME")),
        ]),
        fe.Assign("Motor", "BOOL",
                  fe.Bin("AND",
                         fe.Var("TON1.Q", "BOOL"),
                         fe.Not(fe.Var("Stop", "BOOL")),
                         "BOOL")),
    ])


def motor_cfc_instrs() -> list:
    """CFC 源：保留执行序号 1:TON1、2:AND（D3 导入不重新推断）。"""
    return fe.lower_cfc(fe.CFCGraph(nodes=[
        fe.CFCBlockNode(inst="TON1", order=1, inputs=[
            ("IN", "BOOL", fe.CFCSource("var", "Start", "BOOL")),
            ("PT", "TIME", fe.CFCSource("const", 5000, "TIME")),
        ]),
        fe.CFCOperatorNode(op="AND", order=2, type="BOOL", inputs=[
            (fe.CFCSource("pin", "TON1.Q", "BOOL"), False),
            (fe.CFCSource("var", "Stop", "BOOL"), True),   # 取反输入脚
        ], sink=("Motor", "BOOL")),
    ]))


def motor_task(instrs: list) -> Task:
    return Task(
        cycle_ms=CYCLE_MS,
        programs=[ProgramInstance("PLC_PRG", instrs)],
        gvl=[
            VarDecl("Start", "BOOL", False, section="VAR_GLOBAL"),
            VarDecl("Stop", "BOOL", False, section="VAR_GLOBAL"),
            VarDecl("Motor", "BOOL", False, section="VAR_GLOBAL"),
        ],
        io_map=[IOMap("Motor", "DO_Motor", "OUT",
                      OutputPolicy(var="Motor", iec_type="BOOL", safe_value=False))],
        instances=[InstanceDecl("TON1", "TON", kind="library")],
    )


# ---------------------------------------------------------------- 案例①：整数中间位宽/存储截断

def int_trunc_task() -> Task:
    """C := (A + B) / 2（WORD，中间 90000 溢出 16 位）；D := DINT 常量 70000 存 WORD。"""
    code = fe.lower_st([
        fe.Assign("C", "WORD",
                  fe.Bin("DIV",
                         fe.Bin("ADD", fe.Var("A", "WORD"), fe.Var("B", "WORD"), "WORD"),
                         fe.Const(2, "WORD"), "WORD")),
        fe.Assign("D", "WORD", fe.Const(70000, "DINT")),   # 赋值转换 → CONVERT DINT->WORD
    ])
    return Task(
        cycle_ms=CYCLE_MS,
        programs=[ProgramInstance("INT_PRG", code)],
        gvl=[
            VarDecl("A", "WORD", 60000, section="VAR_GLOBAL"),
            VarDecl("B", "WORD", 30000, section="VAR_GLOBAL"),
            VarDecl("C", "WORD", section="VAR_GLOBAL"),
            VarDecl("D", "WORD", section="VAR_GLOBAL"),
        ],
    )


# ---------------------------------------------------------------- 案例②：REAL/F1 量化

def real_quant_task() -> Task:
    """R := RB + RC；RB=16777216.0（2^24），RC=1.0：binary32 下 +1 丢失，float64 不丢。"""
    code = fe.lower_st([
        fe.Assign("R", "REAL",
                  fe.Bin("ADD", fe.Var("RB", "REAL"), fe.Var("RC", "REAL"), "REAL")),
    ])
    return Task(
        cycle_ms=CYCLE_MS,
        programs=[ProgramInstance("REAL_PRG", code)],
        gvl=[
            VarDecl("RB", "REAL", 16777216.0, section="VAR_GLOBAL"),
            VarDecl("RC", "REAL", 1.0, section="VAR_GLOBAL"),
            VarDecl("R", "REAL", section="VAR_GLOBAL"),
        ],
    )


# ---------------------------------------------------------------- 用户 FB 定义

def counter_fb_def() -> POUDefinition:
    """CounterFB：IF ENABLE THEN N := N + 1; END_IF; CNT := N;（实例内存 + JMP 覆盖）"""
    return POUDefinition(
        name="CounterFB",
        pou_kind="FUNCTION_BLOCK",
        interface=[
            VarDecl("ENABLE", "BOOL", section="VAR_INPUT"),
            VarDecl("CNT", "INT", section="VAR_OUTPUT"),
        ],
        locals=[VarDecl("N", "INT", 0)],
        code=fe.lower_st([
            fe.If(fe.Var("self.ENABLE", "BOOL"), [
                fe.Assign("self.N", "INT",
                          fe.Bin("ADD", fe.Var("self.N", "INT"), fe.Const(1, "INT"), "INT")),
            ]),
            fe.Assign("self.CNT", "INT", fe.Var("self.N", "INT")),
        ]),
    )


def accum_fb_def() -> POUDefinition:
    """AccumFB：ACC := ACC + INC；ACC 是 VAR_IN_OUT（别名，写透到调用方，无 OUT 拷回）。"""
    return POUDefinition(
        name="AccumFB",
        pou_kind="FUNCTION_BLOCK",
        interface=[
            VarDecl("INC", "REAL", section="VAR_INPUT"),
            VarDecl("ACC", "REAL", section="VAR_IN_OUT"),
        ],
        code=fe.lower_st([
            fe.Assign("self.ACC", "REAL",
                      fe.Bin("ADD", fe.Var("self.ACC", "REAL"), fe.Var("self.INC", "REAL"),
                             "REAL")),
        ]),
    )


# ---------------------------------------------------------------- 案例③：VAR_IN_OUT 写透

def var_in_out_task() -> Task:
    code = fe.lower_st([
        fe.FBInstanceCall("ACC1", [
            Binding("INC", "IN", "const", 2.5, "REAL"),
            Binding("ACC", "INOUT", "var", "Total", "REAL"),
        ]),
    ])
    return Task(
        cycle_ms=CYCLE_MS,
        programs=[ProgramInstance("ACC_PRG", code)],
        gvl=[VarDecl("Total", "REAL", 0.0, section="VAR_GLOBAL")],
        instances=[InstanceDecl("ACC1", "AccumFB", kind="user_fb")],
        pou_lib={"AccumFB": accum_fb_def()},
    )


# ---------------------------------------------------------------- 案例⑤：双 FB 实例隔离

def isolation_task() -> Task:
    """同一定义两份实例 CNT_A / CNT_B + 两份 TON 实例，分别驱动。"""
    code = fe.lower_st([
        fe.FBInstanceCall("CNT_A", [
            Binding("ENABLE", "IN", "var", "EnA", "BOOL"),
            Binding("CNT", "OUT", "var", "CntA", "INT"),
        ]),
        fe.FBInstanceCall("CNT_B", [
            Binding("ENABLE", "IN", "var", "EnB", "BOOL"),
            Binding("CNT", "OUT", "var", "CntB", "INT"),
        ]),
        fe.FBCall("T_A", [("IN", "BOOL", fe.Var("EnA", "BOOL")),
                          ("PT", "TIME", fe.Const(1000, "TIME"))]),
        fe.FBCall("T_B", [("IN", "BOOL", fe.Var("EnA", "BOOL")),
                          ("PT", "TIME", fe.Const(2000, "TIME"))]),
    ])
    return Task(
        cycle_ms=CYCLE_MS,
        programs=[ProgramInstance("ISO_PRG", code)],
        gvl=[
            VarDecl("EnA", "BOOL", False, section="VAR_GLOBAL"),
            VarDecl("EnB", "BOOL", False, section="VAR_GLOBAL"),
            VarDecl("CntA", "INT", 0, section="VAR_GLOBAL"),
            VarDecl("CntB", "INT", 0, section="VAR_GLOBAL"),
        ],
        instances=[
            InstanceDecl("CNT_A", "CounterFB", kind="user_fb"),
            InstanceDecl("CNT_B", "CounterFB", kind="user_fb"),
            InstanceDecl("T_A", "TON", kind="library"),
            InstanceDecl("T_B", "TON", kind="library"),
        ],
        pou_lib={"CounterFB": counter_fb_def()},
    )


# ---------------------------------------------------------------- 案例④：输出三路径（shadow / 扫描异常 / 提交失败）

def output_paths_task() -> Task:
    """Motor_req := InMotor；AV_req := InAV；FaultTrig 为真时触发除零 → scan_fault。"""
    code = fe.lower_st([
        fe.Assign("Motor_req", "BOOL", fe.Var("InMotor", "BOOL")),
        fe.Assign("AV_req", "REAL", fe.Var("InAV", "REAL")),
        fe.If(fe.Var("FaultTrig", "BOOL"), [
            fe.Assign("X", "INT",
                      fe.Bin("DIV", fe.Const(1, "INT"), fe.Const(0, "INT"), "INT")),
        ]),
    ])
    return Task(
        cycle_ms=CYCLE_MS,
        programs=[ProgramInstance("OUT_PRG", code)],
        gvl=[
            VarDecl("InMotor", "BOOL", False, section="VAR_GLOBAL"),
            VarDecl("InAV", "REAL", 0.0, section="VAR_GLOBAL"),
            VarDecl("FaultTrig", "BOOL", False, section="VAR_GLOBAL"),
            VarDecl("Motor_req", "BOOL", False, section="VAR_GLOBAL"),
            VarDecl("AV_req", "REAL", 0.0, section="VAR_GLOBAL"),
            VarDecl("X", "INT", 0, section="VAR_GLOBAL"),
        ],
        io_map=[
            IOMap("Motor_req", "DO1", "OUT",
                  OutputPolicy(var="Motor_req", iec_type="BOOL", safe_value=False)),
            IOMap("AV_req", "AO1", "OUT",
                  OutputPolicy(var="AV_req", iec_type="REAL", safe_value=5.0,
                               rate_limit=10.0, on_operator_disable="hold")),
        ],
    )
