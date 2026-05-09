import React from 'react';
import { Icon, ICON, ComingSoon } from '../components/ui/SharedUI';

export default function GoogleWorkspace() {
  const mockEmails = [
    { from: 'Sarah Chen', sub: 'Q3 roadmap review', time: '9:41 AM', unread: true },
    { from: 'James Wright', sub: 'Sprint planning notes', time: 'Yesterday', unread: true },
    { from: 'Layla Hassan', sub: 'Product feedback summary', time: 'Mon', unread: false },
    { from: 'Alex Rivera', sub: 'Infrastructure update', time: 'Mon', unread: false },
  ];

  return (
    <div className="animate-in">
      {/* Tabs */}
      <div style={{ display: 'flex', gap: 2, background: 'var(--bg-2)', border: '1px solid var(--b1)', borderRadius: 10, padding: 4, width: 'fit-content', marginBottom: 16 }}>
        {['Gmail', 'Calendar', 'Drive'].map(t => (
          <button key={t} style={{ padding: '5px 16px', borderRadius: 7, fontSize: 13, fontWeight: 500, background: t === 'Gmail' ? 'var(--bg-4)' : 'transparent', color: t === 'Gmail' ? 'var(--t1)' : 'var(--t3)', border: 'none', cursor: 'default' }}>{t}</button>
        ))}
      </div>

      {/* Gmail skeleton */}
      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 0, height: 480, borderRadius: 12, border: '1px solid var(--b1)', overflow: 'hidden', opacity: 0.4, pointerEvents: 'none', filter: 'blur(0.5px)' }}>
        <div style={{ borderRight: '1px solid var(--b1)', display: 'flex', flexDirection: 'column' }}>
          {mockEmails.map((e, i) => (
            <div key={i} style={{ padding: '12px 16px', borderBottom: '1px solid var(--b0)', display: 'flex', gap: 10, alignItems: 'flex-start', background: i === 0 ? 'var(--bg-3)' : 'transparent' }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: e.unread ? 'var(--p)' : 'transparent', flexShrink: 0, marginTop: 5 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: e.unread ? 600 : 400, color: 'var(--t1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.from}</div>
                <div style={{ fontSize: 12, color: 'var(--t3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.sub}</div>
              </div>
              <div style={{ fontSize: 11, color: 'var(--t3)', flexShrink: 0 }}>{e.time}</div>
            </div>
          ))}
        </div>
        <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ height: 20, background: 'var(--bg-3)', borderRadius: 6, width: '60%' }} />
          <div style={{ height: 14, background: 'var(--bg-3)', borderRadius: 4, width: '40%' }} />
          <div style={{ height: 1, background: 'var(--b1)', margin: '4px 0' }} />
          <div style={{ height: 14, background: 'var(--bg-3)', borderRadius: 4, width: '100%' }} />
          <div style={{ height: 14, background: 'var(--bg-3)', borderRadius: 4, width: '90%' }} />
          <div style={{ height: 14, background: 'var(--bg-3)', borderRadius: 4, width: '95%' }} />
        </div>
      </div>

      <div style={{ marginTop: -200, position: 'relative', zIndex: 10 }}>
        <ComingSoon
          icon={ICON.gmail}
          title="Google Workspace — In Progress"
          desc="Gmail integration is built on the backend. Connect your account from the AI Co-Pilot and say 'check my emails' to get started now."
        />
      </div>
    </div>
  );
}
