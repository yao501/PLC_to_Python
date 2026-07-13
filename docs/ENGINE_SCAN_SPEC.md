# 执行引擎一拍时序规格（ENGINE_SCAN_SPEC）v2.2.2（阶段 0.5 冻结评审裁决写回）

> v2.2.2（2026-07-12，0.5 冻结评审两项正式裁决写回）：① `PLATFORM-OUTPUT-BASELINE-1`——需要重建物理输出基准的边界（冷启动首拍、shadow→实写、提交故障恢复、`channel_fault` 复位后首拍）统一为"**可信设备反馈优先，否则 `safe_value`**"；`last_physical_committed` 只是驱动确认写出的最后命令值与诊断记录，**不得冒充设备反馈或作为对齐基准**（§4.1）；区分瞬时 `commit_fault` 与锁存 `channel_fault`，后者不自动清除、须显式确认/复位（§4.4）。② D3 载体分支——§5.1 改为 PLCopen XML / .export 自动模式 / 显式顺序载体三分支，删除无条件"每元素序号原样保留"。**以上输出基准与复位制度为项目工程约定、非 CODESYS 官方语义，且尚未经真实 HAL/执行器验证**；可信反馈接口在阶段 7 HAL 实现。
> v2.2.1（三轮评审一致性修正，**其中"shadow→实写首拍例外用 `last_physical_committed`"已被 v2.2.2 取代**——现行口径为可信反馈优先、否则 `safe_value`，见 §4.1）：`hold`/限速基准逻辑层口径 = `last_effective`；写失败改**固定行为** + `commit_fault_retry_n` 字段入 `OutputPolicy`（取消名实不符的策略枚举，§4.4）。

> 规格层级：L4（执行引擎）。v2 落实阶段 0.5 修订：**执行可执行 IR 指令列表**、**输出门控改 `OutputPolicy`（支持模拟量与非 0 安全值）**、**CFC 顺序/反馈按载体分支处理（v2.2.2 D3：显式序号载体原样保留；`.export` 自动模式须重建，算法未就绪拒绝生成可执行 IR；新建图拓扑定序，见 §5.1）**。
> v2.1（外部评审 P0 修正）：`OutputPolicy` 故障策略**按原因分别定义 + 安全优先级**（取代单一 `hold_on_gate_false`）；明确"上次提交值"存储、安全值与限速关系、扫描异常时的提交责任方（§4）。
> v2.2（二轮修正）：输出状态拆**逻辑生效值 / 物理提交值**两层（shadow 下限速/保持可连续模拟，§4.1）；新增 `on_commit_fault` 与驱动写失败处理（§4.4）；显式声明**软件能力边界**——进程卡死/OS 崩溃场景的安全兜底属外部硬件 watchdog/安全回路（§4.5）。
>
> **代码示例约定**：本文档 `dataclass` 为示意伪代码；实现时可变默认值用 `field(default_factory=...)`。
> 配套：`docs/IR_SPEC.md`（L3，可执行 IR）、`docs/COMPONENT_CONTRACT.md`（L2）、`docs/TARGET_PROFILE.md`、`.cursor/rules/00a-runtime-contract.mdc`。

---

## 1. 定位

规定"一个扫描周期"的精确执行。程序已在加载期 lower 成**语言无关的可执行 IR 指令列表**（`IR_SPEC §5`），引擎对 ST/CFC 来源无差别，只执行指令列表 + 按 `OutputPolicy` 门控提交。

## 2. 扫描上下文

```python
@dataclass
class ScanContext:
    dt_ms: int                # 固定 500
    mode: str                 # "engineering" / "fidelity_f1" / "fidelity_f2"（数值模式，见 IR_SPEC §8）
    store: Store              # 扁平变量空间（命名见 IR_SPEC §7）
    input_image: dict         # 本拍输入快照（只读）
    output_pending: dict      # 门控前的待提交输出
    prev: Store               # 上一拍提交后的快照（供 LOAD_PREV 反馈起点读上一拍值）
```

## 3. 一拍的精确顺序

```python
def scan(task, ctx):
    # 1) 输入映像锁存
    latch_inputs(ctx)                         # 物理/测试输入一次性进 input_image 与 store

    # 2)+3) 执行可执行 IR（ST 内联 / CFC 逐节点，均已 lower 成指令列表）
    for prog in task.programs:                # D2：按 programs 列表顺序
        exec_instrs(prog.code, ctx)           # 逐条执行；数值模式钩子按 ctx.mode 生效
                                              # 业务逻辑只往 store 写 request 变量，不碰物理输出

    # 4) 输出门控：按每个输出的 OutputPolicy 生成最终值（不是布尔 AND）
    for ch in task.io_map where direction == "OUT":
        ctx.output_pending[ch] = apply_output_policy(ch.policy, ctx)

    # 5) 一次性提交：shadow 模式只算不写
    if not shadow_mode:
        commit_outputs(ctx.output_pending)
    ctx.prev = snapshot(ctx.store)
```

不变式（过程映像、request/final 分离、shadow、块内授权两层）同 v1，从略；变化点在第 4 步。

## 4. 输出安全策略 OutputPolicy（取代 BOOL 门控；v2.1 按故障原因分策略）

v1 的 `final = req AND system_ready AND ...` 只适用 BOOL，且表达不了"安全默认值非 0"、模拟量限速/保持。v2 为每个物理输出定义策略。**v2.1 修正**：不同故障**不共用一个 `hold_on_gate_false`**——safety 跳闸或 watchdog 超时时"保持旧输出"在工业上不可接受，必须按原因分别定义并强制安全优先级。

```python
FaultAction = Literal["safe", "hold"]   # safe=落 safe_value；hold=保持 last_effective（上次逻辑生效值，§4.1）

@dataclass
class OutputPolicy:
    var: str                  # 业务侧 request 变量（GVL）
    iec_type: str             # BOOL / REAL / INT ...（决定门控算法）
    safe_value: Any           # 安全默认值（可非 0，按工程量纲）
    rate_limit: float = None  # 模拟量每拍最大变化量（None=不限），仅约束正常路径（见 4.2）
    commit_fault_retry_n: int = 3   # 写失败连续重试拍数，超过则升级通道级故障（§4.4）

    # —— 按原因分别定义的故障策略（v2.1）——
    on_startup_not_ready: FaultAction = "safe"   # system_ready 未建立（启动抑制期）
    on_operator_disable:  FaultAction = "safe"   # output_enable 为假（人为禁用，普通输出可按需 hold）
    on_comm_loss:         FaultAction = "safe"   # 通信失联
    on_safety_trip:       FaultAction = "safe"   # safety_ok / interlock_ok 为假 —— 强制 safe，不可配置为 hold
    on_scan_fault:        FaultAction = "safe"   # 本拍扫描抛异常 —— 强制 safe，不可配置为 hold
    on_watchdog:          FaultAction = "safe"   # watchdog 超时 —— 强制 safe，不可配置为 hold
```

**约束与优先级（规范性）**：

1. `on_safety_trip` / `on_scan_fault` / `on_watchdog` **固定为 `"safe"`**：加载器校验，配置成 `"hold"` 视为非法工程，拒绝加载。可配置的只有启动/人为禁用/失联三类。
2. 多故障并发时按**安全优先级**取最严者：`safety_trip ≥ watchdog ≥ scan_fault > comm_loss > startup_not_ready > operator_disable`。命中任何一个强制 safe 的原因，结果即 safe，不再看其余配置。
3. `hold` 语义 = **`last_effective`（上次逻辑生效值，见 4.1）**，不是业务 request 的旧值，也不是物理提交值——全文唯一口径：`hold` 与 `rate_limit` 基准一律用 `last_effective`；`last_physical_committed` 仅用于提交失败判断（4.4）与诊断记录，**不作为对齐基准**（v2.2.2 裁决，边界首拍基准见 4.1）。

### 4.1 输出状态两层：逻辑生效值 vs 物理提交值（v2.2）

引擎为每个物理输出维护**两个**引擎级状态（均不属于业务 `store`，业务不可见不可写）：

| 状态 | 更新时机 | 用途 |
|---|---|---|
| `last_effective` | **每拍第 4 步策略计算完成后**更新（shadow 与正常模式都更新） | 限速基准、`hold` 取值来源——保证 shadow 模式下限速/保持策略**可连续模拟** |
| `last_physical_committed` | **仅第 5 步 `commit_outputs` 对设备写成功后**更新（shadow 不更新） | **最后一次驱动确认写入的命令值**（≠ 传感器确认的设备实际位置——它只是已确认写命令和诊断记录，**不得自动等同于可信设备反馈，也不作为基准对齐依据**，v2.2.2 裁决）；提交失败判断（§4.4）、诊断 |

**物理输出基准规则（v2.2.2 冻结裁决，`PLATFORM-OUTPUT-BASELINE-1`；项目工程约定、非 CODESYS 官方语义）**：

在**需要重新建立物理输出基准的边界**——冷启动首拍、shadow→实写切换首拍、提交故障恢复首拍、`channel_fault` 复位后首拍——限速/对齐基准按以下选择函数确定：

- **有可信设备反馈**：优先使用设备反馈作为限速/对齐基准。
- **无可信设备反馈**，或反馈过期、质量无效、类型/量纲不匹配：使用 `safe_value`。

**可信设备反馈**至少应满足：HAL 明确标记有效、数据新鲜、质量正常，并完成 IEC 类型和工程量纲校验；具体接口在阶段 7 HAL 中实现（实现前所有边界首拍等效于"无可信反馈"分支，即 `safe_value`）。

常规规则：非边界拍的 `rate_limit` 与 `hold` 一律以 `last_effective` 为基准。冷启动两层状态皆空——任何 `hold` 在无历史值时**退化为 `safe_value`**（与上述选择函数一致）。

### 4.2 安全值与限速的关系

`rate_limit` 只约束**正常路径**（request → final 的每拍变化）。**故障落 `safe_value` 不受 `rate_limit` 约束**——安全动作必须一步到位（阀门立即全关，而不是按限速慢慢关）。若某输出确需"斜坡去安全值"（工艺原因），那是业务逻辑的职责，不由 OutputPolicy 承担。从故障恢复、重新进入正常路径的第一拍，按 §4.1 基准选择函数（可信设备反馈优先，否则 `safe_value`）确定限速基准，避免恢复瞬间跳变。

### 4.3 扫描异常时由谁提交安全值

第 2 步执行 IR 抛异常时，`scan()` 内部**不再继续第 3/4 步的正常路径**。提交责任方是**扫描函数外层的引擎运行时**（scan runner）：捕获异常 → 置 `scan_fault` 标志 → 对每个输出按 `on_scan_fault`（强制 safe）生成输出映像 → 走同一个 `commit_outputs` 一次性提交。即：**安全值提交不依赖本拍扫描逻辑是否活着**；watchdog 超时同理，由独立于扫描的 runner/监视层驱动提交。此路径必须有专门测试（扫描中途抛异常 → 输出全部落安全值）。

### 4.4 驱动写失败处理（v2.2.1：固定行为，不设策略枚举）

第 5 步对某通道写设备**失败**（驱动报错/超时/总线拒绝）时，行为**固定**（原 `retry_then_safe`/`safe` 两枚举实际收敛于同一行为，v2.2.1 取消枚举、消除名实不符）：

1. 该通道立即置 `commit_fault` 标志（**瞬时提交层故障**）并**告警**（事件日志，可上 HMI）；`last_physical_committed` **不**更新（如实反映设备侧未知/旧值）。
2. 从下一拍起，该通道**持续尝试写 `safe_value`**（不再写业务值）。后续一次安全值写成功可**清除瞬时 `commit_fault`**、按 §4.2 恢复规则回正常路径——但**不能自动清除**已升级的 `channel_fault`（见第 3 条）。
3. 连续 `commit_fault_retry_n` 拍（默认 3，OutputPolicy 字段）仍失败 → 升级 **`channel_fault`（通道级锁存故障）** + 系统级告警（可触发联锁）。**没有静默放弃路径**。**`channel_fault` 复位制度（v2.2.2 冻结裁决；项目工程约定）**：
   - 达到升级阈值后**锁存**：继续禁止业务值写出并维持安全输出；
   - 期间安全值写成功只清瞬时 `commit_fault`，**不自动清除 `channel_fault`**；
   - 只有"**故障原因已消失 + 安全值已确认写成功 + 操作员或上层安全状态机显式确认/复位**"三条件同时满足后，才允许解除锁存；未满足复位条件时**拒绝复位**；
   - 解除后的首个正常输出按 §4.1 基准选择函数（可信反馈优先，否则 `safe_value`）重新建立基准。
4. 写失败不影响其他通道的提交（逐通道隔离）。
5. **与策略层的关系（唯一实现路径，消除二义）**：`commit_fault`/`channel_fault` 是**提交层**状态，**不**作为故障原因进入 `apply_output_policy` 的故障集合；策略层照常计算 `final` 并更新 `last_effective`（保持逻辑连续），提交层对故障通道**忽略 `final`、改写 `safe_value`** 直至写成功且（若已锁存）完成显式复位。恢复后的首拍，限速基准按 §4.1 基准选择函数（v2.2.2：不再用 `last_physical_committed` 对齐），此后回到 `last_effective`。

### 4.5 软件能力边界（诚实声明，v2.2）

本节所有"故障→写安全值"承诺的**前提是本进程还活着且 OS/驱动还能执行写操作**。以下场景软件自身**无法**保证任何输出动作：进程被 OS 强杀/卡死（GIL 死锁、OOM）、内核崩溃、掉电、驱动/总线硬件失效。这些场景的安全兜底**必须由外部机制承担**：外部硬件 watchdog（超时未喂狗即由硬件把输出驱动到失电安全态）、失电安全型执行器选型、独立安全回路/安全 PLC。阶段 7 接现场前必须完成该外部机制的选型与联调（`RISKS.md::PLATFORM-RT-JITTER-1` 关联）；在此之前，本平台**不得**被用于无外部安全兜底的物理设备控制。"异常时必然写安全值"只在软件存活域内成立。

`apply_output_policy(policy, ctx)` 算法（正常拍）：

1. 汇总本拍故障原因集合（gate 各条件、通信、看门狗、扫描状态）；非空则按 §4 优先级与对应 `on_*` 得 `safe_value` 或 `last_effective`，返回。
2. 否则取业务 `request = store.get(policy.var)`：
   - **BOOL**：`final = request`。
   - **模拟量（REAL/INT）**：`final = request`，按 `rate_limit` 对"与**限速基准**之差"限速。限速基准 = `last_effective`；**唯一例外**：需重建物理基准的边界首拍（冷启动、shadow→实写切换、故障/复位恢复）按 §4.1 基准选择函数——可信设备反馈优先，否则 `safe_value`（v2.2.2 裁决，此后回到 `last_effective`）。
3. 返回 `final`；更新 `last_effective = final`（shadow 同样更新）。

要点：安全默认值**按类型、可非 0**（如某阀门安全态是全关=0、某设定是预定义安全设定）；模拟量有独立限速与失联策略。`AV`（APCM 输出）这类模拟量在此被正确处理。

## 5. CFC 执行顺序与反馈（D3 v2.2.2 载体分支裁决：按载体能提供什么保留什么，不重新推断已有序号）

### 5.1 导入既有 CFC（来自 CODESYS 工程；按导出载体三分支，v2.2.2 冻结裁决）

- **PLCopen XML**：目标 SP16.1 样本实测**每元素显式携带 `executionOrderId`**（block/outVariable 均带）。导入器**原样保存**该字段及其来源标识（`order_source = exported`）——"导入保留序号"在该载体上直接成立。阶段 5 导入器候选首选载体。
- **.export 自动数据流模式**（样本实测 `UseExplicitExecutionOrder=False`）：文件**不存储**原始元素执行序号（编辑器显示的 0..N 是派生值）；只能保留顺序模式、网络拓扑、连线和可用的 `IsFeedbackStart` 字段，**不得表述为"保留原始顺序/序号"**。执行序必须在后续阶段**重建**（`order_source = reconstructed`）："拓扑排序 + 同层按元素 Id 升序"目前仅在一个无环样本上与编辑器显示吻合，**不是已验证算法，本次不冻结**；重建算法未就绪时导入器**必须拒绝生成可执行 IR，不得静默猜测**（延后阶段 5，`RISKS.md::PLATFORM-CFC-AUTOORDER-1`）。
- **显式顺序载体**（.export `UseExplicitExecutionOrder=True` 等）：尚无样本，**不对其字段结构作已验证结论**；预期显式序号可直接保留，待样本验证（用户已裁决暂缓采样）。
- 反馈起点处的源读取 lower 成 `LOAD_PREV`（读 `ctx.prev`，即上一拍提交值），复刻 PLC"反馈量看上一拍"语义。**注意：这是待验证的映射假设，不是已确认事实**——CODESYS 反馈起点是元素级标记并决定反馈环最低执行序号，"起点元素的哪些入边对应 `LOAD_PREV`"须用真实工程导出样本验证后冻结；PLCopen XML 载体**无显式反馈起点字段**，反馈语义须由"序号 + 拓扑"推断（`RISKS.md::PLATFORM-CFC-FEEDBACK-MAP-1`）。

### 5.2 新建 CFC（平台内编辑）

- 默认"自动数据流顺序"：对无环图拓扑排序；有环时由用户标**反馈起点**打破，或按编辑器执行序号。
- 拓扑推断**仅用于平台通用的新建图定序**，是辅助，不用于改写载体已显式提供的既有顺序（PLCopen XML / 显式顺序载体，§5.1）；`.export` 自动模式**不走本节**，其顺序重建另属阶段 5 导入器重建算法（未冻结，未就绪时拒绝生成可执行 IR，§5.1）。

## 6. RETAIN 快照时机（阶段 8 实现）

RETAIN/PERSISTENT 快照在**第 5 步提交后、`ctx.prev` 生成同一时点**取，保证"恢复后状态 = 某拍提交完成"语义（`RISKS.md::PLATFORM-RETAIN-1`）。

## 7. 实时驱动与 AI 边界（预留）

- 实时节拍/抖动/超时/watchdog 升级在阶段 7（`RISKS.md::PLATFORM-RT-JITTER-1`）。
- 重 AI 推理**不得**进扫描进程同步执行；分进程异步算、结果锁存进 `store`、经第 4 步 `OutputPolicy`/门控采纳（`RISKS.md::PLATFORM-AI-DETERMINISM-1`）。

## 8. 最小示例（阶段 1 首个验收用例蓝本）

逻辑："`Start` 经 5 秒延时且未 `Stop` 则 `Motor` 置位"。声明：实例 `TON1: TON`；GVL `Start:BOOL, Stop:BOOL, Motor:BOOL`；`Motor` 输出 `OutputPolicy(type=BOOL, safe_value=False, gate=[system_ready,...])`。

- **ST 源** → lower 成 IR（全类型化，`IR_SPEC §5`）：
  ```
  LOAD_VAR Start,BOOL ; STORE_VAR TON1.IN,BOOL
  LOAD_CONST 5000,TIME ; STORE_VAR TON1.PT,TIME
  CALL_FB TON1
  LOAD_VAR TON1.Q,BOOL ; LOAD_VAR Stop,BOOL ; UNOP NOT,BOOL ; BINOP AND,BOOL ; STORE_VAR Motor,BOOL
  ```
- **CFC 源** → lower 成**同样的指令序列**（本示例假定源模型已有确定的执行序号，例如 PLCopen XML 的 `executionOrderId`：1:TON1, 2:AND；`.export` 自动模式须先经阶段 5 重建算法得到确定序，见 §5.1）。
- 一拍：① 锁存 `Start/Stop`；② 执行上述指令写 `TON1.Q`、`Motor`；③ `apply_output_policy(Motor)`：门控满足取 `request`，否则落 `safe_value=False`；④ 提交（shadow 下只算不写），快照 `prev`。

ST 与 CFC lower 出同一指令列表、结果逐值一致 —— "同引擎"的工程证明。

## 9. 验收要点

- [ ] §8 最小示例：ST 与 CFC 各自 lower 出的**可执行指令列表**跑通且逐值一致。
- [ ] 一个模拟量输出（如仿 `AV`）经 `OutputPolicy` 正确处理：安全值非 0、限速、失联保持/落安全值三种路径有测试。
- [ ] **按原因分策略**：`on_safety_trip`/`on_scan_fault`/`on_watchdog` 配置 `"hold"` 被加载器拒绝；多故障并发按优先级取最严者有测试。
- [ ] **扫描中途抛异常** → runner 在扫描函数外提交安全值（§4.3）有测试；冷启动无历史值时 `hold` 退化为 safe 有测试；故障落安全值不受限速、恢复第一拍限速基准正确有测试。
- [ ] **两层输出状态**（§4.1）：shadow 模式下 `last_effective` 连续更新（限速/保持可模拟）、`last_physical_committed` 不更新有测试；shadow→实写切换首拍按 §4.1 基准选择函数（可信反馈优先，否则 `safe_value`；v2.2.2 裁决，不再用 `last_physical_committed` 对齐）渐进对齐有测试。
- [ ] **提交失败**（§4.4）：写失败告警、重试收敛到安全值、逐通道隔离有测试。
- [ ] **基准与复位裁决（v2.2.2）**：有效反馈优先有测试；无效/缺失反馈退 `safe_value` 有测试；`last_physical_committed` 不冒充反馈（不被用作基准）有测试；`channel_fault` 不自动恢复有测试；未满足复位条件时拒绝复位有测试。
- [ ] 导入用例（按 §5.1 载体分支）：PLCopen XML 载体保留 `executionOrderId` 与来源标识；.export 自动模式在重建算法未就绪时拒绝生成可执行 IR；反馈起点读上一拍值。
- [ ] E / F1 模式下同一 IR 都能执行，结果分别满足容差判定 / 容差+边界 binary32 编码检查（`TARGET_PROFILE §4.1`）。
