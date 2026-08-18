"""严格验证 Checkpoint 完整快照与 Memory 精选事实的持久化契约。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import kaiji.persistence as persistence_module
from kaiji.persistence import (
    Checkpoint,
    JsonCheckpointStore,
    JsonMemoryStore,
    MemoryEntry,
)


def test_todo_8b_save_isolated_from_load_and_latest(tmp_path, monkeypatch) -> None:
    """TODO 8b 只依赖 8a 读取结果与原子写 helper，不触发后续恢复方法。"""

    store = JsonCheckpointStore(tmp_path / "checkpoints.json")
    data = {"thread": {"old": {"step": 1}, "keep": {"step": 2}}}
    writes = []
    monkeypatch.setattr(store, "_read_all", lambda: data)
    monkeypatch.setattr(
        persistence_module,
        "_write_json_atomic",
        lambda path, payload: writes.append((path, payload)),
    )

    store.save(Checkpoint(thread_id="thread", checkpoint_id="old", state={"step": 3}))

    assert list(data["thread"]) == ["keep", "old"]
    assert data["thread"]["old"] == {"step": 3}
    assert writes == [(tmp_path / "checkpoints.json", data)]


def test_todo_8c_load_isolated_from_save_and_latest(tmp_path, monkeypatch) -> None:
    """TODO 8c 使用已由 8a 校验的数据，可独立验证命中与未命中语义。"""

    store = JsonCheckpointStore(tmp_path / "checkpoints.json")
    monkeypatch.setattr(
        store,
        "_read_all",
        lambda: {"thread": {"cp": {"step": 2}}},
    )

    assert store.load("thread", "cp") == Checkpoint(
        thread_id="thread",
        checkpoint_id="cp",
        state={"step": 2},
    )
    assert store.load("thread", "missing") is None


def test_todo_8d_latest_isolated_from_save_and_load(tmp_path, monkeypatch) -> None:
    """TODO 8d 直接读取 8a 的有序结果，不借用尚未实现的 load。"""

    store = JsonCheckpointStore(tmp_path / "checkpoints.json")
    monkeypatch.setattr(
        store,
        "_read_all",
        lambda: {"thread": {"first": {"step": 1}, "last": {"step": 2}}},
    )

    assert store.latest("thread") == Checkpoint(
        thread_id="thread",
        checkpoint_id="last",
        state={"step": 2},
    )
    assert store.latest("missing") is None


def test_todo_9b_put_isolated_from_get_and_list(tmp_path, monkeypatch) -> None:
    """TODO 9b 只使用 9a 结果与原子写 helper，并保留覆盖键的位置。"""

    store = JsonMemoryStore(tmp_path / "memory.json")
    data = {
        "user": {
            "known": {"key": "known", "value": "old", "tags": []},
            "keep": {"key": "keep", "value": "fact", "tags": []},
        }
    }
    writes = []
    monkeypatch.setattr(store, "_read_all", lambda: data)
    monkeypatch.setattr(
        persistence_module,
        "_write_json_atomic",
        lambda path, payload: writes.append((path, payload)),
    )

    store.put("user", MemoryEntry(key="known", value="new", tags=["updated"]))

    assert list(data["user"]) == ["known", "keep"]
    assert data["user"]["known"]["value"] == "new"
    assert writes == [(tmp_path / "memory.json", data)]


def test_todo_9c_get_isolated_from_put_and_list(tmp_path, monkeypatch) -> None:
    """TODO 9c 在 9a 的已校验字典上独立恢复单条事实。"""

    store = JsonMemoryStore(tmp_path / "memory.json")
    monkeypatch.setattr(
        store,
        "_read_all",
        lambda: {
            "user": {"known": {"key": "known", "value": "fact", "tags": ["stable"]}}
        },
    )

    assert store.get("user", "known") == MemoryEntry(
        key="known",
        value="fact",
        tags=["stable"],
    )
    assert store.get("user", "missing") is None


def test_todo_9d_list_isolated_from_put_and_get(tmp_path, monkeypatch) -> None:
    """TODO 9d 直接按 9a 的记录顺序构造列表，不调用 get。"""

    store = JsonMemoryStore(tmp_path / "memory.json")
    monkeypatch.setattr(
        store,
        "_read_all",
        lambda: {
            "user": {
                "first": {"key": "first", "value": "1", "tags": []},
                "second": {"key": "second", "value": "2", "tags": []},
            }
        },
    )

    assert [entry.key for entry in store.list("user")] == ["first", "second"]
    assert store.list("missing") == []


def test_todo_8a_checkpoint_read_boundary_is_independently_verifiable(tmp_path) -> None:
    """TODO 8a 可在尚未实现 save/load/latest 时独立验收。"""

    path = tmp_path / "checkpoints.json"
    store = JsonCheckpointStore(path)
    assert store._read_all() == {}

    valid = {"thread-1": {"cp-1": {"step": 1}}}
    path.write_text(json.dumps(valid), encoding="utf-8")
    assert store._read_all() == valid

    for corrupt in (
        {"thread-1": None},
        {"thread-1": {"cp-1": None}},
    ):
        path.write_text(json.dumps(corrupt), encoding="utf-8")
        with pytest.raises(ValueError):
            store._read_all()


def test_checkpoint_save_load_round_trips_full_nested_state(tmp_path) -> None:
    path = tmp_path / "checkpoints.json"
    state = {
        "step": 2,
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": '{"hits": []}'},
        ],
        "route": None,
        "flags": {"approved": False},
    }
    JsonCheckpointStore(path).save(
        Checkpoint(thread_id="thread-1", checkpoint_id="cp-1", state=state)
    )

    restored = JsonCheckpointStore(path).load("thread-1", "cp-1")

    assert restored == Checkpoint(
        thread_id="thread-1", checkpoint_id="cp-1", state=state
    )


def test_checkpoint_latest_means_last_successful_save_even_after_overwrite(
    tmp_path,
) -> None:
    path = tmp_path / "checkpoints.json"
    store = JsonCheckpointStore(path)
    store.save(
        Checkpoint(thread_id="thread-1", checkpoint_id="cp-1", state={"step": 1})
    )
    store.save(
        Checkpoint(thread_id="thread-1", checkpoint_id="cp-2", state={"step": 2})
    )
    store.save(
        Checkpoint(
            thread_id="thread-1",
            checkpoint_id="cp-1",
            state={"step": 3, "overwritten": True},
        )
    )

    latest = JsonCheckpointStore(path).latest("thread-1")

    assert latest == Checkpoint(
        thread_id="thread-1",
        checkpoint_id="cp-1",
        state={"step": 3, "overwritten": True},
    )
    assert store.load("thread-1", "cp-2") == Checkpoint(
        thread_id="thread-1", checkpoint_id="cp-2", state={"step": 2}
    )


def test_checkpoint_threads_are_isolated(tmp_path) -> None:
    store = JsonCheckpointStore(tmp_path / "checkpoints.json")
    store.save(
        Checkpoint(thread_id="thread-a", checkpoint_id="same-id", state={"owner": "a"})
    )
    store.save(
        Checkpoint(thread_id="thread-b", checkpoint_id="same-id", state={"owner": "b"})
    )

    assert store.load("thread-a", "same-id") == Checkpoint(
        thread_id="thread-a", checkpoint_id="same-id", state={"owner": "a"}
    )
    assert store.load("thread-b", "same-id") == Checkpoint(
        thread_id="thread-b", checkpoint_id="same-id", state={"owner": "b"}
    )
    assert store.latest("thread-a") is not None
    assert store.latest("thread-a").state == {"owner": "a"}


def test_checkpoint_failed_save_does_not_replace_latest_snapshot(tmp_path) -> None:
    store = JsonCheckpointStore(tmp_path / "checkpoints.json")
    previous = Checkpoint(thread_id="thread-1", checkpoint_id="cp-1", state={"step": 1})
    store.save(previous)

    with pytest.raises(TypeError):
        store.save(
            Checkpoint(
                thread_id="thread-1",
                checkpoint_id="cp-broken",
                state={"not_json_serializable": object()},
            )
        )

    assert store.latest("thread-1") == previous
    assert store.load("thread-1", "cp-broken") is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_checkpoint_rejects_non_finite_json_without_replacing_file(
    tmp_path, bad
) -> None:
    path = tmp_path / "checkpoints.json"
    store = JsonCheckpointStore(path)
    previous = Checkpoint(thread_id="thread-1", checkpoint_id="cp-1", state={"step": 1})
    store.save(previous)

    with pytest.raises(ValueError):
        store.save(
            Checkpoint(
                thread_id="thread-1",
                checkpoint_id="cp-bad",
                state={"value": bad},
            )
        )

    assert store.latest("thread-1") == previous


@pytest.mark.parametrize("field", ["thread_id", "checkpoint_id"])
@pytest.mark.parametrize("bad", ["", "   "])
def test_checkpoint_rejects_empty_identifiers(field, bad) -> None:
    values = {"thread_id": "thread", "checkpoint_id": "cp", "state": {}}
    values[field] = bad
    with pytest.raises(ValidationError):
        Checkpoint.model_validate(values)


@pytest.mark.parametrize(
    "payload",
    [
        '{"thread": {"cp": {"value": NaN}}}',
        '{"thread": {"cp": {"value": Infinity}}}',
        '{"thread": {"cp": {"value": -Infinity}}}',
        '{"   ": {"cp": {}}}',
        '{"thread": {"   ": {}}}',
    ],
)
def test_checkpoint_read_boundary_rejects_nonstandard_json_and_blank_keys(
    tmp_path,
    payload,
) -> None:
    path = tmp_path / "checkpoints.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        JsonCheckpointStore(path)._read_all()


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("load", (" ", "cp")),
        ("load", ("thread", " ")),
        ("load", (None, "cp")),
        ("load", ("thread", 7)),
        ("load", (True, "cp")),
        ("latest", (" ",)),
        ("latest", (None,)),
        ("latest", (1,)),
    ],
)
def test_checkpoint_lookup_rejects_blank_identifiers(tmp_path, method, args) -> None:
    store = JsonCheckpointStore(tmp_path / "checkpoints.json")
    with pytest.raises(ValueError):
        getattr(store, method)(*args)


def test_checkpoint_replace_failure_preserves_previous_file_and_latest(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "checkpoints.json"
    store = JsonCheckpointStore(path)
    previous = Checkpoint(
        thread_id="thread-1",
        checkpoint_id="cp-1",
        state={"step": 1},
    )
    store.save(previous)
    replace_calls: list[tuple[Path, Path]] = []

    def fail_replace(temporary: Path, target: Path) -> None:
        replace_calls.append((temporary, target))
        assert temporary.parent == target.parent == path.parent
        assert temporary.name == "checkpoints.json.tmp"
        assert temporary.exists()
        assert json.loads(temporary.read_text(encoding="utf-8")) == {
            "thread-1": {
                "cp-1": {"step": 1},
                "cp-2": {"step": 2},
            }
        }
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "thread-1": {"cp-1": {"step": 1}}
        }
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        store.save(
            Checkpoint(
                thread_id="thread-1",
                checkpoint_id="cp-2",
                state={"step": 2},
            )
        )

    assert replace_calls == [(path.with_suffix(".json.tmp"), path)]
    assert store.latest("thread-1") == previous
    assert store.load("thread-1", "cp-2") is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"thread-1": []},
        {"thread-1": {"cp-1": None}},
        {"thread-1": {"cp-1": "not-an-object"}},
    ],
)
def test_checkpoint_corrupt_outer_or_state_is_never_missing(
    tmp_path,
    payload: object,
) -> None:
    path = tmp_path / "checkpoints.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = JsonCheckpointStore(path)

    with pytest.raises(ValueError):
        store.load("thread-1", "cp-1")
    with pytest.raises(ValueError):
        store.latest("thread-1")
    original = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        store.save(
            Checkpoint(
                thread_id="thread-new",
                checkpoint_id="cp-new",
                state={"step": 1},
            )
        )
    assert path.read_text(encoding="utf-8") == original


def test_checkpoint_invalid_json_is_not_treated_as_empty_store(tmp_path) -> None:
    path = tmp_path / "checkpoints.json"
    path.write_text("{", encoding="utf-8")
    store = JsonCheckpointStore(path)

    with pytest.raises(json.JSONDecodeError):
        store.load("thread-1", "cp-1")
    with pytest.raises(json.JSONDecodeError):
        store.latest("thread-1")
    with pytest.raises(json.JSONDecodeError):
        store.save(
            Checkpoint(
                thread_id="thread-new",
                checkpoint_id="cp-new",
                state={"step": 1},
            )
        )
    assert path.read_text(encoding="utf-8") == "{"


def test_checkpoint_missing_file_thread_or_id_returns_none(tmp_path) -> None:
    store = JsonCheckpointStore(tmp_path / "missing" / "checkpoints.json")

    assert store.load("unknown-thread", "unknown-id") is None
    assert store.latest("unknown-thread") is None

    store.save(
        Checkpoint(thread_id="thread-1", checkpoint_id="cp-1", state={"step": 1})
    )

    assert store.load("thread-1", "unknown-id") is None
    assert store.load("unknown-thread", "cp-1") is None
    assert store.latest("unknown-thread") is None


def test_memory_put_get_round_trips_value_and_tags_across_store_instances(
    tmp_path,
) -> None:
    path = tmp_path / "memory.json"
    entry = MemoryEntry(
        key="preferred_mode",
        value="hybrid",
        tags=["retrieval", "preference"],
    )
    JsonMemoryStore(path).put("user-1", entry)

    restored = JsonMemoryStore(path).get("user-1", "preferred_mode")

    assert restored == entry


def test_todo_9a_memory_read_boundary_is_independently_verifiable(tmp_path) -> None:
    """TODO 9a 可在尚未实现 put/get/list 时独立验收。"""

    path = tmp_path / "memory.json"
    store = JsonMemoryStore(path)
    assert store._read_all() == {}

    valid = {
        "user-1": {
            "preferred_mode": {
                "key": "preferred_mode",
                "value": "hybrid",
                "tags": ["preference"],
            }
        }
    }
    path.write_text(json.dumps(valid), encoding="utf-8")
    assert store._read_all() == valid

    for corrupt in (
        {"user-1": None},
        {"user-1": {"preferred_mode": None}},
        {
            "user-1": {
                "outer-key": {
                    "key": "inner-key",
                    "value": "hybrid",
                    "tags": [],
                }
            }
        },
    ):
        path.write_text(json.dumps(corrupt), encoding="utf-8")
        with pytest.raises(ValueError):
            store._read_all()


def test_memory_replace_failure_preserves_previous_file(
    tmp_path,
    monkeypatch,
) -> None:
    """Memory 也必须通过同目录临时文件原子替换，失败时保留旧事实。"""

    path = tmp_path / "memory.json"
    store = JsonMemoryStore(path)
    previous = MemoryEntry(key="first", value="old", tags=["stable"])
    store.put("user-1", previous)
    replace_calls: list[tuple[Path, Path]] = []

    def fail_replace(temporary: Path, target: Path) -> None:
        replace_calls.append((temporary, target))
        assert temporary.parent == target.parent == path.parent
        assert temporary.name == "memory.json.tmp"
        assert json.loads(temporary.read_text(encoding="utf-8")) == {
            "user-1": {
                "first": {
                    "key": "first",
                    "value": "old",
                    "tags": ["stable"],
                },
                "second": {"key": "second", "value": "new", "tags": []},
            }
        }
        assert JsonMemoryStore(path).get("user-1", "first") == previous
        raise OSError("simulated memory replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated memory replace failure"):
        store.put("user-1", MemoryEntry(key="second", value="new"))

    assert replace_calls == [(path.with_suffix(".json.tmp"), path)]
    assert JsonMemoryStore(path).get("user-1", "first") == previous
    assert JsonMemoryStore(path).get("user-1", "second") is None


def test_memory_overwrite_keeps_first_insertion_order(tmp_path) -> None:
    path = tmp_path / "memory.json"
    store = JsonMemoryStore(path)
    store.put("user-1", MemoryEntry(key="first", value="v1", tags=["old"]))
    store.put("user-1", MemoryEntry(key="second", value="v2"))
    store.put("user-1", MemoryEntry(key="first", value="updated", tags=["new"]))

    entries = JsonMemoryStore(path).list("user-1")

    assert [entry.key for entry in entries] == ["first", "second"]
    assert entries[0] == MemoryEntry(key="first", value="updated", tags=["new"])


def test_memory_users_are_isolated_even_when_keys_match(tmp_path) -> None:
    store = JsonMemoryStore(tmp_path / "memory.json")
    store.put("user-a", MemoryEntry(key="language", value="中文"))
    store.put("user-b", MemoryEntry(key="language", value="English"))

    assert store.get("user-a", "language") == MemoryEntry(key="language", value="中文")
    assert store.get("user-b", "language") == MemoryEntry(
        key="language", value="English"
    )
    assert [entry.value for entry in store.list("user-a")] == ["中文"]
    assert [entry.value for entry in store.list("user-b")] == ["English"]


def test_memory_missing_file_user_or_key_has_explicit_empty_result(tmp_path) -> None:
    store = JsonMemoryStore(tmp_path / "missing" / "memory.json")

    assert store.get("unknown-user", "unknown-key") is None
    assert store.list("unknown-user") == []

    store.put("user-1", MemoryEntry(key="known", value="fact"))

    assert store.get("user-1", "unknown-key") is None
    assert store.get("unknown-user", "known") is None
    assert store.list("unknown-user") == []


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (None, ValidationError),
        ({}, ValidationError),
        ({"key": "different", "value": "fact", "tags": []}, ValueError),
        ({"key": "known", "value": 7, "tags": []}, ValidationError),
        ({"key": "known", "value": "fact", "tags": "tag"}, ValidationError),
    ],
)
def test_memory_corrupt_record_is_not_disguised_as_missing(
    tmp_path,
    payload: object,
    expected_error: type[Exception],
) -> None:
    """存在但非法的持久化记录必须报错，不能被 ``get`` 当成未命中。"""

    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps({"user-1": {"known": payload}}, ensure_ascii=False),
        encoding="utf-8",
    )
    store = JsonMemoryStore(path)

    with pytest.raises(expected_error):
        store.get("user-1", "known")
    with pytest.raises(expected_error):
        store.list("user-1")
    original = path.read_text(encoding="utf-8")
    with pytest.raises(expected_error):
        store.put("user-2", MemoryEntry(key="new", value="fact"))
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("payload", [[], {"user-1": []}])
def test_memory_corrupt_outer_container_is_rejected(
    tmp_path,
    payload: object,
) -> None:
    path = tmp_path / "memory.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = JsonMemoryStore(path)

    with pytest.raises(ValueError):
        store.get("user-1", "known")
    with pytest.raises(ValueError):
        store.list("user-1")
    original = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        store.put("user-2", MemoryEntry(key="new", value="fact"))
    assert path.read_text(encoding="utf-8") == original


def test_memory_invalid_json_is_not_treated_as_empty_store(tmp_path) -> None:
    path = tmp_path / "memory.json"
    path.write_text("{", encoding="utf-8")
    store = JsonMemoryStore(path)

    with pytest.raises(json.JSONDecodeError):
        store.get("user-1", "known")
    with pytest.raises(json.JSONDecodeError):
        store.list("user-1")
    with pytest.raises(json.JSONDecodeError):
        store.put("user-2", MemoryEntry(key="new", value="fact"))
    assert path.read_text(encoding="utf-8") == "{"


@pytest.mark.parametrize("user_id", ["", "   ", None, 7])
def test_memory_rejects_invalid_user_identity(tmp_path, user_id) -> None:
    store = JsonMemoryStore(tmp_path / "memory.json")
    with pytest.raises(ValueError, match="user_id"):
        store.put(user_id, MemoryEntry(key="known", value="fact"))
    with pytest.raises(ValueError, match="user_id"):
        store.get(user_id, "known")
    with pytest.raises(ValueError, match="user_id"):
        store.list(user_id)


def test_memory_rejects_extra_persisted_fields(tmp_path) -> None:
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            {
                "user-1": {
                    "known": {
                        "key": "known",
                        "value": "fact",
                        "tags": [],
                        "unexpected": "would previously be dropped",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        JsonMemoryStore(path).get("user-1", "known")


@pytest.mark.parametrize(
    "payload",
    [
        '{"user": {"known": {"key": "known", "value": "x", "tags": [], "score": NaN}}}',
        '{"   ": {}}',
        '{"user": {"   ": {"key": "   ", "value": "x", "tags": []}}}',
    ],
)
def test_memory_read_boundary_rejects_nonstandard_json_and_blank_keys(
    tmp_path,
    payload,
) -> None:
    path = tmp_path / "memory.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        JsonMemoryStore(path)._read_all()


@pytest.mark.parametrize("bad", ["", "   "])
def test_memory_entry_rejects_blank_key(bad) -> None:
    with pytest.raises(ValidationError):
        MemoryEntry(key=bad, value="fact")
