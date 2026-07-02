import React, { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, EmptyState, Spinner } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';

export default function GoalsPage() {
  const { BACKEND_URL } = useNexus();
  const [goals,   setGoals]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [adding,  setAdding]  = useState(false);
  const [title,   setTitle]   = useState('');

  const load = useCallback(async () => {
    try { const res = await axios.get(`${BACKEND_URL}/api/v1/goals/`); setGoals(res.data?.goals || []); }
    catch { setGoals([]); }
    finally { setLoading(false); }
  }, [BACKEND_URL]);
  useEffect(() => { load(); }, [load]);

  const createGoal = async (e) => {
    e.preventDefault();
    if (!title.trim()) return;
    try { await axios.post(`${BACKEND_URL}/api/v1/goals/`, { title: title.trim() }); setTitle(''); setAdding(false); load(); } catch {}
  };

  const bump = async (g, delta) => {
    const next = Math.max(0, Math.min(100, Math.round((g.progress_pct || 0) + delta)));
    try { await axios.patch(`${BACKEND_URL}/api/v1/goals/${g.id}/progress`, { progress_pct: next }); load(); } catch {}
  };

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spinner size={20} /></div>;

  return (
    <div className="animate-in">
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
        <button className="btn btn-secondary btn-sm" onClick={() => setAdding(a => !a)}>
          <Icon path={ICON.plus} size={13} /> New goal
        </button>
      </div>

      {adding && (
        <form onSubmit={createGoal} className="card" style={{ display: 'flex', gap: 8, marginBottom: 16, padding: 14 }}>
          <input className="nx-input" value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Ship v1.0 by end of quarter" autoFocus style={{ flex: 1 }} />
          <button type="submit" className="btn btn-primary btn-sm" disabled={!title.trim()}>Create</button>
        </form>
      )}

      {goals.length === 0 ? (
        <EmptyState icon={ICON.goals} title="No goals yet" desc="Create an objective above, or ask Nexus to set one and link tasks to it." />
      ) : (
        <div className="nx-grid-3">
          {goals.map(g => {
            const pct = Math.round(g.progress_pct || 0);
            const offset = 113 - (113 * pct / 100);
            return (
              <div key={g.id} className="card">
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14, gap: 8 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{safeStr(g.title)}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--t3)', marginTop: 3 }}>
                      {safeStr(g.owner)}{g.target_date ? ` · due ${g.target_date}` : ''}
                    </div>
                  </div>
                  <div style={{ position: 'relative', width: 44, height: 44, flexShrink: 0 }}>
                    <svg width={44} height={44} viewBox="0 0 44 44" style={{ transform: 'rotate(-90deg)' }}>
                      <circle cx={22} cy={22} r={18} fill="none" stroke="var(--b1)" strokeWidth={4} />
                      <circle cx={22} cy={22} r={18} fill="none" stroke={pct >= 100 ? 'var(--green)' : 'var(--p)'} strokeWidth={4} strokeDasharray={113} strokeDashoffset={offset} strokeLinecap="round" style={{ transition: 'stroke-dashoffset 0.5s var(--ease)' }} />
                    </svg>
                    <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: 'var(--t2)', fontFamily: 'var(--font-mono)' }}>{pct}%</div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: 12, color: 'var(--t3)' }}>{g.linked_tasks ?? 0} linked task{(g.linked_tasks ?? 0) !== 1 ? 's' : ''}</span>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <button className="btn btn-ghost btn-sm" style={{ padding: '2px 9px' }} onClick={() => bump(g, -10)} title="-10%">−</button>
                    <button className="btn btn-ghost btn-sm" style={{ padding: '2px 9px' }} onClick={() => bump(g, +10)} title="+10%">+</button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
