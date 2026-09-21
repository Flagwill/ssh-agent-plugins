# Codex SSH plugin

这是一个 Codex MCP 插件，manifest 版本为 `0.1.0`。

入口：

- `.codex-plugin/plugin.json`
- `.mcp.json`
- `server.py`
- `board_ssh_exec.py`

连接配置支持 `SSH_*` 环境变量；`BOARD_SSH_*` 变量作为旧版兼容别名保留。请复制 `board.env.example` 为本地未跟踪的 env 文件，或使用环境变量，不要提交真实凭据。

