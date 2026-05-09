import React from 'react';
import { Icon, ICON, ComingSoon } from '../components/ui/SharedUI';

const INTEGRATIONS = [
  { name: 'Slack',       cat: 'Communication',   color: '#E01E5A', connected: false },
  { name: 'Jira',        cat: 'Project Mgmt',    color: '#0052CC', connected: false },
  { name: 'Salesforce',  cat: 'CRM',             color: '#00A1E0', connected: false },
  { name: 'WhatsApp',    cat: 'Communication',   color: '#25D366', connected: false },
  { name: 'Asana',       cat: 'Project Mgmt',    color: '#F06A6A', connected: false },
  { name: 'HubSpot',     cat: 'CRM',             color: '#FF7A59', connected: false },
  { name: 'Zoom',        cat: 'Meetings',         color: '#2D8CFF', connected: false },
  { name: 'Stripe',      cat: 'Finance',          color: '#635BFF', connected: false },
  { name: 'Linear',      cat: 'Project Mgmt',    color: '#5E6AD2', connected: false },
  { name: 'Notion',      cat: 'Docs',            color: '#000000', connected: false },
  { name: 'Workday',     cat: 'HR',              color: '#005CB9', connected: false },
  { name: 'Custom API',  cat: 'Developer',        color: '#10b981', connected: false },
];

export default function Integrations() {
  return (
    <div className="animate-in">
      {/* Show the marketplace skeleton */}
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>Integration Marketplace</div>
          <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 2 }}>Connect your existing tools</div>
        </div>
        <span className="nx-cs-badge">In Development</span>
      </div>

      <div style={{ opacity: 0.5, pointerEvents: 'none', filter: 'blur(0.5px)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
          {INTEGRATIONS.map(intg => (
            <div key={intg.name} className="card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                <div style={{ width: 38, height: 38, borderRadius: 10, background: `${intg.color}18`, border: `1px solid ${intg.color}30`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <div style={{ width: 18, height: 18, borderRadius: 4, background: intg.color, opacity: 0.7 }} />
                </div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{intg.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--t3)' }}>{intg.cat}</div>
                </div>
              </div>
              <button className="btn btn-ghost btn-sm" style={{ width: '100%', justifyContent: 'center', fontSize: 11 }}>Connect</button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
