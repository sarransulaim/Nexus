import React from 'react';
import { Icon, ICON, ComingSoon } from '../components/ui/SharedUI';

export default function Analytics() {
  return (
    <div className="animate-in">
      {/* Show the real layout skeleton so demo looks complete */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, marginBottom: 20, opacity: 0.35, pointerEvents: 'none', filter: 'blur(1px)' }}>
        {['Agent Efficiency', 'Tasks Completed', 'Avg Response Time', 'Active Users'].map(label => (
          <div key={label} className="stat-card">
            <div className="stat-label">{label}</div>
            <div className="stat-value">—</div>
          </div>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 20, opacity: 0.35, pointerEvents: 'none', filter: 'blur(1px)' }}>
        <div className="card" style={{ height: 200 }}><div className="nx-label" style={{ marginBottom: 8 }}>Productivity Trend</div><div style={{ height: '100%', background: 'var(--bg-3)', borderRadius: 8 }} /></div>
        <div className="card" style={{ height: 200 }}><div className="nx-label" style={{ marginBottom: 8 }}>Team Performance</div><div style={{ height: '100%', background: 'var(--bg-3)', borderRadius: 8 }} /></div>
      </div>
      <ComingSoon
        icon={ICON.analytics}
        title="Analytics Coming Soon"
        desc="Performance metrics, productivity trends, agent effectiveness scores, and workload forecasting will appear here."
      />
    </div>
  );
}
