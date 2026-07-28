import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { BACKEND_URL } from '../config';
import { SectionHeader, Spinner, ICON, Icon } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';

/* "Needs your decision" — the manager's action queue.
   Everything here is waiting on a HUMAN: drifted interface contracts, queued
   outward actions, escalations, work blocked by an unfinished dependency, and
   unowned tasks. One SQL snapshot, zero AI calls — checking the org is free. */

const GROUPS = [
  { key: 'drift',       label: 'Interface drift',   tone: 'amber',
    blurb: 'A producer changed after the interface was agreed — the consumer may break.' },
  { key: 'approvals',   label: 'Awaiting approval', tone: 'indigo',
    blurb: 'Queued by the AI; nothing leaves the org until you approve it.' },
  { key: 'escalations', label: 'Escalations',       tone: 'red',
    blurb: 'Someone hit a wall and needs a decision.' },
  { key: 'blocked',     label: 'Blocked work',      tone: 'amber',
    blurb: 'Started work waiting on an unfinished dependency.' },
  { key: 'unassigned',  label: 'Unassigned',        tone: 'default',
    blurb: 'Open work nobody owns — so nobody is doing it.' },
];

const badgeClass = (tone) =>
  tone === 'red' ? 'badge badge-red'
  : tone === 'amber' ? 'badge badge-amber'
  : tone === 'indigo' ? 'badge badge-indigo'
  : 'badge';

function Row({ children }) {
  return (
    <div style={{ fontSize: 12, color: 'var(--t2)', padding: '7px 0',
                  borderBottom: '1px solid var(--b1)', lineHeight: 1.5 }}>
      {children}
    </div>
  );
}

const strong = { color: 'var(--t1)', fontWeight: 500 };
const muted  = { color: 'var(--t3)' };

function renderItem(key, it) {
  if (key === 'drift') return (
    <Row key={it.id}>
      <span style={strong}>{safeStr(it.name)}</span>
      <span style={muted}> — “{safeStr(it.producer)}” ({safeStr(it.producer_owner)}) changed;
        “{safeStr(it.consumer)}” ({safeStr(it.consumer_owner)}) may need a re-check</span>
    </Row>
  );
  if (key === 'approvals') return (
    <Row key={it.id}>
      <span style={strong}>{it.type === 'send_email' ? 'Email' :
        it.type === 'create_calendar_event' ? 'Calendar invites' : safeStr(it.type)}</span>
      <span style={muted}> — {safeStr(it.detail)} · requested by {safeStr(it.by)}</span>
    </Row>
  );
  if (key === 'escalations') return (
    <Row key={it.id}>
      <span style={strong}>{safeStr(it.from)}</span>
      <span style={muted}> — {safeStr(it.reason)}</span>
    </Row>
  );
  if (key === 'blocked') return (
    <Row key={it.id}>
      <span style={strong}>{safeStr(it.title)}</span>
      <span style={muted}> ({safeStr(it.owner)}) waiting on “{safeStr(it.waiting_on)}”
        — {safeStr(it.waiting_owner)}</span>
    </Row>
  );
  return (
    <Row key={it.id}>
      <span style={strong}>{safeStr(it.title)}</span>
      <span style={muted}> — {it.priority ? `${safeStr(it.priority)} priority` : 'no priority'}
        {it.due ? ` · due ${safeStr(it.due)}` : ''}</span>
    </Row>
  );
}

export default function AttentionPanel({ onOpenApprovals }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr]         = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await axios.get(`${BACKEND_URL}/api/v1/attention/`);
      setData(res.data); setErr(null);
    } catch (e) {
      setErr(e.response?.data?.detail || 'Could not load the attention queue.');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60000);   // stays fresh; still costs nothing
    return () => clearInterval(t);
  }, [load]);

  if (loading) return (
    <div className="card" style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
      <Spinner size={18} />
    </div>
  );
  if (err) return (
    <div className="card" style={{ fontSize: 13, color: 'var(--t3)' }}>{err}</div>
  );
  if (!data) return null;

  const groups = GROUPS.filter(g => (data.items?.[g.key] || []).length > 0);
  const total  = data.total || 0;

  return (
    <div className="card">
      <SectionHeader
        title="Needs your decision"
        action={
          <span style={{ fontSize: 11, color: 'var(--t3)' }}>
            {data.scope === 'team' ? 'your team' : 'organization'} · live
          </span>
        }
      />

      {total === 0 ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '18px 2px' }}>
          <Icon path={ICON.check} size={16} />
          <div>
            <div style={{ fontSize: 13, color: 'var(--t1)', fontWeight: 500 }}>Nothing needs you right now.</div>
            <div style={{ fontSize: 12, color: 'var(--t3)' }}>
              No drifted interfaces, no queued sends, no escalations, nothing blocked or unowned.
            </div>
          </div>
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '2px 0 12px' }}>
            {GROUPS.map(g => {
              const n = (data.items?.[g.key] || []).length;
              if (!n) return null;
              return <span key={g.key} className={badgeClass(g.tone)}>{n} {g.label.toLowerCase()}</span>;
            })}
          </div>

          {groups.map(g => (
            <div key={g.key} style={{ marginBottom: 14 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 2 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--t1)' }}>{g.label}</span>
                <span style={{ fontSize: 11, color: 'var(--t3)' }}>{g.blurb}</span>
              </div>
              {(data.items[g.key] || []).map(it => renderItem(g.key, it))}
              {g.key === 'approvals' && onOpenApprovals && (
                <button className="btn btn-sm" style={{ marginTop: 8 }} onClick={onOpenApprovals}>
                  Review approvals →
                </button>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
