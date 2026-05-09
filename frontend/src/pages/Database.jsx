import React, { useState } from 'react';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, PriorityBadge, StatusBadge } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';

export default function Database() {
  const { employees, tasks, meetings, handleDbAction, setActiveTab, setManualCommand } = useNexus();
  const [table, setTable] = useState('employees');

  const tables = [
    { id: 'employees', label: 'Employees', count: (employees || []).length },
    { id: 'tasks',     label: 'Tasks',     count: (tasks || []).length },
    { id: 'meetings',  label: 'Meetings',  count: (meetings || []).length },
  ];

  const handleInsert = () => {
    setActiveTab('commands');
    if (setManualCommand) setManualCommand(`Add a new record to ${table}: `);
  };

  return (
    <div className="animate-in">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        {/* Tab switcher */}
        <div style={{ display: 'flex', gap: 2, background: 'var(--bg-2)', border: '1px solid var(--b1)', borderRadius: 10, padding: 4 }}>
          {tables.map(t => (
            <button
              key={t.id}
              onClick={() => setTable(t.id)}
              style={{ padding: '5px 14px', borderRadius: 7, fontSize: 13, fontWeight: 500, cursor: 'pointer', transition: 'all var(--fast)', background: table === t.id ? 'var(--bg-4)' : 'transparent', color: table === t.id ? 'var(--t1)' : 'var(--t3)', border: 'none' }}
            >
              {t.label}
              <span style={{ marginLeft: 5, fontSize: 11, color: table === t.id ? 'var(--p)' : 'var(--t4)', fontFamily: 'var(--font-mono)' }}>{t.count}</span>
            </button>
          ))}
        </div>

        <button onClick={handleInsert} className="btn btn-secondary btn-sm">
          <Icon path={ICON.plus} size={12} /> Insert Row
        </button>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ overflowX: 'auto' }}>
          <table className="nx-table">
            {table === 'employees' && (
              <>
                <thead><tr><th>ID</th><th>Name</th><th>Role</th><th>Team</th><th>Exp</th><th style={{ textAlign: 'right' }}>Actions</th></tr></thead>
                <tbody>
                  {[...(employees || [])].reverse().slice(0, 100).map(emp => (
                    <tr key={emp.id}>
                      <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--t3)', fontSize: 12 }}>{emp.id}</td>
                      <td style={{ fontWeight: 500, color: 'var(--t1)' }}>{safeStr(emp.name)}</td>
                      <td>{safeStr(emp.role)}</td>
                      <td><span className="badge badge-indigo">{safeStr(emp.team) || 'Unassigned'}</span></td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{emp.experience}y</td>
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                          <button onClick={() => handleDbAction(`Update employee ID ${emp.id}: `)} className="btn btn-ghost btn-sm"><Icon path={ICON.edit} size={11} /> Edit</button>
                          <button onClick={() => handleDbAction(`Delete employee ID ${emp.id}.`)} className="btn btn-danger btn-sm"><Icon path={ICON.trash} size={11} /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {(employees || []).length === 0 && <tr><td colSpan={6} style={{ textAlign: 'center', padding: '48px', color: 'var(--t3)' }}>No employees found</td></tr>}
                </tbody>
              </>
            )}

            {table === 'tasks' && (
              <>
                <thead><tr><th>ID</th><th>Title</th><th>Owner</th><th>Priority</th><th>Status</th><th style={{ textAlign: 'right' }}>Actions</th></tr></thead>
                <tbody>
                  {[...(tasks || [])].reverse().slice(0, 100).map(task => (
                    <tr key={task.id}>
                      <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--t3)', fontSize: 12 }}>{task.id}</td>
                      <td style={{ maxWidth: 220 }}><div style={{ fontWeight: 500, color: 'var(--t1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{safeStr(task.title)}</div></td>
                      <td style={{ fontSize: 12 }}>ID {task.owner_id}</td>
                      <td><PriorityBadge priority={task.priority} /></td>
                      <td><StatusBadge isCompleted={task.is_completed} /></td>
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                          <button onClick={() => handleDbAction(`Reassign task ID ${task.id} to employee ID `)} className="btn btn-ghost btn-sm"><Icon path={ICON.edit} size={11} /> Edit</button>
                          <button onClick={() => handleDbAction(`Delete task ID ${task.id}.`)} className="btn btn-danger btn-sm"><Icon path={ICON.trash} size={11} /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {(tasks || []).length === 0 && <tr><td colSpan={6} style={{ textAlign: 'center', padding: '48px', color: 'var(--t3)' }}>No tasks found</td></tr>}
                </tbody>
              </>
            )}

            {table === 'meetings' && (
              <>
                <thead><tr><th>ID</th><th>Topic</th><th>Scheduled</th><th>Attendees</th><th style={{ textAlign: 'right' }}>Actions</th></tr></thead>
                <tbody>
                  {[...(meetings || [])].reverse().slice(0, 100).map(m => (
                    <tr key={m.id}>
                      <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--t3)', fontSize: 12 }}>{m.id}</td>
                      <td style={{ fontWeight: 500, color: 'var(--t1)' }}>{safeStr(m.topic)}</td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{safeStr(m.scheduled_time)}</td>
                      <td style={{ fontSize: 12 }}>{m.attendees?.map(a => a.name).join(', ') || m.attendee_ids || '—'}</td>
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                          <button onClick={() => handleDbAction(`Reschedule meeting ID ${m.id} to `)} className="btn btn-ghost btn-sm"><Icon path={ICON.edit} size={11} /> Edit</button>
                          <button onClick={() => handleDbAction(`Delete meeting ID ${m.id}.`)} className="btn btn-danger btn-sm"><Icon path={ICON.trash} size={11} /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {(meetings || []).length === 0 && <tr><td colSpan={5} style={{ textAlign: 'center', padding: '48px', color: 'var(--t3)' }}>No meetings found</td></tr>}
                </tbody>
              </>
            )}
          </table>
        </div>
      </div>
    </div>
  );
}
