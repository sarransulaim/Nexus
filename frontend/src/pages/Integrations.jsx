import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { useNexus } from '../context/NexusContext';
import { Spinner } from '../components/ui/SharedUI';

// Channels the backend actually supports — status comes from /integrations/status.
const SUPPORTED = [
  { key: 'slack',    name: 'Slack',            cat: 'Team chat',        color: '#E01E5A' },
  { key: 'whatsapp', name: 'WhatsApp',         cat: 'Messaging',        color: '#25D366' },
  { key: 'telegram', name: 'Telegram',         cat: 'Messaging',        color: '#229ED9' },
  { key: 'google',   name: 'Google Workspace', cat: 'Email · Calendar', color: '#4285F4' },
];

// Genuinely not built yet — shown honestly as roadmap, not fake-connected.
const ROADMAP = [
  { name: 'Jira',       cat: 'Project Mgmt', color: '#0052CC' },
  { name: 'Salesforce', cat: 'CRM',          color: '#00A1E0' },
  { name: 'Asana',      cat: 'Project Mgmt', color: '#F06A6A' },
  { name: 'HubSpot',    cat: 'CRM',          color: '#FF7A59' },
  { name: 'Zoom',       cat: 'Meetings',     color: '#2D8CFF' },
  { name: 'Stripe',     cat: 'Finance',      color: '#635BFF' },
  { name: 'Linear',     cat: 'Project Mgmt', color: '#5E6AD2' },
  { name: 'Notion',     cat: 'Docs',         color: '#111111' },
  { name: 'Workday',    cat: 'HR',           color: '#005CB9' },
];

const Logo = ({ color }) => (
  <div style={{ width: 38, height: 38, borderRadius: 10, background: `${color}18`, border: `1px solid ${color}30`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
    <div style={{ width: 18, height: 18, borderRadius: 5, background: color }} />
  </div>
);

const SectionLabel = ({ children }) => (
  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t2)', margin: '4px 0 12px', paddingBottom: 8, borderBottom: '1px solid var(--b1)' }}>{children}</div>
);

export default function Integrations() {
  const { BACKEND_URL } = useNexus();
  const [status, setStatus] = useState(null);

  useEffect(() => {
    axios.get(`${BACKEND_URL}/api/v1/integrations/status`)
      .then(r => setStatus(r.data || {}))
      .catch(() => setStatus({}));
  }, [BACKEND_URL]);

  if (!status) return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spinner size={20} /></div>;

  return (
    <div className="animate-in">
      <SectionLabel>Channels Nexus supports</SectionLabel>
      <div className="nx-grid-3" style={{ marginBottom: 28 }}>
        {SUPPORTED.map(i => {
          const on = !!status[i.key];
          return (
            <div key={i.key} className="card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                <Logo color={i.color} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{i.name}</div>
                  <div style={{ fontSize: 11.5, color: 'var(--t3)' }}>{i.cat}</div>
                </div>
              </div>
              {on
                ? <span className="badge" style={{ background: 'var(--green-bg)', color: 'var(--green)', borderColor: 'var(--green-border)' }}>● Configured</span>
                : <span className="badge" style={{ color: 'var(--t3)' }}>Not configured</span>}
            </div>
          );
        })}
      </div>

      <SectionLabel>On the roadmap</SectionLabel>
      <div className="nx-grid-3" style={{ opacity: 0.62 }}>
        {ROADMAP.map(i => (
          <div key={i.name} className="card" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Logo color={i.color} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{i.name}</div>
              <div style={{ fontSize: 11.5, color: 'var(--t3)' }}>{i.cat}</div>
            </div>
            <span className="badge badge-indigo" style={{ flexShrink: 0 }}>Soon</span>
          </div>
        ))}
      </div>
    </div>
  );
}
