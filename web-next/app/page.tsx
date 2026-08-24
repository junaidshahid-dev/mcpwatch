import Link from "next/link";
import CopySnippet from "./CopySnippet";

export default function Landing() {
  return (
    <>
      <nav>
        <div className="wrap nav">
          <Link className="brand" href="/">
            <span className="mark">
              <span className="halo" />
              <span className="core" />
            </span>
            MCP<b>Watch</b>
          </Link>
          <div className="nav-links">
            <a href="#how">How it works</a>
            <a href="#features">Features</a>
            <a href="#pricing">Pricing</a>
            <Link className="btn sm cta" href="/dashboard">Open dashboard →</Link>
          </div>
        </div>
      </nav>

      <header className="hero">
        <div className="wrap">
          <span className="eyebrow"><span className="dot" /> Monitoring for the Model Context Protocol</span>
          <div className="hero-grid">
            <div>
              <h1>Know the second your <span className="c">MCP server</span> breaks.</h1>
              <p className="lead">
                MCPWatch pings every MCP server your agents depend on, grades its tool schemas
                0–100, and alerts you the moment it goes down or degrades — before your agents do.
              </p>
              <div className="hero-cta">
                <Link className="btn" href="/dashboard">Start monitoring — free</Link>
                <a className="btn ghost" href="#badge">See a live badge</a>
              </div>
              <div className="trust">
                <span><b>◇</b> No credit card</span>
                <span><b>◇</b> 1 server free, forever</span>
                <span><b>◇</b> Badge in 2 minutes</span>
              </div>
            </div>

            <div className="scope" aria-label="Live monitor preview">
              <div className="scope-top">
                <span className="scope-name">
                  <span className="mark" style={{ width: 20, height: 20 }}><span className="core" /></span> payments-mcp
                </span>
                <span className="pill up">● operational</span>
              </div>
              <svg className="ekg" viewBox="0 0 600 96" preserveAspectRatio="none" aria-hidden="true">
                <line className="base" x1="0" y1="70" x2="600" y2="70" />
                <path d="M0,70 L120,70 L140,70 L150,34 L162,92 L176,18 L188,70 L300,70 L330,70 L345,52 L358,70 L470,70 L490,70 L500,40 L512,90 L526,26 L540,70 L600,70" />
              </svg>
              <div className="metrics">
                <div className="metric"><div className="k">Status</div><div className="v grade-hi">Up</div></div>
                <div className="metric"><div className="k">Schema</div><div className="v grade-A">A <small>/ 93</small></div></div>
                <div className="metric"><div className="k">Latency</div><div className="v">41<small>ms</small></div></div>
                <div className="metric"><div className="k">Tools</div><div className="v">12</div></div>
                <div className="metric"><div className="k">Uptime 30d</div><div className="v">99.9<small>%</small></div></div>
                <div className="metric"><div className="k">Checks</div><div className="v">hourly</div></div>
              </div>
            </div>
          </div>
        </div>
      </header>

      <section id="how">
        <div className="wrap">
          <div className="kicker">How it works</div>
          <h2>Three steps to peace of mind</h2>
          <p className="sub">Point MCPWatch at a server. It does the watching, the grading, and the shouting.</p>
          <div className="steps">
            <div className="step"><div className="n">STEP 01</div><h3>Add your server</h3>
              <p>Paste a launch command (stdio) or an HTTPS endpoint. Local dev tools and remote production servers alike.</p></div>
            <div className="step"><div className="n">STEP 02</div><h3>We probe on a schedule</h3>
              <p>MCPWatch connects, lists tools, measures latency, and grades every advertised schema 0–100 — with zero adversarial traffic, safe to run anywhere.</p></div>
            <div className="step"><div className="n">STEP 03</div><h3>Get alerted, show a badge</h3>
              <p>An email hits your inbox the moment a server drops or its grade regresses. A public status badge proves your uptime to everyone else.</p></div>
          </div>
        </div>
      </section>

      <section id="features">
        <div className="wrap">
          <div className="kicker">What you get</div>
          <h2>Observability built for MCP</h2>
          <div className="feat">
            <div className="card"><div className="ico">🩺</div><h3>Schema health grade</h3>
              <p>Every tool scored on description, typing and validation — a single 0–100 grade and A–F letter, powered by the open-source mcp-probe engine.</p></div>
            <div className="card"><div className="ico">⏱️</div><h3>Uptime & latency</h3>
              <p>Reachability and response time tracked on every check, with a rolling 30-day uptime ratio per server.</p></div>
            <div className="card"><div className="ico">🔀</div><h3>Schema-diff alerts</h3>
              <p>Detects when a server’s tool surface changes and classifies it non-breaking, potentially-breaking, or breaking — before it breaks your agents.</p></div>
            <div className="card"><div className="ico">🚨</div><h3>Incidents & alerts</h3>
              <p>Opens an incident on repeated failure, tracks its duration, auto-resolves on recovery, and alerts by email, Slack, Discord or webhook.</p></div>
            <div className="card"><div className="ico">🛡️</div><h3>On-demand deep audit</h3>
              <p>For servers you own, run the full adversarial fuzz — hundreds of malformed payloads — gated so you never hammer someone else’s live backend.</p></div>
            <div className="card"><div className="ico">📛</div><h3>Public status badge</h3>
              <p>An embeddable SVG for your README that shows live status and grade — trust, earned automatically.</p></div>
          </div>
        </div>
      </section>

      <section id="badge">
        <div className="wrap">
          <div className="kicker">Growth loop</div>
          <h2>A badge that markets itself</h2>
          <div className="badge-show">
            <div>
              <p style={{ color: "var(--muted)", fontSize: 16 }}>
                Drop one line in your README. Everyone who visits your repo sees your server is
                healthy — and where that badge came from.
              </p>
              <div className="badge-demo" aria-hidden="true">
                <svg className="badgeimg" viewBox="0 0 168 20" xmlns="http://www.w3.org/2000/svg">
                  <rect width="98" height="20" rx="3" fill="#24292f" /><rect x="98" width="70" height="20" rx="3" fill="#2ea043" />
                  <g fill="#fff" fontFamily="Verdana,sans-serif" fontSize="11">
                    <text x="10" y="14">payments-mcp</text><text x="108" y="14">UP · A</text></g>
                </svg>
                <svg className="badgeimg" viewBox="0 0 150 20" xmlns="http://www.w3.org/2000/svg">
                  <rect width="70" height="20" rx="3" fill="#24292f" /><rect x="70" width="80" height="20" rx="3" fill="#d29922" />
                  <g fill="#fff" fontFamily="Verdana,sans-serif" fontSize="11">
                    <text x="10" y="14">search-mcp</text><text x="80" y="14">DEGRADED</text></g>
                </svg>
              </div>
              <CopySnippet text="![MCP status](https://mcpwatch.dev/badge/<your-id>.svg)" />
            </div>
            <div className="scope">
              <div className="scope-top"><span className="scope-name">status.mcpwatch.dev</span>
                <span className="pill up">● public</span></div>
              <div className="metrics" style={{ gridTemplateColumns: "1fr 1fr" }}>
                <div className="metric"><div className="k">This month</div><div className="v grade-A">99.97<small>%</small></div></div>
                <div className="metric"><div className="k">Incidents</div><div className="v">0</div></div>
                <div className="metric"><div className="k">Avg latency</div><div className="v">38<small>ms</small></div></div>
                <div className="metric"><div className="k">Grade</div><div className="v grade-A">A</div></div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="pricing">
        <div className="wrap">
          <div className="kicker">Pricing</div>
          <h2>Start free. Upgrade when it’s critical.</h2>
          <p className="sub">Billed through LemonSqueezy — global cards, taxes handled, cancel anytime.</p>
          <div className="prices">
            <div className="price">
              <div className="pn">Free</div>
              <div className="pp">$0<small>/mo</small></div>
              <ul><li>1 monitored server</li><li>Daily health checks</li><li>Schema grade & uptime</li><li>Public status badge</li></ul>
              <Link className="btn ghost" href="/dashboard">Get started</Link>
            </div>
            <div className="price feature">
              <div className="tag">Most popular</div>
              <div className="pn">Pro</div>
              <div className="pp">$19<small>/mo</small></div>
              <ul><li>10 monitored servers</li><li>Hourly checks</li><li>Email regression alerts</li><li>Programmatic API</li><li>On-demand deep audits</li></ul>
              <Link className="btn" href="/dashboard?plan=pro">Start Pro</Link>
            </div>
            <div className="price">
              <div className="pn">Team</div>
              <div className="pp">$49<small>/mo</small></div>
              <ul><li>Unlimited servers</li><li>Checks every 15 minutes</li><li>Alerts & deep audits</li><li>Team access</li><li>Priority support</li></ul>
              <Link className="btn ghost" href="/dashboard?plan=team">Start Team</Link>
            </div>
          </div>
        </div>
      </section>

      <section>
        <div className="wrap">
          <div className="kicker">Questions</div>
          <h2>Good to know</h2>
          <div className="faq">
            <details open><summary>What exactly is an MCP server?</summary>
              <p>A Model Context Protocol server exposes tools and data to AI agents (like Claude) as typed, callable functions. If your agent stops being able to reach one — or the server starts advertising broken schemas — your agent silently fails. MCPWatch catches that.</p></details>
            <details><summary>Does monitoring send attack traffic to my servers?</summary>
              <p>No. Scheduled checks are read-only: connect, list tools, statically grade the schemas. The full adversarial fuzz is opt-in, on-demand, and only permitted on servers you mark as owned.</p></details>
            <details><summary>Can it monitor remote production servers?</summary>
              <p>Yes — MCPWatch speaks the Streamable HTTP transport (including SSE responses and session ids), as well as launching local stdio servers.</p></details>
            <details><summary>How does the schema grade work?</summary>
              <p>It’s the open-source mcp-probe engine: each tool is scored independently on description, typing and input validation, then averaged into a 0–100 score and A–F grade, so a big server isn’t punished for its size.</p></details>
          </div>
        </div>
      </section>

      <section>
        <div className="wrap">
          <div className="band">
            <div className="kicker">Ready?</div>
            <h2>Stop finding out from your users.</h2>
            <p className="sub">Add your first MCP server and get a status badge in the next two minutes.</p>
            <div style={{ marginTop: 26 }}><Link className="btn" href="/dashboard">Start monitoring — free</Link></div>
          </div>
        </div>
      </section>

      <footer>
        <div className="wrap foot">
          <div className="sig">Designed &amp; built by <b>M. Junaid Shahid</b> <span className="heart">◆</span> &nbsp;·&nbsp; Lahore, Pakistan</div>
          <div className="foot-links">
            <a href="https://github.com/junaidshahid-dev" target="_blank" rel="noopener">GitHub</a>
            <a href="https://junaidshahid-dev.github.io" target="_blank" rel="noopener">Portfolio</a>
            <Link href="/dashboard">Dashboard</Link>
          </div>
        </div>
      </footer>
    </>
  );
}
