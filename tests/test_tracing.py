"""验证 Trace 的 JSONL 边界、唯一标识、读取顺序和稳定回放。"""

import json

import pytest
from pydantic import ValidationError

from kaiji.models import StopReason, TraceEvent
from kaiji.tracing import JsonlTraceStore, TraceRecord


def make_record(run_id: str, *, text: str = "证据") -> TraceRecord:
    """构造包含 Unicode 和换行内容的完整轨迹。"""

    return TraceRecord(
        run_id=run_id,
        events=[
            TraceEvent(
                step=1,
                event="model_action",
                data={"text": text, "kind": "tool_call"},
            ),
            TraceEvent(step=1, event="stop", data={"reason": "completed"}),
        ],
        stop_reason=StopReason.COMPLETED,
        metadata={"owner": "人"},
    )


def test_todo_10b_iter_isolated_from_load_append_and_replay(tmp_path) -> None:
    """TODO 10b 只解析物理行，并能在后续方法未实现时独立验收。"""

    first = make_record("first")
    second = make_record("second")
    path = tmp_path / "trace.jsonl"
    path.write_text(
        f"{first.model_dump_json()}\n\n{second.model_dump_json()}\n",
        encoding="utf-8",
    )

    assert JsonlTraceStore(path).iter_records() == [first, second]


def test_todo_10c_load_isolated_from_append_and_replay(tmp_path, monkeypatch) -> None:
    """TODO 10c 复用 10b 的记录列表，不接触文件追加或回放实现。"""

    store = JsonlTraceStore(tmp_path / "trace.jsonl")
    first = make_record("first")
    second = make_record("second")
    monkeypatch.setattr(store, "iter_records", lambda: [first, second])

    assert store.load("second") == second
    assert store.load("missing") is None


def test_todo_10d_append_isolated_from_replay(tmp_path, monkeypatch) -> None:
    """TODO 10d 只依赖已经完成的 10c 查重，不调用 10e。"""

    path = tmp_path / "nested" / "trace.jsonl"
    store = JsonlTraceStore(path)
    record = make_record("append-only")
    monkeypatch.setattr(store, "load", lambda run_id: None)

    store.append(record)

    assert path.read_text(encoding="utf-8") == record.model_dump_json() + "\n"


def test_todo_10e_replay_isolated_from_file_reader(tmp_path, monkeypatch) -> None:
    """TODO 10e 使用 10c 已加载对象，独立验证事件顺序与规范 JSON。"""

    store = JsonlTraceStore(tmp_path / "trace.jsonl")
    record = make_record("replay-only", text="中文")
    monkeypatch.setattr(store, "load", lambda run_id: record)

    assert store.replay_summary("replay-only") == [
        'step=1 event=model_action data={"kind":"tool_call","text":"中文"}',
        'step=1 event=stop data={"reason":"completed"}',
    ]


def test_append_writes_one_physical_line_and_preserves_order(tmp_path) -> None:
    store = JsonlTraceStore(tmp_path / "trace.jsonl")
    first = make_record("run-1", text="第一行\n仍属于同一 JSON 字符串")
    second = make_record("run-2", text="第二条轨迹")

    store.append(first)
    store.append(second)

    physical_lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(physical_lines) == 2
    assert [json.loads(line)["run_id"] for line in physical_lines] == [
        "run-1",
        "run-2",
    ]
    assert store.iter_records() == [first, second]
    assert store.load("run-1") == first


def test_append_creates_nested_parent_directories(tmp_path) -> None:
    path = tmp_path / "nested" / "trace" / "runs.jsonl"
    store = JsonlTraceStore(path)

    store.append(make_record("run-nested"))

    assert path.is_file()
    assert store.load("run-nested") == make_record("run-nested")


def test_append_separates_existing_valid_record_without_final_newline(tmp_path) -> None:
    """外部 JSONL 即使缺少末尾换行，追加后也不能把两个对象粘成一行。"""

    path = tmp_path / "trace.jsonl"
    first = make_record("run-existing")
    second = make_record("run-appended")
    path.write_text(first.model_dump_json(), encoding="utf-8")

    store = JsonlTraceStore(path)
    store.append(second)

    assert path.read_text(encoding="utf-8").splitlines() == [
        first.model_dump_json(),
        second.model_dump_json(),
    ]
    assert store.iter_records() == [first, second]


def test_missing_file_and_blank_lines_are_not_records(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    store = JsonlTraceStore(path)

    assert store.iter_records() == []
    assert store.load("missing") is None

    path.write_text("\n  \n", encoding="utf-8")
    assert store.iter_records() == []


def test_trace_record_rejects_empty_run_id() -> None:
    with pytest.raises(ValidationError):
        make_record("")


def test_trace_record_rejects_empty_event_timeline() -> None:
    with pytest.raises(ValidationError):
        TraceRecord(
            run_id="run-empty",
            events=[],
            stop_reason=StopReason.COMPLETED,
        )


def test_todo_10a_trace_record_rejects_timeline_without_stop() -> None:
    with pytest.raises(ValidationError, match="恰好包含一个 stop"):
        TraceRecord(
            run_id="run-no-stop",
            events=[TraceEvent(step=1, event="model_action", data={})],
            stop_reason=StopReason.COMPLETED,
        )


def test_trace_record_rejects_stop_that_is_not_last() -> None:
    with pytest.raises(ValidationError, match="stop 事件必须是.*最后一项"):
        TraceRecord(
            run_id="run-stop-not-last",
            events=[
                TraceEvent(step=1, event="stop", data={"reason": "completed"}),
                TraceEvent(step=2, event="model_action", data={}),
            ],
            stop_reason=StopReason.COMPLETED,
        )


def test_trace_record_rejects_multiple_stop_events() -> None:
    with pytest.raises(ValidationError, match="恰好包含一个 stop"):
        TraceRecord(
            run_id="run-multiple-stop",
            events=[
                TraceEvent(step=1, event="stop", data={"reason": "completed"}),
                TraceEvent(step=2, event="stop", data={"reason": "completed"}),
            ],
            stop_reason=StopReason.COMPLETED,
        )


@pytest.mark.parametrize(
    "stop_data",
    [
        {},
        {"reason": "not-a-stop-reason"},
    ],
)
def test_trace_record_rejects_missing_or_invalid_stop_reason(stop_data: dict) -> None:
    with pytest.raises(ValidationError, match="data.reason 必须是合法 StopReason"):
        TraceRecord(
            run_id="run-invalid-reason",
            events=[TraceEvent(step=1, event="stop", data=stop_data)],
            stop_reason=StopReason.COMPLETED,
        )


def test_trace_record_rejects_stop_reason_summary_mismatch() -> None:
    with pytest.raises(ValidationError, match="必须与 TraceRecord.stop_reason 一致"):
        TraceRecord(
            run_id="run-mismatch",
            events=[TraceEvent(step=1, event="stop", data={"reason": "tool_failed"})],
            stop_reason=StopReason.COMPLETED,
        )


def test_store_checks_structure_but_does_not_judge_business_answer(tmp_path) -> None:
    store = JsonlTraceStore(tmp_path / "trace.jsonl")
    structurally_valid = TraceRecord(
        run_id="run-business-not-judged",
        events=[
            TraceEvent(
                step=1,
                event="model_action",
                data={"kind": "final_answer", "final_answer": None},
            ),
            TraceEvent(step=1, event="stop", data={"reason": "completed"}),
        ],
        stop_reason=StopReason.COMPLETED,
        metadata={"task_success": False},
    )

    store.append(structurally_valid)

    assert store.load("run-business-not-judged") == structurally_valid


def test_non_empty_invalid_line_is_rejected_with_line_number(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text('{"run_id":"broken"}\n', encoding="utf-8")
    store = JsonlTraceStore(path)

    with pytest.raises(ValueError, match="第 1 行"):
        store.iter_records()

    path.write_text(
        f"\n{make_record('run-1').model_dump_json()}\n\nnot-json\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="第 4 行"):
        store.iter_records()


def test_duplicate_run_id_is_rejected_without_appending(tmp_path) -> None:
    store = JsonlTraceStore(tmp_path / "trace.jsonl")
    first = make_record("run-1", text="原始轨迹")
    store.append(first)

    with pytest.raises(ValueError, match="run_id 已存在"):
        store.append(make_record("run-1", text="不应覆盖"))

    assert store.iter_records() == [first]


def test_duplicate_run_id_already_in_file_is_rejected(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    line = make_record("run-1").model_dump_json()
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="第 2 行包含重复 run_id"):
        JsonlTraceStore(path).iter_records()


def test_replay_uses_event_order_and_stable_compact_json(tmp_path) -> None:
    store = JsonlTraceStore(tmp_path / "trace.jsonl")
    record = TraceRecord(
        run_id="run-stable",
        events=[
            TraceEvent(
                step=1,
                event="tool_result",
                data={"z": ["中文"], "a": {"y": 2, "x": 1}},
            ),
            TraceEvent(step=2, event="stop", data={"reason": "completed"}),
        ],
        stop_reason=StopReason.COMPLETED,
    )
    store.append(record)

    assert store.replay_summary("run-stable") == [
        'step=1 event=tool_result data={"a":{"x":1,"y":2},"z":["中文"]}',
        'step=2 event=stop data={"reason":"completed"}',
    ]


@pytest.mark.parametrize("step", [True, 0, -1, 1.5, "1"])
def test_trace_event_requires_strict_positive_integer_step(step) -> None:
    """审计序号不能接受 bool、宽松转换或非正数。"""

    with pytest.raises(ValidationError):
        TraceEvent(step=step, event="stop", data={"reason": "completed"})


def test_trace_record_rejects_decreasing_steps() -> None:
    """物理事件顺序不能与逻辑 step 时间线相互矛盾。"""

    with pytest.raises(ValidationError, match="非递减"):
        TraceRecord(
            run_id="run-backwards",
            events=[
                TraceEvent(step=2, event="model_action", data={}),
                TraceEvent(step=1, event="stop", data={"reason": "completed"}),
            ],
            stop_reason=StopReason.COMPLETED,
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), object()])
def test_trace_record_rejects_non_json_or_non_finite_payloads(bad) -> None:
    with pytest.raises(ValidationError, match="有限 JSON"):
        TraceRecord(
            run_id="run-invalid-json-value",
            events=[TraceEvent(step=1, event="stop", data={"reason": "completed"})],
            stop_reason=StopReason.COMPLETED,
            metadata={"bad": bad},
        )


def test_replay_rejects_unknown_run(tmp_path) -> None:
    store = JsonlTraceStore(tmp_path / "trace.jsonl")

    with pytest.raises(KeyError, match="unknown run_id"):
        store.replay_summary("missing")
