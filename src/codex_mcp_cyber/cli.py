"""codex-mcp-cyber 控制台入口。

由 pyproject.toml 中的 `[project.scripts]` 映射为 `codex-mcp-cyber` 命令。
"""

from codex_mcp_cyber.server import run


def main() -> None:
    """启动 codex-mcp-cyber MCP 服务器。"""
    run()


if __name__ == "__main__":
    main()
