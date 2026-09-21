# OpenCode SSH plugin

这是 OpenCode 原生 TypeScript 插件实现，配套本机 OpenCode `v2.0.9`。

- `index.ts` 注册 `ssh` 工具。
- `ssh_exec.py` 执行 SSH/SCP 操作。

支持 `action=run|put|get`，并可通过参数或 `SSH_*` 环境变量提供连接信息。私钥路径只作为参数传入，不应把私钥复制到仓库。

