import React, { useEffect, useState, useCallback } from 'react';
import {
  AreaChart, Area,
  BarChart, Bar,
  PieChart, Pie, Cell,
  ScatterChart, Scatter,
  XAxis, YAxis, ZAxis,
  CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Legend, LabelList,
} from 'recharts';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, Spinner, EmptyState } from '../components/ui/SharedUI';

// ── Design system colours (recharts can't read CSS vars) ─────
const C = {
  p:     '#6366f1', pMuted: '#a5b4fc', pBg: 'rgba(99,102,241,0.07)',
  green: '#10b981', greenBg: 'rgba(16,185,129,0.07)',
  amber: '#f59e0b', amberBg: 'rgba(245,158,11,0.07)',
  red:   '#ef4444', redBg:   'rgba(239,68,68,0.07)',
  ai:    '#8b5cf6', peer: '#ec4899',
  t1: '#f0f0f4', t2: '#8f8fa0', t3: '#52526a', t4: '#303045',
  b0: '#181820', b1: '#1f1f2e', b2: '#2a2a3d',
  bg0: '#09090d', bg2: '#131318', bg3: '#18181f', bg4: '#1e1e27',
};

const PRIORITY_COLORS = { Critical: C.red, High: C.amber, Medium: C.p, Low: '#475569' };

// ── Shared dark tooltip ───────────────────────────────────────
const DarkTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: C.bg3, border: `1px solid ${C.b2}`, borderRadius: 8, padding: '10px 14px', minWidth: 130 }}>
      {label && <div style={{ fontSize: 11, color: C.t2, marginBottom: 6, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>}
      {payload.map((p, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, marginBottom: 2 }}>
          <div style={{ width: 7, height: 7, borderRadius: 2, background: p.color, flexShrink: 0 }} />
          <span style={{ color: C.t2 }}>{p.name}:</span>
          <span style={{ fontWeight: 700, color: C.t1 }}>{p.value}</span>
        </div>
      ))}
    </div>
  );
};

const ScatterTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div style={{ background: C.bg3, border: `1px solid ${C.b2}`, borderRadius: 8, padding: '10px 14px' }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: C.t1, marginBottom: 6 }}>{d.full_name}</div>
      <div style={{ fontSize: 11, color: C.t3, marginBottom: 8 }}>{d.role} · {d.team}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ fontSize: 12, color: C.t2 }}>Active tasks: <b style={{ color: C.t1 }}>{d.active}</b></span>
        <span style={{ fontSize: 12, color: C.t2 }}>Completion: <b style={{ color: C.t1 }}>{d.completion_rate}%</b></span>
        {d.overdue > 0 && <span style={{ fontSize: 12, color: C.red }}>Overdue: {d.overdue}</span>}
        <span style={{ fontSize: 12, color: C.t2 }}>AI commands: <b style={{ color: C.ai }}>{d.ai_messages}</b></span>
      </div>
    </div>
  );
};

const AXIS_STYLE = { fill: C.t3, fontSize: 11 };

// ── KPI card ─────────────────────────────────────────────────
function KPI({ label, value, sub, accent = 'default', icon, onClick }) {
  const a = {
    default: { icon: C.t3,    border: C.b1,                         bg: C.bg3 },
    indigo:  { icon: C.p,     border: 'rgba(99,102,241,0.22)',      bg: C.pBg },
    green:   { icon: C.green, border: 'rgba(16,185,129,0.18)',      bg: C.greenBg },
    amber:   { icon: C.amber, border: 'rgba(245,158,11,0.18)',      bg: C.amberBg },
    red:     { icon: C.red,   border: 'rgba(239,68,68,0.18)',       bg: C.redBg },
    ai:      { icon: C.ai,    border: 'rgba(139,92,246,0.18)',      bg: 'rgba(139,92,246,0.07)' },
  }[accent] || { icon: C.t3, border: C.b1, bg: C.bg3 };

  return (
    <div
      className="stat-card"
      onClick={onClick}
      style={{ cursor: onClick ? 'pointer' : 'default', transition: 'background 150ms' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span className="stat-label">{label}</span>
        {icon && (
          <div style={{ width: 30, height: 30, borderRadius: 7, background: a.bg, border: `1px solid ${a.border}`, display: 'flex', alignItems: 'center', justifyContent: 'center', color: a.icon, flexShrink: 0 }}>
            <Icon path={icon} size={14} />
          </div>
        )}
      </div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub" style={{ color: C.t2 }}>{sub}</div>}
    </div>
  );
}

// ── Section card ──────────────────────────────────────────────
function Section({ title, children, style, action }) {
  return (
    <div className="card" style={style}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: C.t3 }}>{title}</span>
        {action}
      </div>
      {children}
    </div>
  );
}

// ── Severity dot ──────────────────────────────────────────────
const SEV_COLOR = { high: C.red, medium: C.amber, low: C.t3 };

// ── Quadrant colour for scatter dots ─────────────────────────
function quadrantColor(d) {
  const highLoad  = d.active >= 5;
  const highPerf  = d.completion_rate >= 50;
  if (highLoad && highPerf)  return C.amber;  // Overloaded star
  if (!highLoad && highPerf) return C.green;  // Balanced star
  if (highLoad && !highPerf) return C.red;    // Overwhelmed
  return C.t3;                                // Underutilized
}

// ── Drill-down slide panel ────────────────────────────────────
function DrillDownPanel({ type, id, name, BACKEND_URL, period, onClose }) {
  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = sessionStorage.getItem('nexus_access_token');
    const url = type === 'team'
      ? `${BACKEND_URL}/api/v1/analytics/team/${encodeURIComponent(name)}?period=${period}`
      : `${BACKEND_URL}/api/v1/analytics/employee/${id}`;

    fetch(url, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [type, id, name, BACKEND_URL, period]);

  return (
    <div style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, width: 480,
      background: C.bg2, borderLeft: `1px solid ${C.b1}`, zIndex: 200,
      display: 'flex', flexDirection: 'column', boxShadow: '-24px 0 64px rgba(0,0,0,0.5)',
      animation: 'slideRight 200ms cubic-bezier(0.16,1,0.3,1)',
    }}>
      {/* Header */}
      <div style={{ padding: '18px 20px', borderBottom: `1px solid ${C.b1}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 600, color: C.t1 }}>{name}</div>
          <div style={{ fontSize: 11, color: C.t3, marginTop: 2, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{type === 'team' ? 'Team Drill-Down' : 'Employee Drill-Down'}</div>
        </div>
        <button onClick={onClose} className="btn btn-ghost btn-icon"><Icon path={ICON.close} size={16} /></button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, gap: 10, color: C.t2 }}><Spinner size={16} /> Loading...</div>
        ) : !data || data.error ? (
          <div style={{ color: C.t3, fontSize: 13, textAlign: 'center', padding: 40 }}>{data?.error || 'Failed to load'}</div>
        ) : type === 'team' ? (
          <TeamDrillContent data={data} />
        ) : (
          <EmployeeDrillContent data={data} />
        )}
      </div>
    </div>
  );
}

function TeamDrillContent({ data }) {
  const { stats, members, tasks, trend } = data;
  return (
    <>
      {/* Stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
        {[
          { label: 'Completion', value: `${stats.completion_rate}%`, color: stats.completion_rate >= 70 ? C.green : C.amber },
          { label: 'Active',     value: stats.active,               color: C.t1 },
          { label: 'Overdue',    value: stats.overdue,              color: stats.overdue > 0 ? C.red : C.green },
        ].map((s, i) => (
          <div key={i} style={{ background: C.bg3, border: `1px solid ${C.b1}`, borderRadius: 10, padding: '12px 14px', textAlign: 'center' }}>
            <div style={{ fontSize: 20, fontWeight: 700, color: s.color, fontFamily: 'var(--font-mono)', lineHeight: 1 }}>{s.value}</div>
            <div style={{ fontSize: 10, color: C.t3, marginTop: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Completion trend */}
      {trend.some(d => d.completed > 0) && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: C.t3, marginBottom: 10 }}>Completion Trend</div>
          <ResponsiveContainer width="100%" height={100}>
            <AreaChart data={trend} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
              <XAxis dataKey="date" tick={{ fill: C.t3, fontSize: 10 }} axisLine={false} tickLine={false} interval={Math.floor(trend.length / 4)} />
              <YAxis tick={{ fill: C.t3, fontSize: 10 }} axisLine={false} tickLine={false} allowDecimals={false} />
              <Tooltip content={<DarkTooltip />} />
              <Area type="monotone" dataKey="completed" name="Completed" stroke={C.p} fill={C.pBg} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Members */}
      <div>
        <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: C.t3, marginBottom: 10 }}>Members ({members.length})</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {members.map((m, i) => (
            <div key={i} style={{ background: C.bg3, border: `1px solid ${C.b1}`, borderRadius: 8, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 32, height: 32, borderRadius: 8, background: C.bg4, border: `1px solid ${C.b1}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, color: C.t2, flexShrink: 0 }}>{m.name.charAt(0)}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500, color: C.t1 }}>{m.name}</div>
                <div style={{ fontSize: 11, color: C.t3 }}>{m.role}</div>
              </div>
              <div style={{ textAlign: 'right', flexShrink: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: m.completion_rate >= 70 ? C.green : C.amber, fontFamily: 'var(--font-mono)' }}>{m.completion_rate}%</div>
                <div style={{ fontSize: 10, color: C.t3 }}>{m.active} active</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Tasks */}
      <div>
        <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: C.t3, marginBottom: 10 }}>All Tasks ({tasks.length})</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {tasks.map((t, i) => (
            <div key={i} style={{ background: C.bg3, border: `1px solid ${t.overdue ? 'rgba(239,68,68,0.2)' : C.b1}`, borderRadius: 8, padding: '9px 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0, background: t.is_completed ? C.green : t.overdue ? C.red : C.p }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, color: t.is_completed ? C.t3 : C.t1, textDecoration: t.is_completed ? 'line-through' : 'none', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title}</div>
                <div style={{ fontSize: 10, color: C.t3, marginTop: 1 }}>{t.owner_name} · {t.priority}</div>
              </div>
              {t.overdue && <span style={{ fontSize: 10, color: C.red, flexShrink: 0 }}>{t.days_overdue}d late</span>}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function EmployeeDrillContent({ data }) {
  const { employee, stats, tasks, goals, peer_history } = data;
  return (
    <>
      {/* Profile */}
      <div style={{ background: C.bg3, border: `1px solid ${C.b1}`, borderRadius: 10, padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
          <div style={{ width: 44, height: 44, borderRadius: 11, background: C.bg4, border: `1px solid ${C.b2}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, fontWeight: 700, color: C.t1, flexShrink: 0 }}>{employee.name.charAt(0)}</div>
          <div>
            <div style={{ fontSize: 15, fontWeight: 600, color: C.t1 }}>{employee.name}</div>
            <div style={{ fontSize: 12, color: C.t3 }}>{employee.role} · {employee.team}</div>
            <div style={{ fontSize: 11, color: C.t3, marginTop: 2 }}>{employee.experience}yr exp · {employee.skills || 'No skills listed'}</div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
          {[
            { label: 'Completion', value: `${stats.completion_rate}%`, color: stats.completion_rate >= 70 ? C.green : C.amber },
            { label: 'AI Cmds',    value: stats.ai_messages,           color: C.ai },
            { label: 'Overdue',    value: stats.overdue,               color: stats.overdue > 0 ? C.red : C.green },
          ].map((s, i) => (
            <div key={i} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 20, fontWeight: 700, color: s.color, fontFamily: 'var(--font-mono)', lineHeight: 1 }}>{s.value}</div>
              <div style={{ fontSize: 9, color: C.t3, marginTop: 3, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Tasks */}
      <div>
        <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: C.t3, marginBottom: 10 }}>Tasks ({tasks.length})</div>
        {tasks.length === 0 ? <div style={{ fontSize: 12, color: C.t3, textAlign: 'center', padding: 20 }}>No tasks assigned</div> : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {tasks.map((t, i) => (
              <div key={i} style={{ background: C.bg3, border: `1px solid ${t.overdue ? 'rgba(239,68,68,0.18)' : C.b1}`, borderRadius: 8, padding: '9px 12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0, background: t.is_completed ? C.green : t.overdue ? C.red : C.p }} />
                  <div style={{ flex: 1, fontSize: 12, color: t.is_completed ? C.t3 : C.t1, textDecoration: t.is_completed ? 'line-through' : 'none', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title}</div>
                  <span style={{ fontSize: 10, color: PRIORITY_COLORS[t.priority] || C.t3, flexShrink: 0 }}>{t.priority}</span>
                </div>
                {(t.subtasks_total > 0 || t.overdue) && (
                  <div style={{ display: 'flex', gap: 12, marginTop: 5, marginLeft: 15 }}>
                    {t.subtasks_total > 0 && <span style={{ fontSize: 10, color: C.t3 }}>{t.subtasks_done}/{t.subtasks_total} subtasks</span>}
                    {t.overdue && <span style={{ fontSize: 10, color: C.red }}>{t.days_overdue}d overdue</span>}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Goals */}
      {goals.length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: C.t3, marginBottom: 10 }}>Goals ({goals.length})</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {goals.map((g, i) => (
              <div key={i} style={{ background: C.bg3, border: `1px solid ${C.b1}`, borderRadius: 8, padding: '10px 12px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: C.t1, flex: 1, marginRight: 8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{g.title}</div>
                  <span style={{ fontSize: 12, fontWeight: 700, color: g.progress_pct >= 100 ? C.green : C.p, fontFamily: 'var(--font-mono)' }}>{g.progress_pct}%</span>
                </div>
                <div style={{ height: 3, background: C.b1, borderRadius: 99, overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${g.progress_pct}%`, background: g.progress_pct >= 100 ? C.green : C.p, borderRadius: 99, transition: 'width 0.6s' }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Peer history */}
      {peer_history.length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: C.t3, marginBottom: 10 }}>Peer History ({peer_history.length})</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {peer_history.slice(0, 10).map((r, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', background: C.bg3, borderRadius: 7, border: `1px solid ${C.b1}` }}>
                <span style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: r.direction === 'sent' ? C.p : C.peer, flexShrink: 0 }}>{r.direction}</span>
                <span style={{ fontSize: 11, color: C.t2, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.topic}</span>
                <span style={{ fontSize: 10, color: r.status === 'Accepted' ? C.green : r.status === 'Declined' ? C.red : C.amber, flexShrink: 0 }}>{r.status}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}


// ══════════════════════════════════════════════════════════════
// TABS
// ══════════════════════════════════════════════════════════════

function OverviewTab({ d, period, onEmpClick }) {
  const { stats, risk_alerts, completion_trend, priority_breakdown, peer_requests, escalations, employee_scatter } = d;
  const vel = stats.velocity_pct;
  const velColor = vel > 0 ? C.green : vel < 0 ? C.red : C.t2;

  // Custom scatter dot coloured by quadrant
  const ScatterDot = (props) => {
    const { cx, cy, payload } = props;
    if (!cx || !cy) return null;
    const color = quadrantColor(payload);
    return (
      <g>
        <circle cx={cx} cy={cy} r={8} fill={color} fillOpacity={0.85} stroke={C.bg3} strokeWidth={2} style={{ cursor: 'pointer' }} onClick={() => onEmpClick(payload)} />
        <text x={cx} y={cy - 13} textAnchor="middle" fill={C.t2} fontSize={9}>{payload.name}</text>
      </g>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* Risk alerts */}
      {risk_alerts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {risk_alerts.map((alert, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 10, background: alert.severity === 'high' ? 'rgba(239,68,68,0.06)' : alert.severity === 'medium' ? 'rgba(245,158,11,0.06)' : C.bg3, border: `1px solid ${alert.severity === 'high' ? 'rgba(239,68,68,0.18)' : alert.severity === 'medium' ? 'rgba(245,158,11,0.18)' : C.b1}` }}>
              <div style={{ width: 7, height: 7, borderRadius: '50%', background: SEV_COLOR[alert.severity], flexShrink: 0, animation: alert.severity === 'high' ? 'pulse-anim 2s infinite' : 'none' }} />
              <span style={{ fontSize: 13, color: alert.severity === 'high' ? C.red : alert.severity === 'medium' ? C.amber : C.t2, flex: 1 }}>{alert.message}</span>
              <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: SEV_COLOR[alert.severity], flexShrink: 0 }}>{alert.severity}</span>
            </div>
          ))}
        </div>
      )}

      {/* KPI row 1 */}
      <div className="nx-grid-4">
        <KPI label="Completion Rate" value={`${stats.completion_rate}%`}
          sub={`${stats.completed_tasks} of ${stats.total_tasks} done`}
          accent={stats.completion_rate >= 70 ? 'green' : stats.completion_rate >= 40 ? 'indigo' : 'amber'}
          icon={ICON.check} />
        <KPI label={`${d.period_label} Velocity`} value={stats.this_period_completed}
          sub={<span style={{ color: velColor, fontWeight: 600 }}>{vel > 0 ? `↑ ${vel}%` : vel < 0 ? `↓ ${Math.abs(vel)}%` : '→ Same'} vs prior period</span>}
          accent="indigo" icon={ICON.time} />
        <KPI label="Overdue Tasks" value={stats.overdue_count}
          sub={`${stats.overdue_rate}% of active tasks`}
          accent={stats.overdue_count > 0 ? 'red' : 'green'} icon={ICON.bell} />
        <KPI label="AI Adoption" value={`${stats.ai_adoption_rate}%`}
          sub={`${stats.total_employees} employees`}
          accent={stats.ai_adoption_rate >= 70 ? 'ai' : 'amber'} icon={ICON.commands} />
      </div>

      {/* KPI row 2 */}
      <div className="nx-grid-4">
        <KPI label="Active Tasks" value={stats.active_tasks} sub={`Across ${stats.total_employees} people`} icon={ICON.tasks} />
        <KPI label="Avg Completion" value={stats.avg_completion_hours > 0 ? `${stats.avg_completion_hours}h` : '—'} sub="Created → done" icon={ICON.time} />
        <KPI label="Meetings" value={stats.meetings_this_week} sub="This week" icon={ICON.meetings} />
        <KPI label="Goal Progress" value={`${stats.avg_goal_progress}%`} sub={`${stats.goals_achieved} achieved · ${stats.goals_active} active`} accent={stats.avg_goal_progress >= 70 ? 'green' : 'indigo'} icon={ICON.goals} />
      </div>

      {/* Scatter + Trend */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>

        {/* Workload vs Performance scatter */}
        <Section title="Workload vs Performance — Click a dot to drill in">
          <div style={{ fontSize: 10, color: C.t3, marginBottom: 12, display: 'flex', gap: 14 }}>
            {[['#10b981', 'Balanced + High perf'], ['#f59e0b', 'Overloaded + High perf'], ['#ef4444', 'Overwhelmed'], ['#52526a', 'Underutilized']].map(([c, l]) => (
              <span key={l} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: c, display: 'inline-block' }} />{l}
              </span>
            ))}
          </div>
          {employee_scatter.length === 0 ? <EmptyState icon={ICON.team} title="No employees" desc="Add team members" /> : (
            <ResponsiveContainer width="100%" height={240}>
              <ScatterChart margin={{ top: 16, right: 16, bottom: 16, left: -10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.b1} />
                <XAxis type="number" dataKey="active" name="Active Tasks" tick={AXIS_STYLE} axisLine={false} tickLine={false} label={{ value: 'Active Tasks', position: 'insideBottom', offset: -8, fill: C.t3, fontSize: 10 }} domain={[0, 'dataMax + 1']} />
                <YAxis type="number" dataKey="completion_rate" name="Completion %" tick={AXIS_STYLE} axisLine={false} tickLine={false} domain={[0, 100]} label={{ value: 'Completion %', angle: -90, position: 'insideLeft', offset: 12, fill: C.t3, fontSize: 10 }} />
                <ReferenceLine x={5} stroke={C.red} strokeDasharray="4 4" strokeOpacity={0.5} />
                <ReferenceLine y={50} stroke={C.amber} strokeDasharray="4 4" strokeOpacity={0.5} />
                <Tooltip content={<ScatterTooltip />} />
                <Scatter data={employee_scatter} shape={<ScatterDot />} />
              </ScatterChart>
            </ResponsiveContainer>
          )}
        </Section>

        {/* Created vs Completed trend */}
        <Section title={`Task Volume — ${d.period_label}`}>
          {completion_trend.every(p => p.created === 0 && p.completed === 0) ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 240, flexDirection: 'column', gap: 8, color: C.t3, fontSize: 13 }}>
              <Icon path={ICON.time} size={24} />No activity recorded yet
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={completion_trend} margin={{ top: 4, right: 8, left: -16, bottom: 16 }}>
                <defs>
                  <linearGradient id="gradCreated" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={C.p} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={C.p} stopOpacity={0.02} />
                  </linearGradient>
                  <linearGradient id="gradCompleted" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={C.green} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={C.green} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.b1} />
                <XAxis dataKey="date" tick={{ fill: C.t3, fontSize: 10 }} axisLine={false} tickLine={false} interval={Math.max(0, Math.floor(completion_trend.length / 6) - 1)} />
                <YAxis tick={AXIS_STYLE} axisLine={false} tickLine={false} allowDecimals={false} />
                <Tooltip content={<DarkTooltip />} />
                <Legend wrapperStyle={{ fontSize: 11, color: C.t2, paddingTop: 8 }} />
                <Area type="monotone" dataKey="created" name="Created" stroke={C.p} fill="url(#gradCreated)" strokeWidth={2} dot={false} />
                <Area type="monotone" dataKey="completed" name="Completed" stroke={C.green} fill="url(#gradCompleted)" strokeWidth={2} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Section>
      </div>

      {/* Priority + Peer + Escalation */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>

        <Section title="Active Priority Breakdown">
          {priority_breakdown.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '28px 0', color: C.t3, fontSize: 13 }}>No active tasks</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
              <ResponsiveContainer width="100%" height={140}>
                <PieChart>
                  <Pie data={priority_breakdown} cx="50%" cy="50%" innerRadius={38} outerRadius={62} dataKey="value" strokeWidth={0}>
                    {priority_breakdown.map((e, i) => <Cell key={i} fill={PRIORITY_COLORS[e.name] || C.t2} />)}
                  </Pie>
                  <Tooltip content={<DarkTooltip />} />
                </PieChart>
              </ResponsiveContainer>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', justifyContent: 'center' }}>
                {priority_breakdown.map((p, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11 }}>
                    <div style={{ width: 7, height: 7, borderRadius: 2, background: PRIORITY_COLORS[p.name] || C.t2 }} />
                    <span style={{ color: C.t2 }}>{p.name}</span>
                    <span style={{ color: C.t1, fontWeight: 700 }}>{p.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Section>

        <Section title="Peer Collaboration">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span style={{ fontSize: 32, fontWeight: 700, color: C.t1, fontFamily: 'var(--font-mono)', lineHeight: 1 }}>{peer_requests.acceptance_rate}%</span>
              <span style={{ fontSize: 11, color: C.t2 }}>acceptance</span>
            </div>
            {[
              { label: 'Accepted',  val: peer_requests.accepted,  color: C.green },
              { label: 'Completed', val: peer_requests.completed, color: C.p },
              { label: 'Pending',   val: peer_requests.pending,   color: C.amber },
              { label: 'Declined',  val: peer_requests.declined,  color: C.red },
            ].filter(x => x.val > 0).map((item, i) => (
              <div key={i}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                  <span style={{ fontSize: 11, color: C.t2 }}>{item.label}</span>
                  <span style={{ fontSize: 11, fontWeight: 700, color: item.color }}>{item.val}</span>
                </div>
                <div style={{ height: 3, background: C.b1, borderRadius: 99, overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${peer_requests.total > 0 ? (item.val / peer_requests.total) * 100 : 0}%`, background: item.color, borderRadius: 99, transition: 'width 0.8s' }} />
                </div>
              </div>
            ))}
            {peer_requests.total === 0 && <div style={{ fontSize: 12, color: C.t3, textAlign: 'center', padding: '12px 0' }}>No peer requests yet</div>}
          </div>
        </Section>

        <Section title="Escalation Health">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span style={{ fontSize: 32, fontWeight: 700, color: C.t1, fontFamily: 'var(--font-mono)', lineHeight: 1 }}>{d.escalations.resolution_rate}%</span>
              <span style={{ fontSize: 11, color: C.t2 }}>resolved</span>
            </div>
            {[
              { label: 'Resolved', val: d.escalations.resolved, color: C.green },
              { label: 'Pending',  val: d.escalations.pending,  color: C.amber },
            ].map((item, i) => (
              <div key={i}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                  <span style={{ fontSize: 11, color: C.t2 }}>{item.label}</span>
                  <span style={{ fontSize: 11, fontWeight: 700, color: item.color }}>{item.val}</span>
                </div>
                <div style={{ height: 3, background: C.b1, borderRadius: 99, overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${d.escalations.total > 0 ? (item.val / d.escalations.total) * 100 : 0}%`, background: item.color, borderRadius: 99, transition: 'width 0.8s' }} />
                </div>
              </div>
            ))}
            {d.escalations.total === 0 && <div style={{ fontSize: 12, color: C.t3, textAlign: 'center', padding: '12px 0' }}>No escalations</div>}
          </div>
        </Section>
      </div>
    </div>
  );
}

function TeamsTab({ d, period, onTeamClick }) {
  const { team_breakdown } = d;
  if (team_breakdown.length === 0) return <EmptyState icon={ICON.team} title="No teams yet" desc="Add employees with teams to see breakdown" />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* Team comparison bar chart */}
      <Section title="Team Completion Rates">
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={team_breakdown} margin={{ top: 0, right: 8, left: -16, bottom: 0 }} barSize={28}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.b1} vertical={false} />
            <XAxis dataKey="name" tick={AXIS_STYLE} axisLine={false} tickLine={false} />
            <YAxis tick={AXIS_STYLE} axisLine={false} tickLine={false} domain={[0, 100]} unit="%" />
            <Tooltip content={<DarkTooltip />} cursor={{ fill: 'rgba(99,102,241,0.04)' }} />
            <Bar dataKey="completion_rate" name="Completion %" fill={C.p} radius={[4, 4, 0, 0]}>
              <LabelList dataKey="completion_rate" position="top" style={{ fontSize: 10, fill: C.t2 }} formatter={v => `${v}%`} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Section>

      {/* Team cards */}
      <div className="nx-grid-auto">
        {team_breakdown.map((team, i) => {
          const isHealthy = team.completion_rate >= 70;
          const hasIssues = team.overdue > 0;
          return (
            <div key={i} className="card card-hover" onClick={() => onTeamClick(team.name)}
              style={{ borderLeft: `3px solid ${hasIssues ? C.amber : isHealthy ? C.green : C.p}`, cursor: 'pointer' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600, color: C.t1, marginBottom: 2 }}>{team.name}</div>
                  <div style={{ fontSize: 12, color: C.t3 }}>{team.member_count} member{team.member_count !== 1 ? 's' : ''}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: 22, fontWeight: 700, color: isHealthy ? C.green : C.amber, fontFamily: 'var(--font-mono)', lineHeight: 1 }}>{team.completion_rate}%</div>
                  <div style={{ fontSize: 10, color: C.t3, marginTop: 2 }}>completion</div>
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 14 }}>
                {[
                  { l: 'Active',   v: team.active,    c: C.t1 },
                  { l: 'Done',     v: team.completed, c: C.green },
                  { l: 'Overdue',  v: team.overdue,   c: team.overdue > 0 ? C.red : C.t3 },
                ].map((s, j) => (
                  <div key={j} style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 18, fontWeight: 700, color: s.c, fontFamily: 'var(--font-mono)' }}>{s.v}</div>
                    <div style={{ fontSize: 9, color: C.t3, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{s.l}</div>
                  </div>
                ))}
              </div>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ fontSize: 10, color: C.t3, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Progress</span>
                  <span style={{ fontSize: 10, color: C.t3 }}>{team.avg_tasks_per_person} tasks/person</span>
                </div>
                <div style={{ height: 3, background: C.b1, borderRadius: 99, overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${team.completion_rate}%`, background: isHealthy ? C.green : C.p, borderRadius: 99, transition: 'width 0.8s' }} />
                </div>
              </div>
              <div style={{ marginTop: 12, fontSize: 11, color: C.t3, display: 'flex', alignItems: 'center', gap: 4 }}>
                <Icon path={ICON.chevronRight} size={11} /> Click to see all tasks and members
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EmployeesTab({ d, onEmployeeClick }) {
  const { employee_scatter } = d;
  const [sortBy, setSortBy] = useState('total');
  const [sortDir, setSortDir] = useState('desc');

  const sorted = [...employee_scatter].sort((a, b) => {
    const val = sortDir === 'desc' ? b[sortBy] - a[sortBy] : a[sortBy] - b[sortBy];
    return val;
  });

  const toggleSort = (col) => {
    if (sortBy === col) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortBy(col); setSortDir('desc'); }
  };

  const maxTotal = Math.max(...employee_scatter.map(e => e.total), 1);

  const SortTh = ({ col, children }) => (
    <th style={{ cursor: 'pointer', userSelect: 'none' }} onClick={() => toggleSort(col)}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        {children}
        {sortBy === col && <span style={{ fontSize: 9, color: C.p }}>{sortDir === 'desc' ? '↓' : '↑'}</span>}
      </span>
    </th>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <Section title="All Employees — Click any row to drill in" action={<span style={{ fontSize: 11, color: C.t3 }}>Sort by column header</span>}>
        {employee_scatter.length === 0 ? <EmptyState icon={ICON.team} title="No employees" desc="Add team members" /> : (
          <div style={{ overflowX: 'auto' }}>
            <table className="nx-table">
              <thead>
                <tr>
                  <th>Employee</th>
                  <th>Team</th>
                  <SortTh col="active">Active</SortTh>
                  <SortTh col="completed">Done</SortTh>
                  <SortTh col="overdue">Overdue</SortTh>
                  <SortTh col="completion_rate">Rate</SortTh>
                  <SortTh col="ai_messages">AI Cmds</SortTh>
                  <th>Load</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((emp, i) => {
                  const loadPct  = maxTotal > 0 ? Math.round((emp.total / maxTotal) * 100) : 0;
                  const isBusy   = emp.active >= 5;
                  const crColor  = emp.completion_rate >= 70 ? C.green : emp.completion_rate >= 40 ? C.amber : C.red;
                  return (
                    <tr key={i} onClick={() => onEmployeeClick(emp)} style={{ cursor: 'pointer' }}>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                          <div style={{ width: 28, height: 28, borderRadius: 7, background: C.bg4, border: `1px solid ${C.b1}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, color: C.t2, flexShrink: 0 }}>{emp.full_name.charAt(0)}</div>
                          <div>
                            <div style={{ fontSize: 13, fontWeight: 500, color: C.t1 }}>{emp.full_name}</div>
                            <div style={{ fontSize: 11, color: C.t3 }}>{emp.role}</div>
                          </div>
                        </div>
                      </td>
                      <td><span className="badge badge-indigo">{emp.team}</span></td>
                      <td><span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600, color: isBusy ? C.amber : C.t1 }}>{emp.active}</span></td>
                      <td><span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600, color: C.green }}>{emp.completed}</span></td>
                      <td>{emp.overdue > 0 ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600, color: C.red }}>{emp.overdue}</span> : <span style={{ color: C.t3, fontSize: 12 }}>—</span>}</td>
                      <td><span style={{ fontSize: 13, fontWeight: 700, color: crColor, fontFamily: 'var(--font-mono)' }}>{emp.completion_rate}%</span></td>
                      <td><span style={{ fontSize: 13, fontWeight: 600, color: emp.ai_messages > 10 ? C.ai : C.t2, fontFamily: 'var(--font-mono)' }}>{emp.ai_messages}</span></td>
                      <td style={{ minWidth: 90 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <div style={{ flex: 1, height: 4, background: C.b1, borderRadius: 99, overflow: 'hidden' }}>
                            <div style={{ height: '100%', width: `${loadPct}%`, background: isBusy ? C.amber : C.p, borderRadius: 99 }} />
                          </div>
                          <span style={{ fontSize: 10, color: C.t3, width: 22, textAlign: 'right', flexShrink: 0, fontFamily: 'var(--font-mono)' }}>{emp.total}t</span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}

function AITab({ d }) {
  const { agent_activity, peer_requests, escalations } = d;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* AI usage bar */}
      <Section title="AI Agent Commands per Employee">
        {agent_activity.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '32px 0', color: C.t3, fontSize: 13 }}>No AI activity recorded yet. Employees need to log in and use their AI agent.</div>
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(160, agent_activity.length * 50)}>
            <BarChart layout="vertical" data={agent_activity} margin={{ top: 0, right: 40, left: 0, bottom: 0 }} barSize={18}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.b1} horizontal={false} />
              <YAxis type="category" dataKey="name" tick={AXIS_STYLE} width={72} axisLine={false} tickLine={false} />
              <XAxis type="number" tick={AXIS_STYLE} axisLine={false} tickLine={false} />
              <Tooltip content={<DarkTooltip />} cursor={{ fill: 'rgba(139,92,246,0.04)' }} />
              <Bar dataKey="messages" name="AI Commands" fill={C.ai} radius={[0, 4, 4, 0]}>
                <LabelList dataKey="messages" position="right" style={{ fontSize: 11, fill: C.t2 }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </Section>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>

        {/* Peer request stats */}
        <Section title="Peer Collaboration Stats">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'flex', gap: 20 }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: C.t1, fontFamily: 'var(--font-mono)' }}>{peer_requests.total}</div>
                <div style={{ fontSize: 10, color: C.t3, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Total Requests</div>
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: C.green, fontFamily: 'var(--font-mono)' }}>{peer_requests.acceptance_rate}%</div>
                <div style={{ fontSize: 10, color: C.t3, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Acceptance Rate</div>
              </div>
            </div>
            {peer_requests.total > 0 ? (
              <ResponsiveContainer width="100%" height={140}>
                <PieChart>
                  <Pie dataKey="value" data={[
                    { name: 'Accepted',  value: peer_requests.accepted,  color: C.green },
                    { name: 'Completed', value: peer_requests.completed, color: C.p },
                    { name: 'Pending',   value: peer_requests.pending,   color: C.amber },
                    { name: 'Declined',  value: peer_requests.declined,  color: C.red },
                  ].filter(x => x.value > 0)} cx="50%" cy="50%" innerRadius={35} outerRadius={58} strokeWidth={0}>
                    {[C.green, C.p, C.amber, C.red].map((c, i) => <Cell key={i} fill={c} />)}
                  </Pie>
                  <Tooltip content={<DarkTooltip />} />
                  <Legend wrapperStyle={{ fontSize: 11, color: C.t2 }} />
                </PieChart>
              </ResponsiveContainer>
            ) : <div style={{ textAlign: 'center', padding: '20px 0', color: C.t3, fontSize: 12 }}>No peer requests yet</div>}
          </div>
        </Section>

        {/* Escalation stats */}
        <Section title="Escalation Summary">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'flex', gap: 20 }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: C.t1, fontFamily: 'var(--font-mono)' }}>{escalations.total}</div>
                <div style={{ fontSize: 10, color: C.t3, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Total</div>
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: escalations.pending > 0 ? C.amber : C.green, fontFamily: 'var(--font-mono)' }}>{escalations.pending}</div>
                <div style={{ fontSize: 10, color: C.t3, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Pending</div>
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: C.green, fontFamily: 'var(--font-mono)' }}>{escalations.resolved}</div>
                <div style={{ fontSize: 10, color: C.t3, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Resolved</div>
              </div>
            </div>
            {escalations.total > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 11, color: C.t2 }}>Resolution rate</span>
                    <span style={{ fontSize: 12, fontWeight: 700, color: escalations.resolution_rate >= 70 ? C.green : C.amber }}>{escalations.resolution_rate}%</span>
                  </div>
                  <div style={{ height: 6, background: C.b1, borderRadius: 99, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${escalations.resolution_rate}%`, background: escalations.resolution_rate >= 70 ? C.green : C.amber, borderRadius: 99, transition: 'width 0.8s' }} />
                  </div>
                </div>
                {escalations.pending > 0 && (
                  <div style={{ padding: '8px 12px', background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.18)', borderRadius: 8, fontSize: 12, color: C.amber }}>
                    ⚠ {escalations.pending} escalation{escalations.pending > 1 ? 's' : ''} need your attention
                  </div>
                )}
              </div>
            ) : <div style={{ textAlign: 'center', padding: '20px 0', color: C.t3, fontSize: 12 }}>No escalations — system is clean ✓</div>}
          </div>
        </Section>
      </div>
    </div>
  );
}


// ══════════════════════════════════════════════════════════════
// MAIN PAGE
// ══════════════════════════════════════════════════════════════

const TABS = [
  { id: 'overview',   label: 'Overview' },
  { id: 'teams',      label: 'Teams' },
  { id: 'employees',  label: 'Employees' },
  { id: 'ai',         label: 'AI Activity' },
];

const PERIODS = [
  { id: 'week',    label: '7 Days' },
  { id: 'month',   label: '30 Days' },
  { id: 'quarter', label: '90 Days' },
];

export default function Analytics() {
  const { BACKEND_URL } = useNexus();
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(false);
  const [tab,     setTab]     = useState('overview');
  const [period,  setPeriod]  = useState('month');
  const [drill,   setDrill]   = useState(null); // { type: 'team'|'employee', id, name }

  const fetchData = useCallback((p) => {
    setLoading(true);
    setError(false);
    const token = sessionStorage.getItem('nexus_access_token');
    fetch(`${BACKEND_URL}/api/v1/analytics/summary?period=${p}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(d => { setData(d); setLoading(false); })
      .catch(() => { setError(true); setLoading(false); });
  }, [BACKEND_URL]);

  useEffect(() => { fetchData(period); }, [period, fetchData]);

  const handlePeriod = (p) => { setPeriod(p); setDrill(null); };

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 400, gap: 12, color: C.t2 }}>
      <Spinner size={18} /> Loading analytics...
    </div>
  );

  if (error || !data) return <EmptyState icon={ICON.analytics} title="Analytics unavailable" desc="Could not load data. Check the backend is running." />;

  return (
    <>
      <div className="animate-in" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* ── Header ───────────────────────────────────────── */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {/* Org health badge */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 16px', background: `${data.org_health.color}12`, border: `1px solid ${data.org_health.color}30`, borderRadius: 10 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: data.org_health.color, animation: 'pulse-anim 2s infinite' }} />
              <div>
                <div style={{ fontSize: 18, fontWeight: 700, color: data.org_health.color, fontFamily: 'var(--font-mono)', lineHeight: 1 }}>{data.org_health.score}</div>
                <div style={{ fontSize: 9, color: C.t3, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Org Health · {data.org_health.label}</div>
              </div>
            </div>
          </div>

          {/* Period selector */}
          <div style={{ display: 'flex', gap: 2, background: C.bg2, border: `1px solid ${C.b1}`, borderRadius: 10, padding: 4 }}>
            {PERIODS.map(p => (
              <button key={p.id} onClick={() => handlePeriod(p.id)}
                style={{ padding: '5px 14px', borderRadius: 7, fontSize: 13, fontWeight: 500, cursor: 'pointer', transition: 'all 120ms', background: period === p.id ? C.bg4 : 'transparent', color: period === p.id ? C.t1 : C.t3, border: 'none' }}>
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* ── Tab bar ──────────────────────────────────────── */}
        <div style={{ display: 'flex', gap: 2, background: C.bg2, border: `1px solid ${C.b1}`, borderRadius: 10, padding: 4, width: 'fit-content' }}>
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              style={{ padding: '6px 18px', borderRadius: 7, fontSize: 13, fontWeight: 500, cursor: 'pointer', transition: 'all 120ms', background: tab === t.id ? C.bg4 : 'transparent', color: tab === t.id ? C.t1 : C.t3, border: 'none' }}>
              {t.label}
              {t.id === 'overview' && data.risk_alerts?.length > 0 && (
                <span style={{ marginLeft: 6, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 16, height: 16, borderRadius: '50%', background: C.red, fontSize: 9, fontWeight: 700, color: '#fff' }}>{data.risk_alerts.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* ── Tab content ──────────────────────────────────── */}
        {tab === 'overview'  && <OverviewTab  d={data} period={period} onEmpClick={emp => setDrill({ type: 'employee', id: emp.id, name: emp.full_name })} />}
        {tab === 'teams'     && <TeamsTab     d={data} period={period} onTeamClick={name => setDrill({ type: 'team', id: null, name })} />}
        {tab === 'employees' && <EmployeesTab d={data} onEmployeeClick={emp => setDrill({ type: 'employee', id: emp.id, name: emp.full_name })} />}
        {tab === 'ai'        && <AITab        d={data} />}

      </div>

      {/* ── Drill-down backdrop + panel ──────────────────── */}
      {drill && (
        <>
          <div onClick={() => setDrill(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(9,9,13,0.6)', zIndex: 199, backdropFilter: 'blur(2px)' }} />
          <DrillDownPanel type={drill.type} id={drill.id} name={drill.name} BACKEND_URL={BACKEND_URL} period={period} onClose={() => setDrill(null)} />
        </>
      )}
    </>
  );
}
