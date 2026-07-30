"""定义工具契约、注册表、参数与结果校验、超时和有界重试。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from kaiji.models import ToolCall, ToolErrorCode, ToolFailure, ToolResult


class ToolExecutionError(Exception):
    """表示工具预期内的执行失败，并显式携带是否可重试。"""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """定义工具名称、说明、输入输出模型、处理函数和可靠性策略。"""

    name: str
    description: str
    arguments_model: type[BaseModel]
    result_model: type[BaseModel]
    handler: Callable[[BaseModel], Any]
    timeout_seconds: float = 2.0
    max_retries: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool name cannot be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries not in (0, 1):
            raise ValueError("the v0 runtime permits at most one retry")


class ToolRegistry:
    """按唯一名称保存工具定义，并向模型暴露参数 Schema。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        # TODO 1：拒绝重复名称，否则保存工具定义。
        # 名称是唯一查找键；描述文本不参与工具身份判断。
        pass

    def get(self, name: str) -> ToolDefinition | None:
        # TODO 1：未知名称返回 None。此处不抛异常，因为 Executor 负责把它
        # 转换为结构化 UNKNOWN_TOOL 失败。
        return None

    def schemas(self) -> list[dict[str, Any]]:
        # TODO 1：暴露工具名称、描述，以及放在 parameters 字段下的
        # Pydantic JSON Schema。
        return []


class ToolExecutor:
    """在工具权限边界内完成调用准备、执行、结果校验和有界重试。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(self, call: ToolCall) -> ToolResult:
        prepared = self._prepare_call(call)
        if isinstance(prepared, ToolResult):
            return prepared

        definition, arguments = prepared
        return self._execute_with_retry(call, definition, arguments)

    def _prepare_call(
        self, call: ToolCall
    ) -> ToolResult | tuple[ToolDefinition, BaseModel]:
        """查找工具并校验参数，但不执行处理函数。"""
        # TODO 2：
        # 1. 通过 Registry 查找 call.name。
        # 2. 工具不存在时返回 UNKNOWN_TOOL，attempts=0。
        # 3. 使用 definition.arguments_model 校验 call.arguments。
        # 4. 捕获 ValidationError，返回 INVALID_ARGUMENTS，attempts=0。
        #
        # 失败时返回 ToolResult，成功时返回 (definition, arguments)，可以让
        # execute() 的控制流保持显式，而不是用异常代替正常分支。
        return self._failure(
            call,
            ToolErrorCode.EXECUTION_FAILED,
            "TODO 2: resolve the tool and validate its arguments",
            retryable=False,
            attempts=0,
        )

    def _execute_with_retry(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        arguments: BaseModel,
    ) -> ToolResult:
        """执行已准备的调用，校验输出，并应用有界重试策略。"""
        # TODO 3：
        # - attempts_allowed 等于 max_retries + 1。
        # - 在尝试循环内部调用 _invoke_with_timeout。
        # - 返回成功前用 definition.result_model 校验原始输出。
        # - TIMEOUT 和 retryable=True 的 ToolExecutionError 可以重试。
        # - ValidationError 与未预期的 Exception 不可重试。
        # - 遇到不可重试失败或耗尽次数时立即停止。
        return self._failure(
            call,
            ToolErrorCode.EXECUTION_FAILED,
            "TODO 3: execute the handler with bounded retry",
            retryable=False,
            attempts=0,
        )

    @staticmethod
    def _invoke_with_timeout(
        definition: ToolDefinition, arguments: BaseModel
    ) -> Any:
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
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            ok=False,
            attempts=attempts,
            error=ToolFailure(code=code, message=message, retryable=retryable),
        )
