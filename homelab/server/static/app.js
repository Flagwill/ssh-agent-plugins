/* homelab PWA 前端逻辑 */
(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);
  const H = { "X-HL-Request": "1" };
  let currentTab = "overview";
  let currentHost = null;
  let timer = null;

  const fmtBytes = (b) => {
    if (b == null) return "-";
    const u = ["B", "K", "M", "G", "T"];
    let i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return b.toFixed(i ? 1 : 0) + u[i];
  };
  const fmtUptime = (s) => {
    if (s == null) return "-";
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return (d ? d + "天" : "") + h + "时" + m + "分";
  };
  const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false }) : "-";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  async function api(path, opts = {}) {
    const r = await fetch(path, { headers: H, ...opts });
    if (r.status === 401) { showLogin(); throw new Error("unauthorized"); }
    return r.json();
  }

  /* ---------------- 登录 ---------------- */
  function showLogin() {
    $("#login").classList.remove("hidden");
    $("#app").classList.add("hidden");
  }
  function showApp() {
    $("#login").classList.add("hidden");
    $("#app").classList.remove("hidden");
  }

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#login-err").textContent = "";
    try {
      const r = await fetch("/home/api/login", {
        method: "POST", headers: H,
        body: JSON.stringify({ password: $("#login-pw").value }),
      });
      const d = await r.json();
      if (!r.ok) { $("#login-err").textContent = d.error || "登录失败"; return; }
      $("#login-pw").value = "";
      showApp(); boot();
    } catch { $("#login-err").textContent = "网络错误"; }
  });

  $("#logout-btn").addEventListener("click", async () => {
    await fetch("/home/api/logout", { method: "POST", headers: H });
    showLogin();
  });

  /* ---------------- tab ---------------- */
  $$(".tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  function switchTab(tab) {
    currentTab = tab;
    $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $$(".view").forEach((v) => v.classList.toggle("hidden", v.dataset.view !== tab));
    if (tab === "overview") loadOverview();
    if (tab === "hosts") loadHosts();
    if (tab === "approvals") loadApprovals();
    if (tab === "audit") loadAudit();
  }

  $("#refresh-btn").addEventListener("click", () => switchTab(currentTab));

  /* ---------------- 总览 ---------------- */
  function hostCard(h) {
    const cell = (k, v) => `<div>${k} <b>${v}</b></div>`;
    return `<li class="card" data-host="${esc(h.id)}">
      <div class="name"><span class="state ${h.online ? "on" : "off"}"></span>${esc(h.label)}</div>
      <div class="grid">
        ${cell(" Load", h.load ? h.load[0].toFixed(2) : "-")}
        ${cell(" CPU", h.cpu_pct != null ? h.cpu_pct + "%" : "-")}
        ${cell(" 内存", h.mem_used_pct != null ? h.mem_used_pct + "%" : "-")}
        ${cell(" 磁盘", h.disk_used_pct != null ? h.disk_used_pct + "%" : "-")}
        ${cell(" 温度", h.temp_max != null ? h.temp_max.toFixed(0) + "°C" : "-")}
        ${cell(" 运行", fmtUptime(h.uptime_s))}
      </div></li>`;
  }

  async function loadOverview() {
    const d = await api("/home/api/overview");
    $("#host-cards").innerHTML = d.hosts.map(hostCard).join("");
    $$("#host-cards .card").forEach((c) =>
      c.addEventListener("click", () => { switchTab("hosts"); openHost(c.dataset.host); }));
    const pb = $("#pending-banner");
    if (d.pending_count > 0) {
      pb.classList.remove("hidden");
      pb.textContent = `⏳ ${d.pending_count} 个待审批请求 — 前往「审批」处理`;
      pb.onclick = () => switchTab("approvals");
    } else pb.classList.add("hidden");
    $("#overview-audit").innerHTML = d.audit_recent.map((a) =>
      `<li><time>${fmtTime(a.ts)}</time><span class="what"><b>${esc(a.actor)}</b> ${esc(a.kind)} ${esc(a.host)}</span></li>`).join("");
    updateBadge(d.pending_count);
  }

  function updateBadge(n) {
    const b = $("#approval-badge");
    b.textContent = n; b.classList.toggle("hidden", n <= 0);
  }

  /* ---------------- 主机 ---------------- */
  async function loadHosts() {
    const d = await api("/home/api/hosts");
    $("#host-detail").classList.add("hidden");
    $("#host-list").innerHTML = d.hosts.map((h) =>
      `<li data-host="${esc(h.id)}"><div class="title"><span class="state ${h.online ? "on" : "off"}"></span> ${esc(h.label)}</div>
       <div class="sub">${h.online ? "在线 · Load " + (h.load ? h.load[0].toFixed(2) : "-") : (h.error || "离线")}</div></li>`).join("");
    $$("#host-list li").forEach((li) =>
      li.addEventListener("click", () => openHost(li.dataset.host)));
  }

  async function openHost(hid) {
    currentHost = hid;
    $("#host-list").classList.add("hidden");
    $("#host-detail").classList.remove("hidden");
    $("#hd-title").textContent = hid;
    const [m, s] = await Promise.all([
      api(`/home/api/hosts/${hid}/metrics?hours=6`),
      api(`/home/api/hosts/${hid}/services`),
    ]);
    drawChart(m.points);
    const last = m.points[m.points.length - 1] || {};
    $("#hd-stats").innerHTML = [
      ["CPU", last.cpu_pct != null ? last.cpu_pct + "%" : "-"],
      ["内存", last.mem ? fmtBytes(last.mem.MemTotal - (last.mem.MemAvailable || 0)) + " / " + fmtBytes(last.mem.MemTotal) : "-"],
      ["磁盘", last.disk_root ? fmtBytes(last.disk_root.total - (last.disk_root.free || 0)) + " / " + fmtBytes(last.disk_root.total) : "-"],
      ["温度", last.temp_max != null ? last.temp_max.toFixed(1) + "°C" : "-"],
      ["网络 ↓/↑", fmtBytes(last.net_rx_Bps) + "/s · " + fmtBytes(last.net_tx_Bps) + "/s"],
      ["运行", fmtUptime(last.uptime_s)],
    ].map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
    const units = Object.entries(s.units || {}).map(([u, st]) =>
      `<li><span class="${st === "active" ? "ok" : "bad"}">●</span>${esc(u)}</li>`).join("");
    const procs = Object.entries(s.procs || {}).map(([p, on]) =>
      `<li><span class="${on ? "ok" : "bad"}">●</span>${esc(p)}</li>`).join("");
    $("#hd-services").innerHTML = units + procs;
  }

  $("#host-back").addEventListener("click", loadHosts);

  $("#hd-logs-btn").addEventListener("click", async () => {
    const unit = $("#hd-unit").value.trim();
    const d = await api(`/home/api/hosts/${currentHost}/logs?unit=${encodeURIComponent(unit)}&lines=200`);
    $("#hd-logs").textContent = d.log || d.error || "(无输出)";
  });

  function drawChart(points) {
    const svg = $("#hd-chart");
    if (!points.length) { svg.innerHTML = ""; return; }
    const n = points.length;
    const line = (get, color) => {
      const vals = points.map(get);
      const max = Math.max(1, ...vals.map((v) => v || 0));
      const pts = vals.map((v, i) =>
        `${(i / (n - 1 || 1) * 300).toFixed(1)},${(85 - (v || 0) / max * 80).toFixed(1)}`).join(" ");
      return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>`;
    };
    svg.innerHTML =
      line((p) => p.cpu_pct, "#5ec2ff") +
      line((p) => p.mem && p.mem.MemTotal ? 100 * (p.mem.MemTotal - (p.mem.MemAvailable || 0)) / p.mem.MemTotal : 0, "#ffb454") +
      line((p) => Math.min(100, (p.net_rx_Bps || 0) / 1024), "#3ddc97");
  }

  /* ---------------- 审批 ---------------- */
  function approvalItem(a, actions) {
    const left = a.seconds_left;
    const cd = left > 0 ? `<span class="countdown">⏳ ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}</span>` : "";
    const stMap = { approved: "✅ 已批准", denied: "⛔ 已拒绝", consumed: "✔️ 已执行", expired: "⌛ 已过期", pending: "" };
    return `<li>
      <div class="title">${esc(a.host)} <span class="muted">#${esc(a.id)}</span> ${cd}</div>
      <div class="sub">agent: ${esc(a.agent)} · ${fmtTime(a.created_at)}</div>
      <div class="sub mono">$ ${esc(a.argv.join(" "))}</div>
      <div class="sub">原因: ${esc(a.reason)}</div>
      ${actions ? `<div class="approval-actions">
        <button class="btn" data-act="approve" data-id="${esc(a.id)}">批准</button>
        <button class="btn danger" data-act="deny" data-id="${esc(a.id)}">拒绝</button></div>` : `<div class="sub">${stMap[a.state] || esc(a.state)}</div>`}
    </li>`;
  }

  async function loadApprovals() {
    const d = await api("/home/api/approvals?state=pending");
    $("#approval-pending").innerHTML = d.approvals.length
      ? d.approvals.map((a) => approvalItem(a, true)).join("")
      : `<li class="muted">暂无待审批请求</li>`;
    $$("#approval-pending [data-act]").forEach((b) =>
      b.addEventListener("click", async () => {
        b.disabled = true;
        await api(`/home/api/approvals/${b.dataset.id}/${b.dataset.act}`, { method: "POST" });
        loadApprovals(); loadOverview();
      }));
    const h = await api("/home/api/approvals?state=&limit=30");
    $("#approval-history").innerHTML = h.approvals
      .filter((a) => a.state !== "pending")
      .slice(0, 15).map((a) => approvalItem(a, false)).join("") ||
      `<li class="muted">暂无历史</li>`;
    updateBadge(d.approvals.filter((a) => a.seconds_left > 0).length);
  }

  /* ---------------- 审计 ---------------- */
  async function loadAudit() {
    const [d, ag] = await Promise.all([
      api("/home/api/audit?limit=80"), api("/home/api/agents")]);
    $("#audit-list").innerHTML = d.audit.map((a) =>
      `<li><time>${fmtTime(a.ts)}</time><span class="what"><b>${esc(a.actor)}</b> ${esc(a.kind)} ${esc(a.host)} ${esc(a.detail || "")}</span></li>`).join("");
    $("#agents-list").innerHTML = ag.agents.map((a) =>
      `<li><div class="title">${esc(a.label || a.key)}</div>
       <div class="sub">${a.enabled ? "已启用" : "⛔ 已禁用"} · 最近活跃 ${fmtTime(a.last_seen)}</div>
       <div class="approval-actions"><button class="btn small ${a.enabled ? "danger" : ""}" data-agent="${esc(a.key)}" data-en="${a.enabled ? 0 : 1}">
       ${a.enabled ? "禁用" : "启用"}</button></div></li>`).join("");
    $$("#agents-list [data-agent]").forEach((b) =>
      b.addEventListener("click", async () => {
        await api(`/home/api/agents/${b.dataset.agent}/${b.dataset.en === "1" ? "enable" : "disable"}`, { method: "POST" });
        loadAudit();
      }));
  }

  /* ---------------- 启动 ---------------- */
  async function boot() {
    try {
      const me = await api("/home/api/me");
      showApp();
      const d = me.expires_at ? new Date(me.expires_at * 1000) : null;
      $("#user-badge").textContent = d
        ? `flagw · 有效期至 ${d.getMonth() + 1}/${d.getDate()}`
        : "flagw";
      switchTab("overview");
      clearInterval(timer);
      timer = setInterval(() => {
        if (document.hidden) return;
        if (currentTab === "overview") loadOverview();
        if (currentTab === "approvals") loadApprovals();
      }, 15000);
    } catch { /* showLogin 已处理 */ }
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/home/sw.js").catch(() => {});
  }
  api("/home/api/me").then(boot).catch(() => {});
})();
