"""homelab fleet: 统一 MCP 服务 + PWA 审批监控台（部署于 ali）。

模块:
  config      配置加载 (hosts.toml)
  db          sqlite 存储
  audit       审计流水
  sshutil     argv-only SSH 执行
  approvals   一次性 nonce 审批
  tools       MCP 工具实现（web 审批共用）
  mcp_stdio   MCP stdio JSON-RPC 服务
  execd       root 执行守护（unix socket）
  collector   后台巡检采集
  webapp      FastAPI: PWA 静态 + REST API
  initdb      初始化 / 改密码
"""
