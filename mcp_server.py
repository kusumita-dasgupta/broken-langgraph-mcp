# mcp_server.py
# MCP server exposing a mock filesystem + mock DB over stdio.
# Run standalone (optional): python3 mcp_server.py
# In our demo, broken_agent.py will spawn this server automatically.

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mock-fs-db")

# Mock in-memory filesystem + DB
FILES = {
    "/configs/app.yaml": "feature_flag: false\nowner: team-a\n",
    "/docs/readme.md": "Welcome!\n",
}

DB = {
    "user:123": {"status": "active", "plan": "pro"},
    "order:999": {"state": "shipped"},
}

@mcp.tool()
def read_file(path: str) -> str:
    if path not in FILES:
        raise FileNotFoundError(f"File not found: {path}")
    return FILES[path]

@mcp.tool()
def search_files(query: str) -> list[str]:
    q = query.lower()
    return [p for p in FILES.keys() if q in p.lower()]

@mcp.tool()
def delete_file(path: str) -> str:
    # Destructive action (HITL should block this later)
    if path not in FILES:
        raise FileNotFoundError(f"File not found: {path}")
    del FILES[path]
    return "deleted"

@mcp.tool()
def get_record(key: str) -> dict:
    if key not in DB:
        raise KeyError(f"Record not found: {key}")
    return DB[key]

@mcp.tool()
def update_record(key: str, patch: dict) -> dict:
    # Destructive action (HITL should block this later)
    if key not in DB:
        raise KeyError(f"Record not found: {key}")
    DB[key].update(patch)
    return DB[key]

if __name__ == "__main__":
    # Uses stdio transport by default.
    mcp.run()
