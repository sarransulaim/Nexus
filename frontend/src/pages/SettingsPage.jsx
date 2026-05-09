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
export default function SettingsPage() {
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
            <ROW label="Daily Briefing" sub="Morning digest of your day" />
          </SECTION>
          <SECTION title="Google Workspace">
            <ROW label="Gmail Integration" sub="Read and send emails through Nexus" />
            <ROW label="Calendar Sync" sub="Sync your Google Calendar" />
          </SECTION>
        </div>
        <ComingSoon icon={ICON.settings} title="Settings Coming Soon" desc="Profile, notification preferences, integrations, and account management." />
      </div>
    </div>
  );
}
