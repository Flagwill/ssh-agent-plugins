# OpenClaw 接入 homelab MCP（部署记录）

> OpenClaw 本体**不在本机**：它只部署在 asus（`flagw@asus`，Ubuntu 24.04 华硕笔记本），
> 以 gateway 形式常驻 `127.0.0.1:18789`。本机（flagw 开发机）没有 OpenClaw 可执行文件，
> 因此本目录不包含运行在本机的插件源码，只记录 **asus 上 OpenClaw 接入 homelab MCP 的配方、
> 架构事实与踩坑**。完整背景见根目录 `AGENTS.md` §3.1。

## 事实表（2026-10-12 盘点）

| 项 | 值 |
|---|---|
| 版本 | `OpenClaw 2026.10.1 (e178c39)` |
| 安装方式 | npm 全局：`~/.npm-global/lib/node_modules/openclaw`（node v24.21.0） |
| 运行形态 | `openclaw gateway --port 18789`，用户 flagw，仅监听 127.0.0.1 |
| 配置 | `/home/flagw/.openclaw/openclaw.json`（MCP 入口为 `mcp.servers`） |
| MCP 管理命令 | `openclaw mcp add / list / show / probe / reload / doctor / status / tools` |
| 接入的 MCP | `homelab`（stdio → SSH → `mcp@ali`），agent 身份 `openclaw` |
| asus 专用密钥 | `/home/flagw/.ssh/homelab_openclaw`（ed25519，comment `homelab-mcp-openclaw@asus`） |
| ali 侧注册 | 公钥 append 至 ali `/opt/homelab/.ssh/authorized_keys`（mcp 用户家目录是 `/opt/homelab`） |

## 为什么没有「插件源码」

OpenClaw 的 MCP 客户端配置与 Codex/Claude 同形（stdio command + args + env），
不需要 harness 侧适配器；homelab 的 MCP 服务端（ali）也是现成的。所以部署 = 生成密钥 +
`openclaw mcp add`，没有新代码。这与 codex/、opencode/ 两个目录（本机 harness 的适配器）性质不同。

## 部署配方（asus 上以 flagw 执行）

```bash
# 1) 专用客户端密钥（幂等）
test -f ~/.ssh/homelab_openclaw || \
  ssh-keygen -t ed25519 -f ~/.ssh/homelab_openclaw -N '' -C 'homelab-mcp-openclaw@asus'

# 2) 公钥注册到 ali —— 必须走运维直达 ssh，MCP 通道对 authorized_keys 是硬拒绝
ssh -i ~/.ssh/homelab_agent mcp@flagw.apay.eu.cc \
  "grep -qF \"$(cat ~/.ssh/homelab_openclaw.pub)\" /opt/homelab/.ssh/authorized_keys \
   || cat ~/.ssh/homelab_openclaw.pub >> /opt/homelab/.ssh/authorized_keys"

# 3) 注册 MCP（保存前自动 probe）
openclaw mcp add homelab --command ssh \
  --arg -i --arg /home/flagw/.ssh/homelab_openclaw \
  --arg -o --arg IdentitiesOnly=yes --arg -o --arg BatchMode=yes \
  --arg -o --arg StrictHostKeyChecking=accept-new --arg -o --arg ConnectTimeout=10 \
  --arg -o --arg ServerAliveInterval=30 --arg -o --arg ServerAliveCountMax=3 \
  --arg mcp@flagw.apay.eu.cc \
  --arg "exec /usr/local/bin/homelab-mcpd --agent openclaw" \
  --env HOMELAB_AGENT=openclaw --timeout 60 --connect-timeout 20

# 4) 验收 + 生效
openclaw mcp probe homelab   # 期望: 16 tools
openclaw mcp reload          # gateway 下一轮构建 runtime 时生效，无需重启 gateway
```

## 验收记录（2026-10-12）

- `openclaw mcp probe homelab` → `16 tools, Codex approval auto`
- `openclaw mcp status` → `homelab: stdio`
- ali 侧 homelab `agents` 表新增 `openclaw`（enabled，last_ip=183.192.11.16 即 asus 出口）
- audit 可见 `session_start ... stdio session from 183.192.11.16`
- asus→ali 走公网直连 `mcp@flagw.apay.eu.cc:22`，不经过 frps / ssh -R 隧道

## 踩坑

1. **openclaw CLI 每次调用启动约 20s**：`mcp add/probe/reload` 都要留足超时。
2. **`openclaw mcp add` 会先 probe 再保存**：密钥没注册好时直接失败，不会写坏配置。
3. **`mcp reload` 只清缓存的 MCP runtime**，不重启 gateway；下次 agent turn 自动用新配置。
4. **MCP 工具通道（run_read/run_user）拒绝以 `-i` 开头的参数**（防交互 flag）——
   在 asus 上做 `grep -i` 会被判交互参数，用 `bash -lc "..."` 包一层即可。
5. **MCP 工具通道对 `authorized_keys` 硬拒绝**（tools.py / execd.py 双侧）：
   换密钥/加密钥只能走运维直达 ssh（本机 `~/.ssh/homelab_agent` → `mcp@ali`，mcp 写自己的文件，无需 root）。
6. `openclaw.json` 内含 openclaw 自己的 frp 隧道配置（`~/.openclaw/secrets/ssh-frp/id_ed25519`），
   与 homelab 的 `homelab_openclaw` 是两把 key，别混。

## 待办

- [ ] 在 openclaw 里跑一次真实 agent turn，验证工具调用/审批闭环（probe 与 reload 已过）
- [ ] 若 asus 重装/换机：按上方配方重来，记得同步 ali `authorized_keys` 清理旧 key
