# broken_agent.py
# Intentionally BROKEN LangGraph agent wired to a real MCP server (mcp_server.py).
#
# Broken behaviors (by design):
# 1) Tool errors crash the run (no try/except around MCP tool call)
# 2) Ambiguous prompt crashes in planner (IndexError)
# 3) Destructive actions run immediately (no HITL approval gate)

import asyncio
from typing import TypedDict, Optional, Any, Dict

from langgraph.graph import StateGraph, END

# MCP client imports (works with common MCP Python SDK layouts)
from mcp.client.stdio import stdio_client
from mcp import ClientSession, StdioServerParameters


class AgentState(TypedDict):
    user_input: str
    plan: Optional[Dict[str, Any]]
    tool_result: Optional[Any]
    final_answer: Optional[str]


def naive_planner(state: AgentState) -> AgentState:
    """
    Intentionally naive planner:
    - Brittle parsing
    - Assumes user always provides required args
    - Produces destructive plans without approval gates
    """
    text = state["user_input"].strip().lower()

    if text.startswith("read "):
        path = state["user_input"].split(" ", 1)[1].strip()
        state["plan"] = {"tool": "read_file", "args": {"path": path}}
        return state

    if text.startswith("delete "):
        path = state["user_input"].split(" ", 1)[1].strip()
        state["plan"] = {"tool": "delete_file", "args": {"path": path}}
        return state

    if text.startswith("get "):
        key = state["user_input"].split(" ", 1)[1].strip()
        state["plan"] = {"tool": "get_record", "args": {"key": key}}
        return state

    # update <key> <field>=<value>
    # Example: update user:123 plan=free
    if text.startswith("update "):
        parts = state["user_input"].split(" ", 2)
        key = parts[1].strip()
        kv = parts[2].strip()            # IndexError if missing third token
        field, value = kv.split("=", 1)  # ValueError if missing '='
        state["plan"] = {"tool": "update_record", "args": {"key": key, "patch": {field: value}}}
        return state

    # default: search for whatever user typed
    state["plan"] = {"tool": "search_files", "args": {"query": state["user_input"]}}
    return state


async def call_tool_via_mcp(state: AgentState) -> AgentState:
    """
    Intentionally broken tool caller:
    - No try/except
    - If MCP tool errors, the exception bubbles up and crashes the graph
    """
    plan = state["plan"]
    tool_name = plan["tool"]
    args = plan["args"]

    server_params = StdioServerParameters(
        command="python3",
        args=["mcp_server.py"],
    )

    # Start MCP server as a subprocess and call the tool over stdio
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args)

    # Note: result is whatever MCP returns; we keep it raw for demo.
    state["tool_result"] = result
    return state


def finalize(state: AgentState) -> AgentState:
    state["final_answer"] = f"Result: {state.get('tool_result')}"
    return state


def build_broken_graph():
    g = StateGraph(AgentState)

    g.add_node("plan", naive_planner)
    g.add_node("call_tool", call_tool_via_mcp)
    g.add_node("finalize", finalize)

    # BROKEN ROUTING: plan -> call_tool -> finalize
    # No ambiguity handling, no HITL gate, no recovery loop.
    g.set_entry_point("plan")
    g.add_edge("plan", "call_tool")
    g.add_edge("call_tool", "finalize")
    g.add_edge("finalize", END)

    return g.compile()


if __name__ == "__main__":
    app = build_broken_graph()

    print("Type one of the demo inputs:")
    print("  1) read /configs/missing.yaml")
    print("  2) update user:123")
    print("  3) delete /configs/app.yaml")
    print("Type 'exit' to quit.")

    while True:
        user_input = input("\nYou> ").strip()
        if user_input in {"exit", "quit"}:
            break

        init_state: AgentState = {
            "user_input": user_input,
            "plan": None,
            "tool_result": None,
            "final_answer": None,
        }

        # Because call_tool_via_mcp is async, use ainvoke
        out = asyncio.run(app.ainvoke(init_state))  # may crash (intentionally)
        print("Agent>", out.get("final_answer"))
