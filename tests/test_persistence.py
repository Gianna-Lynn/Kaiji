"""严格验证 Checkpoint 完整快照与 Memory 精选事实的持久化契约。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from kaiji.persistence import (
    Checkpoint,
    JsonCheckpointStore,
    JsonMemoryStore,
    MemoryEntry,
)


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
        Checkpoint(
            thread_id="thread-1", checkpoint_id="cp-1", state={"step": 1}
        )
    )
    store.save(
        Checkpoint(
            thread_id="thread-1", checkpoint_id="cp-2", state={"step": 2}
        )
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
        Checkpoint(
            thread_id="thread-a", checkpoint_id="same-id", state={"owner": "a"}
        )
    )
    store.save(
        Checkpoint(
            thread_id="thread-b", checkpoint_id="same-id", state={"owner": "b"}
        )
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
    previous = Checkpoint(
        thread_id="thread-1", checkpoint_id="cp-1", state={"step": 1}
    )
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
        Checkpoint(
            thread_id="thread-1", checkpoint_id="cp-1", state={"step": 1}
        )
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
    store.put(
        "user-1", MemoryEntry(key="first", value="updated", tags=["new"])
    )

    entries = JsonMemoryStore(path).list("user-1")

    assert [entry.key for entry in entries] == ["first", "second"]
    assert entries[0] == MemoryEntry(
        key="first", value="updated", tags=["new"]
    )


def test_memory_users_are_isolated_even_when_keys_match(tmp_path) -> None:
    store = JsonMemoryStore(tmp_path / "memory.json")
    store.put("user-a", MemoryEntry(key="language", value="中文"))
    store.put("user-b", MemoryEntry(key="language", value="English"))

    assert store.get("user-a", "language") == MemoryEntry(
        key="language", value="中文"
    )
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
