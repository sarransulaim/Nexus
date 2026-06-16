import { useState, useEffect } from 'react';
import { useNexus } from '../context/NexusContext';
import axios from 'axios';

const BACKEND_URL = `http://${window.location.hostname}:8000`;

/* ═══════════════════════════════════════════════════════════════
 * ConnectionsPage — one home for every integration.
 *  • Google Workspace  → one-click OAuth
 *  • WhatsApp / Telegram → verification-code link
 *  • Slack             → info (own bot)
 *  • Enterprise (Jira, MS Graph, …) → coming soon
 * ═══════════════════════════════════════════════════════════════ */
export default function ConnectionsPage() {
  const { currentUser } = useNexus();
  const [connections, setConnections] = useState([]);
  const [googleConnected, setGoogleConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  const empId = currentUser?.dbId;

  const authHeaders = () => {
    const token = sessionStorage.getItem('nexus_access_token');
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  const loadAll = async () => {
    try {
      const [chan, goog] = await Promise.allSettled([
        axios.get(`${BACKEND_URL}/api/v1/channels/my`, { headers: authHeaders() }),
        empId ? axios.get(`${BACKEND_URL}/api/v1/google/status/${empId}`) : Promise.reject(),
      ]);
      if (chan.status === 'fulfilled') setConnections(chan.value.data);
      else setError('Could not load your channels. Check your connection and try again.');
      if (goog.status === 'fulfilled') setGoogleConnected(!!goog.value.data.connected);
    } catch (e) {
      setError('Could not load connections.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadAll(); }, [empId]);

  const connectGoogle = () => {
    const url = `${BACKEND_URL}/api/v1/google/connect/${empId}`;
    const popup = window.open(url, 'google-oauth', 'width=520,height=640');
    const timer = setInterval(() => {
      if (popup?.closed) {
        clearInterval(timer);
        setTimeout(loadAll, 800);
      }
    }, 700);
  };

  const disconnectGoogle = async () => {
    if (!confirm('Disconnect Google Workspace?')) return;
    try {
      await axios.post(`${BACKEND_URL}/api/v1/google/disconnect/${empId}`, {}, { headers: authHeaders() });
      loadAll();
    } catch { setError('Failed to disconnect Google.'); }
  };

  return (
    <div style={{ maxWidth: 760, margin: '0 auto', padding: 24 }}>
      <h2 style={{ color: 'var(--t1)', marginBottom: 4 }}>Connections</h2>
      <p style={{ color: 'var(--t2)', fontSize: 14, marginBottom: 24 }}>
        Connect Nexus to the tools you already use. Your AI works across every channel with one shared memory.
      </p>

      {error && (
        <div style={{ color: '#ef4444', background: 'rgba(239,68,68,0.1)', padding: '10px 14px',
                      borderRadius: 8, marginBottom: 16, fontSize: 13 }}>
          {error}
        </div>
      )}

      <SectionLabel>Messaging</SectionLabel>
      <div style={{ display: 'grid', gap: 12, marginBottom: 28 }}>
        <ChannelLinker channel={WHATSAPP} onLinked={loadAll} authHeaders={authHeaders} existing={connections} />
        <ChannelLinker channel={TELEGRAM} onLinked={loadAll} authHeaders={authHeaders} existing={connections} />
        <SlackLinker onLinked={loadAll} authHeaders={authHeaders} existing={connections} />
      </div>

      <SectionLabel>Productivity</SectionLabel>
      <div style={{ display: 'grid', gap: 12, marginBottom: 28 }}>
        <div style={{ background: 'var(--bg-2)', border: `1px solid ${googleConnected ? '#34a853' : 'var(--b1)'}`,
                      borderRadius: 10, padding: 16, display: 'flex', justifyContent: 'space-between',
                      alignItems: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18 }}>🗓️</span>
            <div>
              <div style={{ color: 'var(--t1)', fontWeight: 600, fontSize: 14 }}>Google Workspace</div>
              <div style={{ color: 'var(--t3)', fontSize: 12 }}>
                {googleConnected
                  ? <span style={{ color: '#34a853' }}>✓ Connected — Gmail & Calendar</span>
                  : 'Gmail, Calendar, availability'}
              </div>
            </div>
          </div>
          {googleConnected ? (
            <button onClick={disconnectGoogle} style={ghostBtn}>Disconnect</button>
          ) : (
            <button onClick={connectGoogle} disabled={!empId}
                    style={{ ...solidBtn, background: '#34a853' }}>Connect</button>
          )}
        </div>
      </div>

      <SectionLabel>Enterprise — coming soon</SectionLabel>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 10 }}>
        {COMING_SOON.map(c => (
          <div key={c.name} style={{ background: 'var(--bg-2)', border: '1px dashed var(--b1)',
                    borderRadius: 10, padding: 14, opacity: 0.55 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 16 }}>{c.icon}</span>
              <div style={{ color: 'var(--t2)', fontSize: 13, fontWeight: 500 }}>{c.name}</div>
            </div>
            <div style={{ color: 'var(--t3)', fontSize: 11, marginTop: 4 }}>{c.desc}</div>
          </div>
        ))}
      </div>

      {connections.length > 0 && (
        <>
          <SectionLabel style={{ marginTop: 28 }}>Your linked channels</SectionLabel>
          <div style={{ display: 'grid', gap: 8 }}>
            {connections.map(c => (
              <ConnectionRow key={c.id} conn={c} onChange={loadAll}
                             authHeaders={authHeaders} setError={setError} />
            ))}
          </div>
        </>
      )}

      {loading && <div style={{ color: 'var(--t3)', fontSize: 13, marginTop: 16 }}>Loading…</div>}
    </div>
  );
}

const WHATSAPP = {
  id: 'whatsapp', name: 'WhatsApp', color: '#25D366', icon: '🟢',
  placeholder: '+1 234 567 8900',
  hint: 'A code arrives via WhatsApp.',
  prereq: 'First, send the join code to the Twilio sandbox number on WhatsApp.',
};
const TELEGRAM = {
  id: 'telegram', name: 'Telegram', color: '#0088cc', icon: '✈️',
  placeholder: 'your numeric chat ID',
  hint: 'A code arrives via the Nexus bot.',
  prereq: 'First, open the Nexus bot in Telegram and press Start.',
};
const COMING_SOON = [
  { name: 'Microsoft 365', icon: '🟦', desc: 'Teams, Outlook, Calendar' },
  { name: 'Jira',          icon: '🔷', desc: 'Issues & sprints' },
  { name: 'Linear',        icon: '▲',  desc: 'Issues & cycles' },
  { name: 'Asana',         icon: '🔺', desc: 'Tasks & projects' },
  { name: 'Notion',        icon: '⬛', desc: 'Docs & databases' },
  { name: 'GitHub',        icon: '🐙', desc: 'Repos & PRs' },
];

function SectionLabel({ children, style }) {
  return (
    <div style={{ color: 'var(--t3)', fontSize: 11, textTransform: 'uppercase',
                  letterSpacing: '0.06em', marginBottom: 10, ...style }}>
      {children}
    </div>
  );
}

function ConnectionRow({ conn, onChange, authHeaders, setError }) {
  const unlink = async () => {
    if (!confirm(`Unlink ${conn.platform}?`)) return;
    try {
      await axios.delete(`${BACKEND_URL}/api/v1/channels/${conn.id}`, { headers: authHeaders() });
      onChange();
    } catch { setError('Failed to unlink.'); }
  };
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '12px 16px', background: 'var(--bg-2)', border: '1px solid var(--b1)', borderRadius: 8 }}>
      <div>
        <div style={{ color: 'var(--t1)', fontSize: 14, fontWeight: 500, textTransform: 'capitalize' }}>{conn.platform}</div>
        <div style={{ color: 'var(--t3)', fontSize: 12 }}>
          {conn.identifier}
          {conn.verified
            ? <span style={{ color: '#10b981', marginLeft: 8 }}>✓ verified</span>
            : <span style={{ color: '#f59e0b', marginLeft: 8 }}>pending</span>}
        </div>
      </div>
      <button onClick={unlink} style={ghostBtn}>Unlink</button>
    </div>
  );
}

function ChannelLinker({ channel, onLinked, authHeaders, existing }) {
  const [stage, setStage]   = useState('idle');
  const [identifier, setId] = useState('');
  const [code, setCode]     = useState('');
  const [busy, setBusy]     = useState(false);
  const [msg, setMsg]       = useState(null);

  const alreadyLinked = existing.some(c => c.platform === channel.id && c.verified);

  const startLink = async () => {
    setBusy(true); setMsg(null);
    try {
      await axios.post(`${BACKEND_URL}/api/v1/channels/link`,
        { platform: channel.id, identifier }, { headers: authHeaders() });
      setStage('code_sent'); setMsg(`Code sent. Check your ${channel.name}.`);
    } catch (e) {
      setMsg(e.response?.data?.detail || 'Failed to send code.');
    } finally { setBusy(false); }
  };

  const confirmCode = async () => {
    setBusy(true); setMsg(null);
    try {
      await axios.post(`${BACKEND_URL}/api/v1/channels/verify`,
        { platform: channel.id, identifier, code }, { headers: authHeaders() });
      setStage('done'); setMsg('Linked successfully.'); onLinked();
      setTimeout(() => { setStage('idle'); setId(''); setCode(''); setMsg(null); }, 2000);
    } catch (e) {
      setMsg(e.response?.data?.detail || 'Invalid code.');
    } finally { setBusy(false); }
  };

  return (
    <div style={{ background: 'var(--bg-2)', border: `1px solid ${stage !== 'idle' ? channel.color : 'var(--b1)'}`,
                  borderRadius: 10, padding: 16, transition: 'border-color 0.2s' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 18 }}>{channel.icon}</span>
          <div>
            <div style={{ color: 'var(--t1)', fontWeight: 600, fontSize: 14 }}>{channel.name}</div>
            <div style={{ color: 'var(--t3)', fontSize: 12 }}>
              {alreadyLinked ? 'Already linked' : channel.hint}
            </div>
          </div>
        </div>
        {stage === 'idle' && !alreadyLinked && (
          <button onClick={() => setStage('entering')} style={{ ...solidBtn, background: channel.color }}>Connect</button>
        )}
      </div>

      {stage === 'entering' && (
        <div style={{ marginTop: 12 }}>
          <div style={{ color: '#f59e0b', fontSize: 11, marginBottom: 8 }}>{channel.prereq}</div>
          <input value={identifier} onChange={e => setId(e.target.value)} placeholder={channel.placeholder} style={inp} />
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button onClick={startLink} disabled={busy || !identifier}
                    style={{ ...solidBtn, background: channel.color, opacity: busy ? 0.6 : 1 }}>
              {busy ? 'Sending…' : 'Send code'}
            </button>
            <button onClick={() => { setStage('idle'); setMsg(null); }} style={ghostBtn}>Cancel</button>
          </div>
        </div>
      )}

      {stage === 'code_sent' && (
        <div style={{ marginTop: 12 }}>
          <input value={code} onChange={e => setCode(e.target.value)} placeholder="Enter 6-digit code" maxLength={6} style={inp} />
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button onClick={confirmCode} disabled={busy || !code}
                    style={{ ...solidBtn, background: channel.color, opacity: busy ? 0.6 : 1 }}>
              {busy ? 'Verifying…' : 'Verify'}
            </button>
            <button onClick={() => { setStage('idle'); setCode(''); setMsg(null); }} style={ghostBtn}>Cancel</button>
          </div>
        </div>
      )}

      {msg && (
        <div style={{ marginTop: 10, fontSize: 12, color: stage === 'done' ? '#10b981' : 'var(--t2)' }}>{msg}</div>
      )}
    </div>
  );
}

function SlackLinker({ onLinked, authHeaders, existing }) {
  const [stage, setStage] = useState('idle');   // idle | code_shown
  const [code, setCode]   = useState('');
  const [busy, setBusy]   = useState(false);
  const [msg, setMsg]     = useState(null);

  const alreadyLinked = existing.some(c => c.platform === 'slack' && c.verified);

  const startLink = async () => {
    setBusy(true); setMsg(null);
    try {
      const res = await axios.post(`${BACKEND_URL}/api/v1/channels/slack/start-link`,
        {}, { headers: authHeaders() });
      setCode(res.data.code);
      setStage('code_shown');
      // Poll for the link to complete (user DMs the bot from Slack)
      const timer = setInterval(async () => {
        try {
          const my = await axios.get(`${BACKEND_URL}/api/v1/channels/my`, { headers: authHeaders() });
          if (my.data.some(c => c.platform === 'slack' && c.verified)) {
            clearInterval(timer);
            setMsg('Linked successfully.');
            setStage('idle');
            onLinked();
          }
        } catch { /* keep polling */ }
      }, 2500);
      // Stop polling after 5 minutes
      setTimeout(() => clearInterval(timer), 300000);
    } catch (e) {
      setMsg(e.response?.data?.detail || 'Failed to start Slack linking.');
    } finally { setBusy(false); }
  };

  return (
    <div style={{ background: 'var(--bg-2)', border: `1px solid ${stage !== 'idle' ? '#611f69' : 'var(--b1)'}`,
                  borderRadius: 10, padding: 16, transition: 'border-color 0.2s' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 18 }}>💬</span>
          <div>
            <div style={{ color: 'var(--t1)', fontWeight: 600, fontSize: 14 }}>Slack</div>
            <div style={{ color: 'var(--t3)', fontSize: 12 }}>
              {alreadyLinked ? 'Connected — DM the bot anytime' : 'Link your Slack to chat with your AI there'}
            </div>
          </div>
        </div>
        {stage === 'idle' && !alreadyLinked && (
          <button onClick={startLink} disabled={busy} style={{ ...solidBtn, background: '#611f69' }}>
            {busy ? '…' : 'Connect'}
          </button>
        )}
        {alreadyLinked && <span style={{ color: '#34a853', fontSize: 12 }}>✓ Active</span>}
      </div>

      {stage === 'code_shown' && (
        <div style={{ marginTop: 12 }}>
          <div style={{ color: 'var(--t2)', fontSize: 12, marginBottom: 8 }}>
            Open Slack, DM the <b>Nexus</b> bot this code:
          </div>
          <div style={{ background: 'var(--bg-0)', border: '1px dashed #611f69', borderRadius: 8,
                        padding: '12px', textAlign: 'center', fontSize: 24, fontWeight: 700,
                        letterSpacing: '0.2em', color: 'var(--t1)', fontFamily: 'monospace' }}>
            {code}
          </div>
          <div style={{ color: 'var(--t3)', fontSize: 11, marginTop: 8 }}>
            Waiting for you to send it… this updates automatically once linked.
          </div>
          <button onClick={() => { setStage('idle'); setMsg(null); }}
                  style={{ ...ghostBtn, marginTop: 8 }}>Cancel</button>
        </div>
      )}

      {msg && (
        <div style={{ marginTop: 10, fontSize: 12, color: msg.includes('success') ? '#10b981' : 'var(--t2)' }}>{msg}</div>
      )}
    </div>
  );
}

const inp = {
  width: '100%', background: 'var(--bg-0)', color: 'var(--t1)', border: '1px solid var(--b1)',
  borderRadius: 6, padding: '8px 10px', fontSize: 13, fontFamily: 'inherit', boxSizing: 'border-box',
};
const solidBtn = {
  border: 'none', borderRadius: 6, padding: '8px 16px', fontSize: 13, fontWeight: 500,
  color: '#fff', cursor: 'pointer',
};
const ghostBtn = {
  background: 'transparent', color: 'var(--t3)', border: '1px solid var(--b1)',
  borderRadius: 6, padding: '6px 12px', fontSize: 12, cursor: 'pointer',
};