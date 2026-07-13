"""扫描引擎（ENGINE_SCAN_SPEC v2.2.1 §3/§4 的原型子集）。

一拍：① 输入锁存 → ②③ 逐条执行 IR → ④ 按 OutputPolicy 生成最终值 → ⑤ 一次性提交
（shadow 只算不写）→ prev 快照。

- 扫描异常由 run_scan（runner 层）捕获：置 scan_fault → 全部输出按 on_scan_fault（强制
  safe）生成 → 走同一 commit（§4.3，安全值提交不依赖本拍扫描逻辑活着）。
- 输出两层状态：last_effective（每拍第 4 步后更新，shadow 也更新）/
  last_physical_committed（仅第 5 步写设备成功后更新）（§4.1）。
- 提交失败固定行为（§4.4）：告警、lpc 不更新、下一拍起持续写 safe_value 直至成功，
  连续 commit_fault_retry_n 拍失败升级通道级故障；逐通道隔离；策略层照常算 final/
  last_effective，恢复首拍限速基准 = last_physical_committed。
- 原型约定（待冻结评审裁决，登记 RISKS.md::PLATFORM-OUTPUT-BASELINE-1）：
  ① 冷启动正常路径的限速基准 = safe_value；② 冷启动直接 shadow 后切实写、无
  last_physical_committed 历史时，基准同样 = safe_value（不回退 last_effective，
  避免无物理基准的跳变）。规格只规定了故障恢复与 shadow→实写有历史时的基准。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.compat.conversions import real_to_int

from .ir import BINOPS_CMP, Task
from .loader import load
from .numeric import NumericMode, is_int_type


# ---------------------------------------------------------------- 驱动

class MemoryDriver:
    """内存驱动：记录每次写（测试观察点）。"""

    def __init__(self):
        self.written: list = []          # [(channel, value)]
        self.values: dict = {}

    def write(self, channel: str, value) -> bool:
        self.written.append((channel, value))
        self.values[channel] = value
        return True


class FlakyDriver(MemoryDriver):
    """可注入失败的驱动（提交失败案例用）。fail_channels 中的通道写失败。"""

    def __init__(self):
        super().__init__()
        self.fail_channels: set = set()

    def write(self, channel: str, value) -> bool:
        if channel in self.fail_channels:
            self.written.append((channel, value, "FAIL"))
            return False
        return super().write(channel, value)


# ---------------------------------------------------------------- 扫描状态

@dataclass
class ScanFlags:
    system_ready: bool = True
    output_enable: bool = True
    comm_ok: bool = True
    safety_ok: bool = True
    watchdog_timeout: bool = False
    scan_fault: bool = False      # 由 runner 置位，不由调用方传入


@dataclass
class OutputState:
    last_effective: Any = None
    last_physical_committed: Any = None
    commit_fault: bool = False
    fail_count: int = 0
    channel_fault: bool = False
    align_from_lpc: bool = False   # shadow→实写切换 / 提交失败恢复的首拍基准标记


# 安全优先级：safety_trip ≥ watchdog ≥ scan_fault > comm_loss > startup_not_ready > operator_disable
_REASONS = (
    ("safety_trip", lambda f: not f.safety_ok, "on_safety_trip", True),
    ("watchdog", lambda f: f.watchdog_timeout, "on_watchdog", True),
    ("scan_fault", lambda f: f.scan_fault, "on_scan_fault", True),
    ("comm_loss", lambda f: not f.comm_ok, "on_comm_loss", False),
    ("startup_not_ready", lambda f: not f.system_ready, "on_startup_not_ready", False),
    ("operator_disable", lambda f: not f.output_enable, "on_operator_disable", False),
)


# ---------------------------------------------------------------- 引擎

class Engine:
    def __init__(self, task: Task, mode: NumericMode = None, driver=None,
                 registry: dict = None):
        self.task = task
        self.mode = mode or NumericMode()          # 装载期绑定，无热切换接口（IR_SPEC §8）
        self.driver = driver or MemoryDriver()
        loaded = load(task, self.mode, registry)   # 验证不过不进引擎
        self.store = loaded.store
        self.decl_types = loaded.decl_types
        self.lib_instances = loaded.lib_instances
        self.fb_instances = loaded.fb_instances
        self.prev = dict(self.store)
        self.out_maps = [io for io in task.io_map if io.direction == "OUT"]
        self.output_states = {io.channel: OutputState() for io in self.out_maps}
        self.alarms: list = []
        self.frames: list = []                     # CALL_FB_INSTANCE 调用帧栈
        self._label_cache: dict = {}
        self._was_shadow: Optional[bool] = None
        self.scan_count = 0

    # ------------------------------------------------ runner（§4.3 责任方）
    def run_scan(self, inputs: dict = None, flags: ScanFlags = None,
                 shadow: bool = False) -> dict:
        flags = flags or ScanFlags()
        if self._was_shadow is True and not shadow:      # shadow→实写切换（§4.1）
            for st in self.output_states.values():
                st.align_from_lpc = True
        self._was_shadow = shadow

        try:
            # 1) 输入映像锁存
            for k, v in (inputs or {}).items():
                if k not in self.decl_types:
                    raise KeyError(f"输入映射到未声明变量 {k}")
                self.store[k] = self.mode.on_store(v, self.decl_types[k])
            # 2)+3) 执行可执行 IR（D2 按 programs 列表顺序）
            for prog in self.task.programs:
                self._exec(prog.code)
        except Exception as e:                            # noqa: BLE001（原型：一切异常=scan_fault）
            flags.scan_fault = True
            self.alarms.append(("scan_fault", repr(e)))

        # 4) 输出门控（scan_fault 时同样走此路径 → on_scan_fault 强制 safe）
        pending = {io.channel: self._apply_policy(io.policy, flags,
                                                  self.output_states[io.channel])
                   for io in self.out_maps}
        # 5) 一次性提交（shadow 只算不写）
        if not shadow:
            self._commit(pending)
        self.prev = dict(self.store)
        self.scan_count += 1
        return pending

    # ------------------------------------------------ 第 4 步：OutputPolicy
    def _apply_policy(self, policy, flags: ScanFlags, st: OutputState):
        for _name, pred, attr, forced in _REASONS:
            if pred(flags):
                action = "safe" if forced else getattr(policy, attr)
                if action == "hold" and st.last_effective is not None:
                    final = st.last_effective       # hold 基准唯一口径 = last_effective
                else:
                    final = policy.safe_value       # 无历史值时 hold 退化为 safe（§4.1）
                st.last_effective = final           # 故障落 safe 不受 rate_limit（§4.2）
                return final
        # 正常路径
        request = self.store.get(policy.var)
        if policy.iec_type == "BOOL":
            final = bool(request)
        else:
            final = request
            if policy.rate_limit is not None:
                if st.align_from_lpc:
                    # §4.1/§4.4 对齐规则；无物理历史（冷启动即 shadow）退 safe_value（原型约定②）
                    base = (st.last_physical_committed
                            if st.last_physical_committed is not None
                            else policy.safe_value)
                elif st.last_effective is not None:
                    base = st.last_effective
                else:
                    base = policy.safe_value            # 冷启动基准（原型约定，见模块注释）
                delta = final - base
                if delta > policy.rate_limit:
                    final = base + policy.rate_limit
                elif delta < -policy.rate_limit:
                    final = base - policy.rate_limit
        st.align_from_lpc = False
        st.last_effective = final                       # shadow 同样更新（§4.1）
        return final

    # ------------------------------------------------ 第 5 步：提交（§4.4 固定行为）
    def _commit(self, pending: dict) -> None:
        by_channel = {io.channel: io for io in self.out_maps}
        for ch, final in pending.items():
            st = self.output_states[ch]
            policy = by_channel[ch].policy
            value = policy.safe_value if st.commit_fault else final
            try:
                ok = self.driver.write(ch, value)
            except Exception as e:               # noqa: BLE001
                # 驱动抛异常 = 写失败：不得破坏逐通道隔离（§4.4-4），按失败路径处理
                ok = False
                self.alarms.append(("commit_exception", ch, repr(e)))
            if ok:
                st.last_physical_committed = value
                if st.commit_fault:
                    st.commit_fault = False
                    st.fail_count = 0
                    st.align_from_lpc = True            # 恢复首拍限速基准 = lpc
                    self.alarms.append(("commit_recovered", ch))
            else:
                st.fail_count += 1
                if not st.commit_fault:
                    st.commit_fault = True
                    self.alarms.append(("commit_fault", ch))
                if st.fail_count >= policy.commit_fault_retry_n and not st.channel_fault:
                    st.channel_fault = True             # 升级通道级故障，无静默放弃路径
                    self.alarms.append(("channel_fault_escalated", ch))
                # lpc 不更新（如实反映设备侧未知/旧值）；逐通道隔离：继续下一通道

    # ------------------------------------------------ 执行器
    def _resolve(self, key: str) -> str:
        """FB 实例体内 self.<名> 键解析：INOUT 形参 → 调用方键（别名，写透）；否则实例路径键。"""
        if not key.startswith("self."):
            return key
        if not self.frames:
            raise RuntimeError(f"帧外引用 {key}")
        path, inout_map = self.frames[-1]
        name = key[5:]
        if name in inout_map:
            return inout_map[name]
        return f"{path}.{name}"

    def _labels(self, code) -> dict:
        cached = self._label_cache.get(id(code))
        if cached is None:
            cached = {ins.key: i for i, ins in enumerate(code) if ins.op == "LABEL"}
            self._label_cache[id(code)] = cached
        return cached

    def _exec(self, code) -> None:
        labels = self._labels(code)
        mode = self.mode
        store = self.store
        stack: list = []                                # TypedValue 栈：(value, iec_type)
        pc = 0
        while pc < len(code):
            ins = code[pc]
            pc += 1
            op = ins.op
            if op == "LOAD_CONST":
                stack.append((mode.on_const(ins.value, ins.type), ins.type))
            elif op == "LOAD_VAR":
                stack.append((store[self._resolve(ins.key)], ins.type))
            elif op == "STORE_VAR":
                v, t = stack.pop()
                if t != ins.type:                       # 运行期护栏（加载器已静态验证）
                    raise TypeError(f"STORE_VAR {ins.key}: 栈顶 {t} != {ins.type}")
                store[self._resolve(ins.key)] = mode.on_store(v, ins.type)
            elif op == "BINOP":
                b, _tb = stack.pop()
                a, _ta = stack.pop()
                r = _binop(ins.subop, a, b, ins.type)
                if ins.subop in BINOPS_CMP:
                    stack.append((r, "BOOL"))           # 比较结果恒 BOOL，不量化
                else:
                    stack.append((mode.on_result(r, ins.type), ins.type))
            elif op == "UNOP":
                v, _t = stack.pop()
                if ins.subop == "NOT":
                    stack.append((not v, "BOOL"))
                else:                                   # NEG
                    stack.append((mode.on_result(-v, ins.type), ins.type))
            elif op == "CONVERT":
                v, _t = stack.pop()
                stack.append((self._convert(v, ins.type, ins.to_type), ins.to_type))
            elif op == "CALL_FB":
                self._call_fb(ins.key)
            elif op == "CALL_FB_INSTANCE":
                self._call_fb_instance(ins.key, ins.bindings)
            elif op == "JMP":
                pc = labels[ins.key]
            elif op == "JMP_IF_FALSE":
                v, _t = stack.pop()
                if not v:
                    pc = labels[ins.key]
            elif op == "LABEL":
                pass
            else:
                raise RuntimeError(f"未知指令 {op}")

    def _convert(self, v, from_t: str, to_t: str):
        """CONVERT：显式转换 + 隐式提升/赋值转换唯一落点（IR_SPEC §5.1/§5.3 边界 3）。"""
        if to_t in ("REAL", "LREAL"):
            return self.mode.on_store(float(v), to_t)
        if is_int_type(to_t) or to_t == "TIME":
            if from_t in ("REAL", "LREAL"):
                v = real_to_int(v)                     # 走 src/compat 银行家舍入（规格指定）
            return self.mode.on_store(int(v), to_t)    # 保证截断点（§5.4；E 不回绕）
        if to_t == "BOOL":
            return bool(v)
        raise RuntimeError(f"原型不支持 CONVERT {from_t}->{to_t}")

    def _call_fb(self, inst: str) -> None:
        """库块调用：输入脚已由前置 STORE_VAR 就位（含 F1 边界量化）；输出回收再量化（§5.3 边界 5）。"""
        desc, obj = self.lib_instances[inst]
        ins_vals = {p.name: self.store[f"{inst}.{p.name}"] for p in desc.inputs}
        outputs = desc.call_adapter(obj, self.task.cycle_ms, ins_vals, {})
        for p in desc.outputs:
            self.store[f"{inst}.{p.name}"] = self.mode.on_store(outputs[p.name], p.iec_type)

    def _call_fb_instance(self, path: str, bindings) -> None:
        """用户 FB 实例调用：压帧执行定义级共享 IR；实例内存跨周期保持；绝不创建实例（IR_SPEC §3）。"""
        definition = self.task.pou_lib[self.fb_instances[path]]
        inout_map = {}
        for b in bindings:
            if b.mode == "IN":
                v = b.actual if b.actual_kind == "const" else self.store[self._resolve(b.actual)]
                self.store[f"{path}.{b.formal}"] = self.mode.on_store(v, b.type)
            elif b.mode == "INOUT":
                inout_map[b.formal] = self._resolve(b.actual)   # ValueRef：别名，禁止值拷贝往返
        self.frames.append((path, inout_map))
        try:
            self._exec(definition.code)
        finally:
            self.frames.pop()
        for b in bindings:
            if b.mode == "OUT":
                v = self.store[f"{path}.{b.formal}"]
                self.store[self._resolve(b.actual)] = self.mode.on_store(v, b.type)


def _int_div_trunc(a: int, b: int) -> int:
    """整数除法向零截断（IEC 语义），不经 float——大整数（如 LINT_MAX/1）不失精度。"""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _binop(subop: str, a, b, t: str):
    if subop == "ADD":
        return a + b
    if subop == "SUB":
        return a - b
    if subop == "MUL":
        return a * b
    if subop == "DIV":
        if is_int_type(t) or t == "TIME":
            return _int_div_trunc(a, b)        # 纯整数向零截断（除零异常→scan_fault）
        return a / b
    if subop == "MOD":
        return a - _int_div_trunc(a, b) * b    # IEC MOD 符号随被除数，纯整数算法
    if subop == "AND":
        return (a and b) if t == "BOOL" else (a & b)
    if subop == "OR":
        return (a or b) if t == "BOOL" else (a | b)
    if subop == "XOR":
        return (bool(a) != bool(b)) if t == "BOOL" else (a ^ b)
    if subop == "GT":
        return a > b
    if subop == "GE":
        return a >= b
    if subop == "LT":
        return a < b
    if subop == "LE":
        return a <= b
    if subop == "EQ":
        return a == b
    if subop == "NE":
        return a != b
    raise RuntimeError(f"未知 BINOP {subop}")
