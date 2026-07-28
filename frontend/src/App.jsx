import React, { useMemo, useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { ErrorBoundary, safeStr, formatDueDate } from './utils/helpers';
import { NexusProvider, useNexus } from './context/NexusContext';
import { Icon, ICON, PriorityBadge, StatusBadge, TypingIndicator, Avatar, ProgressBar } from './components/ui/SharedUI';

import Login      from './pages/Login';
import Dashboard  from './pages/Dashboard';
import Commands   from './pages/Commands';
import ChatPage   from './pages/ChatPage';
import Database   from './pages/Database';
import TeamMatrix from './pages/TeamMatrix';
import Directives from './pages/Directives';

// Placeholder pages
import Analytics       from './pages/Analytics';
import GoogleWorkspace from './pages/GoogleWorkspace';
import SettingsPage    from './pages/SettingsPage';
import ApprovalsPage   from './pages/ApprovalsPage';
import GoalsPage       from './pages/GoalsPage';
import MeetingsPage    from './pages/MeetingsPage';
import AdminPage       from './pages/AdminPage';
import ConnectionsPage from './pages/ConnectionsPage';
import TeamLeadPage    from './pages/TeamLeadPage';


/* ─── Page metadata ──────────────────────────────────────────── */
const PAGE_META = {
  dashboard:   { title: 'Dashboard',        sub: 'Real-time org overview and agent telemetry' },
  commands:    { title: 'AI Commands',       sub: 'Natural language interface to Nexus' },
  chat:        { title: 'Team Chat',          sub: 'Project channels with AI summaries' },
  tasks:       { title: 'Task Registry',     sub: 'All directives across the organization' },
  team:        { title: 'Team Matrix',       sub: 'Workload distribution and team coordination' },
  database:    { title: 'Database',          sub: 'Raw schema and AI-driven CRUD operations' },
  directives:  { title: 'My Directives',     sub: 'Tasks, meetings, and peer requests' },
  myteam:      { title: 'My Team',           sub: 'Team workload, overdue work, and escalations at a glance' },
  analytics:   { title: 'Analytics',         sub: 'Performance metrics and productivity trends' },
  google:      { title: 'Google Workspace',  sub: 'Gmail, Calendar, and Drive integration' },
  integrations:{ title: 'Integrations',      sub: 'Connect your tools and services' },
  settings:    { title: 'Settings',          sub: 'Account, preferences, and organization' },
  approvals:   { title: 'Approvals',         sub: 'Pending agent action reviews' },
  goals:       { title: 'Goals',             sub: 'OKRs and objective tracking' },
  meetings:    { title: 'Meetings',          sub: 'Scheduled meetings and transcripts' },
  admin:       { title: 'Admin',             sub: 'User management and system configuration' },
  connections: { title: 'Connections',      sub: 'Integration and service connection status' },
};

/* ─── Sidebar ────────────────────────────────────────────────── */
function Sidebar({ currentUser, activeTab, setActiveTab, isSyncing, handleDisconnect,
                  collapsed, onToggleCollapse, onNavigate }) {
  const isManager = currentUser?.role === 'Manager';

  const managerSections = [
    {
      label: 'Workspace',
      items: [
        { id: 'dashboard',  label: 'Dashboard',   icon: ICON.dashboard  },
        { id: 'commands',   label: 'AI Commands', icon: ICON.commands   },
        { id: 'chat',       label: 'Team Chat',   icon: ICON.team       },
      ],
    },
    {
      label: 'Operations',
      items: [
        { id: 'tasks',    label: 'Task Registry', icon: ICON.tasks    },
        { id: 'team',     label: 'Team Matrix',   icon: ICON.team     },
        { id: 'meetings', label: 'Meetings',      icon: ICON.meetings },
        { id: 'goals',    label: 'Goals',         icon: ICON.goals    },
      ],
    },
    {
      label: 'Intelligence',
      items: [
        { id: 'google',    label: 'Google Workspace', icon: ICON.gmail      },
        { id: 'analytics', label: 'Analytics',        icon: ICON.analytics},
      ],
    },
    {
      label: 'System',
      items: [
        { id: 'approvals',    label: 'Approvals',    icon: ICON.approvals    },
        { id: 'database',     label: 'Database',     icon: ICON.database     },
        { id: 'admin',        label: 'Admin',        icon: ICON.settings     },
        { id: 'connections',  label: 'Connections',  icon: ICON.integrations },
        { id: 'settings',     label: 'Settings',     icon: ICON.settings     },
      ],
    },
  ];

  const employeeSections = [
    {
      label: 'My Workspace',
      items: [
        { id: 'directives', label: 'My Directives', icon: ICON.directives },
        { id: 'commands',   label: 'AI Co-Pilot',   icon: ICON.commands   },
        { id: 'chat',       label: 'Team Chat',     icon: ICON.team       },
        { id: 'connections', label: 'Connections',  icon: ICON.integrations },
      ],
    },
    {
      label: 'My Data',
      items: [
        { id: 'goals',  label: 'My Goals',    icon: ICON.goals    },
        { id: 'google', label: 'My Emails',   icon: ICON.gmail    },
        { id: 'meetings', label: 'My Calendar', icon: ICON.calendar },
      ],
    },
  ];

  let sections = isManager ? managerSections : employeeSections;
  // Team leads keep the employee shell but get a live team dashboard —
  // glanceable numbers are FREE (SQL), the AI is for reasoning.
  if (!isManager && currentUser?.sysRole === 'team_lead') {
    sections = [{ label: 'Lead', items: [{ id: 'myteam', label: 'My Team', icon: ICON.team }] }, ...employeeSections];
  }

  return (
    <aside className="nx-sidebar">
      {/* Logo */}
      {/* Collapsed rail is only 60px wide — stack the toggle UNDER the logo so
          it stays reachable (side by side it gets squeezed out of view, which
          left no way to expand the sidebar again). */}
      <div style={{ padding: collapsed ? '14px 8px' : '16px 14px', borderBottom: '1px solid var(--b1)',
                    display: 'flex', alignItems: 'center', gap: collapsed ? 8 : 10,
                    flexDirection: collapsed ? 'column' : 'row' }}>
        <div style={{ width: 30, height: 30, borderRadius: 8, background: 'var(--p-bg)', border: '1px solid var(--p-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--p)', flexShrink: 0 }}>
          <Icon path={ICON.nexus} size={15} />
        </div>
        {!collapsed && (
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)', letterSpacing: '-0.01em' }}>Nexus</div>
          <div style={{ fontSize: 10, color: 'var(--t3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {isManager ? 'Chief of Staff' : 'Co-Pilot'}
          </div>
        </div>
        )}
        {/* Desktop: collapse to an icon rail. Mobile uses the drawer instead. */}
        <button
          className="nx-only-desktop"
          onClick={onToggleCollapse}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          style={{ marginLeft: collapsed ? 0 : 'auto', background: 'none', border: 'none', cursor: 'pointer',
                   color: 'var(--t3)', padding: 4, borderRadius: 6, alignItems: 'center' }}
        >
          <Icon path={collapsed ? ICON.chevronRight : ICON.chevronLeft} size={16} />
        </button>
      </div>

      {/* Nav sections */}
      <nav style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
        {sections.map(section => (
          <div key={section.label} className="nx-nav-section">
            <span className="nx-nav-label" title={section.label}>
              {collapsed ? section.label.charAt(0) : section.label}
            </span>
            {section.items.map(item => (
              <button
                key={item.id}
                onClick={() => { if (!item.soon) { setActiveTab(item.id); onNavigate?.(); } }}
                className={`nx-nav-item ${activeTab === item.id ? 'active' : ''} ${item.soon ? 'dim' : ''}`}
                title={item.soon ? 'Coming soon' : item.label}
              >
                <Icon path={item.icon} size={15} />
                {!collapsed && (
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {item.label}
                </span>
                )}
                {item.soon && !collapsed && (
                  <span style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--t4)', flexShrink: 0 }}>
                    Soon
                  </span>
                )}
              </button>
            ))}
          </div>
        ))}
      </nav>

      {/* Bottom: status + user + logout — hidden on the collapsed rail */}
      <div style={{ borderTop: '1px solid var(--b1)', padding: '10px 10px 12px',
                    display: collapsed ? 'none' : 'block' }}>
        {/* Sync status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', marginBottom: 4 }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: isSyncing ? 'var(--voice)' : 'var(--green)', flexShrink: 0, animation: isSyncing ? 'pulse-anim 1.5s infinite' : 'none' }} />
          <span style={{ fontSize: 11, color: 'var(--t3)', fontWeight: 500 }}>
            {isSyncing ? 'Syncing...' : 'Online'}
          </span>
        </div>

        {/* User info */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '7px 10px', marginBottom: 2, borderRadius: 8 }}>
          <Avatar name={currentUser?.name} size="sm" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--t1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {safeStr(currentUser?.name)}
            </div>
            <div style={{ fontSize: 11, color: 'var(--t3)', textTransform: 'capitalize' }}>
              {safeStr(currentUser?.role)}
            </div>
          </div>
        </div>

        {/* Logout */}
        <button
          onClick={handleDisconnect}
          className="nx-nav-item"
          style={{ color: 'var(--t3)', marginBottom: 0 }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--red)'; e.currentTarget.style.background = 'var(--red-bg)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--t3)'; e.currentTarget.style.background = 'transparent'; }}
        >
          <Icon path={ICON.logout} size={15} />
          <span>Disconnect</span>
        </button>
      </div>
    </aside>
  );
}

/* ─── Notification Bell ──────────────────────────────────────── */
function NotificationBell({ notifications, unreadCount, markNotificationRead, markAllNotificationsRead }) {
  const [open, setOpen] = useState(false);
  const ref = React.useRef(null);

  useEffect(() => {
    const fn = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', fn);
    return () => document.removeEventListener('mousedown', fn);
  }, []);

  const typeColor = t => t === 'task_assigned' ? 'var(--p)' : t === 'peer_request' ? 'var(--peer)' : t === 'meeting' ? 'var(--ai)' : 'var(--t3)';
  const typeLabel = t => t === 'task_assigned' ? '✓' : t === 'peer_request' ? '⇄' : t === 'meeting' ? '⊡' : '•';

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        className="btn btn-ghost btn-icon"
        style={{ position: 'relative' }}
      >
        <Icon path={ICON.bell} size={15} />
        {unreadCount > 0 && (
          <span style={{ position: 'absolute', top: -3, right: -3, minWidth: 16, height: 16, borderRadius: 99, background: 'var(--peer)', color: '#fff', fontSize: 9, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '0 3px', border: '2px solid var(--bg-1)' }}>
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="nx-notif-drop">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '1px solid var(--b1)' }}>
            <span className="nx-label">Notifications {unreadCount > 0 && <span style={{ color: 'var(--peer)' }}>({unreadCount})</span>}</span>
            {unreadCount > 0 && (
              <button onClick={markAllNotificationsRead} style={{ fontSize: 11, color: 'var(--t3)', cursor: 'pointer' }}>
                Mark all read
              </button>
            )}
          </div>
          <div style={{ maxHeight: 280, overflowY: 'auto' }}>
            {notifications.length === 0 ? (
              <div style={{ padding: '32px 16px', textAlign: 'center', fontSize: 12, color: 'var(--t3)' }}>No notifications</div>
            ) : notifications.map(n => (
              <div
                key={n.id}
                onClick={() => markNotificationRead(n.id)}
                style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '11px 16px', borderBottom: '1px solid var(--b0)', cursor: 'pointer', background: !n.is_read ? 'rgba(99,102,241,0.04)' : 'transparent', transition: 'background var(--fast)' }}
                onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-3)'}
                onMouseLeave={e => e.currentTarget.style.background = !n.is_read ? 'rgba(99,102,241,0.04)' : 'transparent'}
              >
                <div style={{ width: 24, height: 24, borderRadius: 6, background: 'var(--bg-4)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: typeColor(n.type), flexShrink: 0, marginTop: 1 }}>
                  {typeLabel(n.type)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: !n.is_read ? 600 : 400, color: !n.is_read ? 'var(--t1)' : 'var(--t2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.title}</div>
                  {n.message && <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 2 }}>{n.message}</div>}
                </div>
                {!n.is_read && <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--p)', flexShrink: 0, marginTop: 5 }} />}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Top Bar ────────────────────────────────────────────────── */
function TopBar({ activeTab, currentUser, notifications, unreadCount, markNotificationRead,
                 markAllNotificationsRead, onAudit, onOpenNav }) {
  const meta = PAGE_META[activeTab] || { title: 'Nexus', sub: '' };

  return (
    <header className="nx-topbar">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        <button
          className="nx-only-mobile"
          onClick={onOpenNav}
          aria-label="Open menu"
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--t2)',
                   padding: 6, borderRadius: 8, alignItems: 'center', flexShrink: 0 }}
        >
          <Icon path={ICON.menu} size={18} />
        </button>
        <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--t1)', letterSpacing: '-0.01em',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta.title}</div>
        <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 1, overflow: 'hidden',
                      textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta.sub}</div>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {currentUser?.role === 'Manager' && (
          <button onClick={onAudit} className="btn btn-secondary btn-sm">
            <Icon path={ICON.nexus} size={13} />
            System Audit
          </button>
        )}
        <NotificationBell
          notifications={notifications}
          unreadCount={unreadCount}
          markNotificationRead={markNotificationRead}
          markAllNotificationsRead={markAllNotificationsRead}
        />
      </div>
    </header>
  );
}

/* ─── Task Detail Modal ──────────────────────────────────────── */
function TaskModal({ task, currentUser, onClose, onCompleteSubtask, onQuickComplete }) {
  if (!task) return null;
  const subs = task.subtasks || [];
  const done = subs.filter(s => s.is_completed).length;
  const pct  = subs.length > 0 ? Math.round((done / subs.length) * 100) : (task.is_completed ? 100 : 0);

  return (
    <div className="nx-modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="nx-modal animate-scale">
        {/* Header */}
        <div className="nx-modal-header">
          <div style={{ flex: 1, marginRight: 12, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--t1)', marginBottom: 8 }}>{safeStr(task.title)}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <PriorityBadge priority={task.priority} />
              <StatusBadge isCompleted={task.is_completed} />
              {task.due_date && (
                <span style={{ fontSize: 11, color: 'var(--t3)' }}>Due {formatDueDate(task.due_date)}</span>
              )}
            </div>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-icon btn-sm">
            <Icon path={ICON.close} size={14} />
          </button>
        </div>

        {/* Description */}
        {task.description && (
          <div style={{ padding: '14px 24px', borderBottom: '1px solid var(--b1)' }}>
            <div style={{ fontSize: 13, color: 'var(--t2)', lineHeight: 1.7 }}>
              {safeStr(task.description).split('AI EXECUTION PLAN:')[0].trim()}
            </div>
          </div>
        )}

        {/* Progress */}
        {subs.length > 0 && (
          <div style={{ padding: '12px 24px', borderBottom: '1px solid var(--b1)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span className="nx-label">Progress</span>
              <span style={{ fontSize: 11, color: 'var(--t3)' }}>{done}/{subs.length}</span>
            </div>
            <ProgressBar value={pct} />
          </div>
        )}

        {/* Subtasks */}
        <div className="nx-modal-body">
          <div className="nx-label" style={{ marginBottom: 10 }}>Subtasks</div>
          {subs.length === 0 ? (
            <div style={{ padding: '24px', textAlign: 'center', fontSize: 13, color: 'var(--t3)', background: 'var(--bg-3)', borderRadius: 10, border: '1px solid var(--b1)' }}>
              No subtasks. Ask the AI to break this down.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {subs.map(st => (
                <div key={st.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', borderRadius: 8, border: `1px solid ${st.is_completed ? 'var(--green-border)' : 'var(--b1)'}`, background: st.is_completed ? 'var(--green-bg)' : 'var(--bg-3)', transition: 'all var(--base)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
                    <div style={{ width: 16, height: 16, borderRadius: 4, border: `1.5px solid ${st.is_completed ? 'var(--green)' : 'var(--b3)'}`, background: st.is_completed ? 'var(--green)' : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                      {st.is_completed && <Icon path={ICON.check} size={10} style={{ color: '#fff' }} />}
                    </div>
                    <span style={{ fontSize: 13, color: st.is_completed ? 'var(--t3)' : 'var(--t2)', textDecoration: st.is_completed ? 'line-through' : 'none', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {safeStr(st.title)}
                    </span>
                  </div>
                  {!st.is_completed && currentUser?.role === 'Employee' && (
                    <button onClick={() => onCompleteSubtask(st.id)} className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }}>Done</button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        {currentUser?.role === 'Employee' && !task.is_completed && subs.length === 0 && (
          <div className="nx-modal-footer" style={{ justifyContent: 'stretch' }}>
            <button onClick={() => { onQuickComplete(task.id); onClose(); }} className="btn btn-primary" style={{ flex: 1, justifyContent: 'center' }}>
              Mark Complete
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── Task Registry (inline) ─────────────────────────────────── */
function TaskRegistry({ tasks, employees, setSelectedTask }) {
  const empMap = useMemo(() => {
    const m = {};
    (employees || []).forEach(e => { m[e.id] = e.name; });
    return m;
  }, [employees]);

  return (
    <div className="animate-in">
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--b1)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>All Directives</div>
            <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 1 }}>{tasks.length} total</div>
          </div>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="nx-table">
            <thead>
              <tr>
                <th>Directive</th>
                <th>Assigned To</th>
                <th>Priority</th>
                <th>Status</th>
                <th>Progress</th>
                <th style={{ textAlign: 'right' }}>Due</th>
              </tr>
            </thead>
            <tbody>
              {[...tasks].reverse().map(task => {
                const subs = task.subtasks || [];
                const done = subs.filter(s => s.is_completed).length;
                const pct  = subs.length > 0 ? Math.round((done / subs.length) * 100) : (task.is_completed ? 100 : 0);
                return (
                  <tr key={task.id} onClick={() => setSelectedTask(task)}>
                    <td style={{ maxWidth: 280 }}>
                      <div style={{ fontWeight: 500, color: 'var(--t1)', fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{safeStr(task.title)}</div>
                    </td>
                    <td style={{ color: 'var(--t3)', fontSize: 12 }}>{empMap[task.owner_id] || '—'}</td>
                    <td><PriorityBadge priority={task.priority} /></td>
                    <td><StatusBadge isCompleted={task.is_completed} /></td>
                    <td style={{ width: 100 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div style={{ flex: 1 }}><ProgressBar value={pct} /></div>
                        <span style={{ fontSize: 11, color: 'var(--t3)', width: 28, textAlign: 'right', flexShrink: 0 }}>{pct}%</span>
                      </div>
                    </td>
                    <td style={{ textAlign: 'right', fontSize: 12, color: 'var(--t3)' }}>{formatDueDate(task.due_date)}</td>
                  </tr>
                );
              })}
              {tasks.length === 0 && (
                <tr><td colSpan={6} style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--t3)', fontSize: 13 }}>No tasks in the registry</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ─── Main App ───────────────────────────────────────────────── */
function MainApp() {
  const {
    currentUser, activeTab, setActiveTab, isSyncing,
    employees, tasks, selectedTask, setSelectedTask,
    handleDisconnect, handleCompleteSubtask, handleQuickComplete,
    BACKEND_URL, setAiResponse, setThoughts, setIsThinking,
    notifications, unreadCount, markNotificationRead, markAllNotificationsRead,
    handlePeerRequestAction,
  } = useNexus();

  // Sidebar: a persisted icon-rail collapse on desktop, and an off-canvas
  // drawer on mobile (separate state — a phone should never open the rail).
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem('nexus_sidebar_collapsed') === '1'
  );
  const [navOpen, setNavOpen] = useState(false);

  const toggleCollapse = useCallback(() => {
    setCollapsed(prev => {
      localStorage.setItem('nexus_sidebar_collapsed', prev ? '0' : '1');
      return !prev;
    });
  }, []);

  // Escape closes the drawer; lock body scroll behind it
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e) => { if (e.key === 'Escape') setNavOpen(false); };
    window.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [navOpen]);

  // System audit — uses currentUser.dbId (not .id — bug we fixed earlier)
  const handleManualAudit = useCallback(async () => {
    try {
      setActiveTab('commands');
      if (setThoughts)   setThoughts([]);
      if (setIsThinking) setIsThinking(true);
      if (setAiResponse) setAiResponse('');

      const agentId = currentUser?.role === 'Manager'
        ? 'Manager_1'
        : `Employee_${currentUser?.dbId}`;

      const res = await axios.post(`${BACKEND_URL}/api/v1/manager/command`, {
        manager_id:   agentId,
        command_text: 'Perform a comprehensive system audit. Use view_all_tasks and get_team_status. Summarize workload, flag overloaded employees, and identify what needs immediate attention.',
      });
      if (setIsThinking) setIsThinking(false);
      if (setAiResponse) setAiResponse(res.data.ai_response);
    } catch {
      if (setIsThinking) setIsThinking(false);
      if (setAiResponse) setAiResponse('Error: Could not reach the AI Core.');
    }
  }, [currentUser, BACKEND_URL, setActiveTab, setThoughts, setIsThinking, setAiResponse]);

  if (!currentUser) return <Login />;

  return (
    <div className={`nx-app${collapsed ? ' sidebar-collapsed' : ''}${navOpen ? ' nav-open' : ''}`}>
      {/* Tap-outside target for the mobile drawer (CSS shows it only when open) */}
      <div className="nx-nav-overlay" onClick={() => setNavOpen(false)} aria-hidden="true" />
      <Sidebar
        currentUser={currentUser}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        isSyncing={isSyncing}
        handleDisconnect={handleDisconnect}
        collapsed={collapsed}
        onToggleCollapse={toggleCollapse}
        onNavigate={() => setNavOpen(false)}
      />

      <div className="nx-main">
        <TopBar
          activeTab={activeTab}
          currentUser={currentUser}
          notifications={notifications || []}
          unreadCount={unreadCount || 0}
          markNotificationRead={markNotificationRead}
          markAllNotificationsRead={markAllNotificationsRead}
          onAudit={handleManualAudit}
          onOpenNav={() => setNavOpen(true)}
        />

        <main className="nx-page">
          {activeTab === 'dashboard'    && <Dashboard />}
          {activeTab === 'commands'     && <Commands />}
          {activeTab === 'chat'         && <ChatPage />}
          {activeTab === 'team'         && <TeamMatrix />}
          {activeTab === 'directives'   && <Directives />}
          {activeTab === 'myteam'       && <TeamLeadPage />}
          {activeTab === 'database'     && <Database />}
          {activeTab === 'analytics'    && <Analytics />}
          {activeTab === 'google'       && <GoogleWorkspace />}
          {activeTab === 'settings'     && <SettingsPage />}
          {activeTab === 'approvals'    && <ApprovalsPage />}
          {activeTab === 'goals'        && <GoalsPage />}
          {activeTab === 'meetings'     && <MeetingsPage />}
          {activeTab === 'tasks' && currentUser?.role === 'Manager' && (
            <TaskRegistry tasks={tasks || []} employees={employees || []} setSelectedTask={setSelectedTask} />
          )}
          {activeTab === 'admin'        && currentUser?.role === 'Manager' && <AdminPage />}
          {activeTab === 'connections' && <ConnectionsPage />}
        </main>
      </div>

      {selectedTask && (
        <TaskModal
          task={selectedTask}
          currentUser={currentUser}
          onClose={() => setSelectedTask(null)}
          onCompleteSubtask={handleCompleteSubtask}
          onQuickComplete={handleQuickComplete}
        />
      )}
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <NexusProvider>
        <MainApp />
      </NexusProvider>
    </ErrorBoundary>
  );
}