# 阶段 0.5 可执行验证原型（一次性代码，可丢弃）

> 范围严格按 `docs/PLATFORM_ROADMAP.md` 阶段 0.5 定义。**目的仅是证明"IR 语义可执行、
> 双前端可合流、关键语义可测"**，支撑冻结评审；不是阶段 1 交付物，正式工程化实现
> 全部放阶段 1（届时本目录代码不复用即弃）。不修改 `src/` 任何已迁移代码，仅只读
> 复用 `src/primitives/timers.TON` 与 `src/compat/conversions.real_to_int`。

## 运行

```bash
# 原型自带测试（核心原型 55 项，连同两份导入样本回归共 68 项）
python -m unittest discover -s prototype_05 -t .
# 既有迁移基线（690 项，确认零影响）
python -m unittest discover -s tests -t .
```

## 覆盖范围（↔ 路线图阶段 0.5 条目）

| 路线图要求 | 落点 |
|---|---|
| 最小指令集 LOAD_CONST/LOAD_VAR/STORE_VAR/BINOP AND/JMP* | `ir.py`（另含案例所需 UNOP/CONVERT/CALL_FB/CALL_FB_INSTANCE） |
| TON 库块经描述符调用（D1 零改动） | `descriptors.py`（(block_type, variant) 注册表 + call_adapter） |
| 一个 BOOL 输出走 OutputPolicy | `engine.py` §4 实现 + `test_dual_lowering` |
| ST 与 CFC 两条 lowering 路径 → 同一指令列表跑 N 拍 | `frontends.py` + `test_dual_lowering`（与 ENGINE_SCAN_SPEC §8 蓝本逐条一致） |
| ① 整数临时位宽/存储截断（int_intermediate_policy 可切换且结果不同） | `numeric.py` + `test_semantic_cases.TestCase1`（native=45000 / declared=12232） |
| ② REAL/F1 量化（F1-expr 与 E 可区分） | `test_semantic_cases.TestCase2`（2^24+1：E=16777217.0 / F1=16777216.0） |
| ③ VAR_IN_OUT 引用（ValueRef 别名，写透到调用方） | `engine._call_fb_instance` + `test_semantic_cases.TestCase3` |
| ④ shadow / 扫描异常 / 提交失败三条输出路径 | `test_output_paths.py`（§4.1 两层状态、§4.3 runner 提交、§4.4 固定行为） |
| ⑤ 双 FB 实例状态互不串扰（定义/实例分离） | `test_semantic_cases.TestCase5`（CounterFB×2 + TON×2） |
| IR 类型验证 pass（无类型/类型不匹配拒绝加载） | `loader.py` + `test_loader_validation.py` |

## 明确不在本原型范围（阶段 1+ 实现）

`LOAD_PREV`/CFC 反馈环、`CALL_FUNC`/`CALL_STD`、VAR_TEMP、RETAIN、嵌套 FB 实例展开、
omit_policy 除 `use_default` 外的三种语义、F2 变体、watchdog 驱动层、真实 I/O。

## 原型自身的工程约定（非规格结论，Codex 首轮审核已逐条裁决）

1. **冷启动/无物理历史的限速基准**：规格已于 2026-07-12 冻结评审冻结为
   **可信设备反馈优先、否则 `safe_value`**（项目工程约定，见 `ENGINE_SCAN_SPEC` v2.2.2 §4.1；
   `RISKS.md::PLATFORM-OUTPUT-BASELINE-1` 已 resolved）。本原型仅实现**无反馈时的
   `safe_value` 分支**（覆盖 ① 冷启动正常路径首拍；② 冷启动直接 shadow 后切实写且无
   `last_physical_committed`，测试锁定），未实现 HAL 反馈读取与可信性判定（阶段 7 范围）。
2. **native_width 中间回绕按有符号补码实现**：作待真机验证假设保留（黄金轨迹 #7 裁决）。
3. **加载器"语句边界栈空"规则**：审核接受为原型简化；完整基本块数据流验证留阶段 1。
4. **channel_fault 升级后不自动清除**：规格已冻结为**锁存 + 三条件显式复位**（`ENGINE_SCAN_SPEC`
   v2.2.2 §4.4）；本原型仅实现锁存分支，未实现显式复位接口（后续阶段实现）。
5. ST 侧源模型为手构 AST：审核确认符合阶段 0.5 范围（文本解析器属阶段 3）。

## 定向返修（两轮，均有反证测试）

### 第二轮（Codex 复核指出的两个遗留缺口）

| # | 意见 | 修复 | 反证测试 |
|---|---|---|---|
| R2-1 | Binding 表结构非法可加载（重复 formal 静默覆盖、非法 actual_kind、const 值与类型不匹配运行期才爆） | `loader._validate_bindings` 加重复/枚举/const 值检查；`LOAD_CONST` 同一原则 | `TestReview2Round2BindingStructure` |
| R2-2 | 安全配置接受 NaN/Infinity；整数安全值不查范围 | `_value_matches_type` 加有限数与 IEC 位宽范围检查（UINT 拒负值）；`rate_limit` 必须有限正数（不限速用 None） | `TestReview3Round2PolicyFiniteAndRange` |

### 第一轮（6 条意见）

| # | 意见 | 修复 | 反证测试 |
|---|---|---|---|
| 1 | 驱动抛异常破坏逐通道隔离 | `_commit` 捕获异常按写失败路径处理 + `commit_exception` 告警 | `test_review_rework.TestReview1` |
| 2 | 绑定 actual 变量真实类型未校验 | `loader` 比对 `decl_types[b.actual] == b.type` | `TestReview2`（REAL INOUT→BOOL 拒绝） |
| 3 | OutputPolicy 校验不完整 | FaultAction 枚举/rate_limit>0/retry_n≥1/safe_value 类型全部硬拒绝 | `TestReview3` |
| 4 | 冷 shadow→实写无 LPC 直接跳变 | 无 LPC 时基准退 safe_value（暂定语义，见上第 1 条） | `TestReview4` |
| 5 | 整数 DIV/MOD 经 float 失精度 | 纯整数向零截断算法（`_int_div_trunc`），MOD 同步 | `TestReview5`（LINT_MAX/1、负数截断） |
| 6 | 治理文档未对齐 | ROADMAP 阶段 0.5 状态、RISKS（EXEC-IR-1 更新 + 新增 OUTPUT-BASELINE-1 / IMPORT-TRIAL-1）、PROJECT_STATE 同步 | — |

## 文件清单

- `ir.py` — 全类型化指令 + Binding/OutputPolicy/声明/Task（IR_SPEC §2/§3/§5 子集）
- `numeric.py` — E / F1-expr 数值模式（binary32 逐指令量化、整数回绕两政策）
- `descriptors.py` — 块描述符 + (block_type, variant) 注册表 + TON 描述符
- `loader.py` — 类型验证 pass、OutputPolicy/绑定校验、装载期实例展开
- `engine.py` — 五步扫描、runner 异常提交、两层输出状态、提交失败固定行为、执行器
- `frontends.py` — ST AST / CFC 图两条 mini-lowering
- `programs.py` — 测试程序装配
- `tests/` — 68 项（test_dual_lowering / test_loader_validation / test_semantic_cases /
  test_output_paths / test_review_rework，共 55 项原型用例；另含导入试验回归锁
  test_import_trial 5 项 + test_import_trial_plcopen 8 项）

## 测试结果（最近实际运行：2026-07-12）

- `python -m unittest discover -s prototype_05 -t .`：**68/68 通过**（55 原型 + 5 样本一回归锁 + 8 样本二回归锁）。
- 既有基线 `python -m unittest discover -s tests -t .`：**690/690 通过**（本原型零改动 src/）。
- 历史记录：2026-07-05 两轮返修完成时为 55/55（35 原有 + 12 一轮返修反证 + 8 二轮返修反证），系当时尚无导入试验回归锁的历史子集计数,非当前口径。
