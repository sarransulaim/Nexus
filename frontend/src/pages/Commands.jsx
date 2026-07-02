import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, TypingIndicator, Spinner } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';
import FileUploadCard from '../components/FileUploadCard';

import { BACKEND_URL } from '../config';

/* ─── Waveform Visualizer ────────────────────────────────────── */
function WaveformViz({ isListening, isSpeaking, isThinking, volume }) {
  const BAR_COUNT = 32;
  const tick = Math.floor(Date.now() / 80);

  const bars = useMemo(() => Array.from({ length: BAR_COUNT }, (_, i) => {
    const center = (BAR_COUNT - 1) / 2;
    const dist   = Math.abs(i - center) / center;

    if (isListening) {
      const base  = Math.max(0.06, 1 - dist * 0.55);
      const noise = Math.random() * 0.4 * volume;
      return Math.min(1, base * (0.25 + volume * 0.75) + noise);
    }
    if (isSpeaking) {
      const wave = Math.sin((i / BAR_COUNT) * Math.PI * 3.5 + Date.now() / 180);
      const base = Math.max(0.08, 1 - dist * 0.5);
      return Math.min(1, base * (0.35 + volume * 0.65) + wave * 0.2 * volume);
    }
    if (isThinking) {
      const phase = ((Date.now() / 700) + i / BAR_COUNT * 2.5) % 1;
      return 0.04 + Math.sin(phase * Math.PI) * 0.28;
    }
    return 0.03 + (1 - dist) * 0.04;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [isListening, isSpeaking, isThinking, volume, tick]);

  const active   = isListening || isSpeaking || isThinking;
  const barColor = isSpeaking  ? 'var(--speaking)'
    : isListening ? 'var(--voice)'
    : isThinking  ? 'var(--ai)'
    : 'var(--b3)';

  const MAX_H = 44;
  const BAR_W = 3;
  const GAP   = 3;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: GAP, height: MAX_H }}>
        {bars.map((h, i) => (
          <div key={i} style={{ width: BAR_W, height: Math.max(3, h * MAX_H), borderRadius: 99, background: barColor, opacity: active ? 0.25 + h * 0.75 : 0.25, transition: active ? 'height 75ms ease, opacity 75ms ease' : 'height 400ms ease, opacity 400ms ease, background 0.5s ease', flexShrink: 0 }} />
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: active ? barColor : 'var(--b3)', animation: active ? 'pulse-anim 1.5s infinite' : 'none', transition: 'background 0.3s' }} />
        <span style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: active ? barColor : 'var(--t4)', transition: 'color 0.3s' }}>
          {isSpeaking ? 'Speaking' : isListening ? 'Listening' : isThinking ? 'Processing' : 'Standby'}
        </span>
      </div>
    </div>
  );
}

/* ─── A single AI response with show-more truncation ─────────── */
const TRUNCATE_CHARS = 480;   // ~6 lines before "Show more"

function ResponseBubble({ text, speaking }) {
  const [expanded, setExpanded] = useState(false);
  const full = safeStr(text);
  const isLong = full.length > TRUNCATE_CHARS;
  const shown = expanded || !isLong ? full : full.slice(0, TRUNCATE_CHARS).trimEnd() + '…';

  return (
    <div style={{ alignSelf: 'flex-start', maxWidth: '92%' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
        <div style={{ width: 18, height: 18, borderRadius: 5, background: 'var(--ai-bg)', border: '1px solid var(--ai-border)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--ai)' }} />
        </div>
        <span className="nx-label" style={{ color: 'var(--ai)' }}>Nexus</span>
      </div>
      <div style={{
        background: 'var(--bg-2)',
        border: `1px solid ${speaking ? 'rgba(217,70,239,0.25)' : 'var(--b1)'}`,
        borderRadius: '4px 12px 12px 12px',
        padding: '12px 15px',
        fontSize: 14,
        lineHeight: 1.7,
        color: 'var(--t1)',
        whiteSpace: 'pre-wrap',
        transition: 'border-color var(--slow)',
      }}>
        {shown}
        {isLong && (
          <button
            onClick={() => setExpanded(e => !e)}
            style={{ display: 'block', marginTop: 8, background: 'none', border: 'none', padding: 0,
                     color: 'var(--ai)', fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
            {expanded ? '▲ Show less' : '▼ Show more'}
          </button>
        )}
      </div>
    </div>
  );
}

/* ─── A user directive line ──────────────────────────────────── */
function DirectiveBubble({ text }) {
  return (
    <div style={{ alignSelf: 'flex-end', maxWidth: '88%' }}>
      <div style={{
        background: 'var(--p-bg, rgba(99,102,241,0.12))',
        border: '1px solid var(--p-border, rgba(99,102,241,0.25))',
        borderRadius: '12px 4px 12px 12px',
        padding: '10px 14px',
        fontSize: 14,
        lineHeight: 1.6,
        color: 'var(--t1)',
        whiteSpace: 'pre-wrap',
      }}>
        {text}
      </div>
    </div>
  );
}

/* ─── Commands Page ──────────────────────────────────────────── */
export default function Commands() {
  const {
    transcript, aiResponse, isListening, isSpeaking, toggleListening,
    sendCommandToNexus, thoughts, isThinking, stopSpeaking, currentUser,
    employees, fetchDashboardData,
  } = useNexus();

  const [cmd, setCmd]       = useState('');
  const [vol, setVol]       = useState(0);
  const [thread, setThread] = useState([]);   // [{role:'user'|'assistant', content}]
  const rafRef   = useRef(null);
  const ctxRef   = useRef(null);
  const timerRef = useRef(null);
  const threadRef = useRef(null);
  const lastResponseRef = useRef(null);
  const pendingRef = useRef(false);

  const agentId = currentUser?.role === 'Manager' ? 'Manager_1' : `Employee_${currentUser?.dbId}`;

  // Load persisted thread on mount
  useEffect(() => {
    if (!currentUser) return;
    (async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/v1/manager/command-history/${agentId}`, {
          headers: { Authorization: `Bearer ${sessionStorage.getItem('nexus_access_token') || ''}` },
        });
        const data = await res.json();
        if (Array.isArray(data.thread)) {
          setThread(data.thread.slice(-40));   // last ~20 exchanges
        }
      } catch { /* start empty */ }
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUser]);

  // When a new aiResponse arrives, append the exchange to the thread
  useEffect(() => {
    const resp = safeStr(aiResponse);
    if (!resp || resp === 'Processing directive...') return;
    if (resp === lastResponseRef.current) return;
    lastResponseRef.current = resp;
    if (pendingRef.current) {
      setThread(prev => {
        const next = [...prev, { role: 'assistant', content: resp }];
        return next.slice(-40);
      });
      pendingRef.current = false;
    }
  }, [aiResponse]);

  // Auto-scroll thread to bottom
  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [thread, isThinking]);

  // Volume analyser (unchanged)
  useEffect(() => {
    const cleanup = () => {
      if (rafRef.current)  cancelAnimationFrame(rafRef.current);
      if (timerRef.current) clearTimeout(timerRef.current);
      if (ctxRef.current && ctxRef.current.state !== 'closed') {
        ctxRef.current.close().catch(() => {});
        ctxRef.current = null;
      }
    };
    if (isListening) {
      navigator.mediaDevices.getUserMedia({ audio: true, video: false }).then(stream => {
        ctxRef.current = new (window.AudioContext || window.webkitAudioContext)();
        const analyser = ctxRef.current.createAnalyser();
        ctxRef.current.createMediaStreamSource(stream).connect(analyser);
        analyser.fftSize = 256;
        const data = new Uint8Array(analyser.frequencyBinCount);
        const tick = () => { analyser.getByteFrequencyData(data); setVol(data.reduce((a, b) => a + b, 0) / data.length / 100); rafRef.current = requestAnimationFrame(tick); };
        tick();
      }).catch(() => setVol(0));
    } else if (isSpeaking) {
      const pulse = () => { setVol(0.3 + Math.random() * 0.7); timerRef.current = setTimeout(pulse, 75); };
      pulse();
    } else {
      setVol(0);
      cleanup();
    }
    return cleanup;
  }, [isListening, isSpeaking]);

  const submit = (text) => {
    const t = safeStr(text).trim();
    if (!t) return;
    setThread(prev => [...prev, { role: 'user', content: t }].slice(-40));
    pendingRef.current = true;
    sendCommandToNexus(t);
  };

  const handleSubmit = e => {
    e.preventDefault();
    if (!cmd.trim()) return;
    submit(cmd);
    setCmd('');
  };

  // Voice transcripts: when a voice command is sent, capture it into the thread
  useEffect(() => {
    if (transcript && transcript.startsWith('[SENT]:') && !pendingRef.current) {
      const spoken = transcript.replace(/^\[SENT\]:\s*"?|"?$/g, '').trim();
      if (spoken && (thread.length === 0 || thread[thread.length - 1].content !== spoken)) {
        setThread(prev => [...prev, { role: 'user', content: spoken }].slice(-40));
        pendingRef.current = true;
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transcript]);

  const handleWaveClick = () => {
    if (isSpeaking) stopSpeaking();
    else toggleListening();
  };

  const empty = thread.length === 0 && !isThinking;

  return (
    <div className="animate-in" style={{ maxWidth: 720, margin: '0 auto', display: 'flex', flexDirection: 'column', height: 'calc(100vh - 130px)', gap: 12 }}>

      {/* Command thread */}
      <div ref={threadRef} style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 16, padding: '8px 4px 4px' }}>
        {empty && (
          <div style={{ margin: 'auto', textAlign: 'center', color: 'var(--t4)' }}>
            <div style={{ fontSize: 15, color: 'var(--t3)', marginBottom: 6 }}>Your chief of staff is standing by.</div>
            <div style={{ fontSize: 13 }}>Give a directive below — or click the waveform to speak.</div>
          </div>
        )}

        {thread.map((m, i) => (
          m.role === 'user'
            ? <DirectiveBubble key={i} text={m.content} />
            : <ResponseBubble key={i} text={m.content} speaking={isSpeaking && i === thread.length - 1} />
        ))}

        {/* Live thinking indicator */}
        {isThinking && (
          <div style={{ alignSelf: 'flex-start', maxWidth: '92%' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
              <Spinner size={12} />
              <span className="nx-label" style={{ color: 'var(--ai)' }}>Nexus reasoning</span>
            </div>
            {thoughts.length > 0 && (
              <div style={{ padding: '10px 14px', borderRadius: '4px 12px 12px 12px', background: 'var(--ai-bg)', border: '1px solid var(--ai-border)' }}>
                {thoughts.slice(-3).map((t, i) => (
                  <div key={i} style={{ fontSize: 11.5, fontFamily: 'var(--font-mono)', color: 'var(--t3)', lineHeight: 1.6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t}</div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Live voice transcript (while listening) */}
      {isListening && (
        <div style={{ padding: '8px 14px', borderRadius: 10, background: 'rgba(34,211,238,0.05)', border: '1px solid rgba(34,211,238,0.12)', fontSize: 13, color: 'var(--voice)', fontStyle: 'italic', display: 'flex', alignItems: 'center', gap: 8 }}>
          {transcript && !transcript.startsWith('[SENT]')
            ? `"${transcript}"`
            : <>Listening <TypingIndicator /></>}
        </div>
      )}

      {/* File intelligence — drop a doc/spec, AI proposes a project + tasks (manager only; self-hides otherwise) */}
      <FileUploadCard employees={employees} onComplete={fetchDashboardData} />

      {/* Input bar — fixed at bottom */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '4px 0' }}>
        <div onClick={handleWaveClick} style={{ cursor: 'pointer', flexShrink: 0 }}>
          <WaveformViz isListening={isListening} isSpeaking={isSpeaking} isThinking={isThinking} volume={vol} />
        </div>
        <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 8, flex: 1 }}>
          <input
            value={cmd}
            onChange={e => setCmd(e.target.value)}
            placeholder={isListening ? 'Listening… or type here' : 'Give a directive…'}
            className="nx-input"
            style={{ flex: 1, borderRadius: 10 }}
          />
          <button type="submit" disabled={!cmd.trim() || isThinking} className="btn btn-primary btn-icon" style={{ width: 40, height: 40, borderRadius: 10 }}>
            <Icon path={ICON.send} size={15} />
          </button>
          {(isListening || isSpeaking) && (
            <button type="button" onClick={isListening ? toggleListening : stopSpeaking} className="btn btn-ghost" style={{ padding: '0 14px', height: 40, fontSize: 12, borderRadius: 10, color: isSpeaking ? 'var(--speaking)' : 'var(--voice)' }}>
              Stop
            </button>
          )}
        </form>
      </div>

    </div>
  );
}