# PLATFORM-IMPORT-TRIAL-1 试验结论（2026-07-09，含补充样本）

样本一：`sample/test.export`（原生 .export 归档；CFC_TEST / ST_TEST / PLC_PRG / MainTask）。
样本二：`sample/test_fb_feedback.xml`（**PLCopen XML** tc6_0200；覆盖采集清单 ②反馈环 ③TON 实例框）。
解析器：`parse_export.py` / `parse_plcopen.py`（一次性试验代码）；
回归锁：`tests/test_import_trial.py`（5 项）+ `tests/test_import_trial_plcopen.py`（8 项）。

## 结论：可行 ✅

原生 `.export`（归档序列化 XML，非 PLCopen）**可解析、可识别**，无需 CODESYS 参与：

| 目标 | 结果 |
|---|---|
| POU 树与语言 | ✅ PLC_PRG(ST)、ST_TEST(ST)、CFC_TEST(CFC) 正确分类（TypeGuid `6f9dac99…`，CFC/ST 由 Implementation 内 Items vs TextDocument 区分） |
| ST 源码 | ✅ 接口区+实现区逐行还原（TextLines **数组顺序=文档顺序**，Id 是创建序号非行号——导入器不得按 Id 排序） |
| CFC 图 | ✅ 源框（变量/常量表达式+Negated）、调用框（GE/SEL/ADD，`KindOfCall=Operator`，输入/输出管脚 Id 表）、汇框（写变量）、9 条连线（SourcePinId→DestPinId）全部还原，SEL 三脚接线与截图一致 |
| 反馈起点标记 | ✅ 调用框带元素级 `IsFeedbackStart` 字段（本样本全 False；反馈环已由样本二补齐，但该字段在含环 .export 中的精确落点仍缺**可选**对照，`PLATFORM-CFC-FEEDBACK-MAP-1` 转 🟨） |
| CFC 顺序模式 | ✅ POU Properties 的 `UseExplicitExecutionOrder`（本样本=自动数据流） |
| 任务配置 | ✅ MainTask：Cyclic / priority 1 / **500ms** / watchdog 关闭 / 调用 PLC_PRG——与项目"单任务固定 500ms"假设吻合 |
| 目标设备 | ✅ `CODESYS Control Win V3 x64` → Windows x64（`int_native_width=64` 候选） |

## 关键发现（影响 D3，须冻结评审知晓）

**自动数据流顺序模式下，导出不存储每元素执行序号**——编辑器显示的 0..4 是派生值。
因此 D3"导入保留原始序号"只对**显式顺序模式**直接成立；自动模式下导入器必须
重建顺序。本试验用"拓扑排序 + 同层按元素 Id 升序"重建，结果与编辑器显示序号
**逐一吻合**（GE:0 → SEL:1 → A汇:2 → ADD:3 → A汇:4）——但这只是**单样本、无环图**
的吻合，不得当作已验证算法（分支/多页/反馈环样本待补），登记
`RISKS.md::PLATFORM-CFC-AUTOORDER-1`。

> **证据来源说明（用户 2026-07-09 澄清）**：截图中的序号 0..4 是用户在**导出之后**
> 在 CODESYS 里执行"按执行顺序排序"得到的显示结果（导出时未做该操作）。这与本试验
> 结论一致且互为印证：导出内容不随该操作变化（序号本就不存储），截图序号即 CODESYS
> 自身数据流排序的输出——我们的重建与其吻合是对重建假设的正面信号，但单样本限制不变。

## 样本二（PLCopen XML）：②③ 证据 + 三个新发现

程序逻辑（识别还原）：`A := SEL(TON(GE(A,200), T#5S).Q, A, 0)` 一路 + `ADD` 自反馈累加
一路（`ADD.In2` 经 connector 中继接回 ADD 自身输出，汇入 A）。

| 采集目标 | 结果 |
|---|---|
| ② 反馈环 | ✅ 环以纯拓扑存在并可检测（ADD 自环）；环入口 = 执行序号最小元素（ADD=1） |
| ③ FB 实例框 | ✅ `typeName="TON" instanceName="TON1"` + `CallType=functionblock`（与 operator 框可区分）；实例声明在 VAR（`TON1: TON`）；管脚 formal 名 IN/PT/Q/ET 直接可读 |

新发现：

1. **PLCopen XML 每元素显式存储 `executionOrderId`**（block/outVariable 均带）——与
   .export 自动模式"序号不存储"相反。**PLCopen XML 是阶段 5 导入器候选首选载体**
   （D3"导入保留序号"在该载体上直接成立），显著缓解 `PLATFORM-CFC-AUTOORDER-1`。
2. **PLCopen XML 无显式反馈起点标记字段**（全文无 feedback 字样；`IsFeedbackStart`
   是 .export 专有）。反馈语义在该载体上须由"序号 + 拓扑"推断：入环元素执行序号
   最小，其被回接的输入读上一拍值——与 `LOAD_PREV` 映射假设一致，但原生
   `IsFeedbackStart` 的精确落点仍待同工程 .export 对照（可选，不阻塞冻结）。
3. **Patch 级别确认**：fileHeader `productVersion = "CODESYS V3.5 SP16 Patch 1"`
   （设备版本 3.5.16.10）→ `TARGET_PROFILE` v1.3 该 ⬜ 项已补。
4. 连线一律经 `<connector>` 中继元素，导入器须沿 refLocalId 链解引用（已实现验证）。

## 尚未覆盖（不属本试验范围）

语义 lowering（GE/SEL/ADD → CALL_STD 属阶段 3/5）；显式顺序模式样本（用户裁决暂缓）；
含环 .export 的 `IsFeedbackStart` 落点对照（可选）；多任务样本；GVL/RETAIN 导出段；
`.project` 载体；用户自定义 FB 被 CFC 调用的样本（清单④）。

## 采集清单状态

① 显式执行顺序样本——**用户裁决暂缓**（2026-07-09；PLCopen XML 已证实序号显式存储，
需求弱化，留待阶段 5）；
② 反馈环样本——✅ **已采**（样本二；`IsFeedbackStart` 在 .export 载体的落点对照仍缺，
可选）；
③ FB 实例框样本——✅ **已采**（样本二）；
④（可选加分）用户自定义 FB 被 CFC 调用的样本——未采。
