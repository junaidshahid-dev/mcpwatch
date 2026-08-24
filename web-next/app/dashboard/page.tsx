"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

type Me = {
  user: { email: string };
  org: { name: string; plan: string };
  plan: string;
  limits: { max_monitors: number; min_interval_seconds: number; alerts: boolean; api_access: boolean };
  usage: { monitors: number; open_incidents: number };
};
type Monitor = {
  id: string; name: string; kind: string; endpoint: string;
  last_status?: string | null; last_grade?: string | null; last_score?: number | null;
  last_checked_at?: number | null; uptime_30d?: number | null;
};

const H = { "Content-Type": "application/json" };

function fmtUptime(u?: number | null) {
  return u == null ? "—" : (u * 100).toFixed(u >= 0.9995 ? 2 : 1) + "%";
}
function fmtWhen(t?: number | null) {
  if (!t) return "never";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return Math.round(s) + "s ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  return Math.round(s / 3600) + "h ago";
}

export default function Dashboard() {
  const [ready, setReady] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [gateErr, setGateErr] = useState("");
  const [monitors, setMonitors] = useState<Monitor[]>([]);
  const [mName, setMName] = useState("");
  const [mKind, setMKind] = useState("http");
  const [mEndpoint, setMEndpoint] = useState("");
  const [toast, setToast] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const showToast = (m: string) => { setToast(m); setTimeout(() => setToast(""), 1900); };

  const load = useCallback(async () => {
    const r = await fetch("/api/monitors");
    if (r.ok) setMonitors(await r.json());
  }, []);

  const boot = useCallback(async () => {
    const r = await fetch("/api/me");
    if (r.ok) { setMe(await r.json()); await load(); } else { setMe(null); }
    setReady(true);
  }, [load]);

  useEffect(() => { boot(); }, [boot]);

  // URL params: ?reset=<token>, ?verified=1, ?plan=
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const reset = p.get("reset");
    if (reset) {
      const pw = window.prompt("Enter a new password (min 8 chars):");
      if (pw) {
        fetch("/api/auth/reset", { method: "POST", headers: H, body: JSON.stringify({ token: reset, password: pw }) })
          .then((r) => showToast(r.ok ? "Password updated — please log in" : "Reset link invalid or expired"));
      }
    }
    if (p.get("verified") === "1") setTimeout(() => showToast("Email verified ✓"), 300);
    if (p.get("plan")) setTimeout(() => showToast(`Sign in, then upgrade to ${p.get("plan")}`), 400);
  }, []);

  async function submitAuth() {
    if (!email || !password) { setGateErr("Email and password required"); return; }
    const path = mode === "login" ? "/api/auth/login" : "/api/auth/signup";
    const r = await fetch(path, { method: "POST", headers: H, body: JSON.stringify({ email, password }) });
    if (!r.ok) { setGateErr((await r.json().catch(() => ({}))).detail || "Authentication failed"); return; }
    setGateErr(""); setPassword(""); await boot();
  }
  async function forgot() {
    if (!email) { setGateErr("Enter your email first"); return; }
    await fetch("/api/auth/request-reset", { method: "POST", headers: H, body: JSON.stringify({ email }) });
    showToast("If that account exists, a reset link was sent");
  }
  async function logout() {
    await fetch("/api/auth/logout", { method: "POST", headers: H });
    setMe(null); setMonitors([]);
  }

  async function addMonitor() {
    if (!mName || !mEndpoint) { showToast("Name and endpoint required"); return; }
    const r = await fetch("/api/monitors", {
      method: "POST", headers: H,
      body: JSON.stringify({ name: mName, kind: mKind, endpoint: mEndpoint }),
    });
    if (!r.ok) { showToast((await r.json().catch(() => ({}))).detail || "Could not add monitor"); return; }
    const m = await r.json();
    setMName(""); setMEndpoint("");
    showToast("Monitor added — running first check…");
    await checkNow(m.id);
    await boot();
  }
  async function checkNow(id: string) {
    setBusyId(id);
    try {
      const r = await fetch(`/api/monitors/${id}/check`, { method: "POST", headers: H, body: JSON.stringify({ depth: "liveness" }) });
      const c = await r.json();
      showToast(`${(c.status || "done").toUpperCase()} · grade ${c.grade || "—"} · ${c.latency_ms ?? "—"}ms`);
    } catch { showToast("Check failed"); }
    finally { setBusyId(null); await load(); }
  }
  async function del(id: string) {
    if (!window.confirm("Delete this monitor and its history?")) return;
    await fetch(`/api/monitors/${id}`, { method: "DELETE", headers: H });
    showToast("Deleted"); await load(); await boot();
  }
  async function viewChecks(id: string) {
    const r = await fetch(`/api/monitors/${id}`);
    const d = await r.json();
    const lines = (d.recent || []).slice(0, 10).map((c: Record<string, unknown>) =>
      `${new Date((c.checked_at as number) * 1000).toLocaleString()} — ${c.status} · ${c.grade || "—"} · ${c.latency_ms ?? "—"}ms`
    ).join("\n") || "No checks yet.";
    const up = d.metrics?.uptime?.["30d"];
    window.alert(`History — ${d.monitor.name}\nuptime 30d: ${fmtUptime(up)}\n\n${lines}`);
  }
  function copyBadge(url: string) {
    navigator.clipboard?.writeText(`![MCP status](${url})`);
    showToast("Badge markdown copied");
  }

  const Nav = (
    <nav><div className="wrap nav">
      <Link className="brand" href="/"><span className="mark"><span className="halo" /><span className="core" /></span>MCP<b>Watch</b></Link>
      {me && (
        <div className="who">
          <span className="plan-chip">{me.plan}</span>
          <span>{me.user.email}</span>
          <a onClick={logout} style={{ cursor: "pointer", color: "var(--muted)" }}>sign out</a>
        </div>
      )}
    </div></nav>
  );

  if (!ready) return <>{Nav}</>;

  if (!me) {
    return (
      <>
        {Nav}
        <div className="gate"><div className="wrap"><div className="panel">
          <h1 style={{ fontSize: 26 }}>{mode === "login" ? "Sign in to MCPWatch" : "Create your account"}</h1>
          <p className="muted" style={{ color: "var(--muted)", margin: "6px 0 18px", fontSize: 14.5 }}>
            Monitor your MCP servers, get alerts on regressions, and show a live status badge.
          </p>
          <div className="field"><label>Email</label>
            <input type="email" placeholder="you@company.com" autoComplete="email"
              value={email} onChange={(e) => setEmail(e.target.value)} /></div>
          <div className="field"><label>Password</label>
            <input type="password" placeholder="at least 8 characters" autoComplete="current-password"
              value={password} onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submitAuth(); }} /></div>
          <div className="err">{gateErr}</div>
          <div className="row">
            <button className="btn" onClick={submitAuth}>{mode === "login" ? "Log in →" : "Create account →"}</button>
            <button className="btn ghost" onClick={() => { setMode(mode === "login" ? "signup" : "login"); setGateErr(""); }}>
              {mode === "login" ? "Create account" : "I have an account"}
            </button>
          </div>
          <div className="badge-line" style={{ marginTop: 14 }} onClick={forgot}>Forgot password?</div>
        </div></div></div>
        {toast && <div className="toast show">{toast}</div>}
      </>
    );
  }

  const max = me.limits.max_monitors === -1 ? "unlimited" : me.limits.max_monitors;

  return (
    <>
      {Nav}
      <div className="dash"><div className="wrap">
        <div className="head">
          <div>
            <h1 style={{ fontSize: 26 }}>Your monitors</h1>
            <p className="muted" style={{ color: "var(--muted)" }}>
              {me.plan} plan · {me.usage.monitors}/{max} server(s) · checks every{" "}
              {Math.round(me.limits.min_interval_seconds / 60)} min · {me.usage.open_incidents} open incident(s)
            </p>
          </div>
          <Link className="btn ghost sm" href="/">← Landing</Link>
        </div>

        <div className="panel">
          <label style={{ marginBottom: 12 }}>Add a monitor</label>
          <div className="add">
            <div><label>Name</label>
              <input placeholder="payments-mcp" value={mName} onChange={(e) => setMName(e.target.value)} /></div>
            <div><label>Kind</label>
              <select value={mKind} onChange={(e) => setMKind(e.target.value)}>
                <option value="http">http (URL)</option>
                <option value="stdio">stdio (command)</option>
              </select></div>
            <div><label>Endpoint</label>
              <input placeholder="https://api.example.com/mcp" value={mEndpoint} onChange={(e) => setMEndpoint(e.target.value)} /></div>
            <button className="btn" onClick={addMonitor}>Add server</button>
          </div>
        </div>

        {monitors.length === 0 ? (
          <div className="empty">
            No monitors yet. Add your first MCP server above — try kind <b>http</b> with a public
            MCP endpoint, or run the backend locally and use <span className="mono">stdio</span>.
          </div>
        ) : (
          <div className="monitors">
            {monitors.map((m) => {
              const st = m.last_status || "unknown";
              const badge = `${location.origin}/badge/${m.id}.svg`;
              return (
                <div className="mon" key={m.id}>
                  <div>
                    <div className="name"><span className={`dot ${st}`} />{m.name}
                      <span className="plan-chip" style={{ color: "var(--muted)" }}>{m.kind}</span></div>
                    <div className="ep">{m.endpoint}</div>
                    <div className="stats">
                      <span>status <b>{st}</b></span>
                      <span>grade {m.last_grade ? <span className={`g ${m.last_grade}`}>{m.last_grade}</span> : "—"}
                        {m.last_score != null && <span className="dim" style={{ color: "var(--dim)" }}> / {m.last_score}</span>}</span>
                      <span>uptime 30d <b>{fmtUptime(m.uptime_30d)}</b></span>
                      <span>checked <b>{fmtWhen(m.last_checked_at)}</b></span>
                    </div>
                    <div className="badge-line" onClick={() => copyBadge(badge)}>📛 {badge} · click to copy</div>
                  </div>
                  <div className="actions">
                    <button className="btn sm" disabled={busyId === m.id} onClick={() => checkNow(m.id)}>
                      {busyId === m.id ? "Checking…" : "Check now"}</button>
                    <button className="btn ghost sm" onClick={() => viewChecks(m.id)}>History</button>
                    <button className="btn danger sm" onClick={() => del(m.id)}>Delete</button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <footer><div className="sig" style={{ textAlign: "center" }}>Designed &amp; built by <b>M. Junaid Shahid</b> ◆</div></footer>
      </div></div>
      {toast && <div className="toast show">{toast}</div>}
    </>
  );
}
