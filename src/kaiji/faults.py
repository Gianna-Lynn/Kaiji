"""提供按 case 隔离、可计数的工具层与 HTTP 层故障注入器。"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from time import sleep
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from kaiji.tools import ToolExecutionError


class ToolFaultStep(BaseModel):
    """描述工具 handler 一次尝试的确定性结果。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["success", "transient", "permanent", "invalid_result", "delay"]
    output: dict[str, Any] | None = None
    delay_seconds: float = Field(default=0.0, ge=0)


class ScriptedToolHandler:
    """逐次消费故障步骤，并暴露真实 handler 尝试次数。"""

    def __init__(self, steps: list[ToolFaultStep]) -> None:
        if not steps:
            raise ValueError("工具故障序列不能为空")
        self._steps = deque(deepcopy(steps))
        self.attempts = 0

    def __call__(self, arguments: BaseModel) -> dict[str, Any]:
        """执行下一故障步骤；序列耗尽属于测试配置错误。"""

        del arguments
        self.attempts += 1
        if not self._steps:
            raise AssertionError("工具故障序列已耗尽")
        step = self._steps.popleft()
        if step.kind == "transient":
            raise ToolExecutionError("注入的瞬时失败", retryable=True)
        if step.kind == "permanent":
            raise ToolExecutionError("注入的永久失败", retryable=False)
        if step.kind == "invalid_result":
            return step.output or {"schema_drift": True}
        if step.kind == "delay":
            sleep(step.delay_seconds)
        return deepcopy(step.output or {})


class HttpFaultStep(BaseModel):
    """描述一次 MockTransport 请求的响应或网络故障。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["json", "status", "network", "invalid_json"]
    status_code: int = Field(default=200, ge=100, le=599)
    payload: Any = None


class ScriptedHttpTransport:
    """为 Adapter 测试生成状态隔离的 ``httpx.MockTransport``。"""

    def __init__(self, steps: list[HttpFaultStep]) -> None:
        if not steps:
            raise ValueError("HTTP 故障序列不能为空")
        self._steps = deque(deepcopy(steps))
        self.attempts = 0
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        """消费一步并返回响应；网络故障保留原请求对象。"""

        self.attempts += 1
        if not self._steps:
            raise AssertionError("HTTP 故障序列已耗尽")
        step = self._steps.popleft()
        if step.kind == "network":
            raise httpx.ConnectError("注入的网络失败", request=request)
        if step.kind == "invalid_json":
            return httpx.Response(step.status_code, content=b"not-json")
        if step.kind == "status":
            return httpx.Response(step.status_code, json=step.payload)
        return httpx.Response(step.status_code, json=step.payload)
