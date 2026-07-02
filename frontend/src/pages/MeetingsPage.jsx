import React, { useMemo } from 'react';
import { useNexus } from '../context/NexusContext';
import { ICON, EmptyState } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

export default function MeetingsPage() {
  const { meetings, currentUser } = useNexus();
  const isEmployee = currentUser?.role === 'Employee';
  const myId = String(currentUser?.dbId ?? '');

  // Managers see all company meetings; employees ("My Calendar") see only theirs.
  const visible = useMemo(() => {
    const all = meetings || [];
    if (!isEmployee) return all;
    return all.filter(m => {
      const ids = m.attendee_ids ? String(m.attendee_ids).split(',').map(s => s.trim()) : [];
      return ids.includes(myId) || (m.attendees || []).some(a => String(a.id) === myId);
    });
  }, [meetings, isEmployee, myId]);

  const sorted = useMemo(() =>
    [...visible].sort((a, b) => String(a.scheduled_date || '').localeCompare(String(b.scheduled_date || ''))),
    [visible]
  );

  if (!sorted.length) {
    return <EmptyState icon={ICON.meetings} title="No meetings scheduled" desc={isEmployee ? "You're not on any scheduled meetings yet." : "Ask Nexus to schedule one (“schedule a standup tomorrow at 10am”) — meetings show up here as they're created."} />;
  }

  return (
    <div className="animate-in" style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 700 }}>
      {sorted.map(m => {
        const d = m.scheduled_date ? new Date(m.scheduled_date) : null;
        const valid = d && !isNaN(d);
        const names = (m.attendees || []).map(a => a.name).filter(Boolean);
        return (
          <div key={m.id} className="card" style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{ width: 46, height: 46, borderRadius: 10, background: 'var(--p-bg)', border: '1px solid var(--p-border)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--p)', lineHeight: 1, fontFamily: 'var(--font-mono)' }}>{valid ? d.getDate() : '—'}</div>
              <div style={{ fontSize: 9, color: 'var(--p-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{valid ? MONTHS[d.getMonth()] : ''}</div>
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{safeStr(m.topic)}</div>
              <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 2, fontFamily: 'var(--font-mono)' }}>
                {safeStr(m.scheduled_time)}{m.duration_minutes ? ` · ${m.duration_minutes} min` : ''}{m.location ? ` · ${safeStr(m.location)}` : ''}
              </div>
              {names.length > 0 && (
                <div style={{ fontSize: 11.5, color: 'var(--t3)', marginTop: 3 }}>
                  {names.length} attendee{names.length !== 1 ? 's' : ''}: {names.slice(0, 4).join(', ')}{names.length > 4 ? '…' : ''}
                </div>
              )}
            </div>
            <span className="badge badge-indigo" style={{ flexShrink: 0 }}>Scheduled</span>
          </div>
        );
      })}
    </div>
  );
}
