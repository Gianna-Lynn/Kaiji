"""验证会话历史隔离、回合提交边界和终端交互。"""

from __future__ import annotations

from io import StringIO

import pytest

from kaiji.chat import ChatSessionManager, main
from kaiji.demo import build_registry
from kaiji.models import (
    Message,
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ProviderErrorCode,
    ProviderFailure,
    StopReason,
    ToolCall,
)
from kaiji.providers.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolExecutor


class RecordingProvider:
    """依次返回预设动作或失败，并保留每次收到的完整上下文副本。"""

    def __init__(self, outcomes: list[ModelAction | ModelCallResult]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[list[Message]] = []

    def decide(self, messages: list[Message]) -> ModelCallResult:
        self.calls.append([message.model_copy(deep=True) for message in messages])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, ModelCallResult):
            return outcome
        return ModelCallResult(
            action=outcome,
            metadata=ModelCallMetadata(model="scripted", latency_ms=0),
        )


def _runtime(provider: RecordingProvider) -> AgentRuntime:
    return AgentRuntime(provider, ToolExecutor(build_registry()))


def _tool_action(call_id: str) -> ModelAction:
    return ModelAction(
        kind="tool_call",
        tool_call=ToolCall(
            call_id=call_id,
            name="calculate_discount",
            arguments={"price": 100, "percent": 10},
        ),
    )


def _model_failure() -> ModelCallResult:
    return ModelCallResult(
        failure=ProviderFailure(
            code=ProviderErrorCode.RATE_LIMITED,
            message="不可输出的敏感服务内容",
            retryable=True,
        ),
        metadata=ModelCallMetadata(model="scripted", latency_ms=0),
    )


def test_second_turn_sees_first_user_and_assistant_messages() -> None:
    provider = RecordingProvider(
        [
            ModelAction(kind="final_answer", final_answer="第一轮回答"),
            ModelAction(kind="final_answer", final_answer="第二轮回答"),
        ]
    )
    session = ChatSessionManager(lambda: _runtime(provider)).get("alice")

    first = session.turn("第一轮问题")
    second = session.turn("第二轮问题")

    assert first.stop_reason == second.stop_reason == StopReason.COMPLETED
    assert [(message.role, message.content) for message in provider.calls[1]] == [
        ("user", "第一轮问题"),
        ("assistant", "第一轮回答"),
        ("user", "第二轮问题"),
    ]
    assert [message.content for message in session.history] == [
        "第一轮问题",
        "第一轮回答",
        "第二轮问题",
        "第二轮回答",
    ]


def test_tool_history_remains_paired_and_same_signature_is_allowed_next_turn() -> None:
    provider = RecordingProvider(
        [
            _tool_action("first-call"),
            ModelAction(kind="final_answer", final_answer="第一轮折后价为 90。"),
            _tool_action("second-call"),
            ModelAction(kind="final_answer", final_answer="第二轮折后价仍为 90。"),
        ]
    )
    session = ChatSessionManager(lambda: _runtime(provider)).get("repeatable")

    first = session.turn("计算 100 打九折")
    second = session.turn("再算一次 100 打九折")

    assert first.stop_reason == second.stop_reason == StopReason.COMPLETED
    assert len(first.successful_tool_results) == len(second.successful_tool_results) == 1
    second_context = provider.calls[2]
    converter = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.invalid/v1",
            model="test-model",
            api_key_env="MISSING_TEST_KEY",
        ),
        build_registry(),
    )
    converted = converter._convert_messages(second_context)
    assert [message["role"] for message in converted] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert converted[1]["tool_calls"][0]["id"] == "first-call"
    assert converted[2]["tool_call_id"] == "first-call"


def test_sessions_have_independent_runtime_and_history() -> None:
    providers: list[RecordingProvider] = []

    def create_runtime() -> AgentRuntime:
        next_index = len(providers) + 1
        provider = RecordingProvider(
            [ModelAction(kind="final_answer", final_answer=f"回答 {next_index}")]
        )
        providers.append(provider)
        return _runtime(provider)

    manager = ChatSessionManager(create_runtime)
    alice = manager.get("alice")
    bob = manager.get("bob")

    assert manager.get("alice") is alice
    assert alice.turn("Alice 的问题").answer == "回答 1"
    assert bob.turn("Bob 的问题").answer == "回答 2"
    assert len(providers) == 2
    assert [message.content for message in providers[1].calls[0]] == ["Bob 的问题"]
    assert [message.content for message in bob.history] == ["Bob 的问题", "回答 2"]
    assert [message.content for message in alice.history] == ["Alice 的问题", "回答 1"]


def test_manager_rejects_runtime_factory_that_reuses_an_instance() -> None:
    shared_runtime = _runtime(RecordingProvider([]))
    manager = ChatSessionManager(lambda: shared_runtime)
    manager.get("first")

    with pytest.raises(ValueError, match="独立 Runtime"):
        manager.get("second")


def test_failed_turn_does_not_commit_and_reset_clears_history() -> None:
    provider = RecordingProvider(
        [
            ModelAction(kind="final_answer", final_answer="已完成"),
            _model_failure(),
            ModelAction(kind="final_answer", final_answer="清空后回答"),
        ]
    )
    manager = ChatSessionManager(lambda: _runtime(provider))
    session = manager.get("transient")

    session.turn("第一问")
    committed_history = session.history
    failed = session.turn("失败问题")
    assert failed.stop_reason == StopReason.MODEL_FAILED
    assert session.history == committed_history

    manager.reset("transient")
    assert session.history == []
    session.turn("清空后的问题")
    assert [message.content for message in provider.calls[2]] == ["清空后的问题"]
    assert [message.content for message in session.history] == [
        "清空后的问题",
        "清空后回答",
    ]


@pytest.mark.parametrize("session_id", ["", " \t", None, 7])
def test_session_id_must_be_a_nonempty_string(session_id: object) -> None:
    manager = ChatSessionManager(lambda: _runtime(RecordingProvider([])))

    with pytest.raises(ValueError, match="session_id"):
        manager.get(session_id)


def test_history_property_returns_a_copy() -> None:
    provider = RecordingProvider([ModelAction(kind="final_answer", final_answer="答复")])
    session = ChatSessionManager(lambda: _runtime(provider)).get("copy")
    session.turn("问题")

    external_history = session.history
    external_history[0].content = "外部改写"

    assert session.history[0].content == "问题"


def test_cli_exit_and_eof_are_normal_terminations() -> None:
    created = 0

    def runtime_factory() -> AgentRuntime:
        nonlocal created
        created += 1
        return _runtime(RecordingProvider([]))

    exit_output = StringIO()
    assert main(
        [],
        input_stream=StringIO("exit\n"),
        output_stream=exit_output,
        runtime_factory=runtime_factory,
    ) == 0
    assert "会话结束" in exit_output.getvalue()

    eof_output = StringIO()
    assert main(
        [],
        input_stream=StringIO(""),
        output_stream=eof_output,
        runtime_factory=runtime_factory,
    ) == 0
    assert "会话结束" in eof_output.getvalue()
    assert created == 2


def test_cli_reset_and_structured_failure_do_not_print_raw_error() -> None:
    provider = RecordingProvider([_model_failure()])
    output = StringIO()

    assert main(
        ["--session-id", "cli-session"],
        input_stream=StringIO("问题\nreset\nexit\n"),
        output_stream=output,
        runtime_factory=lambda: _runtime(provider),
    ) == 0

    rendered = output.getvalue()
    assert "本地对话历史已清空" in rendered
    assert "model_failed / rate_limited" in rendered
    assert "不可输出的敏感服务内容" not in rendered
    assert "Traceback" not in rendered


def test_cli_rejects_api_key_value_instead_of_environment_variable_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    possible_key = "sk-test-secret-value"

    with pytest.raises(SystemExit):
        main(["--api-key-env", possible_key])

    assert possible_key not in capsys.readouterr().err
