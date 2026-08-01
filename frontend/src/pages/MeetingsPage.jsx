import React, { useMemo } from 'react';
import { useNexus } from '../context/NexusContext';
import { ICON, EmptyState } from '../components/ui/SharedUI';
import { safeStr, parseLocalDay, startOfToday } from '../utils/helpers';

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

/* Human label for how far away a meeting is — "Today"/"Tomorrow" are what
   people actually scan for. */
function whenLabel(day, isPast) {
  if (!day) return { text: 'No date', tone: 'badge' };
  const diff = Math.round((day - startOfToday()) / 86400000);
  if (diff === 0) return { text: 'Today', tone: 'badge badge-amber' };
  if (diff === 1) return { text: 'Tomorrow', tone: 'badge badge-indigo' };
  if (diff > 1) return { text: `in ${diff} days`, tone: 'badge badge-indigo' };
  if (isPast || diff < 0) {
    const ago = Math.abs(diff);
    return { text: ago === 1 ? 'Yesterday' : `${ago} days ago`, tone: 'badge' };
  }
  return { text: 'Scheduled', tone: 'badge badge-indigo' };
}

function MeetingCard({ m, past }) {
  const day = parseLocalDay(m.scheduled_date);
  const names = (m.attendees || []).map(a => a.name).filter(Boolean);
  const when = whenLabel(day, m.is_past);
  return (
    <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 14, opacity: past ? 0.62 : 1 }}>
      <div style={{ width: 46, height: 46, borderRadius: 10, background: past ? 'var(--bg-3)' : 'var(--p-bg)',
                    border: `1px solid ${past ? 'var(--b1)' : 'var(--p-border)'}`, display: 'flex',
                    flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <div style={{ fontSize: 17, fontWeight: 700, color: past ? 'var(--t3)' : 'var(--p)', lineHeight: 1, fontFamily: 'var(--font-mono)' }}>
          {day ? day.getDate() : '—'}
        </div>
        <div style={{ fontSize: 9, color: past ? 'var(--t3)' : 'var(--p-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          {day ? MONTHS[day.getMonth()] : ''}
        </div>
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
      <span className={when.tone} style={{ flexShrink: 0 }}>{when.text}</span>
    </div>
  );
}

const Section = ({ title, count, children }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
    <div style={{ fontSize: 11, color: 'var(--t3)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
      {title} · {count}
    </div>
    {children}
  </div>
);

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

  // Split once the day is over. Trust the server's is_past when present, but
  // fall back to a local-date compare so this still works if the field is
  // missing (older cached payloads) — and so a page left open overnight
  // re-groups correctly on the next render.
  const { upcoming, past, undated } = useMemo(() => {
    const today = startOfToday();
    const up = [], old = [], none = [];
    for (const m of visible) {
      const day = parseLocalDay(m.scheduled_date);
      if (!day) { none.push(m); continue; }
      const isPast = m.is_past !== undefined ? m.is_past : day < today;
      (isPast ? old : up).push(m);
    }
    const byDate = (dir) => (a, b) =>
      dir * String(a.scheduled_date || '').localeCompare(String(b.scheduled_date || ''));
    up.sort(byDate(1));    // soonest first
    old.sort(byDate(-1));  // most recent first
    return { upcoming: up, past: old, undated: none };
  }, [visible]);

  if (!visible.length) {
    return <EmptyState icon={ICON.meetings} title="No meetings scheduled" desc={isEmployee ? "You're not on any scheduled meetings yet." : "Ask Nexus to schedule one (“schedule a standup tomorrow at 10am”) — meetings show up here as they're created."} />;
  }

  return (
    <div className="animate-in" style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 700 }}>
      {upcoming.length > 0 && (
        <Section title="Upcoming" count={upcoming.length}>
          {upcoming.map(m => <MeetingCard key={m.id} m={m} />)}
        </Section>
      )}

      {undated.length > 0 && (
        <Section title="No date set" count={undated.length}>
          {undated.map(m => <MeetingCard key={m.id} m={m} />)}
        </Section>
      )}

      {upcoming.length === 0 && undated.length === 0 && (
        <div style={{ fontSize: 13, color: 'var(--t3)' }}>
          Nothing coming up. Past meetings are below.
        </div>
      )}

      {past.length > 0 && (
        <Section title="Past" count={past.length}>
          {past.map(m => <MeetingCard key={m.id} m={m} past />)}
        </Section>
      )}
    </div>
  );
}
