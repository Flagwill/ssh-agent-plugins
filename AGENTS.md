# AGENTS.md — 仓库记忆（给后来的 agent/维护者）

> 本文件是 `ssh-agent-plugins` 仓库的**操作记忆**：架构、关键路径、部署 SOP、踩过的坑。
> 改任何东西前先读这里；做完重大变更后更新这里。

## 1. 仓库定位

- 本机（flagw 的开发机，非四台服务器之一）agent harness 的 **SSH 能力插件集合**。
- 当前主线是 `homelab/`：部署在 **ali** 上的「四机统一 MCP 服务 + PWA 审批监控台」，
  给 pi / codex / opencode 共用同一个 MCP（`homelab`）。
- `codex/`、`opencode/`、`openclaw/` 是早期按客户端分的通用 SSH 插件（按次传密码），
  仍可用；homelab 是有审批/审计的正式通道，优先用它。
- 分支：`feat/homelab-fleet-mcp`（本文件随功能演进，提交前更新）。

## 2. 四台服务器事实表

| 代号 | SSH 入口 | 系统 | 角色 |
|---|---|---|---|
| ali | `admin@flagw.apay.eu.cc:22`（密码登录，保留） | Ubuntu 24.04 阿里云 ECS，8.130.131.119 | **中枢**：homelab 全套 + frps + Caddy + ss-server |
| raspi | `flagw@flagw.apay.eu.cc:10003`（ali frps 隧道） | Ubuntu 24.04 aarch64 树莓派 | Sentinel D435i 摄像头监控（`/opt/sentinel`，127.0.0.1:8787） |
| asus | `flagw@flagw.apay.eu.cc:14098`（asus `ssh -R` 反代） | Ubuntu 24.04 华硕笔记本 | Clash Verge + openclaw gateway(:18789，已接 homelab MCP，agent=`openclaw`) |
| cone | `flagw@flagw.cloud-ip.cc:22`（公网 182.255.88.7） | Ubuntu 24.04 | xray(reality TCP 443/9443) + sing-box(hy2 UDP 443/9443) + nginx(订阅分发) |

- 用户要求：**四台机器保留密码登录**（人工登录用），安全加固以后再议。只加密钥，不改认证方式。
- 出口：本机/raspi 同在 202.120.8.237 NAT 后；asus 183.192.11.16。

## 3. homelab 架构与关键路径（ali 上）

```
harness(pi/codex/opencode) ─stdio MCP(ssh key)─► mcp@ali:22
   ├─ homelab-web   (user mcp)  FastAPI 127.0.0.1:8790: PWA+REST+collector
   └─ homelab-execd  (root)     /run/homelab-execd.sock: nonce 核销→argv-only 执行
Caddy(已有): handle /home* → 127.0.0.1:8790（不剥前缀，webapp 中间件剥）
公网入口: https://flagw.apay.eu.cc/home （PWA，可加 iOS 主屏幕）
```

harness 接入面（都在 ali 的 `/opt/homelab/.ssh/authorized_keys`，agent 身份自动注册）：

| 客户端 | 位置 | 密钥 | HOMELAB_AGENT |
|---|---|---|---|
| pi / codex / opencode | 本机 | `~/.ssh/homelab_agent` | pi / codex / opencode |
| openclaw gateway | asus(flagw) | `~/.ssh/homelab_openclaw`(ed25519) | openclaw |

### 3.1 openclaw 接入（asus，2026-10-12 首次部署）

- OpenClaw `2026.10.1 (e178c39)`，npm 全局装在 asus `~/.npm-global/lib/node_modules/openclaw`，
  gateway 以 flagw 常驻 `127.0.0.1:18789`；配置 `/home/flagw/.openclaw/openclaw.json`。
- MCP 入口即 `mcp.servers`（与 codex/claude 同形的 stdio 配置），管理命令
  `openclaw mcp add/list/show/probe/reload/doctor`；`mcp add` 保存前会 probe，
  `mcp reload` 让 gateway 下一轮构建新 runtime（不用重启 gateway）。
- asus→ali 走公网直连 `mcp@flagw.apay.eu.cc:22`（asus 出口 183.192.11.16，不走隧道）。
- 部署命令（asus 上以 flagw 执行，幂等由公钥去重保证）：
  `openclaw mcp add homelab --command ssh --arg -i --arg /home/flagw/.ssh/homelab_openclaw
   --arg -o --arg IdentitiesOnly=yes --arg -o --arg BatchMode=yes
   --arg -o --arg StrictHostKeyChecking=accept-new --arg -o --arg ConnectTimeout=10
   --arg -o --arg ServerAliveInterval=30 --arg -o --arg ServerAliveCountMax=3
   --arg mcp@flagw.apay.eu.cc --arg "exec /usr/local/bin/homelab-mcpd --agent openclaw"
   --env HOMELAB_AGENT=openclaw --timeout 60 --connect-timeout 20`
- 密钥注册方式：公钥 append 到 ali `/opt/homelab/.ssh/authorized_keys`——**必须走运维直达
  （本机 `ssh -i ~/.ssh/homelab_agent mcp@ali`，mcp 用户写自己的文件，无需 root）**；
  MCP 通道两侧（tools.py/execd.py）对 `authorized_keys` 是硬拒绝，别绕。
- 验收：`openclaw mcp probe homelab` → 16 tools；ali 侧 `agents` 表出现 `openclaw`，
  audit 有 `session_start ... from 183.192.11.16`。

| 路径 | 说明 |
|---|---|
| `/opt/homelab/` | 代码（homelab 包 + static/ + hosts.toml + deploy/），mcp:mcp |
| `/opt/homelab/etc/keys/id_hosts` | web/collector 用私钥（mcp 0600）→ 各机 flagw |
| `/etc/homelab/keys/id_hosts` | execd 用私钥（root 0600）→ 各机 root |
| `/etc/homelab/known_hosts` | execd 的 known_hosts（root:mcp 664） |
| `/opt/homelab/run/ssh/` | web 的 SSH ControlMaster socket |
| `/var/lib/homelab/homelab.db` | sqlite（会话/审批/审计/指标），mcp:mcp |
| `/var/log/homelab/` | 日志（含 mcp-stdio.err），本仓库 deploy 无 logrotate（轮转内置于 python logging） |
| 各机 `~/.homelab/sampler.py` | 巡检采样器（flagw 家目录） |
| `~/.ssh/homelab_agent`（本机） | harness → `mcp@ali` 的客户端密钥 |
| `~/.local/bin/homelab-mcp`（本机） | stdio 启动器（`ssh … homelab-mcpd --agent X`） |

harness 配置位置：pi `~/.pi/agent/mcp.json`；codex `~/.codex/config.toml`（`[mcp_servers.homelab]`）；
opencode `~/.config/opencode/opencode.json`（`mcp.homelab`）；openclaw 在 asus 的
`/home/flagw/.openclaw/openclaw.json`（`mcp.servers.homelab`，见 §3.1）。前三处 env 都是 `HOMELAB_AGENT=<名字>`。

**Web 控制台密码**：scrypt 哈希存 DB（`meta.web_password_hash`），不明文。轮换：
`cd /opt/homelab && sudo -u mcp python3 -m homelab.initdb --password X`。

## 4. 工具面 = 三档执行模型

| 档位 | 工具 | 审批 | 身份 |
|---|---|---|---|
| 只读统计 | `run_read`（hosts.toml `readonly_whitelist` 42 项） | 否 | flagw/mcp |
| 普通命令 | `run_user`（`user_whitelist` 41 项） | 否 | flagw/mcp |
| root | `sudo_run` | 是* | root |

\* `auto_allow`（hosts.toml 按主机配置：cone 的 `systemctl restart nginx/xray/sing-box`、
raspi 的 `restart sentinel/frp_ssh`、ali 的 `reload caddy` 等）立即执行，仍走 nonce+execd+审计。

其余工具：`hosts/host_status/host_metrics/services/logs/sentinel_events/sentinel_clips/
sudo_wait/sudo_status/sudo_help/pending_approvals/audit_tail/agents`（共 16 个）。
`sudo_help` 返回完整 playbook，agent 有疑问先调它——**这是协议的唯一真相**，
agent 不该去服务器读源码（2026-10-12 的教训：三棒握手不直觉，已改成
`sudo_run`→`sudo_wait` 两步，全部写进工具描述）。

审批流：`sudo_run` pending → 用户在 https://flagw.apay.eu.cc/home 批准 →
`sudo_wait(nonce)` 阻塞轮询、批准即执行。nonce 一次性、绑定 (host, argv) 逐字匹配、
15 分钟过期。**已删除** `sudo_request`/`sudo_confirm` 两个旧工具名，别复活。

自动批准窗口：控制台「审批」页可开一段时长（上限 `hosts.toml` 的
`auto_approve_max_minutes`，默认 480 分钟即 8 小时），窗口内所有 root 请求自动批准执行，
实现为 `meta.auto_approve_until`（epoch 秒），`sudo_run` 创建后先查窗口，命中即
`decide(decided_by=policy:auto-approve-window)` 再走 execd。仍全审计、硬拒绝不受影响。

## 5. 部署 SOP（ali，root）

```bash
# 本地: cd homelab/server && tar czf /tmp/homelab.tgz --exclude='__pycache__' .
# 上传到 ali /tmp/homelab.tgz 后:
tar xzf /tmp/homelab.tgz -C /opt/homelab && chown -R mcp:mcp /opt/homelab
install -m 644 /opt/homelab/deploy/homelab-{web,execd}.service /etc/systemd/system/
systemctl restart homelab-web homelab-execd
```

改 Caddy 路由后必须 `caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile` 再 reload。
 collectors 每 60s 采集；重启 web 后等 10s 再看 metrics 表。

## 6. 坑与修复史（重要，都是真踩过的）

1. **tar 的 cwd 不跨 bash 调用持久**：每次打包必须 `cd /home/flagw/ssh-agent-plugins/homelab/server`，
   否则会把整个仓库打进去（曾差点误部署）。打完 `tar tzf | wc -l` 应为 32。
2. **python 静默替换会失败**：`str.replace` 锚点不匹配时不报错。两次事故：
   `user_whitelist` 插入（锚点少个空行）、TOML 反斜杠被写成 `\\s` 导致正则失效。
   **改完必须 `tomllib.load` + `re.search` 实测断言**。
3. **systemd 单元的唯一真相是 `server/deploy/*.service`**：曾只 sed 过已安装文件，
   下次 `install` 覆盖即复发（execd 丢 WorkingDirectory → `No module named homelab`）。
   单元里同时保留 `WorkingDirectory=/opt/homelab` 和 `PYTHONPATH`。
4. **两个 unit 都不要加 `PrivateTmp`/`ProtectHome`**：会破坏核心功能（真实 /tmp 不可见、
   /root 不可写、run_read 读不了 /home）。边界靠 nonce+审批+硬拒绝+审计，不靠 namespace。
   web 的 ControlMaster 因此在 `/opt/homelab/run/ssh`（ProtectSystem=strict 下 /tmp 只读）。
5. **SSH 私钥不能组可读**（ssh 拒绝 0640）：同密钥分两份 0600（root 版 /etc/homelab，mcp 版 /opt/homelab/etc）。
6. **`shlex.quote` 会让远端 shell 不展开 `~`**：远端命令一律用绝对路径（sampler 路径同理）。
7. **Caddy `/home*` 用 `handle` 不剥前缀**，由 webapp 中间件剥；`handle_path` 搭配命名匹配器会报错。
8. **execd `Connection refused`** → 先 `journalctl -u homelab-execd`，九成是单元/模块问题。
9. **部署后 helathcheck 流程**：`systemctl is-active` 两个服务 → metrics 表四主机 →
   MCP stdio 手工 JSON-RPC → web curl 登录/overview → 审批流 E2E。
10. **`run_read`/`run_user` 会把以 `-i` 开头的参数当交互参数拒绝**（防 `ssh -i`/交互式 flag）。
    踩坑：`grep -i pattern file` 因此整条被拒，一度误判成 openclaw.json 有非 UTF-8 字节
    （实测纯 ASCII）。绕法：`bash -lc "..."` 把整条命令塞进单个参数，或不用 `-i`。
11. **`ls`/`cat` 只在只读白名单，不在 user_whitelist**：列目录/读文件用 `run_read`；
    `run_user` 只认 python3/node/grep/jq/bash 等 41 项。另外 codemode 桥对 error 返回
    会抛 `Invalid MCP tools/call structured content`——看到它先查白名单/拒绝规则，不是网络问题。
12. **mcp 用户的家目录是 `/opt/homelab`（不是 `/home/mcp`）**：其 `authorized_keys` 在
    `/opt/homelab/.ssh/authorized_keys`（mcp 自写即可，见 §3.1）。
13. **openclaw CLI 每次调用启动约 20s**：`mcp add/probe/reload` 都慢，给它留足 timeout。
14. **PWA 部署后看不到新 UI**：旧 `sw.js` 是 cache-first，会一直吐部署前的 HTML/JS。
    已改成 network-first（外壳）+ `Cache-Control: no-cache` + controllerchange 自动 reload；
    但**已装的旧 SW 仍需手动清一次**（Safari 删站点数据 / Chrome DevTools→Service Workers→
    Unregister + Clear site data）。改前端后若用户说"没变化"，先怀疑 SW 缓存再查服务端
    （服务端用 `curl -s https://flagw.apay.eu.cc/home | grep 关键字` 一验即知）。

## 7. 测试 SOP

- MCP 手测：`printf '%s\n' '<jsonrpc 行>' | HOMELAB_AGENT=pi ~/.local/bin/homelab-mcp`
  （initialize → notifications/initialized → tools/list → tools/call）。
- Web：curl 登录拿 cookie（`-H 'X-HL-Request: 1'`），再打 `/home/api/*`。
- 手机视口：本地 `/tmp/pwtest`（puppeteer-core + 本机 Chrome）390×844 模拟，
  验登录持久化/触达目标/图标不透明（canvas 读角点 alpha）。
- 审批 E2E：`sudo_run` pending → curl approve → `sudo_wait` executed；
  另测 denied/expired/replay/硬拒绝。

## 8. 设计决策记录（为什么这么做）

- **stdio-over-SSH 而非 HTTP 端口**：不新增公网口，复用 SSH 密钥体系，多端配置同形。
- **审批放在 PWA 而非 harness 弹窗**：用户选了手机网页渠道；策略集中在服务端，
  不依赖任何 harness 的 permission 实现。
- **execd root + argv-only + 无 shell**：远端命令 `shlex.quote` 逐一转义；本地直 execv 列表。
- **ali 持各机 root key**：v1  pragmatic；硬化方向是每主机本地 execd（ali 失守=全失守是已知风险）。
- **PWA 挂 `/home` 路径而非新子域**：证书只覆盖 `flagw.apay.eu.cc`，无 DNS/证书开销。

## 9. 待办 / 硬化路线

- [ ] Web 认证加 TOTP/Passkey（现为单密码+30 天 cookie）
- [ ] 审批推送（Telegram/APNs），现在靠轮询/角标
- [ ] per-host execd 拆分 root 密钥
- [ ] 密码登录收紧（用户要求暂缓）：至少 ali 上 fail2ban、密钥优先
- [ ] 常用命令 promote 进 auto_allow 的半自动建议（按审计统计）
- [ ] openclaw 侧跑一次真实 agent turn 验证工具调用闭环（probe/reload 已过，turn 未测）
