# SSH agent plugins

本仓库存放本机当前整理出的 SSH 插件实现，按客户端分目录：

- `codex/`：Codex MCP 插件，版本 `0.1.0`。
- `opencode/`：OpenCode 原生插件实现，配套本机 OpenCode `v2.0.9`。
- `openclaw/`：本机盘点时未发现 OpenClaw 可执行文件、配置目录或 SSH 插件源码，因此当前仅保留目录说明，不包含伪造实现。

两个已归档实现都支持：

- SSH 远程执行命令（`run`）
- SCP 上传文件（`put`）
- SCP 下载文件（`get`）
- 每次调用覆盖 host、user、port、私钥路径和 env 文件

## 安全说明

仓库只包含源码、示例配置和客户端适配器，不包含私钥、密码、真实主机、token 或本地运行状态。请通过环境变量或本地未跟踪的 env 文件提供连接信息；不要把真实凭据写进 Git。

## 版本盘点

- Codex CLI：`0.149.0`
- OpenCode：`v2.0.9`
- Codex SSH 插件 manifest：`0.1.0`
- OpenClaw：本机未安装/未发现，因此无可归档版本

## 目录说明

### Codex（Windows）

把 `codex/` 作为 Codex 插件目录使用。Windows 版本通过 `.mcp.json` 调用
`run-server.cmd`，启动器会优先使用 Codex 自带的 Python 运行时，也支持通过
`CODEX_PYTHON` 指定 Python 3 路径。

也可以设置 `CODEX_CONDA_ENV` 为 Conda 环境名或环境目录；启动器会用该环境中的 Python
运行 MCP 服务。

配置可参考 `codex/board.env.example`，或设置 `SSH_HOST`、`SSH_USER`、`SSH_PORT`、`SSH_PASSWORD`、`SSH_IDENTITY_FILE` 等环境变量。

Windows 使用系统 OpenSSH 的 `ssh.exe` 和 `scp.exe`；推荐使用 Windows OpenSSH Agent
或私钥认证。

### OpenCode

把 `opencode/` 放入 OpenCode 的插件目录。`index.ts` 注册 `ssh` 工具，并调用同目录的 `ssh_exec.py`。

## License

Codex 插件 manifest 声明为 MIT；各客户端目录中的源码保留其原始元数据。

