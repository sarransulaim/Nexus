// SettingsPage.jsx
import React from 'react';
import { Icon, ICON, ComingSoon } from '../components/ui/SharedUI';

const SECTION = ({ title, children }) => (
  <div style={{ marginBottom: 24 }}>
    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t2)', marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid var(--b1)' }}>{title}</div>
    {children}
  </div>
);

const ROW = ({ label, sub }) => (
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 0' }}>
    <div>
      <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--t1)' }}>{label}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 1 }}>{sub}</div>}
    </div>
    <div style={{ width: 60, height: 24, borderRadius: 99, background: 'var(--bg-4)', border: '1px solid var(--b1)' }} />
  </div>
);

export function SettingsPage() {
  return (
    <div className="animate-in">
      <div style={{ maxWidth: 560 }}>
        <div style={{ opacity: 0.45, pointerEvents: 'none', filter: 'blur(0.5px)', marginBottom: 24 }}>
          <SECTION title="Profile">
            <ROW label="Display Name" sub="How you appear in the system" />
            <ROW label="Email Address" sub="For notifications and alerts" />
            <ROW label="Time Zone" sub="Used for scheduling and reminders" />
          </SECTION>
          <SECTION title="Notifications">
            <ROW label="Task Assignments" sub="Get notified when tasks are assigned" />
            <ROW label="Peer Requests" sub="Alerts for incoming assistance requests" />
            <ROW label="Meeting Reminders" sub="Reminders before scheduled meetings" />
            <ROW label="Daily Briefing" sub="Morning digest of your day" />
          </SECTION>
          <SECTION title="Google Workspace">
            <ROW label="Gmail Integration" sub="Read and send emails through Nexus" />
            <ROW label="Calendar Sync" sub="Sync your Google Calendar" />
          </SECTION>
          <SECTION title="Danger Zone">
            <ROW label="Delete Account" sub="Permanently remove your data" />
          </SECTION>
        </div>
        <ComingSoon icon={ICON.settings} title="Settings Coming Soon" desc="Profile, notification preferences, integrations, and account management." />
      </div>
    </div>
  );
}

// ApprovalsPage.jsx
export function ApprovalsPage() {
  const mockApprovals = [
    { action: 'Delete Employee', agent: 'Manager_1', reason: 'Employee terminated', created: '5 min ago' },
    { action: 'Send Bulk Email', agent: 'Employee_3', reason: 'Project announcement', created: '2 hours ago' },
  ];

  return (
    <div className="animate-in">
      <div style={{ opacity: 0.45, pointerEvents: 'none', filter: 'blur(0.5px)', marginBottom: 24 }}>
        {mockApprovals.map((a, i) => (
          <div key={i} className="card" style={{ marginBottom: 10, display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span className="badge badge-amber">Pending</span>
                <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{a.action}</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--t3)' }}>Requested by {a.agent} · {a.created}</div>
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

// GoalsPage.jsx
export function GoalsPage() {
  const mockGoals = [
    { title: 'Ship MVP by Q2', progress: 65, status: 'active', tasks: 8 },
    { title: 'Onboard 3 enterprise clients', progress: 33, status: 'active', tasks: 4 },
    { title: 'Reduce response time by 40%', progress: 80, status: 'active', tasks: 3 },
  ];

  return (
    <div className="animate-in">
      <div style={{ opacity: 0.45, pointerEvents: 'none', filter: 'blur(0.5px)', marginBottom: 24 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(260px, 100%), 1fr))', gap: 14 }}>
          {mockGoals.map((g, i) => (
            <div key={i} className="card">
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)', flex: 1, marginRight: 8 }}>{g.title}</div>
                <div style={{ position: 'relative', width: 44, height: 44, flexShrink: 0 }}>
                  <svg width={44} height={44} viewBox="0 0 44 44" style={{ transform: 'rotate(-90deg)' }}>
                    <circle cx={22} cy={22} r={18} fill="none" stroke="var(--b1)" strokeWidth={4} />
                    <circle cx={22} cy={22} r={18} fill="none" stroke="var(--p)" strokeWidth={4} strokeDasharray={113} strokeDashoffset={113 - (113 * g.progress / 100)} strokeLinecap="round" />
                  </svg>
                  <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: 'var(--t2)' }}>{g.progress}%</div>
                </div>
              </div>
              <div style={{ height: 3, background: 'var(--b1)', borderRadius: 99, overflow: 'hidden', marginBottom: 10 }}>
                <div style={{ height: '100%', width: `${g.progress}%`, background: 'var(--p)', borderRadius: 99 }} />
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

// MeetingsPage.jsx
export function MeetingsPage() {
  return (
    <div className="animate-in">
      <div style={{ opacity: 0.4, pointerEvents: 'none', filter: 'blur(0.5px)', marginBottom: 24 }}>
        {[1, 2, 3].map(i => (
          <div key={i} className="card" style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{ width: 44, height: 44, borderRadius: 10, background: 'var(--p-bg)', border: '1px solid var(--p-border)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--p)', lineHeight: 1 }}>{10 + i}</div>
              <div style={{ fontSize: 9, color: 'var(--p-muted)', textTransform: 'uppercase' }}>May</div>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--t1)', marginBottom: 2 }}>Team Standup {i}</div>
              <div style={{ fontSize: 12, color: 'var(--t3)' }}>9:00 AM · 30 min · 4 attendees</div>
            </div>
            <span className="badge badge-indigo">Upcoming</span>
          </div>
        ))}
      </div>
      <ComingSoon icon={ICON.meetings} title="Meeting Intelligence Coming Soon" desc="Transcriptions, AI summaries, action item extraction, and calendar conflict detection." />
    </div>
  );
}
