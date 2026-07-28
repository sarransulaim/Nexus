import React from 'react';

/* ═══════════════════════════════════════════════════════════════
   NEXUS SHARED UI COMPONENT LIBRARY
   All components follow the design system in index.css
═══════════════════════════════════════════════════════════════ */

// ── Icon helper ──────────────────────────────────────────────
export const Icon = ({ path, size = 16, className = '' }) => (
  <svg
    width={size} height={size}
    viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={1.75}
    strokeLinecap="round" strokeLinejoin="round"
    className={className}
    style={{ flexShrink: 0 }}
  >
    <path d={path} />
  </svg>
);

// ── Common icon paths ─────────────────────────────────────────
export const ICON = {
  dashboard:    'M3 13.5l7.5-7.5 4 4L21 4.5M3 20h18',
  commands:     'M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z',
  tasks:        'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4',
  team:         'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z',
  database:     'M4 7v10c0 2 1.5 3 3.5 3h9C18.5 20 20 19 20 17V7c0-2-1.5-3-3.5-3h-9C5.5 4 4 5 4 7zm0 5h16',
  directives:   'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z',
  gmail:        'M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z',
  calendar:     'M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z',
  analytics:    'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
  integrations: 'M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a2 2 0 110-4h1a1 1 0 001-1V7a1 1 0 011-1h3a1 1 0 001-1V4z',
  settings:     'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z',
  approvals:    'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
  goals:        'M13 10V3L4 14h7v7l9-11h-7z',
  meetings:     'M17 8h2a2 2 0 012 2v6a2 2 0 01-2 2h-2v4l-4-4H9a1.994 1.994 0 01-1.414-.586m0 0L11 14h4a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2v4l.586-.586z',
  bell:         'M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9',
  logout:       'M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1',
  close:        'M6 18L18 6M6 6l12 12',
  check:        'M5 13l4 4L19 7',
  chevronDown:  'M19 9l-7 7-7-7',
  chevronRight: 'M9 5l7 7-7 7',
  chevronLeft:  'M15 19l-7-7 7-7',
  menu:         'M4 6h16M4 12h16M4 18h16',
  plus:         'M12 4v16m8-8H4',
  search:       'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0',
  filter:       'M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z',
  arrow_back:   'M10 19l-7-7m0 0l7-7m-7 7h18',
  arrow_up:     'M5 10l7-7m0 0l7 7m-7-7v18',
  arrow_down:   'M19 14l-7 7m0 0l-7-7m7 7V3',
  mic:          'M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z',
  sound:        'M19.114 5.636a9 9 0 010 12.728M16.463 8.288a5.25 5.25 0 010 7.424M6.75 8.25l4.72-4.72a.75.75 0 011.28.53v15.88a.75.75 0 01-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.01 9.01 0 012.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75z',
  stop:         'M5.25 7.5A2.25 2.25 0 017.5 5.25h9a2.25 2.25 0 012.25 2.25v9a2.25 2.25 0 01-2.25 2.25h-9a2.25 2.25 0 01-2.25-2.25v-9z',
  send:         'M12 19l9 2-9-18-9 18 9-2zm0 0v-8',
  eye:          'M15 12a3 3 0 11-6 0 3 3 0 016 0z M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z',
  trash:        'M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16',
  edit:         'M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z',
  link:         'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1',
  time:         'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z',
  user:         'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z',
  slack:        'M14.5 10c-.83 0-1.5-.67-1.5-1.5v-5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5zm2.5 4c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5H17v-1.5zm-9.5-2c.83 0 1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5S6 19.33 6 18.5v-5c0-.83.67-1.5 1.5-1.5zm-2.5-4c0 .83-.67 1.5-1.5 1.5S2 8.83 2 8s.67-1.5 1.5-1.5H4V8zm11 0c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5-.67 1.5-1.5 1.5H13V8zm2.5 9.5c.83 0 1.5.67 1.5 1.5s-.67 1.5-1.5 1.5S14 19.83 14 19s.67-1.5 1.5-1.5zm-9-6c-.83 0-1.5-.67-1.5-1.5v-5C6 4.67 6.67 4 7.5 4S9 4.67 9 5.5v5c0 .83-.67 1.5-1.5 1.5z',
  nexus:        'M13 10V3L4 14h7v7l9-11h-7z',
};

// ── Stat Card ─────────────────────────────────────────────────
export function StatCard({ title, value, sub, icon, accent = 'default' }) {
  const accents = {
    default: { icon: 'var(--t3)',    bg: 'var(--bg-3)',    border: 'var(--b1)' },
    indigo:  { icon: 'var(--p)',     bg: 'var(--p-bg)',    border: 'var(--p-border)' },
    green:   { icon: 'var(--green)', bg: 'var(--green-bg)', border: 'var(--green-border)' },
    amber:   { icon: 'var(--amber)', bg: 'var(--amber-bg)', border: 'var(--amber-border)' },
    red:     { icon: 'var(--red)',   bg: 'var(--red-bg)',   border: 'var(--red-border)' },
    ai:      { icon: 'var(--ai)',    bg: 'var(--ai-bg)',    border: 'var(--ai-border)' },
  };
  const a = accents[accent] || accents.default;

  return (
    <div className="stat-card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span className="stat-label">{title}</span>
        {icon && (
          <div style={{ width: 32, height: 32, borderRadius: 8, background: a.bg, border: `1px solid ${a.border}`, display: 'flex', alignItems: 'center', justifyContent: 'center', color: a.icon }}>
            <Icon path={icon} size={15} />
          </div>
        )}
      </div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

// ── Priority Badge ────────────────────────────────────────────
export function PriorityBadge({ priority }) {
  const map = { Critical: 'red', High: 'red', Medium: 'amber', Low: 'default' };
  return <span className={`badge badge-${map[priority] || 'default'}`}>{priority || 'Medium'}</span>;
}

// ── Status Badge ──────────────────────────────────────────────
export function StatusBadge({ isCompleted }) {
  return isCompleted
    ? <span className="badge badge-green">Done</span>
    : <span className="badge badge-indigo">Active</span>;
}

// ── Typing Indicator ──────────────────────────────────────────
export function TypingIndicator() {
  return (
    <span className="nx-typing">
      <span /><span /><span />
    </span>
  );
}

// ── Empty State ───────────────────────────────────────────────
export function EmptyState({ icon = ICON.tasks, title = 'Nothing here yet', desc = '', action }) {
  return (
    <div className="nx-empty">
      <div className="nx-empty-icon">
        <Icon path={icon} size={20} />
      </div>
      <h3>{title}</h3>
      {desc && <p>{desc}</p>}
      {action}
    </div>
  );
}

// ── Spinner ───────────────────────────────────────────────────
export function Spinner({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="spin" style={{ flexShrink: 0 }}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 2v4m0 12v4M4 12H2m20 0h-2m-2.05-7.05L16.536 7.78m-9.072 8.44l-1.414 1.414M19.05 19.05l-1.414-1.414M4.95 4.95l1.414 1.414" />
    </svg>
  );
}

// ── Section header ────────────────────────────────────────────
export function SectionHeader({ title, action }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
      <span className="nx-label">{title}</span>
      {action}
    </div>
  );
}

// ── Avatar ────────────────────────────────────────────────────
export function Avatar({ name, size = 'md' }) {
  const letter = (name || '?').charAt(0).toUpperCase();
  return <div className={`nx-avatar nx-avatar-${size}`}>{letter}</div>;
}

// ── Progress Bar ──────────────────────────────────────────────
export function ProgressBar({ value, color = '' }) {
  return (
    <div className="nx-progress">
      <div className={`nx-progress-bar ${color}`} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  );
}

// ── Coming Soon Page ──────────────────────────────────────────
export function ComingSoon({ title, desc, icon }) {
  return (
    <div className="nx-coming-soon animate-in">
      {icon && (
        <div style={{ width: 56, height: 56, borderRadius: 14, background: 'var(--bg-3)', border: '1px solid var(--b1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--t3)', marginBottom: 4 }}>
          <Icon path={icon} size={24} />
        </div>
      )}
      <div className="nx-cs-badge">
        <span>●</span> In Development
      </div>
      <h2>{title}</h2>
      <p>{desc}</p>
    </div>
  );
}

// ── Divider ───────────────────────────────────────────────────
export function Divider({ style }) {
  return <hr className="nx-divider" style={style} />;
}
