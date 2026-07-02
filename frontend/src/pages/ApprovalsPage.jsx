import React, { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { useNexus } from '../context/NexusContext';
import { ICON, EmptyState, Spinner } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';

const fmtTime = (iso) => {
  try { return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); }
  catch { return ''; }
};

const ACTION_LABEL = {
  resolve_contract_drift: 'Resolve interface drift',
  send_email:             'Send email',
  create_calendar_event:  'Send calendar invites',
};

// Outward actions (real email/invites leave the org when you approve) get a
// readable preview so the reviewer sees EXACTLY what goes out.
function OutwardAction({ type, p }) {
  const isEmail = type === 'send_email';
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontSize: 12, color: 'var(--t3)', marginBottom: 6 }}>
        {isEmail
          ? <>To: <span style={{ color: 'var(--t2)', fontWeight: 600 }}>{safeStr(p.to)}</span></>
          : <>Invites to: <span style={{ color: 'var(--t2)', fontWeight: 600 }}>{(p.attendee_emails || []).join(', ')}</span></>}
      </div>
      <div style={{ fontSize: 13, color: 'var(--t1)', fontWeight: 600, marginBottom: 6 }}>
        {safeStr(isEmail ? p.subject : p.title)}
        {!isEmail && p.start_time ? <span style={{ color: 'var(--t3)', fontWeight: 400 }}>  ·  {fmtTime(p.start_time)}</span> : null}
      </div>
      {(isEmail ? p.body : p.description) && (
        <div style={{ fontSize: 13, color: 'var(--t2)', background: 'var(--bg-3)', border: '1px solid var(--b1)',
                      borderRadius: 8, padding: '8px 10px', whiteSpace: 'pre-wrap', maxHeight: 180, overflowY: 'auto' }}>
          {safeStr(isEmail ? p.body : p.description)}
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--t3)', marginTop: 6 }}>
        ⚠️ Approving executes this immediately — it leaves the organization.
      </div>
    </div>
  );
}

const effortStyle = (e) => e === 'low'
  ? { background: 'var(--green-bg)', color: 'var(--green)', borderColor: 'var(--green-border)' }
  : {};

// Readable view of a resolution-engine proposal (the headline feature).
function DriftProposal({ p }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontSize: 12, color: 'var(--t3)', marginBottom: 6 }}>
        <span style={{ color: 'var(--t2)', fontWeight: 600 }}>{safeStr(p.producer_task)}</span>
        {' → '}
        <span style={{ color: 'var(--t2)', fontWeight: 600 }}>{safeStr(p.consumer_task)}</span>
        {p.contract_name ? `  ·  ${safeStr(p.contract_name)}` : ''}
      </div>
      {p.summary && <div style={{ fontSize: 13, color: 'var(--t1)', fontWeight: 500, marginBottom: 6 }}>{safeStr(p.summary)}</div>}
      <div style={{ fontSize: 13, color: 'var(--t2)', background: 'var(--p-bg)', border: '1px solid var(--p-border)', borderRadius: 8, padding: '8px 10px' }}>
        <span style={{ color: 'var(--p)', fontWeight: 600 }}>Proposed fix · </span>{safeStr(p.proposed_fix)}
      </div>
      {p.rationale && <div style={{ fontSize: 12, color: 'var(--t3)', marginTop: 6 }}>Why: {safeStr(p.rationale)}</div>}
      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        {p.effort && <span className={p.effort === 'low' ? 'badge' : 'badge badge-amber'} style={effortStyle(p.effort)}>{safeStr(p.effort)} effort</span>}
        {p.who && <span className="badge badge-indigo">fix on: {safeStr(p.who)}</span>}
      </div>
    </div>
  );
}

export default function ApprovalsPage() {
  const { BACKEND_URL } = useNexus();
  const [items,   setItems]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy,    setBusy]    = useState(null);
  const [err,     setErr]     = useState(null);

  const load = useCallback(async () => {
    try { const res = await axios.get(`${BACKEND_URL}/api/v1/approvals/?status=pending`); setItems(res.data?.approvals || []); }
    catch { setItems([]); }
    finally { setLoading(false); }
  }, [BACKEND_URL]);
  useEffect(() => { load(); }, [load]);

  const review = async (id, action) => {
    setBusy(id); setErr(null);
    try { await axios.post(`${BACKEND_URL}/api/v1/approvals/${id}/${action}`, { note: '' }); setItems(prev => prev.filter(a => a.id !== id)); }
    catch (e) { setErr(e.response?.data?.detail || `Could not ${action} that — please try again.`); }
    finally { setBusy(null); }
  };

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spinner size={20} /></div>;
  if (!items.length) return <EmptyState icon={ICON.approvals} title="Nothing to review" desc="High-impact agent actions and proposed fixes that need your sign-off appear here." />;

  return (
    <div className="animate-in" style={{ maxWidth: 720 }}>
      {err && (
        <div style={{ color: '#ef4444', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)',
                      padding: '10px 14px', borderRadius: 8, marginBottom: 12, fontSize: 13 }}>{err}</div>
      )}
      {items.map(a => {
        const isDrift   = a.action_type === 'resolve_contract_drift';
        const isOutward = a.action_type === 'send_email' || a.action_type === 'create_calendar_event';
        return (
          <div key={a.id} className="card" style={{ marginBottom: 10, display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span className="badge badge-amber">Pending</span>
                <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{ACTION_LABEL[a.action_type] || safeStr(a.action_type)}</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--t3)' }}>
                Requested by {safeStr(a.requested_by)}{a.created_at ? ` · ${fmtTime(a.created_at)}` : ''}
              </div>
              {isDrift && a.payload
                ? <DriftProposal p={a.payload} />
                : isOutward && a.payload
                ? <OutwardAction type={a.action_type} p={a.payload} />
                : a.payload != null && (
                    <div style={{ fontSize: 12, color: 'var(--t2)', marginTop: 6, fontFamily: 'var(--font-mono)', background: 'var(--bg-3)', border: '1px solid var(--b1)', borderRadius: 8, padding: '8px 10px', overflowX: 'auto', whiteSpace: 'pre-wrap' }}>
                      {typeof a.payload === 'string' ? a.payload : JSON.stringify(a.payload, null, 1)}
                    </div>
                  )}
            </div>
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              <button disabled={busy === a.id} className="btn btn-sm" style={{ background: 'var(--green-bg)', color: 'var(--green)', borderColor: 'var(--green-border)' }} onClick={() => review(a.id, 'approve')}>
                {isOutward ? 'Approve & Send' : 'Approve'}
              </button>
              <button disabled={busy === a.id} className="btn btn-danger btn-sm" onClick={() => review(a.id, 'reject')}>Reject</button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
