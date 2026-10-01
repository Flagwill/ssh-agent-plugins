# Codex SSH plugin

这是一个可在 Windows 和 POSIX 系统上运行的 Codex MCP 插件，manifest 版本为 `0.3.0`。

入口：

- `.codex-plugin/plugin.json`
- `.mcp.json`
- `server.py`
- `board_ssh_exec.py`
- `run-server.cmd`

POSIX 系统的 Codex CLI 可通过 `codex mcp add ssh -- python3 /path/to/ssh-agent-plugins/codex/server.py` 注册本地服务；Windows 插件目录通过 `.mcp.json` 和 `run-server.cmd` 启动服务。

推荐在每次调用 ssh 工具时通过 password 字段传入密码，避免将密码保存在配置中。为兼容旧版，插件仍支持 `SSH_PASSWORD`、`BOARD_SSH_PASSWORD` 和本地 env 文件；主机、用户、端口等连接设置可通过 `SSH_*` 环境变量提供。不要提交真实凭据。

交互终端使用 `action=terminal`。首次调用时可传入 `host`、`user`、`password` 和可选 `command`，插件会返回 `session_id` 与终端输出。后续携带 `session_id`，使用 `input` 发送文本或控制字符，并通过 `wait_ms` 拉取新输出；`close=true` 发送 EOF。也可在输入中发送 `\u0003`（Ctrl-C）或 `\u0004`（Ctrl-D）。

Windows 交互终端通过 `ssh -tt` 分配远端 TTY，并在 MCP 会话间保持 SSH 进程；密码通过 Windows OpenSSH askpass 传递。POSIX 系统使用本地 PTY 桥接交互输入输出。

Windows 使用系统 OpenSSH 的 `ssh.exe` 和 `scp.exe`。推荐使用 Windows OpenSSH Agent 或
`SSH_IDENTITY_FILE`；旧版配置若设置 `SSH_PASSWORD`，插件会通过临时 askpass 环境传给 OpenSSH，
不会把密码放在命令行参数中。也可以在每次调用 ssh 工具时使用 password 字段传入密码；该值只用于本次 SSH askpass 流程，不会放入命令行参数；必要时 askpass 会通过 OpenSSH 子进程环境读取密码，工具返回文本会移除原密码字符串。MCP 的 writeOnly 标记只是提示，客户端仍可能展示或记录输入。Windows helper 仅在远程命令明确以 sudo -S 开头时，才将同一密码通过 SSH 标准输入转交给 sudo。

启动器支持以下 Python 来源，优先级从高到低为：`CODEX_CONDA_ENV`（Conda 环境名或完整路径）、
当前激活的 `CONDA_PREFIX`、`CODEX_PYTHON`、Codex 自带 Python、系统 `py`/`python`。
