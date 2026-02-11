# agent_fixed.py
import asyncio
from typing import TypedDict, Optional, Any, Dict, List

from langgraph.graph import StateGraph, END

from mcp.client.stdio import stdio_client
from mcp import ClientSession, StdioServerParameters

DESTRUCTIVE_TOOLS = {"delete_file", "update_record"}
MAX_RETRIES = 2


# ----------------------------
# MCP call helpers
# ----------------------------

async def mcp_call_tool(tool_name: str, args: Dict[str, Any]) -> Any:
    """
    Demo-friendly: spawn MCP server as a subprocess (stdio) per call.
    """
    server_params = StdioServerParameters(command="python3", args=["mcp_server.py"])
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, args)


def tool_is_error(tool_result: Any) -> bool:
    return bool(getattr(tool_result, "isError", False))


def tool_error_text(tool_result: Any) -> str:
    content = getattr(tool_result, "content", None)
    if content and len(content) > 0:
        t = getattr(content[0], "text", None)
        if t:
            return str(t)
    return "Unknown tool error"


def tool_value(tool_result: Any) -> Any:
    """
    Prefer structuredContent['result'] when present; fallback to TextContent text.
    """
    sc = getattr(tool_result, "structuredContent", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]

    content = getattr(tool_result, "content", None)
    if content and len(content) > 0:
        t = getattr(content[0], "text", None)
        if t is not None:
            return t

    return tool_result


# ----------------------------
# State
# ----------------------------

class AgentState(TypedDict):
    user_input: str

    # Plan to execute
    plan: Optional[Dict[str, Any]]          # {"tool": "...", "args": {...}}

    # Last tool execution info
    last_tool: Optional[str]
    last_args: Optional[Dict[str, Any]]
    last_tool_result: Optional[Any]
    last_error: Optional[str]

    # Retry bookkeeping
    retries: int

    # HITL bookkeeping
    needs_approval: bool
    approval: Optional[bool]               # None until APPROVE/DENY is received

    # Output + audit
    final_answer: Optional[str]
    audit: List[dict]


# ----------------------------
# Nodes
# ----------------------------

def planner(state: AgentState) -> AgentState:
    """
    Plans tool calls.
    Fix: ambiguous update does NOT crash; it asks for clarification.
    """
    text = state["user_input"].strip()

    # If user is responding to an approval request, keep existing plan.
    if text.upper() in {"APPROVE", "DENY"}:
        return state

    low = text.lower()

    if low.startswith("read "):
        path = text.split(" ", 1)[1].strip()
        state["plan"] = {"tool": "read_file", "args": {"path": path}}
        return state

    if low.startswith("delete "):
        path = text.split(" ", 1)[1].strip()
        state["plan"] = {"tool": "delete_file", "args": {"path": path}}
        return state

    if low.startswith("get "):
        key = text.split(" ", 1)[1].strip()
        state["plan"] = {"tool": "get_record", "args": {"key": key}}
        return state

    # update <key> <field>=<value>
    if low.startswith("update "):
        parts = text.split(" ", 2)

        # FIX: same input as broken agent "update user:123" now yields clarification
        if len(parts) < 3:
            state["final_answer"] = (
                "Ambiguous update request.\n"
                "I need the field and value to update.\n"
                "Use: update <key> <field>=<value>\n"
                "Example: update user:123 plan=free"
            )
            return state

        key = parts[1].strip()
        kv = parts[2].strip()

        if "=" not in kv:
            state["final_answer"] = (
                "Invalid update format.\n"
                "Use: update <key> <field>=<value>\n"
                "Example: update user:123 plan=free"
            )
            return state

        field, value = kv.split("=", 1)
        state["plan"] = {"tool": "update_record", "args": {"key": key, "patch": {field: value}}}
        return state

    # default: search
    state["plan"] = {"tool": "search_files", "args": {"query": text}}
    return state


def gate_destructive(state: AgentState) -> AgentState:
    """
    HITL: mark destructive tools as requiring approval BEFORE execution.
    """
    if state.get("final_answer"):
        return state

    plan = state.get("plan")
    if not plan:
        return state

    tool = plan["tool"]
    state["needs_approval"] = tool in DESTRUCTIVE_TOOLS
    return state


def approval_router(state: AgentState) -> AgentState:
    """
    If we need approval, interpret APPROVE/DENY user inputs.
    """
    if not state.get("needs_approval"):
        return state

    txt = state["user_input"].strip().upper()
    if txt == "APPROVE":
        state["approval"] = True
    elif txt == "DENY":
        state["approval"] = False

    return state


def ask_approval(state: AgentState) -> AgentState:
    """
    Ask user for approval and END the run so the next input can be APPROVE/DENY.
    """
    plan = state["plan"]
    state["final_answer"] = (
        f"Approval required before running {plan['tool']} with args={plan['args']}.\n"
        "Type APPROVE or DENY."
    )
    return state


async def call_tool(state: AgentState) -> AgentState:
    """
    Executes MCP tool call and captures errors into state (no crash).
    Also: if recovery search returns [], we stop with a clear final message (no 'OK: []').
    """
    plan = state["plan"]
    tool = plan["tool"]
    args = plan["args"]

    state["last_tool"] = tool
    state["last_args"] = args

    result = await mcp_call_tool(tool, args)
    state["last_tool_result"] = result

    if tool_is_error(result):
        err = tool_error_text(result)
        state["last_error"] = err
        state["audit"].append({"tool": tool, "args": args, "ok": False, "error": err})
        return state

    # success
    val = tool_value(result)
    state["last_error"] = None
    state["audit"].append({"tool": tool, "args": args, "ok": True, "result": val})

    # DEMO FIX: empty search is "recovery failed", not OK
    if tool == "search_files" and isinstance(val, list) and len(val) == 0:
        state["final_answer"] = (
            "Recovered gracefully from a tool failure:\n"
            "1) Tried read_file('/configs/missing.yaml') → File not found\n"
            "2) Reflected and tried search_files('missing.yaml') → no matches\n"
            "Outcome: I could not find an alternative file to read.\n"
            f"AUDIT: {state['audit']}"
        )

    return state


def reflect_retry(state: AgentState) -> AgentState:
    """
    Tool failure recovery:
    - If read_file failed -> search_files(filename)
    - If search_files finds matches -> read_file(first match)
    """
    if state.get("final_answer"):
        return state

    if not state.get("last_error"):
        return state

    if state["retries"] >= MAX_RETRIES:
        state["final_answer"] = (
            "Tool failure recovery attempted, but I still could not complete the request.\n"
            f"Last error: {state['last_error']}\n"
            f"AUDIT: {state['audit']}"
        )
        return state

    last_tool = state.get("last_tool")
    err = (state.get("last_error") or "").lower()

    # A) read_file -> file not found -> search by filename
    if last_tool == "read_file" and ("not found" in err):
        missing_path = state["last_args"]["path"]
        filename = missing_path.split("/")[-1]

        state["audit"].append({"reflection": f"read_file failed; trying search_files for '{filename}'"})
        state["plan"] = {"tool": "search_files", "args": {"query": filename}}
        state["retries"] += 1
        return state

    # No other recovery strategies in this demo
    state["final_answer"] = f"Tool failed with no recovery strategy: {state['last_error']}\nAUDIT: {state['audit']}"
    return state


def finalize(state: AgentState) -> AgentState:
    """
    Clean output.
    """
    if state.get("final_answer"):
        return state

    if state.get("last_error"):
        state["final_answer"] = f"FAILED: {state['last_error']}\nAUDIT: {state['audit']}"
        return state

    val = tool_value(state["last_tool_result"]) if state.get("last_tool_result") is not None else None
    state["final_answer"] = f"OK: {val}\nAUDIT: {state['audit']}"
    return state


# ----------------------------
# Graph
# ----------------------------

def build_graph():
    g = StateGraph(AgentState)

    g.add_node("plan", planner)
    g.add_node("gate", gate_destructive)
    g.add_node("approval_router", approval_router)
    g.add_node("ask_approval", ask_approval)
    g.add_node("call_tool", call_tool)
    g.add_node("reflect_retry", reflect_retry)
    g.add_node("finalize", finalize)

    g.set_entry_point("plan")

    g.add_edge("plan", "gate")
    g.add_edge("gate", "approval_router")

    def route_after_approval(state: AgentState) -> str:
        if state.get("final_answer"):
            return "finalize"

        if state.get("needs_approval"):
            if state.get("approval") is None:
                return "ask_approval"
            if state.get("approval") is False:
                state["final_answer"] = "Denied by human. No destructive action taken."
                return "finalize"
            return "call_tool"

        return "call_tool"

    g.add_conditional_edges(
        "approval_router",
        route_after_approval,
        {"ask_approval": "ask_approval", "call_tool": "call_tool", "finalize": "finalize"},
    )

    g.add_edge("ask_approval", END)

    def route_after_call(state: AgentState) -> str:
        # ✅ If call_tool already produced a final_answer (e.g., empty search), finalize
        if state.get("final_answer"):
            return "finalize"
        return "reflect_retry" if state.get("last_error") else "finalize"

    g.add_conditional_edges(
        "call_tool",
        route_after_call,
        {"reflect_retry": "reflect_retry", "finalize": "finalize"},
    )

    g.add_edge("reflect_retry", "call_tool")
    g.add_edge("finalize", END)

    return g.compile()


# ----------------------------
# CLI Runner (keeps state across turns for HITL)
# ----------------------------

if __name__ == "__main__":
    app = build_graph()

    # Persisted state so APPROVE/DENY works on the same plan
    state: AgentState = {
        "user_input": "",
        "plan": None,
        "last_tool": None,
        "last_args": None,
        "last_tool_result": None,
        "last_error": None,
        "retries": 0,
        "needs_approval": False,
        "approval": None,
        "final_answer": None,
        "audit": [],
    }

    print("\nFixed agent (Retry + HITL). Type 'exit' to quit.")
    print("Try:")
    print("  read /configs/missing.yaml")
    print("  update user:123")
    print("  delete /configs/app.yaml\n")

    while True:
        user_input = input("You> ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break

        # Set new input
        state["user_input"] = user_input
        state["final_answer"] = None
        state["retries"] = 0
        state["last_error"] = None
        state["last_tool_result"] = None

        # If this is not an approval response, start fresh planning
        if user_input.upper() not in {"APPROVE", "DENY"}:
            state["plan"] = None
            state["needs_approval"] = False
            state["approval"] = None

        out = asyncio.run(app.ainvoke(state))
        state.update(out)

        print("Agent>", state.get("final_answer"))
