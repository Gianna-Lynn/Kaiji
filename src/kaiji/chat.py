"""提供按会话隔离历史的终端聊天入口，并连接 OpenAI-compatible 模型服务。"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from typing import TextIO

from kaiji.demo import build_registry
from kaiji.models import Message, RunResult, StopReason
from kaiji.providers.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolExecutor


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEFAULT_MAX_TOKENS = 512
SYSTEM_PROMPT = (
    "你是 Kaiji 的中文对话助手。先理解用户的问题；需要时调用已提供的安全演示工具。"
    "只能根据用户提供的信息和成功的工具结果作答。工具失败或证据不足时要明确说明。"
    "本地搜索工具只查询演示用的小型本地知识库，不代表联网搜索或真实资料。"
)


class ChatSession:
    """为一个 session_id 保存已完成对话，并隔离其 Runtime 实例。"""

    def __init__(self, session_id: str, runtime: AgentRuntime) -> None:
        """创建会话；会话标识必须是非空字符串。"""

        self._validate_session_id(session_id)
        self._session_id = session_id
        self._runtime = runtime
        self._history: list[Message] = []

    @property
    def session_id(self) -> str:
        """返回创建会话时验证过的不可变标识。"""

        return self._session_id

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        """拒绝空值、纯空白和非字符串会话标识。"""

        if type(session_id) is not str or not session_id.strip():
            raise ValueError("session_id 必须是非空字符串")

    @property
    def history(self) -> list[Message]:
        """返回会话历史的深拷贝，避免外部修改已提交消息。"""

        return [message.model_copy(deep=True) for message in self._history]

    def turn(self, question: str) -> RunResult:
        """执行一轮；只有 completed 结果才追加到已提交历史。"""

        result = self._runtime.run(question, history=self.history)
        if result.stop_reason == StopReason.COMPLETED:
            self._history.extend(
                message.model_copy(deep=True) for message in result.messages
            )
        return result

    def reset(self) -> None:
        """清空当前会话历史，不影响其他会话。"""

        self._history.clear()


class ChatSessionManager:
    """按 session_id 惰性创建会话，并为每个会话调用一次 Runtime 工厂。"""

    def __init__(self, runtime_factory: Callable[[], AgentRuntime]) -> None:
        """创建会话管理器；工厂负责提供全新 Provider 和 Executor。"""

        self._runtime_factory = runtime_factory
        self._sessions: dict[str, ChatSession] = {}

    def get(self, session_id: str) -> ChatSession:
        """返回指定会话；首次访问时创建独立 Runtime。"""

        ChatSession._validate_session_id(session_id)
        session = self._sessions.get(session_id)
        if session is None:
            runtime = self._runtime_factory()
            if any(
                existing._runtime is runtime
                for existing in self._sessions.values()
            ):
                raise ValueError(
                    "runtime_factory 必须为每个 session_id 创建独立 Runtime"
                )
            session = ChatSession(session_id, runtime)
            self._sessions[session_id] = session
        return session

    def reset(self, session_id: str) -> None:
        """清空指定会话；尚不存在的会话保持未创建状态。"""

        ChatSession._validate_session_id(session_id)
        session = self._sessions.get(session_id)
        if session is not None:
            session.reset()


def _build_runtime_factory(
    config: OpenAICompatibleConfig,
) -> Callable[[], AgentRuntime]:
    """为每个会话创建独立 Provider、工具注册表与执行器。"""

    def create_runtime() -> AgentRuntime:
        registry = build_registry()
        provider = OpenAICompatibleProvider(config, registry)
        return AgentRuntime(provider, ToolExecutor(registry))

    return create_runtime


def _environment_variable_name(value: str) -> str:
    """只接受环境变量标识符，避免把凭据本身误作命令行参数。"""

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise argparse.ArgumentTypeError("必须提供环境变量名称")
    return value


def _failure_code(result: RunResult) -> str:
    """提取枚举代码用于提示，不向终端暴露 Provider 或工具原始消息。"""

    if result.stop_reason == StopReason.MODEL_FAILED and result.model_calls:
        failure = result.model_calls[-1].failure
        if failure is not None:
            return failure.code.value
    if result.stop_reason == StopReason.TOOL_FAILED:
        for message in reversed(result.messages):
            if message.tool_result is not None and message.tool_result.error is not None:
                return message.tool_result.error.code.value
    return result.stop_reason.value


def main(
    argv: list[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    runtime_factory: Callable[[], AgentRuntime] | None = None,
) -> int:
    """运行连续对话 CLI；支持退出、清空历史和结构化失败提示。"""

    parser = argparse.ArgumentParser(description="Kaiji DeepSeek 终端聊天")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="模型 API 基础地址")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="模型名称")
    parser.add_argument(
        "--api-key-env",
        type=_environment_variable_name,
        default=DEFAULT_API_KEY_ENV,
        help="保存 API Key 的环境变量名称",
    )
    parser.add_argument("--session-id", default="default", help="本地会话标识")
    args = parser.parse_args(argv)
    try:
        ChatSession._validate_session_id(args.session_id)
        config = OpenAICompatibleConfig(
            base_url=args.base_url,
            model=args.model,
            api_key_env=args.api_key_env,
            temperature=0,
            max_tokens=DEFAULT_MAX_TOKENS,
            system_prompt=SYSTEM_PROMPT,
            thinking="disabled",
        )
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))

    session_manager = ChatSessionManager(
        runtime_factory if runtime_factory is not None else _build_runtime_factory(config)
    )
    stdin = input_stream if input_stream is not None else sys.stdin
    stdout = output_stream if output_stream is not None else sys.stdout
    print("Kaiji 对话已就绪。输入 exit/quit 退出，输入 reset 清空本地历史。", file=stdout)
    session = session_manager.get(args.session_id)

    while True:
        print("你> ", end="", flush=True, file=stdout)
        line = stdin.readline()
        if line == "":
            print("\n会话结束。", file=stdout)
            return 0
        question = line.strip()
        command = question.casefold()
        if command in {"exit", "quit"}:
            print("会话结束。", file=stdout)
            return 0
        if command == "reset":
            session_manager.reset(args.session_id)
            print("本地对话历史已清空。", file=stdout)
            continue
        if not question:
            continue

        try:
            result = session.turn(question)
        except Exception:
            print(
                "Kaiji> 本轮未完成（internal_error）。已保留之前的对话。",
                file=stdout,
            )
            continue
        if result.stop_reason == StopReason.COMPLETED:
            print(f"Kaiji> {result.answer}", file=stdout)
        else:
            code = _failure_code(result)
            print(
                f"Kaiji> 本轮未完成（{result.stop_reason.value} / {code}）。"
                "已保留之前的对话。",
                file=stdout,
            )


if __name__ == "__main__":
    raise SystemExit(main())
