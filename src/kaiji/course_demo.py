from __future__ import annotations

from kaiji.adapters.azusa import MockAzusaAdapter
from kaiji.azusa_tools import build_azusa_registry
from kaiji.langchain_compare import build_search_runnable
from kaiji.langgraph_workflow import build_agent_graph, run_graph
from kaiji.models import ToolCall
from kaiji.tools import ToolExecutor


def main() -> None:
    adapter = MockAzusaAdapter()

    native = ToolExecutor(build_azusa_registry(adapter)).execute(
        ToolCall(
            call_id="course-native-1",
            name="search_azusa",
            arguments={"query": "Kaiji", "k": 2, "mode": "hybrid"},
        )
    )
    print("1. Native ToolExecutor")
    print(native.model_dump_json(indent=2))

    langchain_result = build_search_runnable(adapter).invoke(
        {"query": "Azusa", "k": 1, "mode": "hybrid"}
    )
    print("\n2. LangChain Tool + Runnable")
    print(langchain_result.model_dump_json(indent=2))

    graph = build_agent_graph(adapter)
    graph_result = run_graph(
        graph,
        "What is Kaiji responsible for?",
        thread_id="course-demo",
    )
    print("\n3. LangGraph State + Checkpoint")
    print(
        {
            "answer": graph_result["answer"],
            "steps": graph_result["step_count"],
            "stop_reason": graph_result["stop_reason"],
        }
    )


if __name__ == "__main__":
    main()
