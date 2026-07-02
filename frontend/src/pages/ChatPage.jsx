import React, { useState, useEffect, useRef } from 'react';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, Spinner } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';

import { BACKEND_URL } from '../config';

// Chat endpoints are now authenticated — attach the bearer token (these are
// raw fetch() calls, so the axios interceptor doesn't apply to them).
const authHeaders = () => ({ Authorization: `Bearer ${sessionStorage.getItem('nexus_access_token') || ''}` });

const fmtTime = (iso) => {
  if (!iso) return '';
  try { return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
  catch { return ''; }
};

/* ─── A single chat message ──────────────────────────────────── */
function ChatBubble({ msg, isMine }) {
  const isAI = msg.message_type === 'ai' || msg.ai_agent_id;

  if (isAI) {
    return (
      <div style={{ alignSelf: 'flex-start', maxWidth: '92%' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
          <div style={{ width: 18, height: 18, borderRadius: 5, background: 'var(--ai-bg)', border: '1px solid var(--ai-border)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--ai)' }} />
          </div>
          <span className="nx-label" style={{ color: 'var(--ai)' }}>{msg.ai_agent_id || 'Nexus'}</span>
        </div>
        <div style={{
          background: 'var(--ai-bg)',
          border: '1px solid var(--ai-border)',
          borderRadius: '4px 12px 12px 12px',
          padding: '12px 15px',
          fontSize: 14, lineHeight: 1.7, color: 'var(--t1)', whiteSpace: 'pre-wrap',
        }}>
          {safeStr(msg.content)}
        </div>
        <div style={{ fontSize: 10, color: 'var(--t4)', marginTop: 3 }}>{fmtTime(msg.created_at)}</div>
      </div>
    );
  }

  return (
    <div style={{ alignSelf: isMine ? 'flex-end' : 'flex-start', maxWidth: '88%' }}>
      {!isMine && (
        <span className="nx-label" style={{ color: 'var(--t3)', marginBottom: 4, display: 'block' }}>
          {msg.sender_name || 'Teammate'}
        </span>
      )}
      <div style={{
        background: isMine ? 'var(--p-bg, rgba(99,102,241,0.12))' : 'var(--bg-2)',
        border: `1px solid ${isMine ? 'var(--p-border, rgba(99,102,241,0.25))' : 'var(--b1)'}`,
        borderRadius: isMine ? '12px 4px 12px 12px' : '4px 12px 12px 12px',
        padding: '10px 14px',
        fontSize: 14, lineHeight: 1.6, color: 'var(--t1)', whiteSpace: 'pre-wrap',
      }}>
        {safeStr(msg.content)}
      </div>
      <div style={{ fontSize: 10, marginTop: 3, textAlign: isMine ? 'right' : 'left',
                    color: msg.failed ? 'var(--red)' : 'var(--t4)' }}>
        {msg.failed ? 'Failed to send' : msg.pending ? 'Sending…' : fmtTime(msg.created_at)}
      </div>
    </div>
  );
}

/* ─── Chat Page ──────────────────────────────────────────────── */
export default function ChatPage() {
  const { currentUser, BACKEND_URL: ctxUrl } = useNexus();
  const base = ctxUrl || BACKEND_URL;

  const [channels, setChannels]       = useState([]);
  const [activeId, setActiveId]       = useState(null);
  const [messages, setMessages]       = useState([]);
  const [draft, setDraft]             = useState('');
  const [loading, setLoading]         = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [digesting,   setDigesting]   = useState(false);

  const threadRef = useRef(null);
  const myId = currentUser?.dbId;

  // Load my channels on mount
  useEffect(() => {
    if (!myId) return;
    (async () => {
      try {
        const res = await fetch(`${base}/api/v1/chat/my-channels/${myId}`, { headers: authHeaders() });
        const data = await res.json();
        const chans = data.channels || [];
        setChannels(chans);
        if (chans.length && !activeId) setActiveId(chans[0].channel_id);
      } catch { /* none */ }
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [myId]);

  // Load messages when active channel changes
  useEffect(() => {
    if (!activeId || !myId) return;
    setLoading(true);
    (async () => {
      try {
        const res = await fetch(`${base}/api/v1/chat/${activeId}/messages?employee_id=${myId}`, { headers: authHeaders() });
        const data = await res.json();
        setMessages(data.messages || []);
      } catch { setMessages([]); }
      finally { setLoading(false); }
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, myId]);

  // Live incoming messages via the CHAT: websocket event (dispatched by NexusContext)
  useEffect(() => {
    const handler = (e) => {
      const { channelId, message } = e.detail || {};
      if (channelId === activeId) {
        setMessages(prev => prev.some(m => m.id === message.id) ? prev : [...prev, message]);
      } else {
        // message in a channel we're not viewing → bump its unread badge
        setChannels(prev => prev.map(c =>
          c.channel_id === channelId ? { ...c, unread: (c.unread || 0) + 1 } : c));
      }
    };
    window.addEventListener('nexus:chat', handler);
    return () => window.removeEventListener('nexus:chat', handler);
  }, [activeId]);

  // Auto-scroll
  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [messages, loading]);

  const runSummarize = async () => {
    if (!activeId || summarizing) return;
    setSummarizing(true);
    try {
      const res = await fetch(`${base}/api/v1/chat/${activeId}/summarize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ limit: 40 }),
      });
      const data = await res.json();
      // The summary is persisted server-side as an AI message; show it locally now
      setMessages(prev => [...prev, {
        id: `sum-${Date.now()}`,
        content: data.summary || 'No summary available.',
        message_type: 'ai',
        ai_agent_id: 'Nexus',
      }]);
    } catch {
      setMessages(prev => [...prev, {
        id: `sum-${Date.now()}`,
        content: 'Summary unavailable right now.',
        message_type: 'ai', ai_agent_id: 'Nexus',
      }]);
    } finally { setSummarizing(false); }
  };

  // Manager-only: fire the daily project digest now, then show it in the open channel.
  const runDigest = async () => {
    if (digesting) return;
    setDigesting(true);
    try {
      await fetch(`${base}/api/v1/admin/run-digests-now`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
      });
      // Re-fetch the active channel so the digest appears immediately (no WS timing dependency).
      if (activeId) {
        const res = await fetch(`${base}/api/v1/chat/${activeId}/messages?employee_id=${myId}`, { headers: authHeaders() });
        const data = await res.json();
        setMessages(data.messages || []);
      }
    } catch { /* ignore */ }
    finally { setDigesting(false); }
  };

  const send = async (text) => {
    const t = safeStr(text).trim();
    if (!t || !activeId) return;

    // /summarize command
    if (t.toLowerCase() === '/summarize') {
      setDraft('');
      runSummarize();
      return;
    }

    setDraft('');
    // optimistic add — shown as "Sending…" until the server confirms
    const optimistic = {
      id: `tmp-${Date.now()}`, sender_id: myId, sender_name: currentUser?.name,
      content: t, message_type: 'text', pending: true,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, optimistic]);

    try {
      const res = await fetch(`${base}/api/v1/chat/${activeId}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ content: t }),
      });
      if (!res.ok) throw new Error(`send failed (${res.status})`);
      const real = await res.json();
      // replace optimistic with the real row (real id + server timestamp)
      setMessages(prev => prev.map(m => m.id === optimistic.id ? real : m));
    } catch {
      // never let a dropped message look sent — flag it
      setMessages(prev => prev.map(m => m.id === optimistic.id
        ? { ...m, pending: false, failed: true } : m));
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    send(draft);
  };

  const selectChannel = (id) => {
    setActiveId(id);
    setChannels(prev => prev.map(c => c.channel_id === id ? { ...c, unread: 0 } : c));
  };

  const activeChannel = channels.find(c => c.channel_id === activeId);
  const noChannels = channels.length === 0;

  return (
    <div className="animate-in" style={{ maxWidth: 720, margin: '0 auto', display: 'flex', flexDirection: 'column', height: 'calc(100vh - 130px)', gap: 12 }}>

      {/* Channel switcher + summarize */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
        <div style={{ flex: 1, display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 2 }}>
          {noChannels && <span style={{ color: 'var(--t4)', fontSize: 13, alignSelf: 'center' }}>No project channels yet</span>}
          {channels.map(c => {
            const active = c.channel_id === activeId;
            return (
              <button key={c.channel_id} onClick={() => selectChannel(c.channel_id)}
                title={c.last_message || c.name}
                style={{ height: 40, borderRadius: 10, whiteSpace: 'nowrap', flexShrink: 0, cursor: 'pointer',
                         padding: '0 12px', display: 'flex', alignItems: 'center', gap: 6,
                         background: active ? 'var(--p-bg, rgba(99,102,241,0.12))' : 'var(--bg-2)',
                         border: `1px solid ${active ? 'var(--p-border, rgba(99,102,241,0.25))' : 'var(--b1)'}`,
                         color: active ? 'var(--p)' : 'var(--t2)', fontSize: 13, fontWeight: active ? 600 : 500 }}>
                # {c.name}
                {!active && c.unread > 0 && (
                  <span style={{ minWidth: 18, height: 18, borderRadius: 99, background: 'var(--peer)', color: '#fff',
                                 fontSize: 10, fontWeight: 700, display: 'flex', alignItems: 'center',
                                 justifyContent: 'center', padding: '0 5px' }}>
                    {c.unread > 9 ? '9+' : c.unread}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <button
          onClick={runSummarize}
          disabled={!activeId || summarizing}
          className="btn btn-secondary btn-sm"
          style={{ height: 40, borderRadius: 10, whiteSpace: 'nowrap' }}>
          {summarizing ? <Spinner size={12} /> : <Icon path={ICON.ai || ICON.commands} size={13} />}
          {summarizing ? 'Summarizing…' : 'Summarize'}
        </button>
        {currentUser?.role === 'Manager' && (
          <button
            onClick={runDigest}
            disabled={digesting}
            className="btn btn-secondary btn-sm"
            style={{ height: 40, borderRadius: 10, whiteSpace: 'nowrap' }}
            title="Post the AI daily digest into every project channel">
            {digesting ? <Spinner size={12} /> : <Icon path={ICON.commands} size={13} />}
            {digesting ? 'Posting…' : 'Post Digest'}
          </button>
        )}
      </div>

      {/* Message thread */}
      <div ref={threadRef} style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 14, padding: '8px 4px 4px' }}>
        {noChannels && (
          <div style={{ margin: 'auto', textAlign: 'center', color: 'var(--t4)' }}>
            <div style={{ fontSize: 15, color: 'var(--t3)', marginBottom: 6 }}>No project channels yet.</div>
            <div style={{ fontSize: 13 }}>Channels appear here once you're added to a project.</div>
          </div>
        )}
        {loading && <div style={{ margin: 'auto' }}><Spinner size={18} /></div>}
        {!loading && !noChannels && messages.length === 0 && (
          <div style={{ margin: 'auto', textAlign: 'center', color: 'var(--t4)' }}>
            <div style={{ fontSize: 14, color: 'var(--t3)' }}>No messages yet in #{activeChannel?.name}.</div>
            <div style={{ fontSize: 13 }}>Say hello to your team below.</div>
          </div>
        )}
        {messages.map((m, i) => (
          <ChatBubble key={m.id ?? i} msg={m} isMine={m.sender_id === myId} />
        ))}
      </div>

      {/* Input bar */}
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 8, flexShrink: 0, padding: '4px 0' }}>
        <input
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder={noChannels ? 'No channel selected' : `Message #${activeChannel?.name || ''}  ·  /summarize to catch up`}
          className="nx-input"
          style={{ flex: 1, borderRadius: 10 }}
          disabled={noChannels}
        />
        <button type="submit" disabled={!draft.trim() || noChannels} className="btn btn-primary btn-icon" style={{ width: 40, height: 40, borderRadius: 10 }}>
          <Icon path={ICON.send} size={15} />
        </button>
      </form>

    </div>
  );
}