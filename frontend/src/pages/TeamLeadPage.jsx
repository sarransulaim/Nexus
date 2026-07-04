import React, { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { BACKEND_URL } from '../config';
import { EmptyState, Spinner, ICON } from '../components/ui/SharedUI';

/* "My Team" — the team lead's at-a-glance dashboard. Pure SQL + pixels:
   zero AI calls, so checking on the team is free. Ask the Co-Pilot only
   when you need reasoning ("who should take this?"), not numbers. */

const Tile = ({ label, value, tone, icon }) => (
  <div className="card" style={{ flex: 1, minWidth: 130, padding: '14px 16px' }}>
    <div style={{ fontSize: 11, color: 'var(--t3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>{label}</div>
    <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--t1)', lineHeight: 1, display: 'flex', alignItems: 'baseline', gap: 8 }}>
      {value}
      {tone && <span className={`badge ${tone}`} style={{ fontSize: 10 }}>{icon}</span>}
    </div>
  </div>
);

function MemberRow({ m, maxOpen }) {
  const pct = maxOpen > 0 ? Math.max(4, Math.round((m.open / maxOpen) * 100)) : 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 0', borderBottom: '1px solid var(--b1)' }}>
      <div style={{ width: 170, minWidth: 120 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {m.name}{m.is_lead ? <span style={{ color: 'var(--t3)', fontWeight: 400 }}> (you)</span> : ''}
        </div>
        <div style={{ fontSize: 11, color: 'var(--t3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.role}</div>
      </div>
      {/* workload bar — one hue (magnitude), thin mark, rounded end, value as text */}
      <div style={{ flex: 1, height: 10, background: 'var(--bg-3)', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{ width: `${m.open > 0 ? pct : 0}%`, height: '100%', background: 'var(--p)', borderRadius: 4, transition: 'width .3s' }} />
      </div>
      <div style={{ width: 150, fontSize: 12, color: 'var(--t2)', textAlign: 'right', whiteSpace: 'nowrap' }}>
        {m.open} open · {m.done} done
        {m.overdue > 0 && <span className="badge badge-amber" style={{ marginLeft: 6 }}>⚠ {m.overdue} overdue</span>}
      </div>
    </div>
  );
}

const List = ({ title, rows, render, empty }) => (
  <div className="card" style={{ flex: 1, minWidth: 280 }}>
    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)', marginBottom: 8 }}>{title}</div>
    {rows.length === 0
      ? <div style={{ fontSize: 12, color: 'var(--t3)' }}>{empty}</div>
      : rows.map(render)}
  </div>
);

export default function TeamLeadPage() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await axios.get(`${BACKEND_URL}/api/v1/team-lead/overview`);
      setData(res.data); setErr(null);
    } catch (e) {
      setErr(e.response?.data?.detail || 'Could not load the team overview.');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60000);   // keep it fresh — still zero AI cost
    return () => clearInterval(t);
  }, [load]);

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spinner size={20} /></div>;
  if (err) return <EmptyState icon={ICON.team} title="Team view unavailable" desc={err} />;
  if (!data) return null;

  const maxOpen = Math.max(1, ...data.members.map(m => m.open));
  const s = data.stats;

  return (
    <div className="animate-in" style={{ maxWidth: 980 }}>
      {/* stat tiles */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        <Tile label="Open tasks" value={s.open} />
        <Tile label="Overdue" value={s.overdue} tone={s.overdue > 0 ? 'badge-amber' : ''} icon={s.overdue > 0 ? '⚠ needs attention' : ''} />
        <Tile label="Due in 7 days" value={s.due_soon} />
        <Tile label="Completion" value={`${s.completion_pct}%`} />
      </div>

      {/* per-member workload */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)' }}>Workload — {data.team}</div>
          <div style={{ fontSize: 11, color: 'var(--t3)' }}>bar = open tasks</div>
        </div>
        {data.members.map(m => <MemberRow key={m.id} m={m} maxOpen={maxOpen} />)}
      </div>

      {/* lists */}
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 14 }}>
        <List title="Overdue" rows={data.overdue} empty="Nothing overdue. 🎉"
          render={t => (
            <div key={t.id} style={{ fontSize: 12, color: 'var(--t2)', padding: '5px 0', borderBottom: '1px solid var(--b1)' }}>
              <span style={{ color: 'var(--t1)' }}>{t.title}</span>
              <span style={{ color: 'var(--t3)' }}> — {t.owner} · due {t.due}</span>
            </div>
          )} />
        <List title="Due this week" rows={data.due_soon} empty="Nothing due in the next 7 days."
          render={t => (
            <div key={t.id} style={{ fontSize: 12, color: 'var(--t2)', padding: '5px 0', borderBottom: '1px solid var(--b1)' }}>
              <span style={{ color: 'var(--t1)' }}>{t.title}</span>
              <span style={{ color: 'var(--t3)' }}> — {t.owner} · due {t.due}</span>
            </div>
          )} />
      </div>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        <List title="Pending escalations from the team" rows={data.escalations} empty="No pending escalations."
          render={e => (
            <div key={e.id} style={{ fontSize: 12, color: 'var(--t2)', padding: '5px 0', borderBottom: '1px solid var(--b1)' }}>
              <span style={{ color: 'var(--t1)' }}>{e.from}</span>
              <span style={{ color: 'var(--t3)' }}> — {e.reason}</span>
            </div>
          )} />
        <List title="Upcoming team meetings" rows={data.meetings} empty="No meetings scheduled."
          render={m => (
            <div key={m.id} style={{ fontSize: 12, color: 'var(--t2)', padding: '5px 0', borderBottom: '1px solid var(--b1)' }}>
              <span style={{ color: 'var(--t1)' }}>{m.topic}</span>
              <span style={{ color: 'var(--t3)' }}> — {m.date}{m.time ? ` · ${m.time}` : ''}</span>
            </div>
          )} />
      </div>
    </div>
  );
}
