"""Bridge between Agent and the tools package.

Avoids circular imports by lazily importing tools when needed.
"""


def get_all_tools(workspace: str):
    from core.tools import create_tools
    return create_tools(workspace)


def get_tool(name: str, tools):
    for t in tools:
        if t.name == name:
            return t
    return None
