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
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.arguments_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(self, call: ToolCall) -> ToolResult:
        definition = self._registry.get(call.name)
        if definition is None:
            return self._failure(
                call,
                ToolErrorCode.UNKNOWN_TOOL,
                f"unknown tool: {call.name}",
                retryable=False,
                attempts=0,
            )

        try:
            arguments = definition.arguments_model.model_validate(call.arguments)
        except ValidationError as exc:
            return self._failure(
                call,
                ToolErrorCode.INVALID_ARGUMENTS,
                str(exc),
                retryable=False,
                attempts=0,
            )

        attempts_allowed = definition.max_retries + 1
        for attempt in range(1, attempts_allowed + 1):
            try:
                raw_result = self._invoke_with_timeout(definition, arguments)
                result = definition.result_model.model_validate(raw_result)
                return ToolResult(
                    call_id=call.call_id,
                    tool_name=call.name,
                    ok=True,
                    attempts=attempt,
                    output=result.model_dump(mode="json"),
                )
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
            except Exception as exc:  # A tool must not crash the runtime.
                failure = ToolFailure(
                    code=ToolErrorCode.EXECUTION_FAILED,
                    message=f"{type(exc).__name__}: {exc}",
                    retryable=False,
                )

            if not failure.retryable or attempt == attempts_allowed:
                return ToolResult(
                    call_id=call.call_id,
                    tool_name=call.name,
                    ok=False,
                    attempts=attempt,
                    error=failure,
                )

        raise AssertionError("attempt loop must return")

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
