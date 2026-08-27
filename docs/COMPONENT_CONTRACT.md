# 组件契约规格（COMPONENT_CONTRACT）v2.1（阶段 0.5 修订二）

> 规格层级：L2（组件模型）。v2 落实阶段 0.5 修订：描述符补全**版本/状态/初始化/参数持久性/输入省略语义/序列化**字段，并对齐 fidelity 模式下的块级 float32 取舍。
> v2.1（外部评审 P0 修正）：注册表按 **`(block_type, variant)`** 注册（修复多变体互相覆盖）；`omittable` 改为**省略语义枚举**（覆盖"保持上次值/None=不覆盖"）；`init_overridable` 拆为**上电初值可覆盖**与 **HMI 运行中可写**两个集合；声明 `BlockSchema` / `RuntimeAdapter` 拆分方向（§3.1）。
>
> **代码示例约定**：本文档 `dataclass` 为示意伪代码（表达字段与语义）；实现时可变默认值（`[]`/`{}`/`set()`）必须用 `field(default_factory=...)`，无默认字段须排在有默认字段之前。
> 配套：`docs/IR_SPEC.md`（L3）、`docs/ENGINE_SCAN_SPEC.md`（L4）、`docs/TARGET_PROFILE.md`。
> 关联决策：**D1 = 外挂描述符（不改已迁移的 14 块）**（保留）。

> 规格层级：L2（组件模型）。由 `docs/STAGE0_DESIGN.md` §2/§7 拆分定稿。
> 配套：`docs/IR_SPEC.md`（L3）、`docs/ENGINE_SCAN_SPEC.md`（L4）。
> 关联决策：**D1 = 外挂描述符（不改已迁移的 14 块）**（STAGE0 §9，已确认）。

---

## 1. 目的

规定平台如何**统一识别、连线、调用**标准库里的功能块（14 业务块 + 8 原语），且**不修改**这些已迁移、已被 690 用例锁定的块。

## 2. 不变式

1. 标准库块源码**零改动**；一切适配通过**外挂块描述符（BlockDescriptor）**完成（决策 D1）。
2. 块的输出暴露方式异构（有的 `step` 返回 dict，有的写 `self.*`，有的用 `RealRef` 做 `VAR_IN_OUT`）——引擎**不猜**，由描述符显式声明取值方式。
3. 引擎只与描述符交互：传入"已解析的输入值字典"，取回"输出值字典"。

## 3. 数据结构

```python
OmitPolicy = Literal[
    "use_default",        # 未连线/未赋值：用 default（= FB 源码声明默认）
    "required",           # 必须连线，否则加载期报错
    "keep_previous",      # 首拍用 default，后续省略时保持该管脚上次值（不重置）
    "none_means_no_write" # 省略/传 None 表示"本拍不覆盖块内该值"（APCM 的 None=不覆盖语义）
]

@dataclass
class Pin:
    name: str                 # = step 的 kwarg 名 / 输出键 / self 属性名
    iec_type: str             # "BOOL"/"REAL"/"INT"/"TIME"...（类型映射见 IR_SPEC §8）
    default: Any = None       # 声明默认值
    kind: str = "VAR_INPUT"   # VAR_INPUT / VAR_OUTPUT / VAR_IN_OUT
    omit_policy: OmitPolicy = "use_default"  # 输入省略语义（v2.1：枚举取代 bool，见下）

@dataclass
class BlockDescriptor:
    block_type: str           # "APCHSFOP" / "TON" / "APCM" ...
    variant: str = "engineering"   # "engineering"（64位，E/F1 共用）/ "fidelity_f2"（块级 float32 版）
    descriptor_version: str   # 描述符版本，如 "1.0"（与块行为/测试基线挂钩，升级须评审）
    cls: type                 # 已迁移的 Python 类
    inputs:  list[Pin]
    outputs: list[Pin]
    inouts:  list[Pin] = []
    ctor_args: list[str] = [] # 构造依赖名，如 ["license_context"]
    output_access: dict[str, str] = {}  # 管脚名 -> 'return:KEY' | 'attr:NAME'
    state_vars: list[str] = [] # 跨周期状态属性名（用于实例内存、RETAIN 选择、序列化）
    retainable: set[str] = set() # 允许标 RETAIN 的状态变量子集（IR_SPEC §9）
    # —— v2.1：原 init_overridable 拆成两个正交集合 ——
    init_overridable: set[str] = set()   # 仅指"上电/装载时初值可被 init_overrides 覆盖"的字段
    hmi_writable: set[str] = set()       # 运行中可被上位/HMI 在线写入的字段（写入时机=拍首输入锁存，走校验）
    serializer: Callable = None # 实例状态 <-> dict 的序列化（快照/恢复/调试，阶段 8）
    call_adapter: Callable    # 封装 step 的实际调用约定 + 输入注入 + 输出/inout 回收
```

- **新增字段（阶段 0.5 / v2.1）**：
  - `descriptor_version`：描述符与块行为/测试基线绑定，升级走评审。
  - `Pin.omit_policy`（v2.1）：省略语义枚举。原 `omittable: bool` 表达不了两类真实语义：**"首次用默认、后续省略保持上次值"**（`keep_previous`）与 **APCM 的"`None`=本拍不覆盖"**（`none_means_no_write`）。每个块每个输入脚在写描述符时必须显式选择四者之一，并有对照测试锁定。
  - `init_overridable` / `hmi_writable`（v2.1 拆分）：**上电初值可覆盖**与**运行中可写**是两回事——前者只在装载期生效一次，后者是运行期在线写。一个字段可属于两者、其一或都不属于；合并成一个集合会导致"允许改初值"被误解为"运行期随时可写"。
  - `state_vars` / `retainable`：暴露跨周期状态，支撑实例内存、RETAIN 选择（IR_SPEC §9）。
  - `serializer`：实例状态快照/恢复（阶段 8 持久化）与调试。
  - `variant`（v2.1，取代 `fidelity_variant` 字段名）：作为**注册键的一部分**（§5），标明 64 位块（engineering，E/F1 共用）或块级 float32 版（F2）。
- `output_access`：`'return:AV'` 从 `step` 返回 dict 取键；`'attr:AV'` 从 `self.AV` 读。
- `call_adapter(instance, dt_ms, resolved_inputs, inout_refs) -> outputs_dict`：唯一与块打交道的函数，按真实签名调用 `step`、注入 `VAR_IN_OUT` 引用、按 `output_access` 收集输出。

### 3.1 BlockSchema 与 RuntimeAdapter 拆分（v2.1 方向性决定）

当前 `BlockDescriptor` 把**可序列化元数据**（管脚/类型/默认值/省略语义/状态变量/版本）与 **Python Callable**（`cls`/`call_adapter`/`serializer`）混在同一模型，导致描述符本身无法序列化/落盘/跨进程传输/做差异评审。阶段 1 实现时拆为两层：

- **`BlockSchema`**（纯数据，可 JSON 序列化）：`block_type`/`variant`/`descriptor_version`/`inputs`/`outputs`/`inouts`/`state_vars`/`retainable`/`init_overridable`/`hmi_writable`/`output_access`（字符串规则）。
- **`RuntimeAdapter`**（进程内绑定）：`cls`/`call_adapter`/`serializer`/`ctor_args` 解析。
- 注册表存 `(schema, adapter)` 对；文档/工具/导入导出只依赖 `BlockSchema`。本文件的 `BlockDescriptor` 视为两者的逻辑合并视图。

### 3.1.1 `output_access` 的私有只读载体（WP-20260809-087 候选）

`BlockSchema.output_access` 的公开合同是 `Mapping[str, str]`；实现用未导出的
`_OutputAccessMap` 承载。它只有 `_pairs` 一个实例字段，字段为 exact
`tuple[tuple[exact str, exact str], ...]`，不保留调用方 Mapping/dict、也不建
第二份 dict/index。因此索引、迭代、`len`、`items/keys/get`、Mapping 相等、
`dict(schema.output_access)` 和 `to_json()` 均保留插入顺序和既有值语义。

构造 `BlockSchema` 时，输入可为一般 `Mapping`，但只按迭代顺序快照一次、对每个
已接受键只下标取值一次；实现不调用输入的 `dict/items/keys/len/repr/str`。重复键、
非 exact `str` 键/值或输入协议抛出的任意 `BaseException` 都失败关闭为固定
`SchemaValidationError`，不会返回半构造 Schema。为限制持续产出键的资源消耗，
快照最多接受 4096 项（当前 22-schema 目录最大为 87 项）；该资源上限不能中断一个
自身阻塞、永不返回下一键的迭代器。

该载体在正常 Python 路径下拒绝属性写/删和下标写，且没有 `__dict__`；但是 Python
的特权 `object.__setattr__` / `object.__delattr__` 不可被本合同阻止。后续信任边界可
只读取 `_pairs` 的 exact 形状来检测并拒绝强制篡改或缺字段。这是 descriptor 纯数据
载体，不是通用安全 Mapping，也不构成 PLC/CODESYS、HAL 或现场安全证明。

### 3.2 两个 `ctor_args` 层与启动装配失败关闭（WP-20260728-041 澄清）

本包起启动装配层（`src/runtime/parameters.py::build_runtime`）实际落地以下语义，做**最小契约澄清**（不改阶段路线、不改历史版本结论）：

- **两个同名 `ctor_args` 永久区分**：
  - `RuntimeAdapter.ctor_args: tuple[str, ...]` 只表示从任务 `dependencies` 注入的**共享**构造依赖名（如 `license_context`），按声明顺序位置解析，保持"同任务共享同一 context"（`APCM`/`APCPID`/`APCPIDZZD` 共享 `LicenseContext`）；
  - `InstanceDecl.ctor_args: dict[str, value]`（IR_SPEC §2）只表示**单实例关键字**构造配置，只能命中该实例 `BlockSchema.init_overridable` 且须再过参数类型/值校验；
  - 两者不得互相覆盖/遮蔽/位置传递/静默丢弃；实例配置与共享依赖同名时**失败关闭**（不得遮蔽任务依赖）。不得用 `inspect.signature` 自动开放构造参数——Python 签名只作已授权后的二次一致性反证，Schema 未声明的构造覆盖一律拒绝。
- **`init_overridable` ⊥ `hmi_writable`**（承接 v2.1 拆分）：`init_overridable` 是"仅上电/装载时可覆盖的实例状态字段"，须 ⊆ `state_vars`；两集合是相互独立的分类轴，不共用授权、不因一方声明放宽另一方。本包只 `APCHSACCUM` 声明 `init_overridable={"IV","MS","MC"}`，其余 21 个 Schema 保持空集；`hmi_writable` 全部保持空集（本包不实现运行期在线写）。
- **`init_overrides` 不代表每拍驱动**：它是 Store 管脚**装载初值**通道，`required` 仍须真实连线/驱动，`use_default`/`keep_previous`/`none_means_no_write` 语义不变；某个初值不得被解释为"该输入本拍已驱动"。
- **启动装配失败关闭**：先纯校验并以确定顺序汇总硬错误 → 若有错误一次性失败（不返回半构造 Store/Executor、不改传入 `dependencies`/配置映射、不污染 Registry）→ 全部通过后才构建布局/Store、构造全部 library runtime 并一次性返回；重试同一合法输入必得全新 Store/Executor/块实例。显式时间参数目录只对**实际出现在** `init_overrides`/启动配置中的装载值发结构化 warning（不升级为失败、不 round/coerce）。

### 2.x fidelity 模式与"零改动"的取舍（对齐 TARGET_PROFILE §4）

- **engineering / F1**：用现有 64 位块，描述符 `variant="engineering"`，**零改动**；F1 的边界 REAL32/整数回绕由可执行 IR 在边界处施加（不进块，边界清单见 `IR_SPEC §5.3`）。
- **F2 位级保真候选**：需**块级 float32 版**，另册描述符 `variant="fidelity_f2"`，**与"零改动"互斥**——按需立项，不默认做。同一 `block_type` 的两个变体按 `(block_type, variant)` 分别注册（§5），按 `ctx.mode` 选。

## 4. 特殊接口处理

- **VAR_IN_OUT（如 `APCM.ZLOUT` 的 `RealRef`）**：建模为 `inout` 管脚。引擎用变量空间里一个变量绑定，`call_adapter` 在 `step` 前把当前值塞进 `RealRef`、`step` 后回读写回。
- **构造依赖（`LicenseContext`）**：`ctor_args` 指向 GVL 中的共享对象；同一任务内共享同一 context 的语义必须保持（如 `APCM` 与其内部 `PIDZZD1` 同 context）。
- **块内授权门控（`KZQBDYZMK`）**：属块内部逻辑，第 2 步推进时自然执行，引擎**不重复**做；与系统级 `system_ready/safety_ok` 是两层，互不替代。
- **无 EN/RESET 的原语**：按 `00a` R2，"调不调 `step`"由执行体/调度决定，块本体不加 EN/RESET。

## 5. 库注册表

```python
# v2.1：键 = (block_type, variant)，修复"后注册变体覆盖前者"
REGISTRY: dict[tuple[str, str], BlockDescriptor] = {}

def register(desc):
    key = (desc.block_type, desc.variant)
    if key in REGISTRY:
        raise DuplicateDescriptorError(key)   # 同键重复注册 = 工程错误，显式报错而非静默覆盖
    REGISTRY[key] = desc

def resolve(block_type: str, mode: str) -> BlockDescriptor:
    # E / fidelity_f1 共用 engineering 变体；fidelity_f2 要求 f2 变体，缺失即加载期报错（不静默降级）
    variant = "fidelity_f2" if mode == "fidelity_f2" else "engineering"
    return REGISTRY[(block_type, variant)]
```

- 标准库（14 块 + 8 原语）启动时注册（首批只有 `engineering` 变体）。
- ST 的 `FOP1(...)` 调用、CFC 的 `APCHSFOP` 框，都经 `resolve(block_type, ctx.mode)` 解析。
- F2 模式下缺 f2 变体 → **加载期报错**，绝不静默回退 64 位块（否则"位级保真"名存实亡）。
- 阶段 9 的 AI 功能块用同一机制接入，**不改引擎**。

## 6. 描述符生成方式（决策 D1）

- 描述符**外挂**，置于 `src/runtime/descriptors/`，不动 14 个块文件。
- 可半自动：用 `inspect.signature(cls.step)` 抽 kwarg 名作输入管脚，人工补输出管脚、类型与 `output_access`。
- 验收时每个描述符需有"调用一次该块、输入输出与块自身行为一致"的对照测试。

## 7. 验收要点

- [ ] 14 块 + 8 原语全部有描述符且按 `(block_type, variant)` 注册成功；同键重复注册报错有测试。
- [ ] 每个描述符的 `call_adapter` 经对照测试，证明与直接调用该块 `step` 结果一致（含 `VAR_IN_OUT`、含 `LicenseContext` 共享）。
- [ ] 每个输入脚的 `omit_policy` 显式声明并有对照测试（特别是 APCM 的 `none_means_no_write` 脚与 `keep_previous` 脚）。
- [ ] `init_overridable` 与 `hmi_writable` 语义分离有测试：装载期覆盖只生效一次；HMI 写走拍首锁存+校验。
- [ ] F2 模式缺 f2 变体时加载期报错（不静默降级）有测试。
- [ ] 块源码与既有 690 用例零改动。
