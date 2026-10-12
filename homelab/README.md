# homelab — 四机统一 MCP 服务 + PWA 审批监控台

给所有 agent harness（pi / codex / opencode）提供一套安全、可审计的服务器管理能力：
监控、日志、只读命令免审批；白名单外的 root 操作走 **一次性 nonce 人工审批**。

## 架构

```
本地 harness (pi/codex/opencode)
   └─ stdio MCP ──SSH(key: ~/.ssh/homelab_agent)──► mcp@ali
                                                    │
                              ┌─────────────────────┼──────────────────────┐
                              │  homelab-web (mcp)  │  homelab-execd (root)│
                              │  · FastAPI :8790    │  · unix socket       │
                              │  · PWA 静态 + REST  │  · nonce 原子核销    │
                              │  · collector 巡检   │  · argv-only 执行    │
                              └──────────┬──────────┴──────────┬───────────┘
                                         │                      │
                    Caddy /home* ────────┘                      │ ssh(key: /etc/homelab/keys)
                                                               ▼
ali(local) · raspi(:10003) · asus(:14098) · cone(182.255.88.7)  ← root/flagw 双通道
```

- **ali 是唯一入口**：harness 不持有任何服务器凭据，只持一把 SSH 客户端密钥
- **raspi/asus 复用现有隧道**（frps 10003 / ssh -R 14098），cone 公网直连
- **两层闸门**：白名单只读命令自动执行；其余 root 操作必须先在
  <https://flagw.apay.eu.cc/home> 批准（nonce 绑定 host+argv+15 分钟过期+一次性）
- **硬拒绝清单**：`rm -rf /`、`dd of=/dev/*`、掐断 ssh/审计/fail2ban 等，人工批准也不执行
- **全审计**：每次工具调用/审批/执行都落 sqlite，控制台可查

## 工具（MCP，15 个）

| 工具 | 说明 |
|---|---|
| `hosts` / `host_status` / `host_metrics` | 主机列表、实时状态、指标历史 |
| `services` | systemd unit 与关键进程状态 |
| `logs` | journalctl（只读，强制 `--no-pager`） |
| `run_read` | 白名单只读命令自动执行 |
| `sentinel_events` / `sentinel_clips` | raspi 摄像头事件库与录像列表 |
| **`sudo_run`** | **root 执行的唯一入口**：命中 auto_allow 立即执行；否则返回 pending+nonce+`tell_user` |
| **`sudo_wait`** | 轮询审批并执行（阻塞 25s；批准即执行，返回 exit_code/stdout） |
| `sudo_status` | 只查状态不执行 |
| `sudo_help` | 完整 playbook（何时要审批/两步流程/铁律） |
| `pending_approvals` / `audit_tail` / `agents` | 待批列表、审计、agent 吊销 |

**典型 agent 流程**（工具描述内自包含，无需读源码）：

```
sudo_run("cone", ["systemctl","restart","nginx"], "重启 nginx")
  ├─ auto_allow 命中 → {status: executed, exit_code: 0}        # 秒级, 无需审批
  └─ {status: pending, nonce, tell_user: "请打开…/home 批准"}
        → (把 tell_user 转告用户)
        → sudo_wait(nonce)   # 批准后自动执行; 被拒/过期如实返回
```

**auto_allow**（hosts.toml 按主机配置，免审批但同样走 nonce+execd+审计）：
cone 的 `systemctl restart nginx/xray/sing-box`、raspi 的 `systemctl restart sentinel/frp_ssh`、
ali 的 `systemctl reload caddy` 等。要加新条目改 hosts.toml 重启 homelab-web 即可。

## 本地 harness 配置

启动器 `~/.local/bin/homelab-mcp`（ssh → `mcp@ali`，身份由 `HOMELAB_AGENT` 区分）。
三个客户端配置已就绪：

- **pi**：`~/.pi/agent/mcp.json` → `mcpServers.homelab`
- **codex**：`~/.codex/config.toml` → `[mcp_servers.homelab]`
- **opencode**：`~/.config/opencode/opencode.json` → `mcp.homelab`

## iOS 添加到主屏幕

Safari 打开 <https://flagw.apay.eu.cc/home> → 分享 → 添加到主屏幕。
登录后 cookie 保持 30 天。Push 通知未接（审批提示靠轮询/角标）。

## 部署 / 运维

部署物在 `server/`（ali 上 `/opt/homelab`）+ `deploy/`（systemd/Caddy 片段）：

```bash
# ali 上一键重部署（root）
tar xzf homelab.tgz -C /opt/homelab && chown -R mcp:mcp /opt/homelab
install -m 644 /opt/homelab/deploy/homelab-{web,execd}.service /etc/systemd/system/
systemctl restart homelab-web homelab-execd
```

**沙箱说明（重要，勿回退）**：两个 unit 都**不**设 `PrivateTmp`/`ProtectHome`。
execd 的职责就是执行人工批准的命令——私有 `/tmp` 会让已上传到真实 `/tmp` 的
安装包/文件对命令不可见，`ProtectHome` 会阻断 `/root`、`/home` 读写；web 的
`run_read` 同样需要看到真实 `/tmp`、`/home`。真正的边界是 nonce+人工审批+硬拒绝
清单+审计，不是命名空间。web 的 SSH ControlMaster 目录因此在
`/opt/homelab/run/ssh`（`ProtectSystem=strict` 下 `/tmp` 只读）。

| 操作 | 命令 |
|---|---|
| 改 Web 密码 | `cd /opt/homelab && sudo -u mcp python3 -m homelab.initdb --password 新密码` |
| 禁用某个 agent | 控制台「审计」页 → Agent 身份 → 禁用（立即生效，kill switch） |
| 加主机 | `server/hosts.toml` 增加 `[hosts.xxx]`，远端装公钥+`~/.homelab/sampler.py` |
| 换密钥 | ali 重生成 `/etc/homelab/keys/id_hosts`，同步到 mcp 副本与各主机 authorized_keys |

## 目录

```
homelab/
├── bin/homelab-mcp            # 本地 stdio 启动器
├── scripts/gen_icons.py       # PWA 图标生成(纯标准库)
└── server/
    ├── homelab/               # 服务端代码(config/db/audit/sshutil/approvals/
    │                          #  tools/mcp_stdio/execd/collector/webapp/initdb/sampler)
    ├── hosts.toml             # 主机与策略配置(白名单/硬拒绝)
    ├── static/                # PWA(index/app.js/style.css/manifest/sw/icons)
    └── deploy/                # systemd 单元 + Caddy 片段 + mcpd 入口
```

## 已知限制 / 后续硬化

- [ ] root SSH 密钥集中在 ali（ali 失守=全网失守）→ 可演进为每主机本地 execd
- [ ] Web 单密码认证 → 可加 TOTP/Passkey
- [ ] 审批通知轮询 → 可接 Telegram/APNs
- [ ] 本机与各服务器仍是密码登录（用户要求保留），建议之后至少对 ali 上 fail2ban
- [ ] nonce 绑定 argv 逐字匹配，agent 改一个字符需重新申请（设计使然）
