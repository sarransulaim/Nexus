import React from 'react';
import { ICON, ComingSoon } from '../components/ui/SharedUI';
const mock = [
  { action: 'Delete Employee', agent: 'Manager_1', reason: 'Employee terminated', time: '5 min ago' },
  { action: 'Send Bulk Email', agent: 'Employee_3', reason: 'Project announcement', time: '2 hours ago' },
];
export default function ApprovalsPage() {
  return (
    <div className="animate-in">
      <div style={{ opacity: 0.45, pointerEvents: 'none', filter: 'blur(0.5px)', marginBottom: 24 }}>
        {mock.map((a, i) => (
          <div key={i} className="card" style={{ marginBottom: 10, display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span className="badge badge-amber">Pending</span>
                <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{a.action}</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--t3)' }}>By {a.agent} · {a.time}</div>
              <div style={{ fontSize: 13, color: 'var(--t2)', marginTop: 4 }}>{a.reason}</div>
            </div>
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              <button className="btn btn-sm" style={{ background: 'var(--green-bg)', color: 'var(--green)', borderColor: 'var(--green-border)' }}>Approve</button>
              <button className="btn btn-danger btn-sm">Reject</button>
            </div>
          </div>
        ))}
      </div>
      <ComingSoon icon={ICON.approvals} title="Approvals Coming Soon" desc="Review and approve high-impact agent actions before they execute." />
    </div>
  );
}
