import { useState, useEffect, useRef } from 'react';
import { useNexus } from '../context/NexusContext';
import axios from 'axios';

const BACKEND_URL = `http://${window.location.hostname}:8000`;

/* One clean hub for every integration — Productivity, Messaging, and MCP data sources.
 * (Replaces the old separate Integrations page; all connect flows live here.) */
export default function ConnectionsPage() {
  const { currentUser } = useNexus();
  const [channels, setChannels]              = useState([]);
  const [googleConnected, setGoogleConnected] = useState(false);
  const [mcp, setMcp]                          = useState([]);
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
        <ChannelCard channel={WHATSAPP} linked={linked('whatsapp')} authHeaders={authHeaders} onChange={loadAll} />
        <ChannelCard channel={TELEGRAM} linked={linked('telegram')} authHeaders={authHeaders} onChange={loadAll} />
      </div>

      <SectionLabel>Apps &amp; data sources · MCP</SectionLabel>
      <p style={{ color: 'var(--t3)', fontSize: 12, marginTop: -6, marginBottom: 12 }}>
        Plug enterprise tools in over MCP so the AI reads your real code, docs, and data — not just task text. Tokens are encrypted at rest.
      </p>
      <div className="nx-grid-3">
        {MCP_CATALOG.map(app => (
          <MCPCard key={app.app} app={app} connected={mcp.find(c => c.app === app.app)}
                   authHeaders={authHeaders} onChange={loadAll} setError={setError} />
        ))}
      </div>

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

/* ── Messaging: WhatsApp / Telegram (code-link flow) ───────────── */
const WHATSAPP = { id: 'whatsapp', name: 'WhatsApp', color: '#25D366', placeholder: '+1 234 567 8900',
  hint: 'Chat with your AI on WhatsApp', prereq: 'First send the join code to the Twilio sandbox number on WhatsApp.' };
const TELEGRAM = { id: 'telegram', name: 'Telegram', color: '#229ED9', placeholder: 'your numeric chat ID',
  hint: 'Chat with your AI on Telegram', prereq: 'First open the Nexus bot in Telegram and press Start.' };

function ChannelCard({ channel, linked, authHeaders, onChange }) {
  const [stage, setStage] = useState('idle');
  const [identifier, setId] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const startLink = async () => {
    setBusy(true); setMsg(null);
    try {
      await axios.post(`${BACKEND_URL}/api/v1/channels/link`, { platform: channel.id, identifier }, { headers: authHeaders() });
      setStage('code_sent'); setMsg(`Code sent. Check your ${channel.name}.`);
    } catch (e) { setMsg(e.response?.data?.detail || 'Failed to send code.'); }
    finally { setBusy(false); }
  };
  const confirmCode = async () => {
    setBusy(true); setMsg(null);
    try {
      await axios.post(`${BACKEND_URL}/api/v1/channels/verify`, { platform: channel.id, identifier, code }, { headers: authHeaders() });
      setStage('done'); setMsg('Linked.'); onChange();
      setTimeout(() => { setStage('idle'); setId(''); setCode(''); setMsg(null); }, 1600);
    } catch (e) { setMsg(e.response?.data?.detail || 'Invalid code.'); }
    finally { setBusy(false); }
  };

  const expand = (stage !== 'idle' || msg) ? (
    <div style={{ marginTop: 12 }}>
      {stage === 'entering' && (
        <>
          <div style={{ color: '#f59e0b', fontSize: 11, marginBottom: 8 }}>{channel.prereq}</div>
          <input className="nx-input" value={identifier} onChange={e => setId(e.target.value)} placeholder={channel.placeholder} style={{ marginBottom: 8 }} />
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary btn-sm" onClick={startLink} disabled={busy || !identifier}>{busy ? 'Sending…' : 'Send code'}</button>
            <button className="btn btn-ghost btn-sm" onClick={() => { setStage('idle'); setMsg(null); }}>Cancel</button>
          </div>
        </>
      )}
      {stage === 'code_sent' && (
        <>
          <input className="nx-input" value={code} onChange={e => setCode(e.target.value)} placeholder="6-digit code" maxLength={6} style={{ marginBottom: 8 }} />
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary btn-sm" onClick={confirmCode} disabled={busy || !code}>{busy ? 'Verifying…' : 'Verify'}</button>
            <button className="btn btn-ghost btn-sm" onClick={() => { setStage('idle'); setCode(''); setMsg(null); }}>Cancel</button>
          </div>
        </>
      )}
      {msg && <div style={{ marginTop: 8, fontSize: 12, color: stage === 'done' ? 'var(--green)' : 'var(--t2)' }}>{msg}</div>}
    </div>
  ) : null;

  return (
    <ConnCard logo={<Logo color={channel.color} />} name={channel.name}
              sub={linked ? <span style={{ color: 'var(--green)' }}>✓ Linked</span> : channel.hint} expand={expand}>
      {stage === 'idle' && !linked && <button className="btn btn-primary btn-sm" onClick={() => setStage('entering')}>Connect</button>}
    </ConnCard>
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

/* ── MCP enterprise apps / data sources ───────────────────────── */
const MCP_CATALOG = [
  { app: 'github',    label: 'GitHub',            color: '#6e5494', url: 'https://api.githubcopilot.com/mcp/', hint: 'Read-only PAT (repo) — repos, PRs, issues.' },
  { app: 'notion',    label: 'Notion',            color: '#111111', url: 'https://mcp.notion.com/mcp',         hint: 'Notion internal integration token.' },
  { app: 'linear',    label: 'Linear',            color: '#5E6AD2', url: 'https://mcp.linear.app/mcp',         hint: 'Linear API key / OAuth token.' },
  { app: 'atlassian', label: 'Jira / Confluence', color: '#0052CC', url: 'https://mcp.atlassian.com/v1/sse',   hint: 'Atlassian API token.' },
  { app: 'postgres',  label: 'Postgres',          color: '#336791', url: '',                                    hint: 'Public URL of your Postgres MCP server.' },
  { app: 'custom',    label: 'Custom MCP server', color: '#10b981', url: '',                                    hint: 'Any MCP server — paste its URL + token.' },
];

function MCPCard({ app, connected, authHeaders, onChange, setError }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState(app.url);
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);

  const connect = async () => {
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
      <input className="nx-input" type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="Auth token (encrypted)" style={{ marginBottom: 8 }} />
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-primary btn-sm" onClick={connect} disabled={busy}>{busy ? 'Connecting…' : 'Connect'}</button>
        <button className="btn btn-ghost btn-sm" onClick={() => { setOpen(false); setToken(''); }}>Cancel</button>
      </div>
    </div>
  ) : null;

  return (
    <ConnCard logo={<Logo color={app.color} />} name={app.label}
              sub={connected ? <span style={{ color: 'var(--green)' }}>✓ Connected{connected.enabled ? '' : ' (off)'}</span> : app.hint} expand={expand}>
      {connected
        ? <button className="btn btn-ghost btn-sm" onClick={disconnect}>Disconnect</button>
        : !open && <button className="btn btn-primary btn-sm" onClick={() => setOpen(true)}>Connect</button>}
    </ConnCard>
  );
}
