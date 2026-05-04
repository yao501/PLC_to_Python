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
  primitives/                # 阶段一：TON/TOF/TP/R_TRIG/F_TRIG/SR/RS/BLINK
  blocks/                    # 阶段二：业务基础块（已有 APCHXHCL、APCSTATISTICS、APCHSFOP）
  main/                      # 阶段三：主程序与扫描调度（待填）
tests/                       # 单元测试（详见文末"运行测试"章节）
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
7. **业务块显式时间输入脚 vs runtime `dt_ms`**（R7）：业务块的显式时间参数（`TB / TC / TL` 等）按 PLC 输入脚语义取值，单位由 FB 源码决定（通常**秒**），**不等于** `dt_ms/1000`，也不得被 runtime 自动替代。文档/注释/测试中**禁止**出现"`TB` 必须等于 `cycle_ms/1000`"等绝对化表述。
8. **业务时间参数非负约束（项目级参数契约，R7 第 7 条）**：`TB ≥ 0`、`TC ≥ 0`、`PT_ms ≥ 0`、`TL ≥ 0` 等。此约束由**配置装载层（Runtime 阶段）**集中硬拦截，**不允许业务块内部**为处理负值擅自加入分支/限幅/兜底逻辑。当前承接项：`APCHSFOP-H6`（详见 `docs/RISKS.md::APCHSFOP-H6` 与 `RUNTIME-PARAM-VALIDATION`）。

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
| `src.blocks.APCHSFOP` | `HSFOP.txt` | 一阶惯性低通滤波（IIR）：`AV = (TC·Ok_1 + KG·TB·IN)/(TB+TC)`；含 `(TB+TC)>0.001` 与 `\|AV_TEMP\|<1e10` 双重守护 |
| `src.blocks.APCHSHLLIM` | `APCHSHLLIM.txt` | 幅值限幅：`IN > HL → HL`、`IN < LL → LL`、否则直通；`LL>HL` 时块内静默修正为 `LL=HL`（源块容错） |
| `src.blocks.APCHSRATELIM` | `APCHSRATELIM.txt` | 速率限幅：`HL/LL` 都是**正幅值**（每拍上升/下降速率上限）；块内 `ABS()` 容错；状态变量 `AV_1` 跨周期保持；冷启动 `AV_1=0` 首拍可能不直通 `IN`（源块语义） |
| `src.blocks.APCGCQ` | `GCQ.docx` (CFC) + `CGCQ1.txt` | 观测器（MMYZ）组合块：BLINK 周期方波 → R_TRIG 触发 STAT01 重置 → 两次相邻窗口均值差经一阶低通滤波得"动态观测分量" → 叠加"静态观测分量" → 经速率限幅 + 幅值限幅输出 GCAV。**关键项目修正约定**：`BLINK01.TIMEHIGH = 500ms`（GG4，旧 ST 转换稿写的 `T#300MS` 不采纳，本项目以 500ms 为准；采样窗口 = `TC*1000 + 500ms`）。**核心不变量**：ST 执行顺序锁定（GG1：`JZ_ZUP` 取**旧** `JZ_Z`，不是 RESET 后的 0）。**输出限幅分层**：`OUTV` 经 `RLIM01(HL=LL=OUTV)` 做对称**每拍速率限幅**；`OUTH/OUTL` 经 `LIM01` 做最终**幅值上下限**——两层职责严格分离（GG5）。控制器验证段 `BC_ERROR3` 暂未迁移（GG8 `deferred`）。**说明**：源材料中出现的 `APCGCQ1` 是软 PLC 复制功能块时自动生成的防重名名称，业务功能块的真实命名就是 `APCGCQ`，不构成命名风格选择 |

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

当前：**241 个用例全部通过**，覆盖：
- 8 个原语的基础行为（TON / TOF / TP / R_TRIG / F_TRIG / SR / RS / **BLINK**）
- SR / RS 完整真值表（含置位/复位优先）
- R_TRIG / F_TRIG 冷启动首拍（`CLK=True` / `CLK=False` 两种情况）
- 长周期无漂移（10000 周期）与阈值边界
- R_TRIG + SR 在 `system_ready` 门控下的冷启动防误动作模式
- **BLINK** 17 个契约验证：冷启动 `OUT=False` / `ENABLE=False` 在 `OUT=False` 与 `OUT=True` 两种状态下均保持输出且冻结相位计时（B1）/ 重启 `ENABLE` 从冻结点续跑 / 对称与非对称占空比 / **单拍跨多相位**（B2 已修复，`dt_ms > period` 仍精确）/ 10000 拍无漂移 / 非整除 `dt_ms` 余数保留 / 变步长扫描 / `TIMELOW+TIMEHIGH=0` 退化护栏（B4）
- `check_pt_ms` / `check_tb_sample_n_integer` 校验路径
- `src.compat.conversions` 三类 helper（21 个用例）
- `APCHXHCL` 30 个契约验证：EN 开关、首拍初始化、每拍入列、三类故障、故障冻结、helper 接入、R1 / R3 / R5~R9 保留行为锁定
- `APCSTATISTICS` 24 个契约验证（任务书 §7.1~§7.10）：初值统一 / RESET 当拍不采样 / 首样本 / 递增/递减/常量/负数/小数序列 / RESET 二次统计 / 长序列 10000 样本 / 跨 2e9 不减半 / Welford 公式数值 / 无 SUM / 修正版决策锁定
- `APCHSFOP` 29 个契约验证：首拍 α·KG·IN 数值 / 稳态收敛到 KG·IN / 阶跃响应单调性 / α 强弱对比 / 公式字面数值 / `(TB+TC)≤0.001` 跳过 / `\|AV_TEMP\|≥1e10` 冻结 / KG=0/KG<0 / 与 APCHXHCL 内嵌段一致 / RETAIN 状态保持 / **R7 时间语义：TB 与 cycle_ms 解耦**
- `APCHSHLLIM` 18 个契约验证：三分支正确性 / 边界等号 IN=HL/LL 不被截 / `LL>HL` 静默修正三种 IN 全锁定（HL1）/ 修正不写回参数 / 合法负区间 `[-20,-10]` 不做 ABS（HL3）/ `HL==LL` 单点限幅（含负值）/ `self.AV` 不参与下一拍判定（HL2）/ `dt_ms` 与状态无关
- `APCHSRATELIM` 23 个契约验证：上下方向钳位 / 冷启动 `AV_1=0` 首拍不直通（RL2）/ 单调爬升每拍 +HL / 方向独立判断 / 非对称 HL≠LL / 块内 `ABS()` 容错 + 不写回（RL3）/ 对称速率限幅（GCQ 用法）/ `HL=0` 卡上升 / `LL=0` 卡下降 / `HL=LL=0` 完全冻结 / 严格 `>/<` 等号边界（`delta==HL` / `delta==-LL` 走 ELSE）/ 两实例同状态不同 `dt_ms` 输出一致（RL4）
- `APCGCQ` 33 个契约验证：冷启动初值 / 首个 BLINK 周期内无采样事件 / **ST 执行顺序锁定**（GG1：第一次/第二次采样事件 `JZ_ZUP` 取旧 `JZ_Z`，含从 `STAT01.COUNTER=0` + `JZ_ZUP=100` 双观测面直接锁定"采样快照在 STAT.RESET 之前"的明示测试）/ 采样事件每 `(TC*1000+TIMEHIGH)ms` 一次 / `BLINK_TIMEHIGH_MS=500` 与 `FOP01_DEFAULT_TB_SEC=0.5` 模块级常量锁定 / **GG4 项目修正约定锁定**：dt=100/TC=1.0 时相邻采样事件间距恰为 15 拍（即 1500ms 而非 1300ms）/ 首拍 FOP `α·KG·IN` 数值 / `JTAV = (IN-INSP)*GC1` / `DTAV = AV*GC2` / `K` 倍率 / **死区 SEL 恒假分支锁定**（GG2：`IN==INSP` / `IN<INSP` / `IN>INSP` 三种关系均走 IN0，反证不能误改成 `IN!=INSP`）/ RLIM 对称速率限幅串入主通路 / LIM 幅值限幅串入主通路 / **OUTV vs OUTH/OUTL 分层验证**（GG5：单层 OUTV 紧 / 单层 OUTH 紧 / 两层都紧最终落 OUTH / 反证 OUTV 不是幅值限）/ 模块导出基础健康检查 / 嵌套 FB 实例不共享状态 / `STAT01` 在采样事件 RESET / AV 衰减验证（反向证伪错误 ST 顺序）

## 依赖

无第三方依赖，仅需 Python 3.9+ 标准库。
