# SSH agent plugins

本仓库存放本机当前整理出的 SSH 插件实现，按客户端分目录：

- `codex/`：Codex MCP 插件，版本 `0.3.0`，兼容 Windows OpenSSH。
- `opencode/`：OpenCode 原生插件实现，配套本机 OpenCode `v2.0.9`。
- `openclaw/`：本机盘点时未发现 OpenClaw 可执行文件、配置目录或 SSH 插件源码，因此当前仅保留目录说明，不包含伪造实现。

Codex 插件版本为 `0.3.0`，OpenCode 插件也支持每次调用单独传入 SSH 密码。密码字段在 Codex MCP schema 中标记为 writeOnly，但客户端是否隐藏或从日志中排除该输入取决于客户端实现。

两个已归档实现都支持：

- SSH 远程执行命令（`run`）
- SCP 上传文件（`put`）
- SCP 下载文件（`get`）
- 持久交互式 SSH 终端（`terminal`，通过 `session_id` 续接）
- 每次调用覆盖 host、user、port、私钥路径和 env 文件

## 安全说明

仓库只包含源码、示例配置和客户端适配器，不包含私钥、密码、真实主机、token 或本地运行状态。新调用建议通过客户端 SSH 工具的 `password` 字段按次传入密码；MCP writeOnly 标记不保证客户端一定隐藏或不记录该输入。旧版环境变量和未跟踪 env 文件仍为兼容方式，不要把真实凭据写进 Git。

## 版本盘点

- Codex CLI：`0.149.0`
- OpenCode：`v2.0.9`
- Codex SSH 插件 manifest：`0.3.0`
- OpenClaw：本机未安装/未发现，因此无可归档版本

## 目录说明

### Codex / Windows

把 `codex/` 作为 Codex 插件目录使用。Windows 版本通过 `.mcp.json` 调用
`run-server.cmd`，启动器会优先使用 Codex 自带的 Python 运行时，也支持通过
`CODEX_PYTHON` 指定 Python 3 路径。

也可以设置 `CODEX_CONDA_ENV` 为 Conda 环境名或环境目录；启动器会用该环境中的 Python
运行 MCP 服务。

每次调用建议直接传入 password 字段。交互终端首次调用 `action=terminal` 会返回 `session_id`；后续调用携带该 ID，并用 `input` 收发数据、`wait_ms` 等待输出、`close=true` 发送 EOF。旧版兼容配置仍支持 `SSH_PASSWORD`、`BOARD_SSH_PASSWORD` 和本地 env 文件；连接主机等设置可用 `SSH_HOST`、`SSH_USER`、`SSH_PORT`、`SSH_IDENTITY_FILE` 环境变量。

Windows 使用系统 OpenSSH 的 `ssh.exe` 和 `scp.exe`；推荐使用 Windows OpenSSH Agent
或私钥认证。

### OpenCode

把 `opencode/` 放入 OpenCode 的插件目录。`index.ts` 注册 `ssh` 工具，并调用同目录的 `ssh_exec.py`。支持按次传入 `password`，并通过 `action=terminal` 和返回的 `session_id` 保持交互式 shell。

## License

Codex 插件 manifest 声明为 MIT；各客户端目录中的源码保留其原始元数据。
