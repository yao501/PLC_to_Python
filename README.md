# PLC → Python 迁移工程

将基于 CODESYS SP16.1 的 PLC 程序逐步迁移为 Python 代码，**保持 PLC 扫描周期语义一致**。

## 项目结构

```
.cursor/rules/               # 交给 Cursor AI 的迁移规则（自动生效）
  00-project-global.mdc      # 全局纪律（alwaysApply: true）
  00a-runtime-contract.mdc   # 项目级运行契约（alwaysApply: true）
  01-basic-primitives.mdc    # 阶段一：基础原语
  02-business-blocks.mdc     # 阶段二：业务块
  03-main-program.mdc        # 阶段三：主程序
cursor提示词功能/            # 原始提示词文件（人读参考）
src/
  config.py                  # 项目默认常量：CYCLE_MS、STARTUP_INHIBIT_MS
  validation.py              # PT_ms 配置校验
  primitives/                # 阶段一：TON/TOF/TP/R_TRIG/F_TRIG/SR/RS
  blocks/                    # 阶段二：业务基础块（待填）
  main/                      # 阶段三：主程序与扫描调度（待填）
tests/                       # 单元测试（39 个用例，全部通过）
```

## 项目级运行契约（要点）

详见 `.cursor/rules/00a-runtime-contract.mdc`。核心约束：

1. **时间单位**：全项目统一 **整数毫秒**。命名带 `_ms` 后缀（`dt_ms`、`PT_ms`、`ET_ms`、`cycle_ms`）。严禁浮点时间累积。
2. **扫描周期**：生产模式默认固定，当前项目 `cycle_ms = 500`。基础块禁止读取系统时钟，`dt_ms` 只能由主程序传入。
3. **基础块接口纯净**：标准基础块不添加 `RESET` / `EN` / `ENO` 端口，相关语义由主程序的调度门控层实现。
4. **冷启动保护**：`R_TRIG` / `F_TRIG` 本体按 IEC 标准实现（允许上电首拍产生一次边沿），冷启动防误动作由主程序的 `system_ready` 门控承担。默认 `STARTUP_INHIBIT_MS = 500`，可叠加 `io_ready / bus_ready / comm_ready / safety_ok`。
5. **主程序五步式**：`输入快照 → 功能块推进 → request 生成 → 输出门控 → 一次性提交输出`。
6. **输出安全链**：`final_output = system_ready AND output_enable AND safety_ok AND interlock_ok AND request`。扫描异常/超时/主循环失败 → 物理输出落到预定义安全默认值。首次接设备前先走 shadow mode / write disable。

## 基础原语使用示例

```python
from src.primitives import TON, R_TRIG, SR
from src.validation import check_pt_ms

PT_START_MS = 5000
check_pt_ms(PT_START_MS, name="M1.Start")

ton = TON()
rtrig = R_TRIG()
sr = SR()

dt_ms = 500
IN = True

ton.step(dt_ms, IN=IN, PT_ms=PT_START_MS)
raw_edge = rtrig.step(CLK=IN)
sr.step(SET1=raw_edge, RESET=False)
```

全部 7 个原语都遵守统一的扫描周期模型：**一个周期调用一次 `step(...)`，实例自动维护跨周期状态**。

## 冷启动 + 边沿 + 锁存的正确组合模式

> 注意：`system_ready` 必须在 **进入 SR 之前** 就对 `request` 做一次门控，
> 否则冷启动的上电边沿会把 SR 锁住，等 `system_ready` 释放时会产生"被延迟的幽灵动作"。

```python
raw_edge      = rtrig.step(CLK=CLK)
valid_request = raw_edge and system_ready
sr.step(SET1=valid_request, RESET=False)
final_output  = sr.Q1 and system_ready and safety_ok and interlock_ok and output_enable
```

## PT_ms 配置校验

```python
from src.validation import check_pt_ms

check_pt_ms(100,   name="Sensor.Debounce")
check_pt_ms(1300,  name="Alarm.Delay")
check_pt_ms(5000,  name="Motor.Start")
```

固定扫描周期模式下：
- `PT_ms < cycle_ms` → 发 warning，提示无意义；
- `PT_ms` 不是 `cycle_ms` 整数倍 → 发 warning，提示将被量化。

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

当前：**39 个用例全部通过**，覆盖：
- 7 个原语的基础行为
- SR / RS 完整真值表（含置位/复位优先）
- R_TRIG / F_TRIG 冷启动首拍（`CLK=True` / `CLK=False` 两种情况）
- 长周期无漂移（10000 周期）与阈值边界
- R_TRIG + SR 在 `system_ready` 门控下的冷启动防误动作模式
- `check_pt_ms` 的三类校验路径

## 依赖

无第三方依赖，仅需 Python 3.9+ 标准库。
