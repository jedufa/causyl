from typing import TypedDict

from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph


DEFAULT_MODEL = "qwen3.5:latest"
DEFAULT_PROMPT = "Reply with exactly: hello"


class State(TypedDict):
    input: str
    output: str


def build_llm(model_name: str = DEFAULT_MODEL) -> ChatOllama:
    return ChatOllama(model=model_name, temperature=0)


def call_model(state: State) -> dict[str, str]:
    print("Calling model...")
    response = build_llm().invoke(state["input"])
    print("Model returned.")
    return {"output": str(response.content).strip()}


def build_graph():
    builder = StateGraph(State)
    builder.add_node("llm", call_model)
    builder.set_entry_point("llm")
    builder.add_edge("llm", END)
    return builder.compile()


def main() -> None:
    graph = build_graph()
    result = graph.invoke({"input": DEFAULT_PROMPT})
    print(result["output"])


if __name__ == "__main__":
    main()
