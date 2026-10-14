from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama


class State(TypedDict):
    input: str
    draft: str
    output: str


llm = ChatOllama(model="qwen3.5:latest")


def node1_draft(state: State):
    response = llm.invoke(f"Answer this question:\n{state['input']}")
    return {"draft": response.content}


def node2_improve(state: State):
    response = llm.invoke(
        f"Improve this answer so it is clearer and shorter:\n{state['draft']}"
    )
    return {"output": response.content}


builder = StateGraph(State)

builder.add_node("draft", node1_draft)
builder.add_node("improve", node2_improve)

builder.set_entry_point("draft")
builder.add_edge("draft", "improve")
builder.add_edge("improve", END)

graph = builder.compile()

result = graph.invoke({"input": "Why do satellites stay in orbit?"})
print(result["output"])
