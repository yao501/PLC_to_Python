# 黄金轨迹格式与采集计划（GOLDEN_TRACE_FORMAT）v1.2.1

> 阶段 0.5 产物。定义"真 PLC 对拍"用的轨迹格式与第一批最小采集清单，并**显式记录外部采集阻塞**。
> v1.1（外部评审 P1 修正）：示例改为**合法 JSONL**（无注释）；REAL 增加**原始位模式**通道与 NaN/Inf 表示；meta 补 schema 版本/工程哈希/运行时版本/复位类型/采样相位；长跑类拍数要求上调（§3）。
> v1.2（二轮修正）：meta 补 `stimulus_hash`/`library_versions`/`task_config_checksum`/`var_types`/`output_scope`（逻辑输出 vs 总线输出区分）；采集清单增加 **#7 整数中间溢出用例**（裁决 `int_intermediate_policy`）。
> v1.2.1（三轮修正）：判定口径修正（F1 = 容差+编码检查，非位级）；`bits` 规范编码（大端/大写/定长）；哈希统一 SHA-256；#7 改表达式变体差分法。
> 关联：`RISKS.md::PLATFORM-GOLDEN-EARLY-1`、`docs/TARGET_PROFILE.md`（判定基准）。

---

## 1. 目的

把对拍验证从阶段 6 提前。先**定格式 + 列最小集 + 备好回放脚手架**，真机数据一旦采到即可回放断言，尽早暴露底层语义错误（避免 IR/CFC/ST 成型后才发现地基错、返工面大）。

## 2. 轨迹格式（JSONL，每行一拍，**必须是合法 JSON——不允许注释**）

字段说明（规范文本，不进数据文件）：`scan`=逻辑拍序号（从 0）；`t_ms`=逻辑相对时间（= scan × cycle_ms）；`wall_ns`=真机采集时的单调时钟纳秒（可选，用于核对采样相位）；`inputs`=本拍输入快照；`params`=本拍生效关键配置（可选）；`outputs`=本拍提交输出；`internals`=选定中间量（对拍重点）；`bits`=REAL/LREAL 量的**原始位模式**（十六进制字符串，binary32 8 位 / binary64 16 位），键与所属通道同名。

```json
{"scan": 0, "t_ms": 0, "wall_ns": 812345678901, "inputs": {"Start": false, "PV": 50.0, "SP": 50.0}, "params": {"PT": 100.0, "TI": 150.0}, "outputs": {"AV": 0.0, "Motor": false}, "internals": {"TON1.ET_ms": 0, "FOP1.AV": 0.0}, "bits": {"outputs.AV": "00000000", "internals.FOP1.AV": "00000000"}}
```

- **位模式通道 `bits`**：E 模式可省略；**F1/F2 对拍必须携带**——十进制 JSON 数字往返不保证保留目标端的确切位型。**规范编码（v1.2.1）**：IEEE 754 位型视为无符号整数，**大端字节序（最高有效字节在前）、大写十六进制、无 `0x` 前缀、定长**（binary32 = 8 字符，binary64 = 16 字符）。NaN/Inf 一律**只**出现在 `bits`（如 `"7FC00000"`），对应十进制字段写 `null`（JSON 无 NaN/Inf 字面量）。
- 一个被测对象（一个 POU/FB 实例配置）对应一个 `.jsonl` 文件 + 一个 `meta.json`。
- `internals` 是对拍的关键——只比对输入/输出不足以定位"哪一步语义错"，必须采关键中间量。
- **判定口径（v1.2.1 修正，与 `TARGET_PROFILE §4/§4.1` 对齐）**：**E** = 容差判定；**F1** = 容差判定 + 边界值 binary32 可编码检查（**不是位级判定**——F1 不承诺 bit-exact）；**F2** = 位级判定（只认 `bits`）。
- **哈希规范**：`project_hash`/`stimulus_hash`/`task_config_checksum` 一律 **SHA-256**；被哈希对象先做规范化（文件按原始字节；JSON 数据按 UTF-8、键名排序、无空白的规范序列化），meta 中记录哈希对象说明。

`meta.json`（v1.1 补齐字段）：

```json
{
  "schema_version": "1.2",
  "target": "CODESYS SP16.1",
  "runtime_version_full": "TBD（含 Patch/HotFix 完整版本串）",
  "library_versions": {"Standard": "TBD", "Util": "TBD"},
  "cpu_os": "TBD",
  "project_hash": "TBD（被采 CODESYS 工程文件哈希，锁定采集时工程状态）",
  "task_config_checksum": "TBD（任务配置：周期/优先级/watchdog 设置的校验值）",
  "stimulus_hash": "TBD（本轨迹全部 inputs 序列的哈希，防止回放时输入串换）",
  "block": "APCM",
  "cycle_ms": 500,
  "conformance_target": "E",
  "reset_type": "cold",
  "sampling": {"method": "trace", "phase": "end_of_scan", "aligned_to_task": true},
  "nan_inf_encoding": "bits-only, decimal=null",
  "var_types": {"Start": "BOOL", "PV": "REAL", "AV": "REAL", "TON1.ET_ms": "TIME"},
  "output_scope": "logical",
  "params": {},
  "notes": "采集日期/工况/版本"
}
```

- `var_types`：轨迹中每个通道的 IEC 声明类型表——回放器据此选比对算法（容差/精确/位级），不靠猜。
- `output_scope`：`"logical"` = 采的是任务内逻辑输出变量值；`"physical_bus"` = 采的是总线/IO 驱动侧实际值（两者可能因驱动缩放/字节序/更新相位不同）。对拍基准默认 `logical`；`physical_bus` 轨迹仅用于阶段 7 IO 链路验证，**不得混用**。

- `reset_type`：本条轨迹起点的复位类型（`cold`/`warm`/`origin`/`online_change`），对 RETAIN/边沿冷启动语义对拍必不可少。
- `sampling.phase`：采样点在拍内的位置（`end_of_scan`=输出提交后），保证与本平台"第 5 步后"快照对齐；`aligned_to_task` 为 false 的数据只能做趋势参考、不能做逐拍断言。

## 3. 第一批最小采集清单（覆盖最易错语义）

| # | 被测 | 覆盖语义 | 关键中间量 |
|---|---|---|---|
| 1 | `TON` | 定时器累积、`PT` 边界、`ET` 饱和 | `ET_ms`, `Q` |
| 2 | `R_TRIG`/`F_TRIG` | 边沿、冷启动首拍 | `Q`, 上一拍 CLK |
| 3 | 一个含反馈环的小 CFC | 反馈起点读上一拍值 | 反馈点变量 |
| 4 | `APCHSACCUM`/`APCSTATISTICS` | REAL 长跑递推漂移 | `AV`/`AVG` 逐拍 |
| 5 | 冷启动 + 热启动（RETAIN） | 初始化 vs 恢复语义 | 保持变量 |
| 6 | `APCM`（小工况） | 综合：SEL 极性、PID 三态、RSF 分档 | `AV_P/AV_R/AV_C/AV` |
| 7 | 整数中间溢出小程序（如 `WORD` 乘加链） | **裁决 `int_intermediate_policy`/`int_native_width`**（`IR_SPEC §5.4`、`RISKS::PLATFORM-INT-WIDTH-1`）。**方法约束：不得把待观察的中间值赋给变量**——赋值本身触发截断、会改变被测语义；改为构造**多个仅中间步骤不同的表达式变体**，只采各变体的**最终存储值**，由结果差分反推中间位宽 | 各表达式变体的最终存储值 |

拍数要求（v1.1 上调）：逻辑/时序类（#1/#2/#3/#5）至少 **50–200 连续拍**（覆盖 PT 边界、多次边沿、复位往返）；**长跑积算/统计类（#4，APCHSACCUM/APCSTATISTICS）至少数万拍**（≥ 40 000 拍 ≈ 500ms × 5.5 小时连续采集，或以 CODESYS Trace 降频长录实现）——10–20 拍无法暴露 float 递推累积漂移；APCM 综合工况（#6）≥ 1 000 拍且含至少一次工况切换。反馈/振荡类（如 BLINK"未真机验证"项）需覆盖 ≥ 20 个完整振荡周期。

## 4. 采集方法（在真 CODESYS SP16.1 上）

候选手段（按可控性排序，待现场条件定）：
1. **在线监视 + Trace**：CODESYS 自带 Trace 录变量，导出 CSV → 转本格式。
2. **应用内打点**：在目标工程加一段只读记录逻辑，把选定变量每拍写文件/通信（不改控制逻辑）。
3. **通信侧录制**：经 OPC-UA / Modbus 周期读，注意采样与 500ms 任务边界对齐（否则采到中间态）。

## 5. ⚠️ 外部采集阻塞记录（诚实声明）

**当前无法由本工程内部完成黄金轨迹的"实采数据"**：

- 需要访问**真实的 CODESYS SP16.1 软 PLC 运行环境**（在线监视/Trace/打点），这是本仓库外的资源。
- 本阶段**已完成**：轨迹格式、最小采集清单、采集方法、回放脚手架的规格（本文件）。
- **阻塞项**：第一批真机数据的实际采集——依赖用户提供运行环境或导出数据。
- 登记于 `RISKS.md::PLATFORM-GOLDEN-EARLY-1`，状态 `🟨 in-progress（格式就绪，实采待外部）`。

## 6. 回放脚手架（阶段 1 随引擎落地）

- 读 `.jsonl`，逐拍把 `inputs`/`params` 喂进引擎 `scan`，取引擎 `outputs`/`internals` 与轨迹比对，按 `meta.conformance_target` 选容差/位级判定。
- 输出差异报告：首个偏离拍、偏离量、涉及变量——定位语义错。

## 7. 验收要点

- [ ] 格式与 meta 定稿（schema_version 1.2：合法 JSONL、`bits` 位模式、NaN/Inf 约定、reset_type、采样相位、stimulus_hash、var_types、output_scope），回放脚手架接口确定（随阶段 1 实现）。
- [ ] 最小采集清单 **7 项**确认（含整数中间溢出裁决用例），各项拍数满足 §3 要求。
- [ ] 真机实采阻塞已登记并向用户明确（见 §5）。
