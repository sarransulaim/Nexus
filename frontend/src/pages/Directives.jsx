import React, { useMemo } from 'react';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, PriorityBadge, StatusBadge, EmptyState, ProgressBar } from '../components/ui/SharedUI';
import { formatDueDate, safeStr } from '../utils/helpers';

export default function Directives() {
  const { currentUser, tasks, meetings, employees, setSelectedTask, handlePeerRequestAction } = useNexus();

  const empMap = useMemo(() => {
    const m = {};
    (employees || []).forEach(e => { m[e.id] = e.name; });
    return m;
  }, [employees]);

  const myTasks = useMemo(() =>
    (tasks || []).filter(t => String(t.owner_id) === String(currentUser?.dbId)),
    [tasks, currentUser]
  );

  const { pending, assisting } = useMemo(() => {
    const p = [], a = [];
    (tasks || []).forEach(t => {
      (t.peer_requests || []).forEach(r => {
        if (String(r.recipient_id) === String(currentUser?.dbId)) {
          if (r.status === 'Pending')   p.push({ ...t, prId: r.id, from: empMap[t.owner_id] || `Unit ${t.owner_id}`, topic: r.topic });
          if (r.status === 'Accepted')  a.push({ ...t, prId: r.id, for: empMap[t.owner_id], topic: r.topic });
        }
      });
    });
    return { pending: p, assisting: a };
  }, [tasks, currentUser, empMap]);

  const myMeetings = useMemo(() => {
    if (currentUser?.role !== 'Employee') return [];
    return (meetings || []).filter(m => {
      if (!m.attendee_ids) return false;
      return m.attendee_ids.split(',').map(id => id.trim()).includes(String(currentUser?.dbId));
    });
  }, [meetings, currentUser]);

  const hasContent = myTasks.length || assisting.length || myMeetings.length || pending.length;

  if (!hasContent) {
    return <EmptyState icon={ICON.directives} title="All clear" desc="No active directives, meetings, or peer requests right now." />;
  }

  return (
    <div className="animate-in" style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>

      {/* Peer requests */}
      {pending.length > 0 && (
        <section>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid var(--b1)' }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--peer)', animation: 'pulse-anim 2s infinite' }} />
            <span className="nx-label">Assistance Requests <span style={{ color: 'var(--peer)' }}>({pending.length})</span></span>
          </div>
          <div className="nx-grid-auto">
            {pending.map(req => (
              <div key={`r-${req.prId}`} className="card card-peer">
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                  <span className="badge badge-peer">Action Required</span>
                  <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--peer)', animation: 'pulse-anim 2s infinite' }} />
                </div>
                <div style={{ fontSize: 12, color: 'var(--peer)', opacity: 0.7, marginBottom: 3 }}>From: {req.from}</div>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)', fontStyle: 'italic', marginBottom: 14 }}>"{req.topic}"</div>
                <div style={{ display: 'flex', gap: 8, paddingTop: 12, borderTop: '1px solid var(--peer-border)' }}>
                  <button onClick={() => handlePeerRequestAction(req.prId, 'Accepted')} className="btn" style={{ flex: 1, justifyContent: 'center', background: 'var(--green-bg)', color: 'var(--green)', borderColor: 'var(--green-border)', fontSize: 12 }}>Accept</button>
                  <button onClick={() => handlePeerRequestAction(req.prId, 'Declined')} className="btn" style={{ flex: 1, justifyContent: 'center', background: 'var(--red-bg)', color: 'var(--red)', borderColor: 'var(--red-border)', fontSize: 12 }}>Decline</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Meetings */}
      {myMeetings.length > 0 && (
        <section>
          <div style={{ marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid var(--b1)' }}>
            <span className="nx-label">Upcoming Meetings</span>
          </div>
          <div className="nx-grid-auto">
            {myMeetings.map(m => (
              <div key={`m-${m.id}`} className="card" style={{ borderLeft: '3px solid var(--p)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--p-bg)', border: '1px solid var(--p-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--p)', flexShrink: 0 }}>
                    <Icon path={ICON.calendar} size={16} />
                  </div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--t1)' }}>{safeStr(m.topic)}</div>
                    <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 2, fontFamily: 'var(--font-mono)' }}>{safeStr(m.scheduled_time)}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* My tasks + assisting */}
      {(myTasks.length > 0 || assisting.length > 0) && (
        <section>
          <div style={{ marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid var(--b1)' }}>
            <span className="nx-label">Active Directives</span>
          </div>
          <div className="nx-grid-auto">
            {[...myTasks].reverse().map(task => {
              const subs = task.subtasks || [];
              const done = subs.filter(s => s.is_completed).length;
              const pct  = subs.length > 0 ? Math.round((done / subs.length) * 100) : (task.is_completed ? 100 : 0);
              const collab = (task.peer_requests || []).filter(r => r.status === 'Accepted');

              return (
                <div key={task.id} onClick={() => setSelectedTask(task)} className="card card-hover" style={{ borderLeft: '3px solid var(--p)', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10 }}>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      <PriorityBadge priority={task.priority} />
                      <StatusBadge isCompleted={task.is_completed} />
                    </div>
                    <span style={{ fontSize: 11, color: 'var(--t3)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>{formatDueDate(task.due_date)}</span>
                  </div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)', marginBottom: 10 }}>{safeStr(task.title)}</div>
                  {collab.length > 0 && (
                    <div className="card-ai card-sm" style={{ marginBottom: 10, fontSize: 12 }}>
                      <div className="nx-label" style={{ color: 'var(--ai)', marginBottom: 2 }}>Collaboration active</div>
                      {collab.map(c => <div key={c.id} style={{ color: 'var(--t2)' }}>{empMap[c.recipient_id]} helping</div>)}
                    </div>
                  )}
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                      <span className="nx-label">Progress</span>
                      <span style={{ fontSize: 11, color: 'var(--t3)' }}>{pct}%</span>
                    </div>
                    <ProgressBar value={pct} />
                  </div>
                </div>
              );
            })}

            {assisting.map(task => (
              <div key={`s-${task.id}`} className="card card-peer">
                <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
                  <span className="badge badge-peer">Assisting</span>
                  <span className="badge badge-default">Side Task</span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--peer)', opacity: 0.7, marginBottom: 3 }}>Helping: {task.for}</div>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)', fontStyle: 'italic', marginBottom: 14 }}>"{task.topic}"</div>
                <button onClick={() => handlePeerRequestAction(task.prId, 'Completed')} className="btn" style={{ width: '100%', justifyContent: 'center', background: 'var(--peer-bg)', color: 'var(--peer)', borderColor: 'var(--peer-border)', fontSize: 12 }}>
                  Mark Complete
                </button>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
