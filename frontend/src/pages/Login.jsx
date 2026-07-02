import React, { useState, useEffect } from 'react';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, Spinner } from '../components/ui/SharedUI';

import { BACKEND_URL } from '../config';

const labelStyle = { display: 'block', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--t3)', marginBottom: 6 };

export default function Login() {
  const { handleLogin } = useNexus();
  const [mode,    setMode]    = useState('checking');   // checking | login | setup
  const [name,    setName]    = useState('');
  const [pass,    setPass]    = useState('');
  const [secret,  setSecret]  = useState('');
  const [error,   setError]   = useState('');
  const [info,    setInfo]    = useState('');
  const [loading, setLoading] = useState(false);

  // First run? If no manager exists yet, show the Setup screen instead of Login.
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/v1/auth/status`);
        const data = await res.json();
        setMode(data.initialized ? 'login' : 'setup');
      } catch {
        setMode('login');   // if the check fails, fall back to login
      }
    })();
  }, []);

  const onLogin = async e => {
    e.preventDefault();
    setError(''); setLoading(true);
    const err = await handleLogin(name.trim(), pass);
    if (err) setError(err);
    setLoading(false);
  };

  const onSetup = async e => {
    e.preventDefault();
    setError(''); setInfo(''); setLoading(true);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/auth/setup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), password: pass, secret_key: secret.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || 'Setup failed. Check the setup secret and try again.');
      } else {
        setInfo('Manager account created — log in below.');
        setMode('login'); setPass(''); setSecret('');
      }
    } catch {
      setError('Could not reach the server.');
    }
    setLoading(false);
  };

  const isSetup = mode === 'setup';

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-0)', padding: 24, position: 'relative' }}>
      <div style={{ position: 'absolute', top: '30%', left: '50%', transform: 'translate(-50%,-50%)', width: 600, height: 400, background: 'radial-gradient(ellipse, rgba(99,102,241,0.06) 0%, transparent 65%)', pointerEvents: 'none' }} />

      <div style={{ width: '100%', maxWidth: 360, position: 'relative', zIndex: 1 }}>
        {/* Logo mark */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: 32 }}>
          <div style={{ width: 44, height: 44, borderRadius: 12, background: 'var(--p-bg)', border: '1px solid var(--p-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--p)', marginBottom: 16 }}>
            <Icon path={ICON.nexus} size={22} />
          </div>
          <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--t1)', letterSpacing: '-0.02em', marginBottom: 4 }}>Nexus Core</div>
          <div style={{ fontSize: 12, color: 'var(--t3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            {isSetup ? 'First-run setup' : 'Enterprise AI Chief of Staff'}
          </div>
        </div>

        {mode === 'checking' ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 32 }}><Spinner size={20} /></div>
        ) : (
          <div style={{ background: 'var(--bg-2)', border: '1px solid var(--b1)', borderRadius: 14, padding: 28, boxShadow: '0 8px 32px rgba(0,0,0,0.4)' }}>
            {isSetup && (
              <div style={{ fontSize: 13, color: 'var(--t3)', marginBottom: 18, lineHeight: 1.6 }}>
                No manager account exists yet. Create the first one to get started — you'll need the <strong style={{ color: 'var(--t2)' }}>setup secret</strong> from your instance's environment.
              </div>
            )}

            <form onSubmit={isSetup ? onSetup : onLogin} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <label style={labelStyle}>{isSetup ? 'Manager name' : 'Access ID'}</label>
                <input type="text" required value={name} onChange={e => setName(e.target.value)} placeholder="Your name" className="nx-input" autoFocus />
              </div>

              <div>
                <label style={labelStyle}>{isSetup ? 'Create a passcode' : 'Passcode'}</label>
                <input type="password" required value={pass} onChange={e => setPass(e.target.value)} placeholder="••••••••" className="nx-input" />
              </div>

              {isSetup && (
                <div>
                  <label style={labelStyle}>Setup secret</label>
                  <input type="password" required value={secret} onChange={e => setSecret(e.target.value)} placeholder="From your SETUP_SECRET env var" className="nx-input" />
                </div>
              )}

              {error && (
                <div style={{ padding: '10px 14px', borderRadius: 8, background: 'var(--red-bg)', border: '1px solid var(--red-border)', fontSize: 13, color: 'var(--red)' }}>{error}</div>
              )}
              {info && !error && (
                <div style={{ padding: '10px 14px', borderRadius: 8, background: 'var(--green-bg)', border: '1px solid var(--green-border)', fontSize: 13, color: 'var(--green)' }}>{info}</div>
              )}

              <button type="submit" disabled={loading} className="btn btn-primary btn-lg" style={{ width: '100%', marginTop: 4 }}>
                {loading
                  ? (<><Spinner size={15} /> {isSetup ? 'Creating...' : 'Authenticating...'}</>)
                  : (isSetup ? 'Create manager & continue' : 'Initialize Session')}
              </button>
            </form>
          </div>
        )}

        <div style={{ textAlign: 'center', marginTop: 20, fontSize: 11, color: 'var(--t4)' }}>
          Secured by Nexus Core Enterprise
        </div>
      </div>
    </div>
  );
}
