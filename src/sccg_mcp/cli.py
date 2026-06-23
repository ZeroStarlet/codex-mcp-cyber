"""SCCG-MCP 服务器的控制台入口模块。

本文件由 pyproject.toml 中的 `[project.scripts]` 段映射为命令行命令
`sccg-mcp`，提供启动 MCP 服务器的最小入口函数。

模块说明：
- 仅承担"加载并启动服务器"的职责，不在此处做任何业务逻辑；
- 真正的服务器初始化、工具注册、I/O 循环逻辑全部位于 sccg_mcp.server.run。

边界条件：
- 当作为命令行命令执行（即由 console_scripts 调度）时，会触发 main()；
- 当通过 `python -m sccg_mcp.cli` 直接调用本模块时，也会进入 main()。

副作用：
- 启动 MCP 服务器后会一直占用当前进程，直到上游/下游连接关闭或收到中断。
"""

from sccg_mcp.server import run


def main() -> None:
    """启动 SCCG-MCP 服务器。

    设计契约：本函数无入参、无返回值，调用后会阻塞在 MCP 服务循环中。
    任何启动期间的异常都不会被本函数捕获，将原样向上抛出，便于上层
    （例如 console_scripts 包装器或 IDE 调试器）观察真实失败原因。
    """
    run()


if __name__ == "__main__":
    # 直接执行 `python src/sccg_mcp/cli.py` 时进入此分支；console_scripts
    # 入口同样会调用 main()，两种入口共用一份启动逻辑。
    main()
