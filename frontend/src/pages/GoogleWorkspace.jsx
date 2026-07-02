import React, { useEffect, useState, useCallback, useRef } from 'react';
import axios from 'axios';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, Spinner } from '../components/ui/SharedUI';

const CAPABILITIES = [
  { icon: ICON.gmail,    title: 'Gmail',    desc: 'Read, triage, summarize, and draft replies — ask the Co-Pilot to “summarize my inbox”.' },
  { icon: ICON.calendar, title: 'Calendar', desc: 'See your schedule, spot conflicts, and book meetings in plain English.' },
];

export default function GoogleWorkspace() {
  const { BACKEND_URL, currentUser, handleDbAction } = useNexus();
  const empId = currentUser?.dbId;
  const [connected, setConnected] = useState(null);
  const timersRef = useRef([]);
  useEffect(() => () => timersRef.current.forEach(id => { clearInterval(id); clearTimeout(id); }), []);

  const load = useCallback(() => {
    if (!empId) { setConnected(false); return; }
    axios.get(`${BACKEND_URL}/api/v1/google/status/${empId}`)
      .then(r => setConnected(!!r.data?.connected))
      .catch(() => setConnected(false));
  }, [BACKEND_URL, empId]);
  useEffect(() => { load(); }, [load]);

  const connect = async () => {
    try {
      const res = await axios.get(`${BACKEND_URL}/api/v1/google/connect/${empId}`);
      const url = res.data?.auth_url;
      if (!url) return;
      const popup = window.open(url, 'google-oauth', 'width=520,height=640');
      if (!popup) return;   // popup blocked — nothing to poll (no spinning timer)
      const timer = setInterval(() => { if (popup.closed) { clearInterval(timer); setTimeout(load, 800); } }, 700);
      const stop = setTimeout(() => clearInterval(timer), 180000);
      timersRef.current.push(timer, stop);
    } catch {}
  };

  if (connected === null) return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spinner size={20} /></div>;

  return (
    <div className="animate-in" style={{ maxWidth: 680 }}>
      <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, marginBottom: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 42, height: 42, borderRadius: 10, background: '#4285F418', border: '1px solid #4285F430', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <Icon path={ICON.gmail} size={20} />
          </div>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>Google Workspace</div>
            <div style={{ fontSize: 12, color: connected ? 'var(--green)' : 'var(--t3)', marginTop: 1 }}>
              {connected ? '● Connected' : 'Not connected'}
            </div>
          </div>
        </div>
        {!connected && <button className="btn btn-primary btn-sm" onClick={connect}>Connect Google</button>}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {CAPABILITIES.map(c => (
          <div key={c.title} className="card" style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
            <div style={{ width: 34, height: 34, borderRadius: 8, background: 'var(--p-bg)', border: '1px solid var(--p-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <Icon path={c.icon} size={16} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{c.title}</div>
              <div style={{ fontSize: 12.5, color: 'var(--t3)', marginTop: 2 }}>{c.desc}</div>
            </div>
          </div>
        ))}
      </div>

      {connected ? (
        <div style={{ display: 'flex', gap: 8, marginTop: 16, flexWrap: 'wrap' }}>
          <button className="btn btn-secondary btn-sm" onClick={() => handleDbAction('Summarize my unread emails')}>Summarize my inbox</button>
          <button className="btn btn-secondary btn-sm" onClick={() => handleDbAction("What's on my calendar today?")}>Today's calendar</button>
        </div>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 16 }}>
          Connect your account above, then ask the Co-Pilot to read or act on your email and calendar.
        </div>
      )}
    </div>
  );
}
