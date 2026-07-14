# 程序模型规格（IR_SPEC）v2.2.4（阶段 1 Store 持久键工程约定写回）

> v2.2.4（2026-07-14，阶段 1 实现反馈写回）：在 §7 明确 PROGRAM / 用户 FUNCTION_BLOCK 持久状态的扁平 Store 键为 `<实例全路径>.<变量名>`；PROGRAM 全路径取 `ProgramInstance.store_prefix`，嵌套 FB 全路径按父路径递归追加实例名。键生成必须集中在单一 helper，调用不得创建新的持久实例键。这是本项目运行时工程约定，不是 CODESYS / IEC 61131-3 官方键编码语义。
> v2.2.3（2026-07-14，阶段 1 实现反馈写回）：在 §5.2 明确 `StackSlot.index` 的项目工程约定——它表示距调用点栈顶的偏移（`0` = 栈顶）；同一调用的 `IN × StackSlot` 索引必须为非负整数、互不重复并连续覆盖 `{0..k-1}`，绑定书写顺序不改变语义。该约定来自正式运行时实现与静态校验闭环，不是 CODESYS / IEC 61131-3 官方语义；后续执行器若需改变必须重新评审并同步规格、实现和测试。
> v2.2.2（2026-07-12，D3 载体分支裁决写回）：`CFCGraph` 不再无条件要求"原样保留序号"，改为**载体分支字段**（§4：`execution_order_mode` / 可选 `execution_order_id` / `order_source` / 可选 `feedback_marker` / `carrier`）；CFC lowering 改为"**按已确定的执行序 lower**"（§6）——PLCopen XML 使用已保存序号，.export 自动模式等待后续重建算法，**算法未就绪时必须拒绝生成可执行 IR、不得静默猜测**。
> v2.2.1（三轮评审一致性修正）：整数中间位宽改 **native_width 模型**（§5.4）；REAL 量化**唯一口径 = F1-expr/F2 逐指令 binary32**（§5.3，消除与 §5.4 矛盾）；`CALL_FUNC`/`CALL_FB_INSTANCE` **编码绑定表**（§5.2 `Binding`/`ValueRef`）；`InstanceDecl.kind` 区分库块/用户 FB；FUNCTION 语义子集禁止 GVL/地址访问（§3）。

> 规格层级：L3（程序模型）。v2 落实阶段 0.5 修订：**分离"源模型"与"语言无关可执行 IR"**、**扩展 POU 模型**、**类型系统改 fidelity/engineering 双模式**。
> v2.1（外部评审 P0 修正）：**可执行 IR 全面带类型**（§5）、`LOAD_PREV` 列入正式指令表、`CALL_POU` 拆分为 `CALL_FUNC`/`CALL_FB_INSTANCE`、修正 FUNCTION/VAR_TEMP 语义（§3）。
> v2.2（外部评审二轮修正）：**POU 定义与运行实例分离**（§3）；数值求值区分**声明类型/求值类型/存储类型**，整数**回绕发生点改为目标画像参数**（§5.4）；fidelity 模式**禁止运行中热切换**（§8）。
>
> **代码示例约定**：本文档所有 `dataclass` 均为**示意伪代码**（表达字段与语义，不保证可直接运行）；实现时可变默认值必须用 `field(default_factory=...)`，字段顺序按 Python 语法调整。
> 配套：`docs/COMPONENT_CONTRACT.md`（L2）、`docs/ENGINE_SCAN_SPEC.md`（L4）、`docs/TARGET_PROFILE.md`（一致性等级）。
> 关联决策：D1（外挂描述符）、D2（PROGRAM 列表顺序）保留；D3/D4/D5 见各节。

---

## 1. 定位与三层结构

一段程序经三层下沉：

```
源模型（前端）          可执行 IR（统一）        执行
ST AST          ──lower──┐
                         ├──►  指令列表  ──►  引擎按指令逐条执行
CFC 图          ──lower──┘     (语言无关)
```

- **源模型**：ST AST、CFC 图，是各语言/导入器的前端表示，保留各自结构（ST 的控制流、CFC 的网络/序号/反馈起点）。
- **可执行 IR**：**语言无关的指令列表**（§5）。ST 与 CFC **都 lower 到它**，引擎只执行指令列表。这是"ST/CFC 编译成同一内部形态"的真正落地（修正 v1 只统一 `execute(ctx)` 接口的不足）。

## 2. 声明部分（源模型与 IR 共用）

```python
@dataclass
class InstanceDecl:
    name: str
    block_type: str           # 库块查 COMPONENT_CONTRACT 的 REGISTRY；用户 FB 查 pou_lib
    kind: str = "library"     # "library"（标准库块→CALL_FB）/ "user_fb"（用户 FB→CALL_FB_INSTANCE），lowering 据此选指令
    ctor_args: dict = {}
    init_overrides: dict = {}
    retain: set[str] = set()   # RETAIN 状态变量名（§9）

@dataclass
class VarDecl:
    name: str
    iec_type: str
    initial: Any = None
    retain: bool = False
    persistent: bool = False
    section: str = "VAR"       # VAR / VAR_INPUT / VAR_OUTPUT / VAR_IN_OUT / VAR_TEMP / VAR_GLOBAL

@dataclass
class IOMap:                   # GVL 变量 ↔ 物理点（含 OutputPolicy，见 ENGINE_SCAN_SPEC §4）
    var: str
    channel: str
    direction: str            # "IN" / "OUT"
    policy: "OutputPolicy" = None   # OUT 方向必填
```

## 3. POU 模型（阶段 0.5 扩展）

支持三类 POU，使用户能"像 CODESYS 一样新建 POU"。**v2.2 修正：定义（源代码级）与运行实例（内存级）分离**——原 `POU` 类同时承担两种角色，会诱导"FB 持久内存按调用创建"的实现错误。

```python
@dataclass
class POUDefinition:          # 源代码级：一个 POU 的定义（每名字一份，进 pou_lib）
    name: str
    pou_kind: str             # "PROGRAM" / "FUNCTION_BLOCK" / "FUNCTION"
    language: str             # "ST" / "CFC"
    interface: list[VarDecl]  # VAR_INPUT / VAR_OUTPUT / VAR_IN_OUT
    locals: list[VarDecl]     # VAR（PROGRAM/FB 另可有 VAR_TEMP，见语义要点）
    instances: list[InstanceDecl]   # 定义体内声明的 FB 实例（模板；实例化时按路径展开）
    return_type: str = None   # 仅 FUNCTION
    source: "STBody | CFCGraph"     # 源模型
    code: "list[Instr]" = None      # lower 后的可执行 IR（§5），定义级共享、只读

@dataclass
class ProgramInstance:        # 运行级：PROGRAM 实例（每 Task 装载期创建一份，持久内存）
    definition: str           # POUDefinition 名
    store_prefix: str         # 变量空间前缀（§7）

@dataclass
class FBInstance:             # 运行级：用户 FB 实例（装载期按声明路径展开创建，绝不按调用创建）
    definition: str
    path: str                 # 实例全路径，如 "PLC_PRG.FB1.SUB2"（嵌套 FB 递归展开）
    retain: set[str] = ...    # 承接 InstanceDecl.retain
```

**实例化规则（规范性）**：装载期遍历 `Task.programs` 与各定义的 `instances`，**递归展开**出全部 `ProgramInstance`/`FBInstance` 并在 `Store` 中分配其持久内存；运行期 `CALL_FB_INSTANCE <path>` 只**引用**既有实例，**任何调用都不得创建实例内存**。FUNCTION 没有实例——`CALL_FUNC` 压**调用帧**（帧内含其 `VAR` 的本次调用副本），返回即弹出。

**参数绑定（formal→actual）**：调用点在 lowering 期建立绑定表并**编码进 `CALL_FUNC`/`CALL_FB_INSTANCE` 指令**（`Binding`/`ValueRef` 结构与语义见 §5.2）——执行器仅凭指令即可完成调用，不回查源模型。

语义要点：
- **FUNCTION_BLOCK**：有**实例内存**（跨周期保持 `VAR`，按实例独立）；用户 FB 实例也进 `InstanceDecl`，与库块统一调度。
- **FUNCTION**：**无实例内部状态**，有返回值，其局部 `VAR` 按调用生存（在调用帧内，每次调用重新初始化）。**不是纯函数**——`VAR_IN_OUT`/`VAR_OUTPUT` 是合法的显式副作用通道。**本平台语义子集（v2.2.1）：FUNCTION 禁止访问 GVL 与地址**（CODESYS 官方即规定 FUNCTION 不应使用全局变量和地址）——导入器/前端遇到即**报错**，副作用只允许经 `VAR_IN_OUT`/`VAR_OUTPUT` 显式声明。引擎与优化器仍按可能有副作用处理（不可缓存/消重/重排调用）。
- **VAR_TEMP**：每次进入 POU 清零，不跨周期。按 CODESYS 语义，`VAR_TEMP` **仅允许出现在 PROGRAM 与 FUNCTION_BLOCK**；FUNCTION 中不写 `VAR_TEMP`（其普通 `VAR` 本身就是按调用生存的临时量）。导入器遇到 FUNCTION 内的 `VAR_TEMP` 应报错或降级为 `VAR` 并告警。
- **VAR_IN_OUT**：按引用别名（不是值拷贝），lower 时绑定到调用方变量（见 §5 `CALL_FB`/`CALL_FUNC`/`CALL_FB_INSTANCE` 的 inout 绑定）。
- **调用帧**：用户 POU 调用用户 POU 时压入帧（局部/temp 作用域），返回时弹出；库块（叶子）无需帧。

```python
@dataclass
class Task:                   # 当前工程：单任务、固定 500ms
    cycle_ms: int = 500
    programs: list[ProgramInstance]   # D2：按列表顺序执行
    gvl: list[VarDecl]
    io_map: list[IOMap]
    pou_lib: dict[str, POUDefinition] # 用户自定义 PROGRAM/FB/FUNCTION 定义库
```

## 4. 源模型

- **STBody**：ST AST（赋值 / `IF`-`CASE`-`FOR`-`WHILE` / POU·FB 调用 / 表达式 / `RETURN`/`EXIT`）。
- **CFCGraph**（v2.2.2 载体分支字段，见 ENGINE_SCAN_SPEC §5.1 与 D3 裁决）：节点（FB/POU 调用、运算框、输入/输出引脚）+ 连线 + 以下顺序/来源字段：
  - `execution_order_mode`：自动数据流 / 显式顺序；
  - `execution_order_id`（**可选**）：每元素执行序号，仅当载体显式提供时填写（PLCopen XML 的 `executionOrderId` 原样保存；.export 自动模式**无此数据，不得伪造**）；
  - `order_source = exported | reconstructed | user_defined`：序号来源标识（导出载体原生 / 导入器重建 / 平台内用户新建）；
  - `feedback_marker`（**可选**）：反馈起点标记，仅 .export 载体显式提供（`IsFeedbackStart`）；PLCopen XML 无此字段，反馈语义须由序号+拓扑推断；
  - `carrier`：来源载体（`plcopen_xml` / `export_native` / `user_defined`）。

源模型只负责"如何 lower 到可执行 IR"；不直接被引擎执行。

## 5. 可执行 IR（语言无关、**全类型化**指令集）

POU 体 lower 成一个**指令列表**，对一个扁平变量空间（`Store`）+ 一个求值栈操作。

### 5.1 类型化原则（v2.1，P0 修正）

**每条产生或消费值的指令都显式携带 IEC 类型**；求值栈上的元素是 `TypedValue(value, iec_type)`。类型在 **lowering 期静态确定**（来自声明 `VarDecl.iec_type`、管脚 `Pin.iec_type`、字面量类型、IEC 隐式提升规则），引擎执行期只按指令上的类型行动、不再推断。没有类型，引擎无法判断：ADD 按 INT/DINT/REAL/LREAL 哪种执行、整数按多少位回绕、何时做 REAL32 舍入、隐式提升与赋值转换在哪发生——因此**无类型的指令列表不是合法 IR**，加载器必须拒绝。

- **隐式类型提升**（如 `INT + DINT`、`INT + REAL`）在 lowering 期**显式化为 `CONVERT` 指令**，运行期不存在隐式提升。
- **赋值转换**（表达式类型 ≠ 目标变量类型）同样在 lowering 期插入 `CONVERT`；`STORE_VAR` 要求栈顶类型与其 `type` 严格相等，不符即加载期校验失败。
- 加载器带一个**验证 pass**：模拟栈类型流，逐指令检查操作数类型/栈深匹配，不通过不进引擎。

### 5.2 指令集（首版最小集）

| 指令 | 语义 |
|---|---|
| `LOAD_VAR <key, type>` | 压入变量值（`key` = GVL/局部/`<inst>.<pin>`；`type` = 该变量声明类型） |
| `LOAD_CONST <val, type>` | 压入常量 |
| `LOAD_PREV <key, type>` | 压入该变量**上一拍快照**值（`ctx.prev`）；CFC 反馈起点专用（见 §6、ENGINE_SCAN_SPEC §5） |
| `STORE_VAR <key, type>` | 弹出并写入变量；栈顶类型必须等于 `type`（F1/F2 下写入前按 `type` 量化，见 5.3） |
| `BINOP <op, type>` | 弹 2 压 1；`type` 为**操作数与结果的公共类型**（ADD/SUB/MUL/DIV/MOD/AND/OR/XOR）；比较类（GT/GE/LT/LE/EQ/NE）`type` 为操作数类型、**结果恒为 BOOL** |
| `UNOP <op, type>` | 弹 1 压 1（NOT/NEG），结果类型 = `type` |
| `CONVERT <from, to>` | 显式类型转换（走 `src/compat`，如 REAL_TO_INT 银行家舍入）；也是隐式提升/赋值转换的唯一落点（5.1） |
| `CALL_STD <name, sig>` | 标准函数（SEL/MIN/MAX/LIMIT/ABS/MUX…），按 IEC 语义；`sig` 含各实参类型与返回类型（泛型标准函数按实参实例化） |
| `CALL_FB <inst>` | 调用**库块**实例的 `step`（输入引脚已由前置 `STORE_VAR <inst>.<in>` 就位，管脚类型由描述符 `Pin.iec_type` 给出；输出经描述符回收到 `<inst>.<out>`；`VAR_IN_OUT` 按引用绑定） |
| `CALL_FUNC <name, bindings, ret_type>` | 调用**用户 FUNCTION**（压帧；按 `bindings` 绑定实参；返回值以 `TypedValue(ret_type)` 压栈；额外 `VAR_OUTPUT` 经 bindings 拷回） |
| `CALL_FB_INSTANCE <inst_path, bindings>` | 调用**用户 FUNCTION_BLOCK 实例**（压帧执行其 IR；实例内存跨周期保持）。与 `CALL_FB`（库块叶子、经描述符）区分开——两者调用约定不同，不共用一条指令 |
| `JMP <label>` / `JMP_IF_FALSE <label>` | 控制流（ST 的 IF/CASE/FOR/WHILE 下沉为跳转）；`JMP_IF_FALSE` 要求栈顶为 BOOL |
| `LABEL <id>` | 跳转目标 |

**调用绑定编码（v2.2.1，随指令携带，执行器仅凭指令即可完成调用）**：

```python
@dataclass
class Binding:                # lowering 期生成，随 CALL_FUNC/CALL_FB_INSTANCE 指令携带
    formal: str               # 形参名
    mode: str                 # "IN" / "OUT" / "INOUT"
    actual: "StoreKey | StackSlot | Const"   # 实参来源/去向（模式约束见下）
    type: str                 # IEC 类型（加载期与形参声明核对）

@dataclass(frozen=True)
class StackSlot:             # 调用点求值栈中的一个位置（项目工程约定）
    index: int               # 距调用点栈顶的偏移：0 = 栈顶，1 = 栈顶下一项，依此类推
    writable: bool = False   # 是否允许作为 OUT 写回候选；当前阶段执行器未定义前可保守拒绝

@dataclass
class ValueRef:               # INOUT 的运行期形态：指向 Store 键的别名引用
    key: str                  # 被调方读写即直接作用于该键（与库块 RealRef 语义对齐）
    type: str
```

语义：`IN` = 求值后拷入帧/实例管脚；`OUT` = 执行后拷回 `actual`；`INOUT` = 以 `ValueRef` 传入，禁止值拷贝往返。**`actual` 按模式约束（加载期校验）**：`IN` 可接受 `StoreKey`/`StackSlot`/`Const`；`OUT` 只能绑定**可写位置**（`StoreKey`/可写 `StackSlot`，禁止 `Const`）；`INOUT` 必须绑定**可写 `StoreKey`**（运行期化为 `ValueRef`，禁止 `Const` 与普通值拷贝）。绑定表在 lowering 期做齐全性检查（必连形参缺失 = 加载错误）。

**`StackSlot.index` 唯一口径（项目工程约定）**：

- `index` 是相对调用点栈顶的偏移，`0` 表示栈顶；它不是绑定在列表中的书写序号。
- 同一调用中所有 `IN × StackSlot` 的 `index` 必须是非负整数、互不重复，并恰好连续覆盖 `{0..k-1}`；加载器按索引定位并核对类型，绑定条目的书写顺序不改变取值语义，调用时消费这 `k` 个栈值。
- 负数、布尔值、非整数、重复、不连续、栈深不足或索引所指值类型不匹配均为加载错误，必须阻止 IR 进入执行层。
- `OUT × writable StackSlot` 在模型层保留，但在调用帧写回语义尚未由阶段 1 执行器正式实现并测试前，加载器允许保守拒绝；放开该形态必须同步规格、实现与反证测试。
- 本约定用于保证前端/lowering/加载器/执行器对同一字段的解释一致，**不是** CODESYS 或 IEC 61131-3 官方栈编码语义；若执行器工作包发现需要改变，必须重新走规格裁决。

### 5.3 数值模式钩子（D4/D5；v2.2.1 统一口径）

**唯一口径（与 `TARGET_PROFILE §4.2` 一致）**：**REAL** 在 F1-expr/F2 下于 IR 内**逐指令**量化到 binary32（下列全部边界）；**整数**仅在 `STORE_VAR`/`CONVERT` 保证按声明类型截断，其余指令出口是否回绕由 §5.4 `int_intermediate_policy` 决定。F2 与 F1-expr 的 IR 行为相同，区别仅在库块用 F2 变体（`COMPONENT_CONTRACT §2.x`）。

REAL 量化边界（按指令携带的类型施加，**而不只是 `BINOP`/`CONVERT`**）：

1. `LOAD_CONST`：常量按 `type` 编码（REAL 字面量先舍入到 binary32）。
2. `BINOP` / `UNOP` / `CALL_STD`：结果按结果类型量化。
3. `CONVERT`：按 `to` 类型量化。
4. **`STORE_VAR`**：写入前按目标变量 `type` 量化——这是"变量/管脚边界"约束的主要落点（含 FB 输入脚 `STORE_VAR <inst>.<in>`）。
5. **`CALL_FB` 输出回收**：描述符回收 `<inst>.<out>` 时按 `Pin.iec_type` 量化（库块内部 64 位不受影响，边界被量化）。
6. `CALL_FUNC` 返回值 / `CALL_FB_INSTANCE` 输出：按声明类型量化。

**可执行 IR 是套这层约束的唯一位置**——这正是分离出可执行 IR 的核心收益之一。F1/F2 量化的承诺边界见 `TARGET_PROFILE §4.1/§4.2`（**F1 不承诺与 CODESYS 位级一致；F2 位级一致须真机证明**）。

### 5.4 三种类型角色与整数回绕发生点（v2.2，二轮评审修正）

上文的"指令出口量化"对**整数**是过强假设。CODESYS 的算术**临时结果可能按目标平台原生位宽**计算，真正截断可能发生在**赋值/转换点**，且溢出行为与编译目标相关——`WORD` 运算并不必然每步按 16 位截断。因此 IR 显式区分三种类型角色：

| 角色 | 定义 | 落点 |
|---|---|---|
| **声明类型** | 变量/管脚声明的 IEC 类型 | `VarDecl.iec_type` / `Pin.iec_type` |
| **求值类型** | 表达式求值时的公共类型（IEC 提升规则） | `BINOP/UNOP/CALL_STD` 的 `type` 字段 |
| **存储类型** | 写入目标的类型（=目标的声明类型） | `STORE_VAR` 的 `type`、`CONVERT` 的 `to` |

**整数回绕/截断模型（v2.2.1 修正：按 CODESYS 真实模型建模，不再用"无限精度 vs 逐步截断"二分）**：

CODESYS 整数**临时结果使用目标设备原生位宽**（x86/ARM32 至少 32 位、x64 为 64 位）——既不是无限精度，也不必然按声明类型（如 `WORD` 16 位）逐步截断。目标画像参数：

```python
int_native_width: Literal[32, 64]            # 目标原生位宽（⬜ 待 TARGET_PROFILE 锁定 CPU 后定）
int_intermediate_policy: Literal[
    "native_width",     # 默认假设：中间结果按 int_native_width 模 2ⁿ 回绕
    "declared_width",   # 每条 BINOP/UNOP 出口按求值类型声明位宽回绕（逐步截断）
]                       # E 模式忽略本参数（Python int 不回绕）
int_overflow_convert_policy: str = "TBD"     # 越界 CONVERT / 有符号溢出的具体行为（截断/饱和/未定义），待真机裁决
```

- **保证发生**（所有 fidelity 模式）：`STORE_VAR` 与 `CONVERT` 是截断/量化点。**范围内**转换按确定规则执行；**越界**转换（值超出目标类型范围）——CODESYS 官方说明其结果可能依赖处理器、属未定义行为——按 `int_overflow_convert_policy` 处理，**未裁决前为 TBD**，不承诺具体结果，仅保证确定性（同输入同输出）并可配置告警。
- 中间结果按 `int_intermediate_policy`；哪种匹配真机**必须由黄金轨迹裁决**（`GOLDEN_TRACE_FORMAT §3` #7；`RISKS.md::PLATFORM-INT-WIDTH-1`）。裁决前 fidelity 模式下的整数中间溢出结果视为"待验证"。
- **REAL 量化口径不由本节参数决定**，统一见 §5.3 与 `TARGET_PROFILE §4.2`：F1-expr/F2 下 IR 表达式**逐指令** binary32，本节参数只约束整数。
- 措辞修正：无符号整数回绕 = **模 2ⁿ 回绕**（"二进制补码"是有符号表示法，不用于描述无符号回绕）；**有符号溢出**行为在目标画像锁定前不做一概承诺（`TARGET_PROFILE §3`）。

## 6. Lowering（下沉规则）

- **ST AST → IR**：表达式后序遍历生成 `LOAD/BINOP/CALL_*`（lowering 期做类型标注与 `CONVERT` 插入，见 §5.1）；赋值末尾 `STORE_VAR`；`IF/CASE/FOR/WHILE` 生成 `JMP*`+`LABEL`；库 FB 调用 = 逐输入 `STORE_VAR <inst>.<in>` → `CALL_FB <inst>` → 需要时 `LOAD_VAR <inst>.<out>`；用户 POU 调用 = `CALL_FUNC`/`CALL_FB_INSTANCE`。顺序即代码顺序（显式）。
- **CFC 图 → IR**：按**已确定的执行序**（D3 载体分支，见 ENGINE_SCAN_SPEC §5.1）逐节点——PLCopen XML 载体使用已保存的 `executionOrderId`（`order_source=exported`）；.export 自动模式等待后续重建算法（`order_source=reconstructed`，延后阶段 5），**算法未就绪时必须拒绝生成可执行 IR，不能静默猜测**；平台内新建图按拓扑排序定序（`order_source=user_defined`）。定序后：把上游连线对应的值 `LOAD`→`STORE_VAR <node>.<in>`，`CALL_FB/CALL_STD`，输出供下游 `LOAD`。**反馈起点**的源 `LOAD` 改为 `LOAD_PREV <key, type>`（§5.2 正式指令；引擎实现见 ENGINE_SCAN_SPEC §5）。反馈起点是 CFC **元素级**标记并影响反馈环的执行序，"起点元素的哪些入边 lower 成 `LOAD_PREV`"的精确映射**须用真实 CODESYS 导出样本验证**后冻结（登记 `RISKS.md::PLATFORM-CFC-FEEDBACK-MAP-1`）。

两条 lowering 路径产出**同一种指令列表**——这是"ST 与 CFC 同引擎"的工程落地，验收见 §10。

## 7. 变量空间命名

- GVL：`<var>`。
- PROGRAM / 用户 FUNCTION_BLOCK 的**持久状态**：`<instance_path>.<var>`。PROGRAM 的 `instance_path` = `ProgramInstance.store_prefix`；嵌套 FB 的 `instance_path` = `<parent_path>.<InstanceDecl.name>`，装载期递归展开。`VAR` / `VAR_INPUT` / `VAR_OUTPUT` 按该路径分配；`VAR_IN_OUT` 是引用别名，`VAR_TEMP` 与 FUNCTION 局部属调用帧，不分配持久键。
- 库块/实例引脚：`<instance_path>.<pin>`；引脚集合和类型由 L2 描述符提供，未接入描述符时不得猜测并分配。
- POU 局部/temp 调用帧：`<pou>#<frame>.<var>`（带帧号区分递归/多实例）。

**持久键编码是项目工程约定**：正式实现必须将键生成集中在单一 helper（当前为 `src.runtime.store.persistent_key`），避免 loader / 执行器 / 前端各自拼接而分叉。用户 FB 实例只在装载期展开与分配；运行期 `CALL_FB_INSTANCE` 只引用既有路径，不得创建新键。该编码不是 CODESYS / IEC 61131-3 官方命名语义；若后续序列化、调试或执行器需要改变，必须同步重评规格、实现和测试。

## 8. 类型系统（fidelity / engineering 双模式，D4/D5 同步）

> 本节取代 v1 的"默认不仿真 / 默认 64 位 / 阶段 6 裁决"——**现在就定模式**，对应 `TARGET_PROFILE §4` 的一致性等级。

| IEC 类型 | engineering（E，默认） | fidelity（F1 / F2，量化口径见 §5.3） |
|---|---|---|
| BOOL | `bool` | 同 |
| 整数族 | `int`，**不回绕** | 按目标位宽**模 2ⁿ 回绕**（回绕发生点按 §5.4 `int_intermediate_policy`；有符号溢出待目标画像锁定） |
| **REAL** | `float`(64) | **F1-expr/F2**：IR 表达式**逐指令** binary32（§5.3）；**F1-boundary**：原生块仅管脚边界量化；F2 另需库块 float32 变体 |
| LREAL | `float`(64) | 同 |
| TIME | `int` ms | 同 |
| STRING | `str` | 同 |

- **E（默认）**：零块改动，判定走容差（`TARGET_PROFILE §5`）。MVP 与日常开发用。
- **F1**：两种子行为（`TARGET_PROFILE §4.2`）——**F1-expr**：IR 表达式逐指令 binary32 + 整数按 §5.4 政策；**F1-boundary**：原生库块仅管脚边界量化，块内部仍 64 位（不改块）。**不承诺与 CODESYS 位级一致**（F1-boundary 有双重舍入差异；F1-expr 也受目标编译差异影响，见 `TARGET_PROFILE §4.1`）。
- **F2（位级保真候选）**：块级 `float32` 全程 + 整数全程回绕，**位级一致须真机对拍证明**；**需块改造，与"块零改动"互斥**（见 `TARGET_PROFILE §4`）。
- 模式是**装载期引擎配置**：同一份 IR 在 E/F1 下都能跑（F2 需 fidelity 版块）。**禁止运行中热切换**——切换模式必须停止扫描、重新加载（或显式复位全部状态）后生效；否则跨模式的中间状态（64 位残留 vs 量化值、回绕差异）会产生不可复现的混合行为。

## 9. RETAIN / PERSISTENT 模型（阶段 0.5 加厚，阶段 8 实现）

- **RETAIN**：掉电保持，但**冷启动/下载工程时重新初始化**为 `initial`/`init_overrides`。
- **PERSISTENT**：保持范围更大，下载工程也保留（除非显式清除）。
- **FB 级传播**：在某 FB 实例声明 RETAIN，可能使**整个实例**进入保持区——`InstanceDecl.retain` 支持 `{"*"}` 表示整实例保持。
- IR 在 `VarDecl.retain/persistent` 与 `InstanceDecl.retain` 建模（已就位）；快照/恢复时机见 `ENGINE_SCAN_SPEC §6`。实现留阶段 8（`RISKS.md::PLATFORM-RETAIN-1`）。

## 10. 验收要点

- [ ] 同一逻辑用 ST 与 CFC 表达，**lower 出的可执行指令列表能各自跑通且结果逐值一致**（最小示例见 `ENGINE_SCAN_SPEC §8`）。
- [ ] **IR 类型验证 pass 生效**：无类型/类型不匹配的指令列表被加载器拒绝；隐式提升均已显式化为 `CONVERT`。
- [ ] 至少一个用户自定义 FUNCTION_BLOCK + 一个 FUNCTION 能声明、实例化、被调用（分别经 `CALL_FB_INSTANCE`/`CALL_FUNC`），VAR_TEMP/VAR_IN_OUT 语义正确。
- [ ] E 与 F1 模式同一份 IR 都能执行；类型映射与 `TARGET_PROFILE §4` 一致；F1 量化覆盖 §5.3 全部 6 类边界（含 `STORE_VAR` 与 `CALL_FB` 输出回收）并有测试。
