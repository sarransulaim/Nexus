import React, { useMemo } from 'react';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, EmptyState, Avatar, ProgressBar } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';

export default function TeamMatrix() {
  const { employees, tasks, selectedTeam, setSelectedTeam } = useNexus();

  const empStats = useMemo(() => {
    const map = {};
    (employees || []).forEach(emp => {
      const t = (tasks || []).filter(t => String(t.owner_id) === String(emp.id));
      map[emp.id] = { total: t.length, done: t.filter(x => x.is_completed).length };
    });
    return map;
  }, [employees, tasks]);

  const maxLoad = useMemo(() => Math.max(...Object.values(empStats).map(s => s.total), 1), [empStats]);

  const teams = useMemo(() => {
    const g = {};
    (employees || []).forEach(emp => {
      const t = emp.team || 'Unassigned';
      if (!g[t]) g[t] = { name: t, members: [], total: 0, done: 0 };
      g[t].members.push(emp);
      const s = empStats[emp.id] || { total: 0, done: 0 };
      g[t].total += s.total; g[t].done += s.done;
    });
    return Object.values(g).sort((a, b) => a.name.localeCompare(b.name));
  }, [employees, empStats]);

  if (teams.length === 0) return <EmptyState icon={ICON.team} title="No teams yet" desc="Add employees to see team breakdown" />;

  if (!selectedTeam) {
    return (
      <div className="animate-in nx-grid-auto">
        {teams.map(team => {
          const pct = team.total > 0 ? Math.round((team.done / team.total) * 100) : 0;
          return (
            <div key={team.name} className="card card-hover" onClick={() => setSelectedTeam(team.name)}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{ width: 36, height: 36, borderRadius: 10, background: 'var(--p-bg)', border: '1px solid var(--p-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--p)', flexShrink: 0 }}>
                    <Icon path={ICON.team} size={17} />
                  </div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{team.name}</div>
                    <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 1 }}>{team.members.length} member{team.members.length !== 1 ? 's' : ''}</div>
                  </div>
                </div>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--t3)', fontFamily: 'var(--font-mono)' }}>{pct}%</span>
              </div>
              {/* Member avatars */}
              <div style={{ display: 'flex', gap: -4, marginBottom: 14 }}>
                {team.members.slice(0, 5).map(m => (
                  <div key={m.id} style={{ width: 26, height: 26, borderRadius: 7, background: 'var(--bg-4)', border: '2px solid var(--bg-2)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: 'var(--t2)', marginLeft: -6, flexShrink: 0 }}>
                    {safeStr(m.name).charAt(0).toUpperCase()}
                  </div>
                ))}
                {team.members.length > 5 && (
                  <div style={{ width: 26, height: 26, borderRadius: 7, background: 'var(--bg-5)', border: '2px solid var(--bg-2)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 9, color: 'var(--t3)', marginLeft: -6, flexShrink: 0 }}>
                    +{team.members.length - 5}
                  </div>
                )}
              </div>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span className="nx-label">Progress</span>
                  <span style={{ fontSize: 11, color: 'var(--t3)' }}>{team.done}/{team.total} tasks</span>
                </div>
                <ProgressBar value={pct} />
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  const current = teams.find(t => t.name === selectedTeam);
  if (!current) return null;

  return (
    <div className="animate-in">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <button onClick={() => setSelectedTeam(null)} className="btn btn-ghost btn-sm">
          <Icon path={ICON.arrow_back} size={13} /> Back
        </button>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--t1)' }}>{selectedTeam}</div>
          <div style={{ fontSize: 12, color: 'var(--t3)' }}>{current.members.length} members</div>
        </div>
      </div>

      <div className="nx-grid-auto">
        {current.members.map(emp => {
          const s     = empStats[emp.id] || { total: 0, done: 0 };
          const load  = s.total > 0 ? Math.round((s.total / maxLoad) * 100) : 0;
          const isBusy = load >= 70;
          const assisting = (emp.assisting || []);

          return (
            <div key={emp.id} className="card animate-in">
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{ width: 36, height: 36, borderRadius: 10, background: 'var(--bg-4)', border: '1px solid var(--b1)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, fontWeight: 700, color: 'var(--t1)', flexShrink: 0 }}>
                    {safeStr(emp.name).charAt(0).toUpperCase()}
                  </div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{safeStr(emp.name)}</div>
                    <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 1 }}>{safeStr(emp.role)}</div>
                  </div>
                </div>
                <span className={`badge badge-${isBusy ? 'amber' : 'green'}`}>{isBusy ? 'High Load' : 'Available'}</span>
              </div>

              <div style={{ fontSize: 12, color: 'var(--t3)', marginBottom: 12 }}>
                {emp.experience || 0}y exp · {safeStr(emp.skills) || 'No skills listed'}
              </div>

              {assisting.length > 0 && (
                <div className="card-peer card-sm" style={{ marginBottom: 12, fontSize: 12 }}>
                  <div className="nx-label" style={{ color: 'var(--peer)', marginBottom: 3 }}>Assisting</div>
                  <div style={{ color: 'var(--t2)' }}>{assisting.join(', ')}</div>
                </div>
              )}

              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span className="nx-label">Bandwidth</span>
                  <span style={{ fontSize: 11, color: isBusy ? 'var(--amber)' : 'var(--t3)', fontFamily: 'var(--font-mono)' }}>{load}%</span>
                </div>
                <ProgressBar value={load} color={isBusy ? 'amber' : ''} />
              </div>

              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--b0)' }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--t1)', fontFamily: 'var(--font-mono)' }}>{s.total}</div>
                  <div className="nx-label" style={{ marginTop: 2 }}>Total</div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--green)', fontFamily: 'var(--font-mono)' }}>{s.done}</div>
                  <div className="nx-label" style={{ marginTop: 2 }}>Done</div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--t2)', fontFamily: 'var(--font-mono)' }}>{s.total - s.done}</div>
                  <div className="nx-label" style={{ marginTop: 2 }}>Active</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
