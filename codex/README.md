# Codex SSH plugin

这是一个可在 Windows 上运行的 Codex MCP 插件，manifest 版本为 `0.2.0`。

入口：

- `.codex-plugin/plugin.json`
- `.mcp.json`
- `server.py`
- `board_ssh_exec.py`
- `run-server.cmd`

推荐在每次调用 ssh 工具时通过 password 字段传入密码，避免将密码保存在配置中。为兼容旧版，插件仍支持 `SSH_PASSWORD`、`BOARD_SSH_PASSWORD` 和本地 env 文件；主机、用户、端口等连接设置可通过 `SSH_*` 环境变量提供。不要提交真实凭据。

Windows 使用系统 OpenSSH 的 `ssh.exe` 和 `scp.exe`。推荐使用 Windows OpenSSH Agent 或
`SSH_IDENTITY_FILE`；旧版配置若设置 `SSH_PASSWORD`，插件会通过临时 askpass 环境传给 OpenSSH，
不会把密码放在命令行参数中。也可以在每次调用 ssh 工具时使用 password 字段传入密码；该值只用于本次 SSH askpass 流程，不会放入命令行参数；必要时 askpass 会通过 OpenSSH 子进程环境读取密码，工具返回文本会移除原密码字符串。MCP 的 writeOnly 标记只是提示，客户端仍可能展示或记录输入。Windows helper 仅在远程命令明确以 sudo -S 开头时，才将同一密码通过 SSH 标准输入转交给 sudo。

启动器支持以下 Python 来源，优先级从高到低为：`CODEX_CONDA_ENV`（Conda 环境名或完整路径）、
当前激活的 `CONDA_PREFIX`、`CODEX_PYTHON`、Codex 自带 Python、系统 `py`/`python`。

