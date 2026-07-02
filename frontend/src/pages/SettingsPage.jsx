import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { useNexus } from '../context/NexusContext';
import { Spinner } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';

const SECTION = ({ title, children }) => (
  <div style={{ marginBottom: 24 }}>
    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t2)', marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid var(--b1)' }}>{title}</div>
    {children}
  </div>
);

const ROW = ({ label, value }) => (
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '9px 0' }}>
    <div style={{ fontSize: 13, color: 'var(--t3)' }}>{label}</div>
    <div style={{ fontSize: 13, color: 'var(--t1)', fontWeight: 500, textTransform: label === 'Role' ? 'capitalize' : 'none' }}>{value || '—'}</div>
  </div>
);

export default function SettingsPage() {
  const { BACKEND_URL, currentUser, handleDisconnect } = useNexus();
  const [me,     setMe]     = useState(null);
  const [google, setGoogle] = useState(null);
  const [cur,    setCur]    = useState('');
  const [nw,     setNw]     = useState('');
  const [msg,    setMsg]    = useState('');
  const [err,    setErr]    = useState('');
  const [busy,   setBusy]   = useState(false);

  useEffect(() => {
    (async () => {
      try { const r = await axios.get(`${BACKEND_URL}/api/v1/auth/me`); setMe(r.data); } catch {}
      if (currentUser?.dbId) {
        try { const g = await axios.get(`${BACKEND_URL}/api/v1/google/status/${currentUser.dbId}`); setGoogle(!!g.data?.connected); }
        catch { setGoogle(false); }
      }
    })();
  }, [BACKEND_URL, currentUser]);

  const changePw = async (e) => {
    e.preventDefault();
    setMsg(''); setErr(''); setBusy(true);
    try {
      await axios.post(`${BACKEND_URL}/api/v1/auth/change-password`, { current_password: cur, new_password: nw });
      setMsg('Passcode updated.'); setCur(''); setNw('');
    } catch (e2) {
      setErr(e2.response?.data?.detail || 'Could not change passcode.');
    }
    setBusy(false);
  };

  return (
    <div className="animate-in" style={{ maxWidth: 560 }}>
      <SECTION title="Profile">
        <ROW label="Name"  value={safeStr(me?.name || currentUser?.name)} />
        <ROW label="Role"  value={safeStr(me?.role || currentUser?.role)} />
        <ROW label="Team"  value={safeStr(me?.team || currentUser?.team)} />
        <ROW label="Email" value={safeStr(me?.email)} />
      </SECTION>

      <SECTION title="Change passcode">
        <form onSubmit={changePw} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <input type="password" className="nx-input" placeholder="Current passcode" value={cur} onChange={e => setCur(e.target.value)} required />
          <input type="password" className="nx-input" placeholder="New passcode" value={nw} onChange={e => setNw(e.target.value)} required />
          {err && <div style={{ fontSize: 12.5, color: 'var(--red)' }}>{err}</div>}
          {msg && <div style={{ fontSize: 12.5, color: 'var(--green)' }}>{msg}</div>}
          <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !cur || !nw} style={{ alignSelf: 'flex-start' }}>
            {busy ? <Spinner size={13} /> : 'Update passcode'}
          </button>
        </form>
      </SECTION>

      <SECTION title="Google Workspace">
        <ROW label="Connection" value={google === null ? '…' : google ? 'Connected' : 'Not connected'} />
        <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 4 }}>Connect or disconnect on the Connections page.</div>
      </SECTION>

      <SECTION title="Session">
        <button className="btn btn-ghost btn-sm" onClick={handleDisconnect}>Sign out</button>
      </SECTION>
    </div>
  );
}
