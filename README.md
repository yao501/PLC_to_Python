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
  blocks/                    # 阶段二：业务基础块（APCHXHCL/APCSTATISTICS/APCHSFOP/APCHSHLLIM/APCHSRATELIM/APCGCQ/APCCD/APCHSACCUM/APCPIDZZD/APCPID/APCSPFINDER/APCRSFNAUTOPARA/APCMAUTOPARA/APCM）
  licensing/                 # 授权模块阶段一：一机一码（XTXX/BD_ZCM/BD_MMYZ/BD_MMYZ_ST + Provider）
  globals/                   # 每 Runtime 实例的全局变量容器（LicenseContext）
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
| `src.blocks.APCGCQ` | `GCQ.docx` (CFC) + `CGCQ1.txt` | 观测器（MMYZ）组合块：BLINK 周期方波 → R_TRIG 触发 STAT01 重置 → 两次相邻窗口均值差经一阶低通滤波得"动态观测分量" → 叠加"静态观测分量" → 经速率限幅 + 幅值限幅输出 GCAV。**关键约定**：`BLINK01.TIMEHIGH = 500ms`（GG4，量化到任务周期）——源/转换稿写的 `T#300MS` **不是笔误**：`R_TRIG` 只在 500ms 任务边界采样，亚周期脉宽不可分辨，真实 500ms-PLC 中 300 与 500 等价；本项目 BLINK 余数保留实现取 300 会吞脉冲/抖动，故端口量化到 500ms 才忠实复现（采样窗口 = `TC*1000 + 500ms`）。**核心不变量**：ST 执行顺序锁定（GG1：`JZ_ZUP` 取**旧** `JZ_Z`，不是 RESET 后的 0）。**输出限幅分层**：`OUTV` 经 `RLIM01(HL=LL=OUTV)` 做对称**每拍速率限幅**；`OUTH/OUTL` 经 `LIM01` 做最终**幅值上下限**——两层职责严格分离（GG5）。控制器验证段 `BC_ERROR3` 暂未迁移（GG8 `deferred`）。**说明**：源材料中出现的 `APCGCQ1` 是软 PLC 复制功能块时自动生成的防重名名称，业务功能块的真实命名就是 `APCGCQ`，不构成命名风格选择 |
| `src.blocks.APCCD` | `APCCD.txt` (ST) | 重叠控制组合块：BLINK 周期采样 → R_TRIG1 触发 STAT1 重置 → 相邻统计窗口均值差（`JZ_ZUP2-JZ_ZUP3`）经 `FOP1` 一阶低通滤波得**动态项** → 与**静态项** `(PV-SP)*CD_K_J` 叠加并乘正反作用符号 `SEL(AD,1,-1)` 得专家输出 `CD_BH`；`\|CD_BH\| >= CD_GD` 持续 `TL` 秒后 `TON1` 延时进入，未跟踪时把钳幅后的 `CD_BH*CD_K_FD` 写入 `AV_TEMP`；退出阈值（`\|CD_BH\| < CD_GD`）或进入跟踪（`TS=True`）时由 `R_TRIG2` 触发**一次**回补：把拐点的 `CD_K` 倍累加到 `ZLOUT` 后清零 `AV_TEMP`。**关键事实**：① `BLINK1.TIMEHIGH = 500ms`（量化到任务周期，与 GCQ 统一）——源 ST 写 `T#300MS`，但 `R_TRIG` 只在 500ms 任务边界采样，亚周期脉宽不可分辨，真实 500ms-PLC 中 300 与 500 等价；本项目 BLINK 余数保留实现取 300 会吞脉冲/抖动，故端口量化到 500ms 才忠实复现（CD2，采样窗口 = `TC*1000 + 500ms`）；② ST 顺序锁定（CD1：窗口快照 `JZ_ZUP3/JZ_ZUP2` 必须在 `STAT1.RESET` 之前）；③ `FOP1.TB=0.5s`（ST 未连 TB，沿用 APCHSFOP 声明默认值）；④ `ZLOUT` 是 `VAR_IN_OUT`，Python 采用"**入参 + 返回值**"适配，调用方必须把 `out["ZLOUT"]` 回灌下一拍；⑤ `CD_BH==0` 时 `FLG` 保持旧值（非 `sign()=0`）；⑥ `TS` 进入拍若已有非零 `AV_TEMP` 允许先回补一次再清零 |
| `src.blocks.APCHSACCUM` | `APCHSACCUM.txt` (ST) | 离散积算 / 单次回绕块：每拍按 `AV := AV + MC*I1` 执行一次（**非 dt 积分**，`dt_ms` 不参与公式）；`SS` 表示本拍是否达到/越过 `MS`；`RS` 为**上升沿**复位，且复位发生在**本拍积算之后**（`LR` 存积算后 AV，再 `AV:=IV`）。**关键事实**：① 执行顺序锁定——先处理上拍遗留 `AV>=MS OR AV<0`（置 `IV`）→ 本拍积算/**单次回绕**（只减一次 `MS`，不取模/不循环，单拍跨多 `MS` 留 `AV>=MS` 到下拍开头才置 `IV`）→ 最后查 `RS` 上升沿（AC1）；② 负 `I1` 当拍不修正，下拍开头才因 `AV<0` 置 `IV`（AC2）；③ 冷启动 `AV=0.0`（即使 `IV` 非零）；④ 默认 `MS=1.797693134862e38` 按 ST **可执行字面量**实现，源注释 `E308` 与之冲突，登记待确认不修正（AC3）；⑤ `bPositiveAccum` 源声明未用，保留属性不加"只积正值"语义（AC4）；⑥ 无 `EN/ENO/RESET/system_ready`、无参数校验 |
| `src.blocks.APCPID` | `APCPID.txt` (ST) | 变比例变积分 PID 调节器：跟踪/手动/自动三类运行方式 + 增量式/位置式输出。PID 公式使用源 ST 内部 `CYCLE`（默认 0.5s，绑定 500ms 任务），`dt_ms` 仅驱动外层授权与嵌套 `PIDZZD1`。**关键事实**：① **双层授权**——外层 `KZQBDYZMK.step` + 主逻辑末尾 `PIDZZD1`（其内部又调一次授权），授权通过一拍共 **2 次** 调用、不去重；失败只累加注入式 `BD_ERROR1`、不调用 `PIDZZD1`、不动 PID 状态（GATE-1）；② **`CYCLE` 与 `dt_ms` 分离**，不得 `dt/1000` 替换（CYCLE-1）；③ **旧 EK 顺序**——顶部 `TIi` 的 SVH 判断用上一拍遗留 `EK`，本拍 EK 在其后才重算（ORDER-1）；④ **RM 注释 vs 代码**——`3/4`=跟踪、`0`=手动，其余（含 `2`、非法）一律自动（RM-1）；⑤ **`RM/SP/KD/TD` 是 VAR_INPUT**，本拍局部改写不持久化；`CYCLE/MU/TIi/PTt` 是 VAR 持久化（INPUT-1）；⑥ **`OutRL`** 实为自动末尾 `ABS(AV-AV_TEMP)>ABS(OutRL)` 的 AV 提交阈值，非限速器（OUTRL-1）；⑦ **`PIDZZD1` 在历史更新之后调用**，新 `PT1/TI1` 下一拍才影响 PID 主计算（ZZD-1）；⑧ `nowRM/deadenter/C2/C3/C4` 源声明/计算但不参与活动输出，保留不删（DEADVAR-1）；⑨ `LicenseContext` 构造注入，`PIDZZD1` 共享同一 context |
| `src.blocks.APCPIDZZD` | `APCPIDZZD.txt` (ST) | PID 自整定块：对 `ABS(PV-SP)` 做正/负离散积算，比对最近三次积算结果识别 PV 发散/振荡趋势并调弱 `PT1/TI1`（自适应比例/积分增量）。复用 `TON`（5s 延时进入积算/死区）、`R_TRIG`（长时间偏差两档加强 TI）、`APCHSACCUM`（离散积算）、`APCHSHLLIM`（时间均值限幅）、`LicenseContext.KZQBDYZMK`（授权门控）。**关键事实**：① 业务时间固定 `+0.5s/拍`，绑定 500ms 任务周期，`dt_ms` 仅驱动 TON，只在 `dt_ms=500` 验证语义（CYCLE-1）；② **授权门控严格保序**——每拍先 `KZQBDYZMK.step` 再读 `OK%10000`，失败只累加注入式 `BD_ERROR5`、不动任何自整定状态（GATE-1）；③ `LicenseContext` 经构造函数注入，无模块级单例/无默认授权后门；④ **两处注释 vs 代码冲突按代码实现**——`PT1K/TI1K<=0` 仅周期顶部清零、后续识别分支仍可重新赋值（COMMENT-1）；`JSSJF` 注释写"正积算"实为负积算路径（COMMENT-2）；⑤ TON1 延时窗口内 HSACCUM1 以保留 `I1` 持续累加（ACCUM-1，ST+APCHSACCUM 组合真实行为）；⑥ 收敛识别三档（`>1.1`/`>0.7`/`>0.4`）与理论 TI 三档（≈1.0/0.6/0.2）严格 `>/<` 边界；⑦ 末尾限幅 `PT1∈[-0.7PT,3PT]`、`TI1∈[-0.7TI,3TI]`；⑧ `RM≠1` 仅精确复位 `JS_Z/JS_F/ZJSBZ/FJSBZ/JSSJ/JSSJ2`，其余状态保留（RESET-1） |
| `src.blocks.APCSPFINDER` | `APCSPFINDER.txt` (ST) | 分析用设定值（SP）自动寻找功能块：在自动参数推荐算法内部选 `SP_USE`（人工 `SP_MAN` > 现场 `SP_TAG` > 自动稳定段 `SP_AUTO`）。**纯状态/数值块**：无 FB 依赖、无授权门控、不接 `LicenseContext`、不读系统时钟、`del dt_ms`。**关键事实**：① **`SP_USE` 仅作分析推荐输入**，不写 `APCPID/APCPIDZZD` 或任何现场控制 SP（ANALYSIS-1）；② **时间严格来自输入 `CYCLE`**——`CYCLE_S=MAX(CYCLE,0.001)`、`STABLE_T+=CYCLE_S`，`dt_ms` 仅为统一接口保留、不参与累计、不缩放，`CYCLE` 是本拍 `VAR_INPUT` 不持久化（CYCLE-1）；③ **`EN` 只控制自动稳定段寻找**——基础阈值/`RESET`/`SP_TAG_BAD`/最终 `SP_USE` 四级优先级不受 `EN=False` 影响、不提前 `return`，`EN=False` 时 `PV_1/AV_1` 不更新但仍可使用历史 `SP_AUTO`（EN-1）；④ **历史自动 SP 保留**——不稳定段只清当前稳定段统计、`SAMPLE_OK=False` 冻结稳定段（仍更新 `PV_1/AV_1`）、`SP_AUTO_EN=False` 仅清 `SP_AUTO_OK/CONF`，唯有 `RESET` 清 `SP_AUTO`（HOLD-1）；⑤ **`RESET` 不提前返回**——清自动寻找内部状态但不重置 `PV_1/AV_1/D_*/PV_TH/AV_TH`，当拍仍执行最终 SP 选择（RESET-1）；⑥ 严格 `<=`/`>` 边界、量程兜底 `100`、`SP_SOURCE/SP_REASON` 编码（0~6）按源码；⑦ **边缘四者交叉**(EDGE-1)：`EN=False`+`SP_AUTO_EN=False`+历史 `SP_AUTO_OK=True`+允许替换+现场 SP 可疑时，现场被排除、自动被拦下、落 `SP_USE=PV`/源0/因4——疑似源 ST 设计缺口但按原样保留，回归锁定不自行修复 |
| `src.blocks.APCRSFNAUTOPARA` | `APCRSFNAUTOPARA.txt` (ST) | RSFN 自动参数推荐功能块：窗口统计 + 历史窗口三阶段相似融合，只推荐 `TL/TL1~TL4/E1~E4/AO1~AO4/RSF_LOCK_T/RSF_HYS/RSF_FAST_HYS/RSF_TLOUT_K/ZF_K` 的 `*_REC` 值，**不**写 `APCRSFN` 实际参数或现场 SP。**复用** `APCSPFINDER` 真实子实例（`self.SPF1`）取分析 SP，**纯状态/数值块**：无授权门控、不接 `LicenseContext`、不读系统时钟。**关键事实**：① 时间严格来自输入 `CYCLE`——`CYCLE_S=MAX(CYCLE,0.001)`，`dt_ms` 仅转发给 `SPF1`、不参与累计/缩放（CYCLE-1）；② 量程无效时内部临时 `OUT_RANGE=100` 但 `RANGE_OK=False` 仍拦截有效性（RANGE-1）；③ **已对齐修复版基线**：`RESET` 现清 `WIN_SP/PV/AV_SUM`（原始基线漏清→下一窗口均值带残留，修复版补三行清零，RESET-1）；只清 `H_VALID/H_WEIGHT` 两数组的设计保留；④ `RUNNING:=EN`——`EN=True&RESET=True` 时 `RUNNING=True` 但不采样，冷启动首拍可对 `SPF1` 调用两次，不提前 return（RUNNING-1）；⑤ `SPF1` 固定 `PVMU=MAX(E4_IN*2,1)/PVMD=0/OUTT=OUT_RANGE`，内部偏差 `SP_WORK=SP_USE`、无效时 `=PV`（SPFINDER-1）；⑥ 当前有效窗口**先入库后同拍融合**，每阶段 `NOT FINAL_STRONG` 才执行且清零重算不叠加，弱推荐 `FINAL_VALID=False` 且 `RSF_REASON→5`，`FUSE_SUM_W=0` 回退 `W_*`（FUSION-1）；⑦ `CALC_NOW` 仅上升沿结算，`CALC_OLD/ERR_1/PV_1/AV_1/TP_1/RSF_LEVEL_1/RSF_LOCK_LEVEL_1` 每拍末尾无条件更新（CALC-1）；⑧ 单窗口推荐优先级 `W_SLOW>W_OSC>W_NOISE_HIGH`，AO 上限 `OUT_RANGE×0.35/0.40/0.45/0.50`，`DATA_REASON=2` 为结算点死分支（结算条件恒 `WIN_ELAPSED>=MIN_WIN_T`；ChatGPT5.5 修复版的 Bug2 实时补丁会破坏"最近完成窗口快照"语义，经复核撤回不同步，按源保留，DATAREASON-1）|
| `src.blocks.APCMAUTOPARA` | `APCMAUTOPARA.txt` (ST) | APCM 自动参数推荐功能块：窗口统计 + PID/RSF/观测器/重叠控制四组单窗口推荐 + 历史三阶段相似融合，只输出 `PT/TI/TD/DI/SVH/SVL`、`TL/TL1~TL4/E1~E4/AO1~AO4/RSF_LOCK_T`、`TC/TZ/GC1/GC2/OUTH/OUTL`、`CD_GD/CD_K/CD_K_FD/CD_K_J/CD_K_D/CDH/CDL/TC_CD/TZ_CD` 的 `*_REC` 值，**不**写 APCM 实际控制参数或现场 SP。**复用** `APCSPFINDER` 真实子实例（`self.SPF1`），**纯状态/数值块**：无授权门控、不接 `LicenseContext`、不读系统时钟。**关键事实**：① 时间严格来自输入 `CYCLE`（CYCLE-1）；② **与 APCRSFNAUTOPARA 不同**：顶层 `IF EN`（非 `EN AND NOT RESET`），`EN=True&RESET=True` 时先完整复位、`RUNNING=True`、本拍仍进入采集并重新累计（RESET-1）；③ `SPF1` 调用 `OUTT=MU/OUTB=MD`（物理量程）；④ `DATA_REASON/WINDOW_T/ERR_*` 等为最近完成窗口快照，`DATA_REASON=2` 结算点死分支、积累阶段不实时写（DATAREASON-1）；⑤ `MAN_RESP_ACTIVE` 可跨窗口保留，手动响应统计可能落在不同窗口（MANRESP-1）；⑥ 历史缩小时只清指定摘要项，`H_PT~H_TZ_CD` 由 `H_VALID=False` 屏蔽（HISTORY-1）；⑦ 当前有效窗口先入库后同拍融合，弱推荐 `FINAL_VALID=False` 时四组 `*_REASON→5` |
| `src.blocks.APCM` | `APCM.txt` (ST) | APC 智能综合控制模块：组合 PID、RSF、观测器、重叠控制、PIDZZD 自整定与 APCMAUTOPARA 推荐应用。**关键事实**：① 一个长期存在的 `APCM(license_context)` 对象模拟 CODESYS 普通 `VAR` 功能块实例，所有参数/状态/触发器/子 FB 跨扫描保持，`RETAIN` 仅是重启恢复概念、本轮不做文件持久化；② `step(dt_ms, *, SP, PV, OC, TS, TP, zlout_ref, RM=None, OUTT=None, OUTB=None, SADD=None, SSUB=None, ZLEN=None, ZSYK=None)` 中过程量每拍传入并在授权前写入实例字段，`PT/TI/...` 等配置不进 `step()` 默认参数，上位/HMI 直接写 `self.*`；③ 外层授权失败只累加 `BD_ERROR6` 并冻结内部状态，成功一拍外层 APCM + 内层 PIDZZD 共 2 次授权；④ `ZLOUT` 必须绑定外部 `RealRef`，仅在 `ZLEN=True` 且旧 `AV` 触发 `R_TRIG02.Q=True` 的上升沿累计一次，`ZLEN=False` 不调用 `R_TRIG02`；⑤ `MM` 是一次性手动输出命令，手动 PID 路径末尾清零；⑥ 内部 PID/RSF/GC/CD 按 APCM 源内嵌顺序迁移，不调用 `APCPID` 替代；⑦ `PIDZZD1` 和 `APARA1` 为真实子实例，APARA 人工应用成功直接写回 APCM `self.*` 参数 |

## 授权模块（阶段一：一机一码）

迁移自 CODESYS SoftPLC 授权代码（`BD_MMYZ.zip`：`XTXX` / `BD_ZCM` / `BD_MMYZ` / `BD_MMYZ_ST`），位于 `src/licensing/` 与 `src/globals/`。

| 模块 | 来源 | 说明 |
|---|---|---|
| `src.licensing.XTXX` | `XTXX .txt` | 系统信息读取（仅 `SerialText` 通路有效）。**平台适配**：用可注入 `SerialTextProvider`（Win `MachineGuid`/Linux `machine-id`/macOS `IOPlatformUUID`）替代 `SysTargetGetSerialNumber`，规范化为 `PYPLC\|<OS>\|<ID>`；`EN=False` 保持上次输出；读取失败/空/非 Latin-1/超 255 字节 → `SerialOK=False` |
| `src.licensing.BD_ZCM` | `BD_ZCM.txt` | 注册码生成：四路 DWORD 哈希 → `ZCM1~3`（0~9999）；`SerialOK=False → ERROR=1000` 且注册码清零；`EN=False` 保持上次输出 |
| `src.licensing.BD_MMYZ` | `BD_MMYZ .txt` | 密码验证（可调用对象）：重算本机 `ZCM` → 推 `CheckMM1~4` → 比对 `BD_MM1~4`，错误码 `+1000/+100/+10/+1`；`SerialOK=False → 9000`；密码每次实时读取 |
| `src.licensing.BD_MMYZ_ST` | `BD_MMYZ_ST.txt` | 周期复验：失败每拍重验、成功后仅在 `(注入时间秒)%10==7` 时段强制复验一次（`OK+10000` 标志）；`OK/YZTG/ERR/ERR_N`；时间来自可注入 `DateTimeProvider`，**不读系统时钟** |
| `src.globals.LicenseContext` | GVL | 每实例授权全局容器：`BD_MM1~4`(0)、`BD_ERROR1~9`(0.0)、`KZQBDYZMK`(BD_MMYZ_ST)；`KZQBDYZMK` 实时读取本实例密码；`set_passwords` 拒 bool |

**阶段定位**：注册码/密码为可逆确定性算法，仅做一机一码 + 复制阻断 + 关键模块门控，**不是强密码学保护**；离线签名/公私钥/License 文件属阶段 2（见 `docs/RISKS.md::LIC-PHASE2-1`）。

## 离线注册码发码工具（厂商侧机密，独立存放）

授权方根据客户注册码 `ZCM1~ZCM3` 生成密码 `MM1~MM4` 的发码工具，出于**保密**考虑**刻意不放进本工程**，避免随转写代码一起分发到客户/部署侧。它是一个单文件、零依赖（仅标准库）的独立脚本，复制到任意装有 Python 3 的机器即可运行，不 import 本工程任何模块。

> 本工程内保留的 `src/licensing` 仅含客户/部署侧需要的注册码生成（`BD_ZCM`）与密码验证（`BD_MMYZ`）；厂商侧的"注册码 → 密码"发码工具单独维护于工程目录之外。

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

当前：**690 个用例全部通过**，覆盖：
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
- `APCCD` 35 个契约验证（提示词 A~I + R8 采样量化）：冷启动初值与双实例隔离 / **BLINK.TIMEHIGH=500ms 锁定**（量化到任务周期，相邻采样间隔恒 15 拍；源值 300 会吞脉冲/抖动，CD2）/ **TIMELOW 整数倍→整齐、非整数倍（TC=1.1）→抖动 `{3,4}` 已知限制锁定**（反证非 ceil 模型，R8）/ 首次采样上升沿在 TIMELOW 末 / **ST 顺序锁定**（CD1：窗口快照在 `STAT1.RESET` 之前，含两窗口异值反证"先 RESET 后快照"）/ `FOP1.TB=0.5s` 且与 `dt_ms` 解耦（dt=1000 仍 TB=0.5，反证非 dt/1000）、`FOP1.TC=TZ*2`、`KG=1` / `CD_BH` 全公式与 `SEL(AD,1,-1)` 正反作用 / TON 延时进入（TL 秒 → PT_ms）、`AV_TEMP` 钳幅 `min(max(x,CDL),CDH)` 字面顺序（CDL>CDH 反向恒落 CDH）/ **`CD_BH==0` 保持 FLG**（用 `CD_GD=0` 打到该分支）/ 退出阈值 `R_TRIG2` 仅回补一次（FLG 决定回补符号）/ TS 跟踪切除（进入拍允许一次回补后清零、持续不重复、冷启动不变 ZLOUT）/ `VAR_IN_OUT` 适配（入参直通、不用旧缓存、回灌模式）
- `APCHSACCUM` 24 个契约验证（提示词 A~F）：导入 + 冷启动初值（含 `IV` 非零时 `AV` 仍为 0）+ 双实例隔离 / **离散积算**（MC 缩放、连续累加 2→4→6）/ **`dt_ms` 不参与公式**（任意 dt 累加量相同、同序列不同 dt 输出一致）/ **MS 单次回绕**（`<` 直累、`==` 入 else、`>` 减一次、单拍跨多 `MS` 只减一次留 `AV>=MS`、下一拍才置 `IV`，反证非取模/循环）/ **负值与 IV 恢复**（当拍不修正、下拍置 `IV`、含非零 `IV`）/ **RS 上升沿复位顺序锁定**（先积算后复位、`LR` 存积算后值、电平持续不重复、下降后再上升再触发、回绕拍 `LR` 存回绕后值）/ **源字面量锁定**（默认 `MS=1.797693134862e38`、反证非 `e308`、`bPositiveAccum=True` 不挡负输入）
- `APCPIDZZD` 48 个契约验证（提示词 A~L）：初始状态（`PT1/TI1=0`、`ZJSBZ/FJSBZ=True`、`JSSJZ/JSSJF=20`、数组全零）/ **授权门控**（失败累加 `BD_ERROR5`、`>999999999→1e8`、失败保持全部状态、恢复后下拍继续、每拍仅一次 `KZQBDYZMK.step`）/ **500ms 扫描语义**（TON `PT=5000ms`、`JSSJ/JSSJ2/SQSJ` 固定 `+0.5`、反证非 `dt/1000`）/ **严格阈值**（量程 `0.5%` 恰等不进 TON1/TON2、刚超才计时、`JSSJ==5` 不写/`>5` 才写、`SQSJ` 恰等阈值不清/超才清）/ **离散积算与方向**（积算后 `HSACCUM1.AV` 离散累计、与 `dt` 无关、`PV>SP→ZJSBZ`/`PV<SP→FJSBZ`、`ZDPC` 取最大、复位调用保留上次 `I1`）/ **结束移位复位**（正→`JS_Z`/负→`JS_F`、历史移位 `[1]<-[2]<-[3]<-当前`、冷启动两标志独立 IF 都执行、末尾清零计数与标志）/ **发散/振荡识别三档**（`>1.1`/`>0.7`/`>0.4` 大/中/小幅、正负两路、`PVMAXDI` 不满足不调、`SLL>=10` 不调、识别成功只清两组面积前部）/ **理论 TI 三档**（收敛率≈1.0/0.6/0.2 → `0.5/0.4/0.3` 倍时间、`80%原值+20%受限理论值`、正负两路）/ **R_TRIG 长偏差**（首跨 `2.5x` 调一次、持续不重复、`4x` 第二次调、`SADD` 令 `JSSJ2` 归零）/ **非自动精确复位**（`RM≠1` 清 `JS_*/ZJSBZ/FJSBZ/JSSJ/JSSJ2`，`PT1/TI1/SQSJ/ZDPC/JSSJZ/JSSJF/TON/HSACCUM/R_TRIG` 保留）/ **输出限幅**（`[-0.7PT,3PT]`、`[-0.7TI,3TI]`、发生在主逻辑末尾）/ **`PT1K/TI1K` 注释冲突**（顶部清零后识别分支仍得非零增量）
- `APCPID` 56 个契约验证（提示词 A~N）：初始默认字面量（`AV=0`/`CYCLE=0.5`/`OutRH=5`/`OutRL=0`/`DI=0`/`SVH=30`/`SVL=0.5`/`KP=KI=PT1K=TI1K=0`、未初始化状态全零、`PIDZZD1` 为独立真实实例且共享 `LicenseContext`）/ **授权门控**（失败累加 `BD_ERROR1`、`>999999999→1e8`、失败保持全状态且不调用 `PIDZZD1`、恢复后下拍继续、**成功一拍 2 次授权 / 失败 1 次**）/ **CYCLE 与参数修正**（`CYCLE<=0→0.5` 持久化、`MU-MD=0→MD+1e-5` 持久化、`TIi<=0→0.001`、`PTt<=0→0.001`、`KD<=0` 仅本拍 `0.001`、`TD<0` 仅本拍 `0`、PID 用 `CYCLE` 非 `dt/1000`）/ **旧 EK 顺序**（`TIi` 的 SVH 判断用上一拍 `EK`，本拍 EK 在其后重算）/ **ATE/TS/preRM**（`ATE=False` 不动 RM、`TS=True&RM!=4` 存 preRM 切 4、`TS=False&preRM!=4` 还原、`preRM` 跨拍保留）/ **误差与微分**（0.9/0.1 滤波、`AD=1` 取反 EK 但 `EK_LAST` 留未取反值、`DEK=PV-PV_LAST` 及 AD 取反、末尾历史移位）/ **跟踪模式**（RM=3/4、增量/位置、`OutRH`/`OutT`/`OutB` 限幅、`TM` 令本拍 SP=PV 传入 PIDZZD1、跟踪不复位 MM）/ **手动模式**（CASE `MM=1/2/3/4/非法`、增量/位置、位置式依赖 `LASTUKOUT`、`OutRH` 限幅、`MM` 末尾归零、`TM`）/ **自动核心**（RM=1/2/非法均走自动、死区 `DU=0`、积分分离 `SI`、`B1/B2/C1/C2/C3/C4` 数值、活动 `DU_TEMP` 公式、`DU_TEMP` 恰 `1e10→DU=0` 反证略小则通过）/ **自动增量**（`OutRH-OC` 限幅、`DUOUT=DU+OC`、`SADD→min(0)`/`SSUB→max(0)`/二者同真顺序）/ **自动位置**（`UK_1+DU`、`OutT-OC`/`OutB-OC`、`UKOUT vs LASTUKOUT` 的 `OutRH` 限幅、禁止方向保留旧 `AV_TEMP` 且 `UK=AV_TEMP-OC`）/ **OutRL**（`>ABS(OutRL)` 才提交 AV、等于不提交、负 OutRL 用 ABS）/ **PIDZZD 顺序与延迟**（本拍 PID 用旧 `PT1/TI1`、历史先于 PIDZZD1 更新、回写一致）/ **注释冲突**（非法 RM 走自动、OutRL 非限速器、KD/TD 局部不跨拍持久化）
- `APCSPFINDER` 47 个契约验证（提示词 A~K + 1 条边缘回归 `APCSPFINDER-EDGE-1`：历史 `SP_AUTO_OK=True`+`EN=False`+`SP_AUTO_EN=False`+允许替换+现场 SP 可疑 → 现场被排除、自动被拦下、落 `SP_USE=PV`/源0/因4，按源 ST 原样保留不修复）：**初始默认状态**（10 个输出与 17 个内部状态全 0/False、无 Context/无授权依赖）/ **阈值与量程兜底**（`CYCLE<=0→CYCLE_S=0.001`、`PVMU=PVMD/OUTT=OUTB→量程 100`、`PV/AV/SP_BAD_ABS>0` 优先、比例 K 为负走 `MAX(K,0)`、`PV_TH/AV_TH/SP_BAD_TH` 最小兜底、`EN=False` 仍算阈值）/ **首拍稳定段**（首扫 `PV_1=PV`/`D_PV=D_AV=0`、同拍即进入稳定段 `STABLE_T+=CYCLE_S`/`N=1`/`PV_SUM=PV`）/ **稳定/不稳定边界**（`D_PV==PV_TH`/`D_AV==AV_TH` 仍稳定、刚超即不稳定、进入先清零再同拍累计、不稳定清统计且 `PV_MAX/MIN` 回当前 PV）/ **自动 SP 合格与可信度**（`STABLE_T` 恰 `MAX(SP_STABLE_T,1)` 合格、段范围恰等限值合格、`STABLE_N=0` 不合格、`SP_AUTO=` 段均值、`CONF∈[0,1]`、稳定时间越长基础可信度越高、波动越小最终可信度越高）/ **历史保留语义**（不稳定段保留 `SP_AUTO/OK/CONF`、`SAMPLE_OK=False` 冻结稳定段但更新 `PV_1/AV_1`、`SP_AUTO_EN=False` 仅清 `OK/CONF` 保留 `SP_AUTO` 与稳定段、唯 `RESET` 清 `SP_AUTO`）/ **EN=False 语义**（自动段不推进/`PV_1/AV_1` 不更新、阈值仍更新、`SP_TAG_BAD` 仍重算、最终 SP 仍重选并可用历史 `SP_AUTO`）/ **RESET 当拍**（自动状态重置且不进稳定段、但仍执行最终 SP 选择：人工优先/现场可用/无则 `SP_USE=PV` 且无效）/ **SP_TAG_BAD 边界**（`SP_TAG_EN=False`/`SP_AUTO_OK=False`→False、差值 `==SP_BAD_TH`→False、刚超→True、`EN=False` 仍重算）/ **最终优先级与编码**（人工源1因1、现场正常源2因2、现场可疑不替换源2因5、现场可疑允许替换且自动有效源3因6、无现场自动有效源3因3、无有效 `SP_USE=PV`/`VALID=False`/源0因4）/ **CYCLE 与 dt_ms 分离**（同 CYCLE 不同 dt_ms→`STABLE_T` 相同、不同 CYCLE 同 dt_ms→`STABLE_T` 按 CYCLE 缩放）
- `APCRSFNAUTOPARA` 64 个契约验证（仅采纳修复版 Bug1：`RESET-1` RESET 清 `WIN_SP/PV/AV_SUM`；`DATAREASON-1` Bug2 实时补丁经复核撤回不同步、死分支按源保留；提示词 A~K + 冷启动首拍回归 `APCRSFNAUTOPARA-START-1`：初始化块不重置 `PV_1/AV_1/TP_1` 等"上一拍"变量，首笔样本 `D_PV/D_AV/ABS(TP-TP_1)` 相对零基准，按源 ST 原样保留）：**A 初始/冷启动/RESET**（输出与内部状态全 0/False、`SPF1` 为真实跨扫描 `APCSPFINDER` 子实例、冷启动置 `INIT_DONE`、`EN=True/RESET=False` 首拍即可采集、`EN=True/RESET=True` 时 `RUNNING=True` 但不采样、RESET 当拍 `WINDOW_DONE=False`、RESET 清历史与 `H_VALID`、RESET 置 `SP_USE=PV`）/ **B CYCLE/dt_ms/量程**（`CYCLE<=0→CYCLE_S=0.001`、同 CYCLE 不同 dt_ms 结果一致、`PHY_RANGE_EN` 取 MU/MD 或 PHY、量程无效内部 100 但 `RANGE_OK=False`、`H_N` 限 1..24、`BLEND` 限 0..1）/ **C COLLECT_MODE**（0/1/2 × TS 真值组合、非法 mode 自然无样本）/ **D APCSPFINDER 集成**（人工/现场 SP、SP 无效时 `SP_WORK=PV`/`ERR=0`、`SPF1.PV_RANGE=MAX(E4_IN*2,1)`、`SPF1.OUT_RANGE=OUT_RANGE`）/ **E 累计/事件/边界**（首样本 `WIN_INIT`、正负偏差面积、`D_PV` 噪声、手动事件 `D_AV>=MAN_TH`、穿越 `ERR*ERR_1<0`、`RSF_STEP`/`RSF_LEVEL` 进正区间事件、闭锁 `0→正`、`E1~E4` 区间停留边界）/ **F CALC_NOW 上升沿**（首次 True 成沿、持续 True 不重复结算、`EN=False` 时更新 `CALC_OLD` 防误沿、`CALC_R` 须满足 `MIN_WIN_T`）/ **G DATA_REASON 优先级**（量程 3 / SP 无效 6 / 事件不足 4 / 响应不足 5 / 正常 1；并锁定 `DATA_REASON=2` 为结算点死分支、积累阶段亦不实时写入）/ **H 单窗口推荐**（E/AO 单调与上限、慢响应强推荐→`RSF_REASON=2`+AO×1.10、振荡强推荐→`RSF_REASON=3`+`ZF_K>=0.5`+HYS 降、慢+振荡优先 2、无效窗口 5）/ **I 历史写入/H_N/指针**（有效窗口入库并同拍自融合、`H_IDX` 回绕、`HISTORY_COUNT` 封顶、缩小 `H_N` 清超范围 `H_VALID`、增大不伪造）/ **J 三阶段融合**（严格强推荐 `MATCH_LEVEL=1`、放宽强推荐 `MATCH_LEVEL=2`、单窗口弱推荐 `FINAL_VALID=False`+`RSF_REASON=5`、`FUSE_SUM_W=0` 回退 `W_*`）/ **K 源边缘语义**（修复版 RESET 清 `WIN_SP/PV/AV_SUM` 且下一窗口均值无残留污染、`EN=False` 末尾仍更新 `CALC_OLD/PV_1/AV_1/TP_1/RSF_LEVEL_1/RSF_LOCK_LEVEL_1`、无新窗口时输出保持）
- `APCMAUTOPARA` 60 个契约验证（提示词 A~K）：**A 初始/默认回退**（冷启动默认值、无历史时四类推荐回退到当前输入参数、单调/限幅、`SPF1` 真实独立实例、历史数组 1-based）/ **B CYCLE/量程**（`CYCLE<=0→CYCLE_S=0.001`、同 CYCLE 不同 dt_ms 统计一致、量程无效内部 100 但 `RANGE_OK=False`、`OUT_LIMIT_RANGE` 兜底、`H_N` 限 1..24）/ **C RESET 与 EN**（`EN=False&RESET=True` 不采样；**关键差异** `EN=True&RESET=True` 先复位后本拍仍采集、`RUNNING=True`、`WIN_SP/PV/AV_SUM` 清零后重新累计、冷启动 `SPF1` 调用两次）/ **D APCSPFINDER 集成**（人工/现场/自动 SP、`SP_WORK=PV` 当无效、镜像输出、不写现场 SP）/ **E 自动采样**（COLLECT_MODE/RM/TS、偏差面积/峰值/过零 `ERR*ERR_1<0`、AV 事件合并、噪声样本）/ **F 手动与跨窗口响应**（MAN_MERGE_T 合并、新动作打断旧响应、响应观察跨窗口保留、MAN_BAD_N）/ **G 窗口快照**（CALC_NOW 上升沿、持续高不重复、`EN=False` 更新 `CALC_OLD`、`DATA_REASON=2` 不实时写且结算点不可达）/ **H DATA_REASON 优先级**（3/6/4/5/1）/ **I PID 推荐**（公式基准/融合/理论 TI/原因优先级/限幅）/ **J RSF/观测器/重叠控制**（单调/AO 上限/理由优先级/限幅）/ **K 历史与融合**（入库/回绕/同拍融合/三阶段/弱推荐 reason=5）
- `APCM` 53 个契约验证：初始导出与真实子实例（`PIDZZD1` 共享 `LicenseContext`、`APARA1` 真实实例）/ `step()` 要求 `SP/PV/OC/TS/TP/zlout_ref` 必传，`RM/OUTT/OUTB/SADD/SSUB/ZLEN/ZSYK` 仅非 `None` 时覆盖，且不接 `PT/TI/.../KP/KI/KD` 默认参数 / 过程量和可选覆盖项在授权前写入 / 无内部 ZLOUT 备用引用 / 可调参数（含 `KP/KI/KD`）跨扫描保持且不被 `step()` 覆盖 / `CYCLE/KD/TD/ZSYK/OUTT/OUTB` 源赋值写回 / `FOP1/FOP2.TB=0.5` 源默认值 / `BLINK1/BLINK2` 高电平 500ms、`TIMELOW=TC*1000/TC_CD*1000` 不量化、固定半秒计数不随 `dt_ms` 缩放 / 授权成功 2 次真实授权、失败 1 次且冻结触发器/定时器/PIDZZD/APARA 等内部状态与命令位 / `BD_ERROR6` 回绕 / `ZLOUT` 仅旧 `AV` 的 R_TRIG02 上升沿累计、连续限位不重复、`ZLEN=False` 不调用 R_TRIG02 / `MM` 在 `RM=0` 和强制手动路径均单次消费清零，跟踪分支不清 / 观测器 `AV_GC` 的 `PCMMS/GCEN` 源 SEL 门控 / TS 下 RSF 清零、RSF 2/3/4 档触发、快退（`|EK_R|<E1_FAST_OUT`）、慢退（`CT_RSF_OUT` 达 `2*TL_OUT`）、反向退出闭锁、超时解锁、升档解锁、整理同拍清 `AV_R_TEMP` / CD `TL=0` 与 `TL>0` 进入/恢复回补、严格低于 `CD_GD` 不进入 / 内嵌 PID 自动/手动/跟踪（RM=3 增量/位置）三分支、自动路径避开 `F_TRIG1/F_TRIG2` 冷启动误边沿 / 内嵌 PID 的 `TIi` 使用旧 `EK` / PIDZZD 接收 `SP` 而非 `SP_V` / APARA 经完整 `step()` 链路在 PIDZZD 之后执行，RESET/CALC/APPLY 上升沿消费，PID/RSF/GC/CD 组强弱推荐应用写回 `self.*` 并清按钮，CD 成功应用不改本拍 `AV`，无推荐或强推荐组不可用时不写参数 / APARA 全镜像字段经 `step()` 抽样、HMI 覆盖 `APARA_APPLIED_PT` 下一拍生效
- **授权模块** 61 个契约验证（任务书 A~F）：
  - `dword`（A）：32/16 位回绕 / bool 不得被静默当数值（`to_dword`/`to_word`/`dword_*` 全拒 bool）
  - `XTXX` + Provider（B）：固定 Provider 成功 / **读取失败 `Serial_result!=0`** / **读取成功但空序列号 `Serial_result=0` 且 `SerialOK=False`（严格保留源 ST 语义）** / 非 Latin-1 与超 255 字节仅置 `SerialOK=False`（`Serial_result` 透传底层不改写）/ 恰 255 字节通过 / `EN=False` 保持上次输出 / 平台 Provider 规范化逻辑（不依赖真实机器）/ 辅助字段不伪造
  - `BD_ZCM`（C）：同一 SerialText 多次一致 / 不同 SerialText 不同码 / **固定回归向量 `PYPLC\|TEST\|MACHINE-0001 → ZCM(1159,8702,2216)`**（标注为迁移回归向量，非 CODESYS 黄金样本）/ 失败 `ERROR=1000` 且码清零 / `EN=False` 保持
  - `BD_MMYZ`（D）：正确四组返回 0 / 单密码错对应 `1000/100/10/1` / 组合 `1011` / 全错 `1111` / 序列号不可用 `9000` / 更新密码下一次立即生效 / 发放链路 `BD_ZCM→derive→BD_MMYZ=0`
  - `BD_MMYZ_ST`（E）：初始 `OK=9000` / 冷启动首拍验证 / 失败每拍重验 / 改对下一拍恢复 / 成功态非时段不重验 / `秒%10==7` 设 `OK=10000` 标志 / 同时段仅一次强制验证 / 离开时段去标志 / `ERR_N` 仅失败递增且成功不清零 / `ERR_N` 回绕 `>999999999→1e8` / 同一时间点重复调用不自动推进时间
  - `LicenseContext`（F）：默认值（密码 0 / `BD_ERROR1~9=0.0` / `KZQBDYZMK` 为 `BD_MMYZ_ST`）/ 两 Context 隔离 / `KZQBDYZMK` 实时读最新密码 / `set_passwords` 拒 bool / 完整发放链路门控通过
- `APCGCQ` 33 个契约验证：冷启动初值 / 首个 BLINK 周期内无采样事件 / **ST 执行顺序锁定**（GG1：第一次/第二次采样事件 `JZ_ZUP` 取旧 `JZ_Z`，含从 `STAT01.COUNTER=0` + `JZ_ZUP=100` 双观测面直接锁定"采样快照在 STAT.RESET 之前"的明示测试）/ 采样事件每 `(TC*1000+TIMEHIGH)ms` 一次 / `BLINK_TIMEHIGH_MS=500` 与 `FOP01_DEFAULT_TB_SEC=0.5` 模块级常量锁定 / **GG4 项目修正约定锁定**：dt=100/TC=1.0 时相邻采样事件间距恰为 15 拍（即 1500ms 而非 1300ms）/ 首拍 FOP `α·KG·IN` 数值 / `JTAV = (IN-INSP)*GC1` / `DTAV = AV*GC2` / `K` 倍率 / **死区 SEL 恒假分支锁定**（GG2：`IN==INSP` / `IN<INSP` / `IN>INSP` 三种关系均走 IN0，反证不能误改成 `IN!=INSP`）/ RLIM 对称速率限幅串入主通路 / LIM 幅值限幅串入主通路 / **OUTV vs OUTH/OUTL 分层验证**（GG5：单层 OUTV 紧 / 单层 OUTH 紧 / 两层都紧最终落 OUTH / 反证 OUTV 不是幅值限）/ 模块导出基础健康检查 / 嵌套 FB 实例不共享状态 / `STAT01` 在采样事件 RESET / AV 衰减验证（反向证伪错误 ST 顺序）

## 依赖

无第三方依赖，仅需 Python 3.9+ 标准库。
