from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from kaiji.models import ToolCall, ToolErrorCode, ToolFailure, ToolResult


class ToolExecutionError(Exception):
    """An expected tool failure with an explicit retry policy."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ToolDefinition:
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
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        # TODO 1: Reject duplicate names, otherwise store the definition.
        # Keep the name as the only lookup key; descriptions are not identities.
        pass

    def get(self, name: str) -> ToolDefinition | None:
        # TODO 1: Return None for an unknown name. Do not raise here because the
        # Executor owns the structured UNKNOWN_TOOL policy.
        return None

    def schemas(self) -> list[dict[str, Any]]:
        # TODO 1: Expose each tool's name, description, and Pydantic-generated
        # JSON Schema under the "parameters" key.
        return []


class ToolExecutor:
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
        """Resolve a tool and validate arguments without invoking its handler."""
        # TODO 2:
        # 1. Resolve call.name through the Registry.
        # 2. Return UNKNOWN_TOOL with attempts=0 when it is absent.
        # 3. Validate call.arguments with definition.arguments_model.
        # 4. Return INVALID_ARGUMENTS with attempts=0 on ValidationError.
        #
        # Returning a ToolResult for failure and (definition, arguments) for
        # success keeps execute() explicit without using exceptions as control
        # flow.
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
        """Invoke a prepared call, validate output, and apply bounded retry."""
        # TODO 3:
        # - attempts_allowed is max_retries + 1.
        # - Call _invoke_with_timeout inside the attempt loop.
        # - Validate raw output with definition.result_model before success.
        # - TIMEOUT and retryable ToolExecutionError may retry.
        # - ValidationError and unexpected Exception are not retryable.
        # - Stop when failure is non-retryable or attempts are exhausted.
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
