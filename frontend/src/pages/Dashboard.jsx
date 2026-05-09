import React, { useMemo } from 'react';
import { useNexus } from '../context/NexusContext';
import { StatCard, SectionHeader, EmptyState, ICON, Icon, ProgressBar } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';
import WorkMap from '../components/WorkMap';

export default function Dashboard() {
  const { tasks, employees } = useNexus();

  const empMap = useMemo(() => {
    const m = {};
    (employees || []).forEach(e => { m[e.id] = e.name; });
    return m;
  }, [employees]);

  const stats = useMemo(() => {
    const safe = tasks || [];
    const today = new Date().toISOString().split('T')[0];
    const completed = safe.filter(t => t.is_completed).length;
    const overdue   = safe.filter(t => !t.is_completed && t.due_date && String(t.due_date) < today).length;
    return { total: safe.length, active: safe.length - completed, completed, overdue };
  }, [tasks]);

  // Workload per employee (top 6)
  const workload = useMemo(() => {
    const map = {};
    (tasks || []).forEach(t => {
      const name = empMap[t.owner_id] || 'Unassigned';
      if (!map[name]) map[name] = { total: 0, done: 0 };
      map[name].total++;
      if (t.is_completed) map[name].done++;
    });
    return Object.entries(map)
      .sort((a, b) => b[1].total - a[1].total)
      .slice(0, 6)
      .map(([name, d]) => ({ name, ...d, pct: d.total > 0 ? Math.round((d.done / d.total) * 100) : 0 }));
  }, [tasks, empMap]);

  const maxWorkload = Math.max(...workload.map(w => w.total), 1);

  // Completion donut
  const pct = stats.total > 0 ? Math.round((stats.completed / stats.total) * 100) : 0;
  const r   = 38;
  const circ = 2 * Math.PI * r;
  const offset = circ - (circ * pct / 100);

  // Recent activity (last 5 completed tasks)
  const recentActivity = useMemo(() => (
    [...(tasks || [])].filter(t => t.is_completed).reverse().slice(0, 5)
  ), [tasks]);

  return (
    <div className="animate-in" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* ── Stats row ── */}
      <div className="nx-grid-4">
        <StatCard title="Total Tasks"  value={stats.total}    sub="In registry"         icon={ICON.tasks}   />
        <StatCard title="Active"       value={stats.active}   sub="In execution"        icon={ICON.time}    accent="indigo" />
        <StatCard title="Overdue"      value={stats.overdue}  sub="Needs attention"     icon={ICON.bell}    accent={stats.overdue > 0 ? 'red' : 'default'} />
        <StatCard title="Completed"    value={stats.completed} sub="Successfully closed" icon={ICON.check}  accent="green" />
      </div>

      {/* ── Charts row ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 220px', gap: 16 }}>

        {/* Workload bars */}
        <div className="card">
          <SectionHeader title="Workload Distribution" />
          {workload.length === 0 ? (
            <EmptyState icon={ICON.team} title="No workload data" desc="Assign tasks to see distribution" />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 4 }}>
              {workload.map((w, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{ width: 88, flexShrink: 0, fontSize: 12, color: 'var(--t2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{w.name}</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ flex: 1, height: 4, background: 'var(--b1)', borderRadius: 99, overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${(w.total / maxWorkload) * 100}%`, background: 'var(--p)', borderRadius: 99, transition: 'width 0.5s var(--ease)' }} />
                      </div>
                      <span style={{ fontSize: 11, color: 'var(--t3)', width: 20, textAlign: 'right', flexShrink: 0 }}>{w.total}</span>
                    </div>
                  </div>
                  <div style={{ width: 32, flexShrink: 0, textAlign: 'right', fontSize: 11, color: w.pct === 100 ? 'var(--green)' : 'var(--t3)' }}>{w.pct}%</div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Completion donut */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
          <div className="nx-label">Completion Rate</div>
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width={100} height={100} viewBox="0 0 100 100" style={{ transform: 'rotate(-90deg)' }}>
              <circle cx="50" cy="50" r={r} fill="transparent" stroke="var(--b1)" strokeWidth={10} />
              <circle
                cx="50" cy="50" r={r} fill="transparent"
                stroke="var(--p)" strokeWidth={10}
                strokeDasharray={circ}
                strokeDashoffset={offset}
                strokeLinecap="round"
                style={{ transition: 'stroke-dashoffset 1s var(--ease)' }}
              />
            </svg>
            <div style={{ position: 'absolute', textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--t1)', lineHeight: 1, fontFamily: 'var(--font-mono)' }}>{pct}%</div>
              <div style={{ fontSize: 10, color: 'var(--t3)', marginTop: 2 }}>done</div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 16 }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--t1)', fontFamily: 'var(--font-mono)' }}>{stats.completed}</div>
              <div style={{ fontSize: 10, color: 'var(--t3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Done</div>
            </div>
            <div style={{ width: 1, background: 'var(--b1)' }} />
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--t1)', fontFamily: 'var(--font-mono)' }}>{stats.active}</div>
              <div style={{ fontSize: 10, color: 'var(--t3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Active</div>
            </div>
          </div>
        </div>
      </div>

      {/* ── Team + Recent Activity ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>

        {/* Team summary */}
        <div className="card">
          <SectionHeader title="Team Overview" />
          {employees.length === 0 ? (
            <EmptyState icon={ICON.team} title="No employees" desc="Add team members to see overview" />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {employees.slice(0, 6).map(emp => {
                const empTasks = (tasks || []).filter(t => String(t.owner_id) === String(emp.id));
                const active   = empTasks.filter(t => !t.is_completed).length;
                const load     = empTasks.length;
                const isBusy   = active >= 5;
                return (
                  <div key={emp.id} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{ width: 30, height: 30, borderRadius: 8, background: 'var(--bg-4)', border: '1px solid var(--b1)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, color: 'var(--t2)', flexShrink: 0 }}>
                      {safeStr(emp.name).charAt(0).toUpperCase()}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--t1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{safeStr(emp.name)}</div>
                      <div style={{ fontSize: 11, color: 'var(--t3)' }}>{safeStr(emp.role)}</div>
                    </div>
                    <div style={{ flexShrink: 0, textAlign: 'right' }}>
                      <span style={{ fontSize: 12, fontWeight: 600, color: isBusy ? 'var(--amber)' : 'var(--t2)', fontFamily: 'var(--font-mono)' }}>{active}</span>
                      <span style={{ fontSize: 11, color: 'var(--t3)' }}> tasks</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Recent completions */}
        <div className="card">
          <SectionHeader title="Recent Completions" />
          {recentActivity.length === 0 ? (
            <EmptyState icon={ICON.check} title="No completions yet" desc="Completed tasks will appear here" />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {recentActivity.map(task => (
                <div key={task.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                  <div style={{ width: 18, height: 18, borderRadius: 5, background: 'var(--green-bg)', border: '1px solid var(--green-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 1 }}>
                    <Icon path={ICON.check} size={10} style={{ color: 'var(--green)' }} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: 'var(--t2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{safeStr(task.title)}</div>
                    <div style={{ fontSize: 11, color: 'var(--t3)', marginTop: 1 }}>{empMap[task.owner_id] || 'Unassigned'}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── 3D Brain ── */}
      <WorkMap employees={employees} tasks={tasks} />
    </div>
  );
}
