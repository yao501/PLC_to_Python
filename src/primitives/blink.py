"""PLC 基础原语 ``BLINK``（方波振荡器）。

来源：CODESYS Util 库标准 FB。语义以用户提供的 ``BLINK.docx`` 文档截图为
**唯一真相源**（Codesys 图形化文档页 ``BLINK (FB)``）。

**官方文档明确、本实现严格遵循**：

* 输入：

  - ``ENABLE : BOOL``
    * TRUE  — 开始闪烁；
    * FALSE — 停止闪烁，``OUT`` 保持当前值（原文 "keeps its value"）。
  - ``TIMELOW : TIME`` — ``OUT`` 为 FALSE 的持续时间（本实现为 ``TIMELOW_ms``）。
  - ``TIMEHIGH : TIME`` — ``OUT`` 为 TRUE 的持续时间（本实现为 ``TIMEHIGH_ms``）。

* 输出：

  - ``OUT : BOOL`` — **冷启动为 FALSE**（原文 "starts with FALSE"），按
    high/low 时间交替。

**本项目工程约定（docstring / 测试 / RISKS 三处口径一致）**：

1. ``ENABLE=FALSE`` 时，``OUT`` 保持且 **内部相位计时 ``_elapsed_ms`` 同步冻结**；
   下一次 ``ENABLE=TRUE`` 从冻结点续跑，而不是重置为 0。
2. 一次 ``step(dt_ms=...)`` 允许 ``dt_ms`` 跨越多个相位：使用内部 ``while``
   循环逐相位消费 ``_elapsed_ms``，不会因"每拍最多翻一次"而使波形失真
   或产生长周期漂移。
3. 参数非负（``TIMELOW_ms ≥ 0`` / ``TIMEHIGH_ms ≥ 0``）属项目级参数契约
   （``00a-runtime-contract`` R7 第 7 条），由 ``RUNTIME-PARAM-VALIDATION``
   上层兜底；本块**不内嵌参数非负校验**。
4. 唯一的块内护栏：若 ``TIMELOW_ms + TIMEHIGH_ms <= 0``（无可振荡周期），
   本拍直接保持 ``OUT``、不推进 ``_elapsed_ms``。**该判断仅用于防止状态机
   在退化输入下进入不可终止循环；不构成块内参数合法化，也不替代项目级参数
   校验契约，更不是业务语义兜底。**

按 ``00a-runtime-contract`` 契约：

* 时间单位统一整数毫秒（``dt_ms`` / ``TIMELOW_ms`` / ``TIMEHIGH_ms``）；
* 相位累积采用整数加法 ``self._elapsed_ms += dt_ms``；
* 翻转采用余数保留 ``_elapsed_ms -= threshold``，长周期无漂移；
* 禁止读取系统时钟。
"""

from __future__ import annotations


class BLINK:
    """方波振荡器。冷启动 ``OUT=FALSE``；``ENABLE=FALSE`` 时 ``OUT`` 与
    ``_elapsed_ms`` 同时冻结；单拍可跨多个相位。
    """

    def __init__(self) -> None:
        self.OUT: bool = False
        self._elapsed_ms: int = 0

    def step(
        self,
        dt_ms: int,
        ENABLE: bool,
        TIMELOW_ms: int,
        TIMEHIGH_ms: int,
    ) -> bool:
        if not ENABLE:
            return self.OUT

        if TIMELOW_ms + TIMEHIGH_ms <= 0:
            return self.OUT

        self._elapsed_ms += dt_ms

        while True:
            threshold = TIMEHIGH_ms if self.OUT else TIMELOW_ms
            if threshold <= 0:
                self.OUT = not self.OUT
                continue
            if self._elapsed_ms < threshold:
                break
            self._elapsed_ms -= threshold
            self.OUT = not self.OUT

        return self.OUT
