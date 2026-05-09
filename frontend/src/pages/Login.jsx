import React, { useState } from 'react';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, Spinner } from '../components/ui/SharedUI';

export default function Login() {
  const { handleLogin } = useNexus();
  const [name,    setName]    = useState('');
  const [pass,    setPass]    = useState('');
  const [error,   setError]   = useState('');
  const [loading, setLoading] = useState(false);

  const onSubmit = async e => {
    e.preventDefault();
    setError('');
    setLoading(true);
    const err = await handleLogin(name.trim(), pass);
    if (err) setError(err);
    setLoading(false);
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-0)', padding: 24, position: 'relative' }}>
      {/* Subtle radial gradient behind the card */}
      <div style={{ position: 'absolute', top: '30%', left: '50%', transform: 'translate(-50%,-50%)', width: 600, height: 400, background: 'radial-gradient(ellipse, rgba(99,102,241,0.06) 0%, transparent 65%)', pointerEvents: 'none' }} />

      <div style={{ width: '100%', maxWidth: 360, position: 'relative', zIndex: 1 }}>
        {/* Logo mark */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: 32 }}>
          <div style={{ width: 44, height: 44, borderRadius: 12, background: 'var(--p-bg)', border: '1px solid var(--p-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--p)', marginBottom: 16 }}>
            <Icon path={ICON.nexus} size={22} />
          </div>
          <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--t1)', letterSpacing: '-0.02em', marginBottom: 4 }}>Nexus Core</div>
          <div style={{ fontSize: 12, color: 'var(--t3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Enterprise AI Chief of Staff</div>
        </div>

        {/* Card */}
        <div style={{ background: 'var(--bg-2)', border: '1px solid var(--b1)', borderRadius: 14, padding: 28, boxShadow: '0 8px 32px rgba(0,0,0,0.4)' }}>
          <form onSubmit={onSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--t3)', marginBottom: 6 }}>
                Access ID
              </label>
              <input
                type="text"
                required
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="Your name"
                className="nx-input"
                autoFocus
              />
            </div>

            <div>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--t3)', marginBottom: 6 }}>
                Passcode
              </label>
              <input
                type="password"
                required
                value={pass}
                onChange={e => setPass(e.target.value)}
                placeholder="••••••••"
                className="nx-input"
              />
            </div>

            {error && (
              <div style={{ padding: '10px 14px', borderRadius: 8, background: 'var(--red-bg)', border: '1px solid var(--red-border)', fontSize: 13, color: 'var(--red)' }}>
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="btn btn-primary btn-lg"
              style={{ width: '100%', marginTop: 4 }}
            >
              {loading ? (
                <><Spinner size={15} /> Authenticating...</>
              ) : 'Initialize Session'}
            </button>
          </form>
        </div>

        <div style={{ textAlign: 'center', marginTop: 20, fontSize: 11, color: 'var(--t4)' }}>
          Secured by Nexus Core Enterprise
        </div>
      </div>
    </div>
  );
}
