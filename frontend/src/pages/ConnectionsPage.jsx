import { useState, useEffect, useRef } from 'react';
import { useNexus } from '../context/NexusContext';
import axios from 'axios';

import { BACKEND_URL } from '../config';

/* One clean hub for every integration — Productivity, Slack, and MCP data sources.
 * (Replaces the old separate Integrations page; all connect flows live here.) */
export default function ConnectionsPage() {
  const { currentUser } = useNexus();
  const [channels, setChannels]              = useState([]);
  const [googleConnected, setGoogleConnected] = useState(false);
  const [mcp, setMcp]                          = useState([]);
  const [mcpQuery, setMcpQuery]                = useState('');
  const [loading, setLoading]                  = useState(true);
  const [error, setError]                      = useState(null);

  const empId = currentUser?.dbId;
  const authHeaders = () => {
    const t = sessionStorage.getItem('nexus_access_token');
    return t ? { Authorization: `Bearer ${t}` } : {};
  };
  // Track polling timers so we clear them on unmount (no setState-after-unmount, no leak).
  const timersRef = useRef([]);
  useEffect(() => () => timersRef.current.forEach(id => { clearInterval(id); clearTimeout(id); }), []);

  const loadAll = async () => {
    try {
      const [chan, goog, mcpRes] = await Promise.allSettled([
        axios.get(`${BACKEND_URL}/api/v1/channels/my`, { headers: authHeaders() }),
        empId ? axios.get(`${BACKEND_URL}/api/v1/google/status/${empId}`) : Promise.reject(),
        axios.get(`${BACKEND_URL}/api/v1/mcp/`, { headers: authHeaders() }),
      ]);
      if (chan.status === 'fulfilled') setChannels(Array.isArray(chan.value.data) ? chan.value.data : []);
      else setError('Could not load your channels.');
      if (goog.status === 'fulfilled') setGoogleConnected(!!goog.value.data.connected);
      if (mcpRes.status === 'fulfilled') setMcp(mcpRes.value.data?.connections || []);
    } catch { setError('Could not load connections.'); }
    finally { setLoading(false); }
  };
  useEffect(() => { loadAll(); }, [empId]);

  const connectGoogle = async () => {
    try {
      const res = await axios.get(`${BACKEND_URL}/api/v1/google/connect/${empId}`, { headers: authHeaders() });
      const url = res.data?.auth_url;
      if (!url) { setError('Could not start Google connect.'); return; }
      const popup = window.open(url, 'google-oauth', 'width=520,height=640');
      if (!popup) { setError('Popup blocked — allow popups for this site, then click Connect again.'); return; }
      const timer = setInterval(() => { if (popup.closed) { clearInterval(timer); setTimeout(loadAll, 800); } }, 700);
      // Stop polling after 3 min even if the popup is left open; track both timers
      // so unmount clears them (a blocked/abandoned popup can't leak a spinning timer).
      const stop = setTimeout(() => clearInterval(timer), 180000);
      timersRef.current.push(timer, stop);
    } catch { setError('Could not start Google connect.'); }
  };
  const disconnectGoogle = async () => {
    if (!confirm('Disconnect Google Workspace?')) return;
    try { await axios.post(`${BACKEND_URL}/api/v1/google/disconnect/${empId}`, {}, { headers: authHeaders() }); loadAll(); }
    catch { setError('Failed to disconnect Google.'); }
  };

  const linked = (id) => (channels || []).some(c => c.platform === id && c.verified);

  return (
    <div className="animate-in" style={{ maxWidth: 860 }}>
      <h2 style={{ color: 'var(--t1)', fontSize: 20, fontWeight: 700, marginBottom: 4 }}>Connections</h2>
      <p style={{ color: 'var(--t3)', fontSize: 13, marginBottom: 22 }}>
        One home for every tool. Your AI works across every channel with one shared memory.
      </p>

      {error && (
        <div style={{ color: '#ef4444', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)',
                      padding: '10px 14px', borderRadius: 8, marginBottom: 16, fontSize: 13 }}>{error}</div>
      )}

      <SectionLabel>Productivity</SectionLabel>
      <div className="nx-grid-3" style={{ marginBottom: 26 }}>
        <ConnCard logo={<Logo color="#34a853" />} name="Google Workspace"
                  sub={googleConnected ? <span style={{ color: 'var(--green)' }}>✓ Connected — Gmail &amp; Calendar</span> : 'Gmail, Calendar, availability'}>
          {googleConnected
            ? <button className="btn btn-ghost btn-sm" onClick={disconnectGoogle}>Disconnect</button>
            : <button className="btn btn-primary btn-sm" disabled={!empId} onClick={connectGoogle}>Connect</button>}
        </ConnCard>
      </div>

      <SectionLabel>Messaging</SectionLabel>
      <div className="nx-grid-3" style={{ marginBottom: 26 }}>
        <SlackCard linked={linked('slack')} authHeaders={authHeaders} onChange={loadAll} setError={setError} />
      </div>

      <SectionLabel>Apps &amp; data sources · MCP</SectionLabel>
      <p style={{ color: 'var(--t3)', fontSize: 12, marginTop: -6, marginBottom: 10 }}>
        Connect the tools your team already uses — most are one click. The AI then reads your real
        code, docs, and data, not just task text. Tokens are encrypted at rest; OAuth connections
        are personal to whoever approves them.
      </p>
      <input className="nx-input" value={mcpQuery} onChange={e => setMcpQuery(e.target.value)}
             placeholder="Search apps… (Linear, Stripe, Notion, Jira…)"
             style={{ maxWidth: 340, marginBottom: 14 }} />
      {MCP_CATEGORIES.map(cat => {
        const apps = MCP_CATALOG.filter(a => a.cat === cat &&
          (a.label + ' ' + a.app).toLowerCase().includes(mcpQuery.trim().toLowerCase()));
        if (!apps.length) return null;
        return (
          <div key={cat} style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 11, color: 'var(--t3)', textTransform: 'uppercase',
                          letterSpacing: '0.07em', margin: '2px 0 8px' }}>{cat}</div>
            <div className="nx-grid-3">
              {apps.map(app => (
                <MCPCard key={app.app} app={app} connected={mcp.find(c => c.app === app.app)}
                         authHeaders={authHeaders} onChange={loadAll} setError={setError} />
              ))}
            </div>
          </div>
        );
      })}

      {loading && <div style={{ color: 'var(--t3)', fontSize: 13, marginTop: 16 }}>Loading…</div>}
    </div>
  );
}

/* ── Shared bits ───────────────────────────────────────────────── */
function SectionLabel({ children }) {
  return (
    <div style={{ color: 'var(--t2)', fontSize: 13, fontWeight: 600, marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid var(--b1)' }}>
      {children}
    </div>
  );
}

function Logo({ color }) {
  return (
    <div style={{ width: 38, height: 38, borderRadius: 10, background: `${color}1e`, border: `1px solid ${color}40`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
      <div style={{ width: 16, height: 16, borderRadius: 5, background: color }} />
    </div>
  );
}

function ConnCard({ logo, name, sub, children, expand }) {
  return (
    <div className="card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          {logo}
          <div style={{ minWidth: 0 }}>
            <div style={{ color: 'var(--t1)', fontWeight: 600, fontSize: 13.5 }}>{name}</div>
            <div style={{ color: 'var(--t3)', fontSize: 11.5, marginTop: 1 }}>{sub}</div>
          </div>
        </div>
        <div style={{ flexShrink: 0 }}>{children}</div>
      </div>
      {expand}
    </div>
  );
}


/* ── Slack (DM-the-bot code flow) ──────────────────────────────── */
function SlackCard({ linked, authHeaders, onChange, setError }) {
  const [stage, setStage] = useState('idle');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);
  const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  useEffect(() => stopPoll, []);   // clear the link poll on unmount

  const startLink = async () => {
    setBusy(true);
    try {
      const res = await axios.post(`${BACKEND_URL}/api/v1/channels/slack/start-link`, {}, { headers: authHeaders() });
      setCode(res.data.code); setStage('code_shown');
      stopPoll();   // never run two polls at once
      pollRef.current = setInterval(async () => {
        try {
          const my = await axios.get(`${BACKEND_URL}/api/v1/channels/my`, { headers: authHeaders() });
          if (Array.isArray(my.data) && my.data.some(c => c.platform === 'slack' && c.verified)) { stopPoll(); setStage('idle'); onChange(); }
        } catch { /* keep polling */ }
      }, 2500);
      setTimeout(stopPoll, 300000);
    } catch (e) { setError(e.response?.data?.detail || 'Failed to start Slack linking.'); }
    finally { setBusy(false); }
  };

  const expand = stage === 'code_shown' ? (
    <div style={{ marginTop: 12 }}>
      <div style={{ color: 'var(--t2)', fontSize: 12, marginBottom: 8 }}>DM the <b>Nexus</b> bot in Slack this code:</div>
      <div style={{ background: 'var(--bg-3)', border: '1px dashed #611f69', borderRadius: 8, padding: 12, textAlign: 'center',
                    fontSize: 22, fontWeight: 700, letterSpacing: '0.2em', color: 'var(--t1)', fontFamily: 'var(--font-mono)' }}>{code}</div>
      <div style={{ color: 'var(--t3)', fontSize: 11, marginTop: 8 }}>Updates automatically once linked.</div>
      <button className="btn btn-ghost btn-sm" style={{ marginTop: 8 }} onClick={() => { stopPoll(); setStage('idle'); }}>Cancel</button>
    </div>
  ) : null;

  return (
    <ConnCard logo={<Logo color="#611f69" />} name="Slack"
              sub={linked ? <span style={{ color: 'var(--green)' }}>✓ Active</span> : 'DM your AI in Slack'} expand={expand}>
      {stage === 'idle' && !linked && <button className="btn btn-primary btn-sm" onClick={startLink} disabled={busy}>{busy ? '…' : 'Connect'}</button>}
    </ConnCard>
  );
}

/* ── MCP enterprise apps / data sources ─────────────────────
   Every entry probed live against its OAuth metadata (2026-07-06).
   oauth:true = true one-click (dynamic client registration).
   admin:true = OAuth works after a one-time admin setup (MCP_CLIENT_ID_<APP>
   env on the server — the Connect button explains it). */
const MCP_CATALOG = [
  // Project & work management
  { cat: 'Project & work', app: 'linear',    label: 'Linear',            color: '#5E6AD2', url: 'https://mcp.linear.app/mcp',       oauth: true, hint: 'One click — issues, projects, cycles.' },
  { cat: 'Project & work', app: 'atlassian', label: 'Jira / Confluence', color: '#0052CC', url: 'https://mcp.atlassian.com/v1/sse', oauth: true, hint: 'One click — issues, boards, pages.' },
  { cat: 'Project & work', app: 'asana',     label: 'Asana',             color: '#F06A6A', url: 'https://mcp.asana.com/sse',        oauth: true, hint: 'One click — tasks & projects.' },
  { cat: 'Project & work', app: 'monday',    label: 'Monday.com',        color: '#FF3D57', url: 'https://mcp.monday.com/sse',       oauth: true, hint: 'One click — boards & items.' },
  { cat: 'Project & work', app: 'clickup',   label: 'ClickUp',           color: '#7B68EE', url: 'https://mcp.clickup.com/mcp',      oauth: true, hint: 'One click — tasks, docs, goals.' },
  // Team chat — the MCP server reads channels/history/search as YOU.
  // (The Slack BOT above is a separate thing: it replies in channels and DMs.)
  { cat: 'Team chat', app: 'slack', label: 'Slack (search & history)', color: '#4A154B', url: 'https://mcp.slack.com/mcp', oauth: true, admin: true, hint: 'Let the AI search your Slack history — needs the app client id/secret.' },
  // Dev & code
  { cat: 'Dev & code', app: 'github', label: 'GitHub', color: '#6e5494', url: 'https://api.githubcopilot.com/mcp/', oauth: true, admin: true, hint: 'Repos, PRs, issues — one-time admin setup or a PAT.' },
  { cat: 'Dev & code', app: 'sentry', label: 'Sentry', color: '#8b5cf6', url: 'https://mcp.sentry.dev/mcp',         oauth: true, hint: 'One click — errors & performance issues.' },
  // Docs & design
  { cat: 'Docs & design', app: 'notion', label: 'Notion', color: '#111111', url: 'https://mcp.notion.com/mcp', oauth: true, hint: 'One click — pages & databases.' },
  { cat: 'Docs & design', app: 'figma',  label: 'Figma',  color: '#F24E1E', url: 'https://mcp.figma.com/mcp',  oauth: true, hint: 'One click — files & components.' },
  { cat: 'Docs & design', app: 'canva',  label: 'Canva',  color: '#00C4CC', url: 'https://mcp.canva.com/mcp',  oauth: true, hint: 'One click — designs & folders.' },
  { cat: 'Docs & design', app: 'box',    label: 'Box',    color: '#0061D5', url: 'https://mcp.box.com/mcp',    oauth: true, admin: true, hint: 'Files & folders — one-time admin setup.' },
  // Sales & support
  { cat: 'Sales & support', app: 'hubspot',  label: 'HubSpot',  color: '#FF7A59', url: 'https://mcp.hubspot.com/',     oauth: true, admin: true, hint: 'CRM — one-time admin setup.' },
  { cat: 'Sales & support', app: 'intercom', label: 'Intercom', color: '#1F8DED', url: 'https://mcp.intercom.com/mcp', oauth: true, hint: 'One click — conversations & customers.' },
  // Payments & finance
  { cat: 'Payments', app: 'stripe', label: 'Stripe', color: '#635BFF', url: 'https://mcp.stripe.com/',      oauth: true, hint: 'One click — payments, customers, invoices.' },
  { cat: 'Payments', app: 'paypal', label: 'PayPal', color: '#003087', url: 'https://mcp.paypal.com/mcp',   oauth: true, hint: 'One click — transactions & invoices.' },
  { cat: 'Payments', app: 'square', label: 'Square', color: '#3E4348', url: 'https://mcp.squareup.com/sse', oauth: true, hint: 'One click — payments & catalog.' },
  // Automation & data
  { cat: 'Automation & data', app: 'zapier',   label: 'Zapier',            color: '#FF4F00', url: 'https://mcp.zapier.com/api/mcp/mcp', oauth: true, hint: 'One click — your Zaps & 7000+ app actions.' },
  { cat: 'Automation & data', app: 'postgres', label: 'Postgres',          color: '#336791', url: '', hint: 'Public URL of your Postgres MCP server.' },
  { cat: 'Automation & data', app: 'custom',   label: 'Custom MCP server', color: '#10b981', url: '', hint: 'Any MCP server — OAuth if it supports it, or URL + token.' },
];
const MCP_CATEGORIES = [...new Set(MCP_CATALOG.map(a => a.cat))];

function MCPCard({ app, connected, authHeaders, onChange, setError }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState(app.url);
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  // One-click OAuth: backend does discovery + registration, we open the
  // provider's consent page; the callback page postMessages us when done.
  const oauthConnect = async (customUrl) => {
    const target = (customUrl ?? app.url).trim();
    if (!target) { setError('Enter the MCP server URL first.'); return; }
    setBusy(true); setError(null);
    try {
      const res = await axios.post(`${BACKEND_URL}/api/v1/mcp/oauth/start`,
        { app: app.app, label: app.label, url: target }, { headers: authHeaders() });
      const popup = window.open(res.data.authorize_url, 'nexus-mcp-oauth', 'width=560,height=720');
      if (!popup) { setError('Popup blocked — allow popups for this site and try again.'); setBusy(false); return; }
      const cleanup = () => {
        window.removeEventListener('message', onMsg);
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        setBusy(false); setOpen(false); onChange();
      };
      const onMsg = (ev) => { if (ev.data?.type === 'nexus-mcp-oauth') cleanup(); };
      window.addEventListener('message', onMsg);
      pollRef.current = setInterval(() => { if (popup.closed) cleanup(); }, 1200);
    } catch (e) {
      setError(e.response?.data?.detail || 'Could not start the OAuth connection.');
      setBusy(false);
    }
  };

  const connectToken = async () => {
    if (!url.trim()) { setError('Enter the MCP server URL.'); return; }
    setBusy(true);
    try {
      await axios.post(`${BACKEND_URL}/api/v1/mcp/`, { app: app.app, label: app.label, url: url.trim(), auth_token: token }, { headers: authHeaders() });
      setOpen(false); setToken(''); onChange();
    } catch (e) { setError(e.response?.data?.detail || 'Could not connect.'); }
    finally { setBusy(false); }
  };
  const disconnect = async () => {
    if (!confirm(`Disconnect ${app.label}?`)) return;
    try { await axios.delete(`${BACKEND_URL}/api/v1/mcp/${connected.id}`, { headers: authHeaders() }); onChange(); }
    catch { setError('Could not disconnect.'); }
  };

  const expand = (open && !connected) ? (
    <div style={{ marginTop: 10 }}>
      <input className="nx-input" value={url} onChange={e => setUrl(e.target.value)} placeholder="MCP server URL (https://…)" style={{ marginBottom: 6 }} />
      <input className="nx-input" type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="API token (encrypted at rest)" style={{ marginBottom: 8 }} />
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button className="btn btn-primary btn-sm" onClick={connectToken} disabled={busy}>{busy ? 'Connecting…' : 'Connect with token'}</button>
        <button className="btn btn-ghost btn-sm" onClick={() => oauthConnect(url)} disabled={busy}>Try OAuth instead</button>
        <button className="btn btn-ghost btn-sm" onClick={() => { setOpen(false); setToken(''); }}>Cancel</button>
      </div>
    </div>
  ) : null;

  const subLine = connected
    ? <span style={{ color: 'var(--green)' }}>
        ✓ Connected{connected.enabled ? '' : ' (off)'}{connected.auth_type === 'oauth' ? ' · OAuth' : ''}{connected.shared ? ' · shared' : ' · only you'}
      </span>
    : app.hint;

  return (
    <ConnCard logo={<Logo color={app.color} />} name={app.label} sub={subLine} expand={expand}>
      {connected
        ? <button className="btn btn-ghost btn-sm" onClick={disconnect}>Disconnect</button>
        : app.oauth
          ? <>
              <button className="btn btn-primary btn-sm" onClick={() => oauthConnect()} disabled={busy}>{busy ? 'Waiting…' : 'Connect'}</button>
              {!open && <button className="btn btn-ghost btn-sm" onClick={() => setOpen(true)} title="Use an API token instead">⋯</button>}
            </>
          : !open && <button className="btn btn-primary btn-sm" onClick={() => setOpen(true)}>Connect</button>}
    </ConnCard>
  );
}
