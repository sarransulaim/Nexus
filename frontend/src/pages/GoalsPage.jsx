import React from 'react';
import { ICON, ComingSoon } from '../components/ui/SharedUI';
const goals = [
  { title: 'Ship MVP by Q2', pct: 65, tasks: 8 },
  { title: 'Onboard 3 enterprise clients', pct: 33, tasks: 4 },
  { title: 'Reduce response time by 40%', pct: 80, tasks: 3 },
];
export default function GoalsPage() {
  return (
    <div className="animate-in">
      <div style={{ opacity: 0.45, pointerEvents: 'none', filter: 'blur(0.5px)', marginBottom: 24 }}>
        <div className="nx-grid-3">
          {goals.map((g, i) => (
            <div key={i} className="card">
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)', flex: 1, marginRight: 8 }}>{g.title}</div>
                <div style={{ position: 'relative', width: 44, height: 44, flexShrink: 0 }}>
                  <svg width={44} height={44} viewBox="0 0 44 44" style={{ transform: 'rotate(-90deg)' }}>
                    <circle cx={22} cy={22} r={18} fill="none" stroke="var(--b1)" strokeWidth={4} />
                    <circle cx={22} cy={22} r={18} fill="none" stroke="var(--p)" strokeWidth={4} strokeDasharray={113} strokeDashoffset={113 - (113 * g.pct / 100)} strokeLinecap="round" />
                  </svg>
                  <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: 'var(--t2)' }}>{g.pct}%</div>
                </div>
              </div>
              <div style={{ height: 3, background: 'var(--b1)', borderRadius: 99, overflow: 'hidden', marginBottom: 8 }}>
                <div style={{ height: '100%', width: `${g.pct}%`, background: 'var(--p)', borderRadius: 99 }} />
              </div>
              <div style={{ fontSize: 12, color: 'var(--t3)' }}>{g.tasks} linked tasks</div>
            </div>
          ))}
        </div>
      </div>
      <ComingSoon icon={ICON.goals} title="Goals Coming Soon" desc="Set OKRs, link tasks to objectives, and track progress toward quarterly goals." />
    </div>
  );
}
