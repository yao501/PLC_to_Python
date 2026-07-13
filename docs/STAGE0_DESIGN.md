# 阶段 0 设计稿：平台地基（IR · 引擎时序 · 组件契约）

> 状态：**历史文档（概念设计稿，2026-06-30）——已被 v2.1 正式规格取代**：`IR_SPEC.md` / `ENGINE_SCAN_SPEC.md` / `COMPONENT_CONTRACT.md` / `TARGET_PROFILE.md`。本稿细节（如 §7 注册表 `REGISTRY[block_type]`、无类型指令等）**以正式规格为准**，本稿仅作决策沿革参考，不再更新。经外部评审（ChatGPT5.5）反馈，本稿定位由"已冻结"修正为"概念设计"；D1/D2 保留，**D3/D4/D5 已修订**（见 §9），并新增 §11 阶段 0.5 修订项。IR 的工程冻结改到阶段 0.5 完成后。这是 `docs/PLATFORM_ROADMAP.md` 阶段 0 的产出。
> 目的：在写引擎第一行代码前，把"程序模型(IR) / 引擎一拍时序 / 组件契约 / 类型系统 / 反馈环"五件地基锁定。
> 评审通过后，可拆成 `IR_SPEC.md` / `ENGINE_SCAN_SPEC.md` / `COMPONENT_CONTRACT.md` 三份正式规格。
> 文中数据结构用伪 Python/JSON 表达**意图**，不是最终代码。带 ❓ 的是**待你拍板的决策点**，汇总在 §9。

---

## 1. 目的与范围

锁定五件事：

1. **组件契约（L2）**——已迁移的 14 块 + 8 原语，怎样被引擎统一识别、连线、调用，且**不重写**它们。
2. **程序模型 IR（L3）**——一段程序（ST 或 CFC）在内存里长什么样。
3. **引擎一拍时序（L4）**——固定 500ms 一拍，精确到"谁先读、谁后写、何时提交"。
4. **类型系统**——IEC 类型 ↔ Python 类型，尤其 REAL 精度策略。
5. **反馈环与执行定序**——CFC 回路怎么按 PLC"上一拍值"语义打破。

不在本阶段：ST 解析器实现、CFC 编辑器、I/O 驱动、持久化落盘。这些只在此处**预留接口**。

---

## 2. 组件契约（L2）

### 2.1 核心难点

现有 14 块**输出暴露方式不统一**：有的 `step` 返回 dict（如 `APCHSFOP.step` 返回 `{"AV":...}`），有的写 `self.*`（如 `APCM` 把结果写到 `self.AV/self.AV_P`），`APCM` 还有 `VAR_IN_OUT` 用 `RealRef` 适配，部分块构造时要传 `LicenseContext`。

**结论：不改这些块**。引擎不直接"猜"输出在哪，而是给每个块配一份**块描述符（BlockDescriptor）**，描述它的管脚和调用/取值方式。块本身保持原样，描述符是外挂适配层。

### 2.2 块描述符（BlockDescriptor）

```python
@dataclass
class Pin:
    name: str                 # 管脚名（= step 的 kwarg 名 / 输出键 / 属性名）
    iec_type: str             # "BOOL" / "REAL" / "INT" / "TIME" ...
    default: Any = None       # VAR_INPUT 默认值（无外接时用）
    kind: str = "VAR_INPUT"   # VAR_INPUT / VAR_OUTPUT / VAR_IN_OUT

@dataclass
class BlockDescriptor:
    block_type: str                       # "APCHSFOP" / "TON" / "APCM" ...
    cls: type                             # 已迁移的 Python 类
    inputs:  list[Pin]
    outputs: list[Pin]
    inouts:  list[Pin] = field(default_factory=list)
    ctor_args: list[str] = field(default_factory=list)   # 构造依赖，如 ["license_context"]
    # 输出读取方式：'return:KEY'（从 step 返回 dict 取键）或 'attr:NAME'（从 self 读属性）
    output_access: dict[str, str] = ...
    # step 是否要求 dt_ms 之外的位置参数 / 关键字参数，由 call_adapter 封装
    call_adapter: Callable                # 统一成 engine 期望的调用约定
```

引擎只与 `BlockDescriptor` 打交道：给它"已解析好的输入值字典"，它负责正确调用 `cls.step`、把输出按 `output_access` 收回成"输出值字典"。`VAR_IN_OUT`（如 `APCM.ZLOUT` 的 `RealRef`）由 call_adapter 在调用前后做引用绑定与回读。

### 2.3 库注册表（Library Registry）

```python
REGISTRY: dict[str, BlockDescriptor] = {}
def register(desc: BlockDescriptor): REGISTRY[desc.block_type] = desc
```

- 标准库（14 块 + 8 原语）启动时注册进 `REGISTRY`。
- ST 里 `APCHSFOP1(...)` 调用、CFC 里拖出一个 `APCHSFOP` 框，都通过 `REGISTRY["APCHSFOP"]` 找到描述符。
- 后续 AI 功能块（阶段 9）也用同一注册机制接入，无需改引擎。

### 2.4 与现有块的兼容策略

- **零改动**：14 块 + 8 原语源码不动；只新增 `src/runtime/descriptors/` 下的描述符。
- 描述符可**半自动生成**：用 `inspect.signature(cls.step)` 抽 kwarg 名做输入管脚，结合人工标注输出与类型。
- ❓ **决策点 D1**：描述符是手写、还是给块加一个轻量 `__pins__` 类属性由块自带元数据？（手写=不碰块；自带=更内聚但要动 14 个文件。）

---

## 3. 程序模型 IR（L3）

一段程序（POU）= **声明部分（ST/CFC 共用）+ 执行体（语言相关，但都实现统一接口）**。

### 3.1 声明部分（两种语言完全共用）

```python
@dataclass
class InstanceDecl:
    name: str                 # 实例名，如 "FOP1"
    block_type: str           # 查 REGISTRY
    ctor_args: dict = {}       # 如 {"license_context": <ref to GVL.ctx>}
    init_overrides: dict = {}  # 上电初值覆盖，如 {"PT": 100.0}
    retain: set[str] = set()   # 该实例哪些状态变量是 RETAIN（阶段8用）

@dataclass
class VarDecl:                 # GVL 全局量 / POU 局部量
    name: str
    iec_type: str
    initial: Any = None
    retain: bool = False
    persistent: bool = False

@dataclass
class IOMap:                   # GVL 变量 ↔ 物理点（阶段7用，先占位）
    var: str
    channel: str              # 如 "DI0.3" / "modbus:40001"
    direction: str            # "IN" / "OUT"
```

### 3.2 执行体（语言相关，统一接口）

```python
class Body(Protocol):
    def execute(self, ctx: "ScanContext") -> None: ...
```

- **CFCBody**：节点（FB.step 调用）+ 连线（wire-copy）+ 执行顺序（顺序号或拓扑排序结果）。`execute` = 按顺序逐节点：解析输入→调 step→把输出经连线写到下游/GVL。
- **STBody**：ST 解析后的 AST（赋值 / `IF`-`CASE`-`FOR` / FB 调用 / 表达式）。`execute` = 每拍遍历 AST。

> ST 有控制流（IF/FOR），CFC 是平铺有序图——所以执行体**不能**强行统一成"一个平铺列表"，而是统一成 `execute(ctx)` 这个**接口**。引擎对两者一视同仁地调 `body.execute(ctx)`，差异封装在各自实现里。

### 3.3 程序与任务

```python
@dataclass
class Program:                # 一个 POU（PROGRAM 级）
    name: str
    language: str             # "ST" / "CFC"
    instances: list[InstanceDecl]
    local_vars: list[VarDecl]
    body: Body

@dataclass
class Task:                   # 当前工程：单任务、固定 500ms
    cycle_ms: int = 500
    programs: list[Program]   # 任务内按列出顺序逐个 execute
    gvl: list[VarDecl]
    io_map: list[IOMap]
```

❓ **决策点 D2**：多 PROGRAM 的执行顺序——按 `programs` 列表顺序（简单、确定）即可，确认是否够用（单任务场景通常够）。

---

## 4. 引擎一拍时序（L4）

### 4.1 变量空间与扫描上下文

```python
class Store:                  # 扁平变量空间
    # 命名空间：GVL 变量、"实例名.管脚"、POU 局部量
    # 读写都走 get(key)/set(key,val)
    ...

@dataclass
class ScanContext:
    dt_ms: int                # 固定 500
    store: Store              # 当前拍可读写的变量空间
    input_image: dict         # 本拍输入快照（只读）
    output_pending: dict      # 本拍待提交输出（门控前）
    prev: Store               # 上一拍提交后的快照（供反馈边读"上一拍值"）
```

### 4.2 一拍的精确顺序（泛化自 `00a` 五步式）

```
def scan(task, ctx):
    # 1) 输入映像锁存：一次性把物理/测试输入采进 input_image，并写入 store
    latch_inputs(ctx)                         # 之后本拍不再重采

    # 2)+3) 执行体推进 + 连接解算（ST 内联赋值 / CFC 逐节点 wire-copy）
    for prog in task.programs:
        prog.body.execute(ctx)                # 业务逻辑只往 store 写 request，不碰物理输出

    # 4) 输出门控：对每个输出通道叠加安全链
    for ch in task.io_map if ch.direction=="OUT":
        req = ctx.store.get(ch.var)
        ctx.output_pending[ch] = req AND system_ready AND output_enable \
                                     AND safety_ok AND interlock_ok

    # 5) 一次性提交：门控后输出集中写出；shadow 模式下只算不写
    if not shadow_mode:
        commit_outputs(ctx.output_pending)
    ctx.prev = snapshot(ctx.store)            # 供下一拍反馈边读"上一拍值"
```

要点：
- **过程映像语义**：输入在第 1 步锁存，本拍后续只读快照；输出在第 5 步集中提交，中途不写半成品——与真 PLC 一致。
- **request / final 分离**：执行体只产生逻辑请求（写 store），物理动作只在第 4–5 步经门控产生。
- **shadow mode**：第 5 步只算不写，用于首接现场（阶段 7）。
- **安全态**：`scan` 整体包 try/except + watchdog，异常时输出落预定义安全默认值（阶段 7 细化）。

### 4.3 授权门控的位置

像 `APCM/APCPID/APCPIDZZD` 这类自带 `KZQBDYZMK` 授权的块，其授权逻辑在**块内部**（第 2 步推进时自然执行），引擎不重复做。引擎层的 `system_ready/safety_ok` 是**系统级**门控，与块级授权是两层、各司其职。

---

## 5. 反馈环与执行定序

### 5.1 ST

执行顺序 = 代码书写顺序（显式）。`STBody.execute` 顺着 AST 走，天然定序，无需拓扑排序。

### 5.2 CFC

1. 把连线图按依赖建有向图（边：上游输出管脚 → 下游输入管脚）。
2. **无环** → 拓扑排序得执行顺序。
3. **有环（反馈回路）** → 选定**反馈边**，其源值在执行时**从 `ctx.prev` 读上一拍提交值**（不是本拍现算值）；对"图去掉反馈边"后的部分做拓扑排序得顺序。这正是 PLC"后算的量被先用时看到的是上一拍值"的语义。
4. 反馈边的确定方式：**① 用户显式标注**（CFC 里把某连接标为 feedback）；**② 自动检测**（按 CODESYS 执行顺序号，凡"下游序号 < 上游序号"的连接即反馈边）。两者都支持，显式优先。

❓ **决策点 D3**：反馈边默认策略——优先采信 CODESYS 执行顺序号自动判定，还是强制用户显式标注？（导入既有工程时前者更顺。）

---

## 6. 类型系统决策

### 6.1 映射表（首版）

| IEC 类型 | Python | 说明 |
|---|---|---|
| BOOL | `bool` | |
| BYTE/WORD/DWORD | `int` | 配 `compat` 的 32/16 位回绕（已有 `dword.py`） |
| SINT/INT/DINT/USINT/UINT/UDINT | `int` | ❓溢出回绕是否仿真见 D4 |
| **REAL** | **见 §6.2** | 关键决策 |
| LREAL | `float`（64 位） | 原生等价 |
| TIME | `int`（ms） | 全项目统一整数毫秒（`00a` R1） |
| STRING | `str` | |

### 6.2 REAL 精度（核心决策）❓ 决策点 D5

- **现状**：14 块全用 Python 原生 `float`（64 位）。CODESYS `REAL` 是 32 位单精度。
- **风险**：长跑积算类（`APCHSACCUM` / `APCSTATISTICS`）与多步递推可能逐渐偏离真 PLC（`PLATFORM-REAL-FIDELITY-1`）。
- **建议（首版）**：**默认沿用 64 位 `float`**，同时在类型层预留**可插拔的 "REAL32 模式"**——开启后在每次 REAL 赋值点用 `struct`/`numpy.float32` 做一次单精度round。是否启用，留到**阶段 6 黄金轨迹**用真机数据裁决：若 64 位与真机无显著漂移就保持简单，若有漂移就开 REAL32。
- 这样阶段 0–5 不被精度问题拖住，又不在地基上堵死保真路。

### 6.3 整数溢出 ❓ 决策点 D4

CODESYS 定宽整数会回绕，Python `int` 不会。建议：**默认不仿真回绕**（多数控制逻辑不依赖溢出），仅对已知依赖回绕的点（如授权 `dword`，已处理）保留专门 helper；是否全局仿真留待真机对拍发现需要再加。

---

## 7. 特殊接口的统一处理

- **VAR_IN_OUT（如 `APCM.ZLOUT`）**：在 IR 里建模为 `inout` 管脚，引擎用 store 里的一个变量做绑定，call_adapter 在 `step` 前把当前值塞进 `RealRef`、`step` 后回读写回 store。
- **构造依赖（`LicenseContext`）**：`InstanceDecl.ctor_args` 指向 GVL 里的共享 `LicenseContext`（由 `LicenseContext` 泛化出的全局量容器持有）。同一任务内共享同一 context 的语义保持。
- **无 EN/RESET 的原语**：按 `00a` R2，EN 语义由调度层"调不调 step"实现，不进块本体——引擎在执行体里据连线/逻辑决定是否调用。

---

## 8. 端到端最小示例

同一段逻辑——"`Start` 经 5 秒延时且未 `Stop` 则 `Motor` 置位"——证明 ST 与 CFC 殊途同归到同一 IR 声明、同一引擎。

**声明部分（共用）**：实例 `TON1: TON`；GVL `Start:BOOL, Stop:BOOL, Motor:BOOL`。

**ST 执行体**：
```
TON1(IN := Start, PT := T#5S);
Motor := TON1.Q AND NOT Stop;
```

**CFC 执行体**：框 `TON1`；连线 `Start→TON1.IN`、常量 `T#5S→TON1.PT`、`TON1.Q` 与 `NOT Stop` 进 `AND`、`AND→Motor`；执行顺序 `1:TON1, 2:AND`。

**一拍执行（两者一致）**：
1. 锁存 `Start/Stop`；
2. 执行体：调 `TON1.step(500, IN=Start, PT_ms=5000)` → 写 `TON1.Q`；算 `TON1.Q AND NOT Stop` → 写 `Motor`；
3. 门控：`final_Motor = Motor AND system_ready AND ...`；
4. 提交 `final_Motor`（shadow 下只算不写）；快照供下一拍。

结果逐值相同——这就是"ST 与 CFC 都是同一引擎的前端"的最小证明。

---

## 9. 决策清单（D1/D2 确认；D3/D4/D5 经评审修订，2026-06-30）

> D1、D2 维持；D3/D4/D5 按外部评审反馈修订，最终在阶段 0.5 随语义基线一并冻结（见 §11）。

| 编号 | 决策 | 结论 |
|---|---|---|
| **D1** | 块元数据：外挂描述符 vs 块自带 `__pins__` | ✅ **外挂描述符**（不碰已迁移的 14 块）。描述符需补：状态/初始化/参数持久性/输入省略语义/版本/序列化。 |
| **D2** | 多 PROGRAM 执行顺序 | ✅ **按 `Task.programs` 列表顺序**（CODESYS 任务内 PROGRAM 按配置顺序调用，单任务下成立）。 |
| **D3** | CFC 反馈/执行顺序 | 🔄 **修订**：导入时**保留**原始 CFC 顺序模式、元素序号、网络拓扑、反馈起点，**不重新推断**；拓扑+序号推断仅用于**新建** CFC。 |
| **D4** | 整数语义 | 🔄 **修订**：按**目标运行时画像**执行，绑定一致性模式——`fidelity` 按目标位宽仿真回绕，`engineering` 不仿真。不再是"默认不仿真"。 |
| **D5** | REAL 精度 | 🔄 **修订**：定义两档明确模式——`fidelity`（REAL32 / LREAL64，块级 float32）与 `engineering`（Python float + 容差，默认）。**"块源码零改动"只能给到 `engineering`；位级保真需块级 float32 工作量。** 现在就定，不延到阶段 6。 |

---

## 10. 阶段 0 验收清单

- [x] 本设计稿概念设计完成；D1/D2 确认，D3/D4/D5 经评审修订（2026-06-30，见 §9）。
- [x] 三份规格初稿产出（由本稿拆分）：`docs/IR_SPEC.md` / `docs/ENGINE_SCAN_SPEC.md` / `docs/COMPONENT_CONTRACT.md`——**标注待阶段 0.5 修订冻结**。
- [x] §8 最小示例的"一拍数据流"已纸面走通，作为阶段 1 第一个验收用例蓝本。
- [x] `docs/RISKS.md` 平台级条目已登记于"三-A、平台演进"（含阶段 0.5 新增项）。
- [x] `.cursor/rules/04-platform-runtime.mdc` 平台/运行时引擎规则已补。
- [x] **阶段 0.5 设计物已产出**：`TARGET_PROFILE.md`（目标画像+一致性等级 E/F1/F2）、`IR_SPEC` v2（源模型/可执行 IR 分离 + POU 模型 + 双模式类型系统）、`ENGINE_SCAN_SPEC` v2（OutputPolicy + CFC 保留导入）、`COMPONENT_CONTRACT` v2（描述符补全字段）、`GOLDEN_TRACE_FORMAT.md`（格式+采集计划）。
- [x] 三处正文已与决策表对齐（D3/D4/D5 同步进各规格；`04-platform-runtime.mdc` 已更新为新决策、不再写"IR 已冻结/同进程"）。
- [ ] **（仍未完成，进阶段 1 前）** ① 阶段 0.5 设计物评审通过 → IR 工程冻结；② **真 PLC 黄金轨迹实采**（外部阻塞，需 SP16.1 运行环境，见 `GOLDEN_TRACE_FORMAT §5`）；③ `TARGET_PROFILE` 待确认项（CPU/OS、Patch、CFC 顺序模式、是否要 F2）由用户补齐；④ ST/CFC 最小程序 lower 到同一指令集并跑通（随阶段 1 验收）。

> **阶段 0 概念设计 + 阶段 0.5 设计物已完成；尚待"评审冻结 + 真机实采 + 用户确认项"。** 这三项过后方进阶段 1。

---

## 11. 阶段 0.5 修订项（外部评审反馈纳入，2026-06-30）

经外部评审（ChatGPT5.5），本稿定位修正为**概念设计完成、工程语义基线待阶段 0.5 冻结**。以下修订在进入阶段 1 前完成（详见 `PLATFORM_ROADMAP.md` 阶段 0.5）。**纪律：0.5 只冻结"改起来贵"的语义，其余模型先定、实现后延。**

| # | 修订项 | 等级 | 影响章节 |
|---|---|---|---|
| 1 | 目标环境画像 + 一致性等级（容差等价/位级等价），锁定 CODESYS SP16.1 | 【冻结】 | 新增 `TARGET_PROFILE.md` |
| 2 | 源模型（ST AST / CFC 图）与可执行 IR（Load/Store/CallFB/Convert/Jump，可最小指令集）分离 | 【冻结】 | §3 |
| 3 | 分类型输出安全 `OutputPolicy`（类型/安全值/失联/限速/联锁/保持），替换只适用 BOOL 的门控 | 【冻结】 | §4 / ENGINE_SCAN §3 |
| 4 | D3 改：导入保留原始 CFC 顺序模式/序号/拓扑/反馈起点，不重新推断 | 【冻结】 | §5 / ENGINE_SCAN §4 |
| 5 | D4/D5 改：`fidelity`（REAL32/LREAL64+整数回绕）与 `engineering`（float+容差）双模式 | 【冻结】 | §6 |
| 6 | 早期真 PLC 黄金轨迹最小集（TON/边沿/反馈环/REAL 递推/冷热启动） | 【冻结】 | 路线图阶段 6 提前 |
| 7 | POU 模型扩展：用户自定义 FB/FUNCTION、VAR_TEMP、VAR_IN_OUT 别名、实例内存、调用帧 | 【模型先定】 | §3.3/§3.4 |
| 8 | RETAIN/PERSISTENT 模型加厚（冷/热/下载、FB 级 retain 传播）；实现仍阶段 8 | 【模型先定】 | §3 / §7 |
| 9 | AI 进程隔离：控制运行时与 AI worker 分进程 + 共享内存/IPC | 【模型先定】 | 路线图阶段 9 |
| 10 | 治理一致性：阶段验收勾选与风险状态对齐 | 【治理】 | 本文件 / RISKS |

> **保留不变**：D1（外挂描述符）、D2（PROGRAM 列表顺序）。**未升级优先级**：I/O bus-cycle 细节——单任务 MVP 下五步式够用，仅按"已知简化"在文档诚实标注，不外推为普遍 PLC 真值。

---

> 评审通过后进入**阶段 1（执行引擎内核 MVP）**：把本稿的 IR、引擎一拍时序、组件描述符落成 `src/runtime/`，用 §8 的最小示例跑通第一拍。
