# Codex SSH plugin

这是一个可在 Windows 上运行的 Codex MCP 插件，manifest 版本为 `0.1.0`。

入口：

- `.codex-plugin/plugin.json`
- `.mcp.json`
- `server.py`
- `board_ssh_exec.py`
- `run-server.cmd`

连接配置支持 `SSH_*` 环境变量；`BOARD_SSH_*` 变量作为旧版兼容别名保留。请复制 `board.env.example` 为本地未跟踪的 env 文件，或使用环境变量，不要提交真实凭据。

Windows 使用系统 OpenSSH 的 `ssh.exe` 和 `scp.exe`。推荐使用 Windows OpenSSH Agent 或
`SSH_IDENTITY_FILE`；如果设置 `SSH_PASSWORD`，插件会通过临时 askpass 环境传给 OpenSSH，
不会把密码放在命令行参数中。

启动器支持以下 Python 来源，优先级从高到低为：`CODEX_CONDA_ENV`（Conda 环境名或完整路径）、
当前激活的 `CONDA_PREFIX`、`CODEX_PYTHON`、Codex 自带 Python、系统 `py`/`python`。

