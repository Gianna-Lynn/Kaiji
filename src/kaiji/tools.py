"""定义工具契约、注册表、参数与结果校验、超时和有界重试。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import json
import math
from typing import Any

from pydantic import BaseModel, ValidationError

from kaiji.models import ToolCall, ToolErrorCode, ToolFailure, ToolResult


class ToolExecutionError(Exception):
    """handler 在预期范围内失败时抛出的异常,显式声明是否可重试。

    Executor 捕获该异常后,按 ``retryable`` 决定是否在尝试上限内重试;
    其它未预期异常一律视为不可重试。

    Attributes:
        retryable: 该失败是否允许重试。
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        if type(retryable) is not bool:
            raise TypeError("retryable 必须是真正的 bool")
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """声明一个工具的完整契约:标识、说明、校验模型、处理器与可靠性策略。

    Attributes:
        name: 工具的唯一名称,注册时作为键且不允许重名。
        description: 面向 LLM 的用途说明,会随 JSON Schema 一起暴露给模型。
        arguments_model: 参数校验模型,Executor 用它解析 LLM 传来的参数。
        result_model: 结果校验模型,Executor 用它校验 handler 的返回值。
        handler: 实际执行工具的处理函数,接收校验后的参数模型。
        timeout_seconds: 单次尝试的超时时间(秒),必须为正的有限数。
        max_retries: 失败后允许的最大重试次数,v0 Runtime 只允许 0 或 1。

    Raises:
        ValueError: name/description 为空、timeout_seconds 非正或非有限、
            max_retries 超出允许范围。
        TypeError: 校验模型不是 BaseModel 子类,或 handler 不可调用。
    """

    name: str
    description: str
    arguments_model: type[BaseModel]
    result_model: type[BaseModel]
    handler: Callable[[BaseModel], Any]
    timeout_seconds: float = 2.0
    max_retries: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("tool name cannot be empty")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("tool description cannot be empty")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        if type(self.max_retries) is not int or self.max_retries not in (0, 1):
            raise ValueError("the v0 runtime permits at most one retry")
        if not isinstance(self.arguments_model, type) or not issubclass(
            self.arguments_model,
            BaseModel,
        ):
            raise TypeError("arguments_model 必须是 BaseModel 子类")
        if not isinstance(self.result_model, type) or not issubclass(
            self.result_model,
            BaseModel,
        ):
            raise TypeError("result_model 必须是 BaseModel 子类")
        if not callable(self.handler):
            raise TypeError("handler 必须可调用")


class ToolRegistry:
    """以 name 为键保存 ToolDefinition 的注册表。

    register() 保证名称唯一,重名直接报错;schemas() 把每个工具的
    arguments_model 转成 JSON Schema,供 Runtime 交给 LLM,让模型知道
    每个工具接受什么参数、如何调用。
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        """注册一个工具定义。

        Raises:
            ValueError: 已存在同名工具。
        """

        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        """按名称查询工具定义,未注册时返回 None。"""

        return self._tools.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """按注册顺序返回只读定义序列,供受控 Registry 组合使用。"""

        return tuple(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        """把每个工具的 arguments_model 转成面向 LLM 的 JSON Schema。

        Returns:
            按注册顺序排列的 Schema 列表,每项包含 name、description 与
            parameters。parameters 固定带 ``additionalProperties=False``,
            因为 Executor 一律拒绝未声明参数,暴露给模型的 Schema 必须
            表达同一边界。
        """

        schemas = []
        for tool in self._tools.values():
            parameters = tool.arguments_model.model_json_schema()
            parameters["additionalProperties"] = False
            schemas.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": parameters,
                }
            )
        return schemas


class ToolExecutor:
    """在工具权限边界内完成调用准备、执行、结果校验和有界重试。"""

    def __init__(self, registry: ToolRegistry, *, retry_enabled: bool = True) -> None:
        if type(retry_enabled) is not bool:
            raise TypeError("retry_enabled 必须是真正的 bool")
        self._registry = registry
        self._retry_enabled = retry_enabled

    def execute(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用,返回成功输出或结构化失败。

        处理流程:查找定义 → 校验参数 → 在超时保护下调用 handler →
        校验并序列化结果。可重试的失败会在尝试上限内重试,任何工具
        异常都不会击穿 Runtime。

        Args:
            call: 待执行的工具调用,含工具名称与原始参数字典。

        Returns:
            返回一个ToolResult,表示调用结果。
            成功时 ok=True,output 为仅含 JSON 原生类型的字典;
            失败时 ok=False,error 为结构化 ToolFailure。

        Raises:
            AssertionError: 仅当重试循环意外耗尽时触发,正常流程不会发生。
        """

        # ── 阶段 1/3:查找工具定义 ──────────────────────────────
        # 分支 1a:工具未注册 → 属于调用方错误,不消耗重试次数,
        # 直接返回 UNKNOWN_TOOL(attempts=0)。
        definition = self._registry.get(call.name)
        if definition is None:
            return self._failure(
                call,
                ToolErrorCode.UNKNOWN_TOOL,
                f"unknown tool: {call.name}",
                retryable=False,
                attempts=0,
            )

        # ── 阶段 2/3:校验参数 ──────────────────────────────────
        # 先用 json.dumps 拦截 NaN/Infinity 等无法严格序列化的值,
        # 再按工具 schema 严格校验(extra="forbid")。
        # 分支 2a:任一校验失败 → 返回 INVALID_ARGUMENTS,不重试(attempts=0)。
        try:
            json.dumps(call.arguments, allow_nan=False)
            arguments = definition.arguments_model.model_validate(
                call.arguments,
                extra="forbid",
            )
        except (TypeError, ValueError, ValidationError) as exc:
            return self._failure(
                call,
                ToolErrorCode.INVALID_ARGUMENTS,
                str(exc),
                retryable=False,
                attempts=0,
            )

        # ── 阶段 3/3:有界重试循环 ──────────────────────────────
        # attempts_allowed = max_retries + 1;retry_enabled=False 时
        # 无视 max_retries,只允许 1 次尝试。每轮迭代要么在 3.3 成功
        # 返回,要么在 3.5 返回失败,要么以可重试失败进入下一轮。
        attempts_allowed = (definition.max_retries if self._retry_enabled else 0) + 1
        for attempt in range(1, attempts_allowed + 1):
            try:
                # 步骤 3.1:在超时保护下调用 handler,拿到原始返回值。
                raw_result = self._invoke_with_timeout(definition, arguments)
                # 步骤 3.2:校验并序列化结果,只让合法结果进入成功路径:
                # Pydantic 校验 → 转成仅含 JSON 原生类型的字典 →
                # 试序列化一次,确认不含 NaN/Infinity 等非法值。
                result = definition.result_model.model_validate(
                    raw_result,
                    extra="forbid",
                )
                output = result.model_dump(mode="json")
                try:
                    json.dumps(output, allow_nan=False)
                except (TypeError, ValueError) as exc:
                    raise _InvalidToolResult(str(exc)) from exc
                # 步骤 3.3(成功出口):ok=True,携带序列化后的 output。
                return ToolResult(
                    call_id=call.call_id,
                    tool_name=call.name,
                    ok=True,
                    attempts=attempt,
                    output=output,
                )
            # 步骤 3.4:异常 → 结构化失败映射,任何工具异常都不得击穿Runtime。
            # 分支语义:超时 → 可重试;
            # 执行失败 → 按 handler显式声明的 retryable 决定;
            # 结果校验失败(含非法 JSON 值)与任何未预料异常 → 不可重试。
            except FutureTimeoutError:
                failure = ToolFailure(
                    code=ToolErrorCode.TIMEOUT,
                    message=f"tool exceeded {definition.timeout_seconds}s timeout",
                    retryable=True,
                )
            except ToolExecutionError as exc:
                failure = ToolFailure(
                    code=ToolErrorCode.EXECUTION_FAILED,
                    message=str(exc),
                    retryable=exc.retryable,
                )
            except ValidationError as exc:
                failure = ToolFailure(
                    code=ToolErrorCode.INVALID_RESULT,
                    message=str(exc),
                    retryable=False,
                )
            except _InvalidToolResult as exc:
                failure = ToolFailure(
                    code=ToolErrorCode.INVALID_RESULT,
                    message=str(exc),
                    retryable=False,
                )
            except Exception as exc:
                failure = ToolFailure(
                    code=ToolErrorCode.EXECUTION_FAILED,
                    message=f"{type(exc).__name__}: {exc}",
                    retryable=False,
                )

            # 步骤 3.5(失败出口):
            # 不可重试或已达尝试上限 → 立即返回;
            # 否则回到循环头部,进入下一轮重试。
            if not failure.retryable or attempt == attempts_allowed:
                return ToolResult(
                    call_id=call.call_id,
                    tool_name=call.name,
                    ok=False,
                    attempts=attempt,
                    error=failure,
                )

        # 理论上不可达:每轮迭代要么 return,要么以可重试失败进入下一轮;
        # 循环能耗尽说明重试上限计算与返回逻辑不一致,用断言兜底。
        raise AssertionError("attempt loop must return")

    @staticmethod
    def _invoke_with_timeout(definition: ToolDefinition, arguments: BaseModel) -> Any:
        """在专用线程池中调用 handler,并对单次尝试施加超时。

        Args:
            definition: 目标工具定义,提供 handler 与超时时间。
            arguments: 已通过校验的参数模型。

        Returns:
            handler 的原始返回值,交由调用方做结果校验。

        Raises:
            FutureTimeoutError: 单次尝试超过 definition.timeout_seconds。
        """

        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=definition.name)
        future = pool.submit(definition.handler, arguments)
        try:
            return future.result(timeout=definition.timeout_seconds)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _failure(
        call: ToolCall,
        code: ToolErrorCode,
        message: str,
        *,
        retryable: bool,
        attempts: int,
    ) -> ToolResult:
        """构造字段一致的失败 ToolResult。

        Args:
            call: 原始工具调用,用于回填 call_id 与 tool_name。
            code: 结构化错误码。
            message: 面向调用方与 Trace 的错误描述。
            retryable: 该失败是否可重试。
            attempts: 失败发生在第几次尝试;参数阶段失败时为 0。

        Returns:
            ok=False 且携带结构化 ToolFailure 的 ToolResult。
        """

        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            ok=False,
            attempts=attempts,
            error=ToolFailure(code=code, message=message, retryable=retryable),
        )


class _InvalidToolResult(Exception):
    """标记通过 Pydantic 校验后仍无法严格 JSON 序列化的工具结果。

    仅在 execute() 内部抛出,最终转换为 INVALID_RESULT 失败。
    """
