import React from 'react';
import { ICON, ComingSoon } from '../components/ui/SharedUI';
export default function MeetingsPage() {
  return (
    <div className="animate-in">
      <div style={{ opacity: 0.4, pointerEvents: 'none', filter: 'blur(0.5px)', marginBottom: 24 }}>
        {[1,2,3].map(i => (
          <div key={i} className="card" style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{ width: 44, height: 44, borderRadius: 10, background: 'var(--p-bg)', border: '1px solid var(--p-border)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--p)', lineHeight: 1 }}>{10+i}</div>
              <div style={{ fontSize: 9, color: 'var(--p-muted)', textTransform: 'uppercase' }}>May</div>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--t1)', marginBottom: 2 }}>Team Standup {i}</div>
              <div style={{ fontSize: 12, color: 'var(--t3)' }}>9:00 AM · 30 min · 4 attendees</div>
            </div>
            <span className="badge badge-indigo">Upcoming</span>
          </div>
        ))}
      </div>
      <ComingSoon icon={ICON.meetings} title="Meeting Intelligence Coming Soon" desc="Transcriptions, AI summaries, action item extraction, and calendar conflict detection." />
    </div>
  );
}
