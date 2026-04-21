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
  validation.py              # PT_ms / TB 配置校验
  compat/                    # ST / CODESYS 兼容 helper（conversions.py）
  primitives/                # 阶段一：TON/TOF/TP/R_TRIG/F_TRIG/SR/RS
  blocks/                    # 阶段二：业务基础块（已有 APCHXHCL、APCSTATISTICS）
  main/                      # 阶段三：主程序与扫描调度（待填）
tests/                       # 单元测试（117 个用例，全部通过）
docs/
  RISKS.md                   # 唯一的风险与待完善事项登记簿（必读）
```

## 风险与待完善事项

**全部已知风险与延后项集中在 [`docs/RISKS.md`](docs/RISKS.md)。**
每次交付后必须同步更新。禁止把风险只写在对话或 docstring 里。

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

## PT_ms / TB 配置校验

```python
from src.validation import check_pt_ms, check_tb_sample_n_integer

check_pt_ms(100,   name="Sensor.Debounce")
check_pt_ms(1300,  name="Alarm.Delay")
check_pt_ms(5000,  name="Motor.Start")

check_tb_sample_n_integer(tb=0.5, name="APCHXHCL_1")   # OK
check_tb_sample_n_integer(tb=0.7, name="APCHXHCL_2")   # warning: 60/0.7 非整数
```

固定扫描周期模式下：
- `PT_ms < cycle_ms` → 发 warning，提示无意义；
- `PT_ms` 不是 `cycle_ms` 整数倍 → 发 warning，提示将被量化。
- `60 / TB` 非整数 → 发 warning（APCHXHCL R4）。

## CODESYS 类型转换兼容层

所有 ST `REAL_TO_INT` / `REAL_TO_TIME` 在 Python 侧统一走 `src/compat/conversions.py`，
不允许在业务块内直接使用裸 `int(...)`。详见 `docs/RISKS.md` 的 **APCHXHCL-R4** 条目。

```python
from src.compat import real_to_int, real_to_time_ms

sample_n = real_to_int(60.0 / TB)              # 银行家舍入
pt_ms    = real_to_time_ms(TL * 1000.0)        # 非负整数毫秒
```

## 已迁移业务块

| 模块 | 来源 | 说明 |
|---|---|---|
| `src.blocks.APCHXHCL` | `APCHXHCL1.txt`（v2） | 信号处理：故障检测 + 最近一分钟均值 + 一阶 IIR 滤波 + 故障首拍均值冻结 |
| `src.blocks.APCSTATISTICS` | `statistics.txt`（修正版） | 运行统计：min / max / 累计算术平均（Welford 增量式），支持 RESET 清零；ULINT 计数、LREAL 平均值 |

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

当前：**121 个用例全部通过**，覆盖：
- 7 个原语的基础行为
- SR / RS 完整真值表（含置位/复位优先）
- R_TRIG / F_TRIG 冷启动首拍（`CLK=True` / `CLK=False` 两种情况）
- 长周期无漂移（10000 周期）与阈值边界
- R_TRIG + SR 在 `system_ready` 门控下的冷启动防误动作模式
- `check_pt_ms` / `check_tb_sample_n_integer` 校验路径
- `src.compat.conversions` 三类 helper（21 个用例）
- `APCHXHCL` 30 个契约验证：EN 开关、首拍初始化、每拍入列、三类故障、故障冻结、helper 接入、R1 / R3 / R5~R9 保留行为锁定
- `APCSTATISTICS` 24 个契约验证（任务书 §7.1~§7.10）：初值统一 / RESET 当拍不采样 / 首样本 / 递增/递减/常量/负数/小数序列 / RESET 二次统计 / 长序列 10000 样本 / 跨 2e9 不减半 / Welford 公式数值 / 无 SUM / 修正版决策锁定

## 依赖

无第三方依赖，仅需 Python 3.9+ 标准库。
