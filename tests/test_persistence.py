from kaiji.persistence import (
    Checkpoint,
    JsonCheckpointStore,
    JsonMemoryStore,
    MemoryEntry,
)


def test_checkpoint_saves_full_state_and_restores_latest(tmp_path) -> None:
    store = JsonCheckpointStore(tmp_path / "checkpoints.json")
    store.save(
        Checkpoint(
            thread_id="thread-1",
            checkpoint_id="cp-1",
            state={"step": 1, "messages": ["hello"]},
        )
    )
    store.save(
        Checkpoint(
            thread_id="thread-1",
            checkpoint_id="cp-2",
            state={"step": 2, "messages": ["hello", "tool"]},
        )
    )

    latest = store.latest("thread-1")

    assert latest is not None
    assert latest.checkpoint_id == "cp-2"
    assert latest.state["step"] == 2


def test_memory_persists_selected_fact_not_execution_state(tmp_path) -> None:
    store = JsonMemoryStore(tmp_path / "memory.json")
    store.put(
        "user-1",
        MemoryEntry(key="preferred_mode", value="hybrid", tags=["retrieval"]),
    )

    restored = JsonMemoryStore(tmp_path / "memory.json").get(
        "user-1", "preferred_mode"
    )

    assert restored is not None
    assert restored.value == "hybrid"
