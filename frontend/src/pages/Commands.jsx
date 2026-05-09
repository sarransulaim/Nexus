import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useNexus } from '../context/NexusContext';
import { Icon, ICON, TypingIndicator, Spinner } from '../components/ui/SharedUI';
import { safeStr } from '../utils/helpers';

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

  const MAX_H = 56;
  const BAR_W = 3;
  const GAP   = 3;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '8px 0 4px' }}>
      {/* Bars — no card, floats on page background */}
      <div style={{ display: 'flex', alignItems: 'center', gap: GAP, height: MAX_H }}>
        {bars.map((h, i) => (
          <div key={i} style={{ width: BAR_W, height: Math.max(3, h * MAX_H), borderRadius: 99, background: barColor, opacity: active ? 0.25 + h * 0.75 : 0.25, transition: active ? 'height 75ms ease, opacity 75ms ease' : 'height 400ms ease, opacity 400ms ease, background 0.5s ease', flexShrink: 0 }} />
        ))}
      </div>
      {/* Reflection */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: GAP, height: MAX_H * 0.28, marginTop: 2, maskImage: 'linear-gradient(to bottom, rgba(0,0,0,0.15), transparent)', WebkitMaskImage: 'linear-gradient(to bottom, rgba(0,0,0,0.15), transparent)', overflow: 'hidden' }}>
        {bars.map((h, i) => (
          <div key={i} style={{ width: BAR_W, height: Math.max(2, h * MAX_H * 0.28), borderRadius: 99, background: barColor, opacity: 0.1, flexShrink: 0 }} />
        ))}
      </div>
      {/* Status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 14 }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: active ? barColor : 'var(--b3)', animation: active ? 'pulse-anim 1.5s infinite' : 'none', transition: 'background 0.3s' }} />
        <span style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: active ? barColor : 'var(--t4)', transition: 'color 0.3s' }}>
          {isSpeaking ? 'Speaking' : isListening ? 'Listening' : isThinking ? 'Processing' : 'Standby'}
        </span>
      </div>
    </div>
  );
}

/* ─── Commands Page ──────────────────────────────────────────── */
export default function Commands() {
  const { transcript, aiResponse, isListening, isSpeaking, toggleListening, sendCommandToNexus, thoughts, isThinking, stopSpeaking } = useNexus();

  const [cmd, setCmd]     = useState('');
  const [vol, setVol]     = useState(0);
  const rafRef  = useRef(null);
  const ctxRef  = useRef(null);
  const timerRef = useRef(null);
  const respRef  = useRef(null);

  // Auto-scroll response
  useEffect(() => {
    if (respRef.current) respRef.current.scrollTop = respRef.current.scrollHeight;
  }, [aiResponse]);

  // Volume analyser
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

  const handleSubmit = e => {
    e.preventDefault();
    if (!cmd.trim()) return;
    sendCommandToNexus(cmd);
    setCmd('');
  };

  const handleWaveClick = () => {
    if (isSpeaking) stopSpeaking();
    else toggleListening();
  };

  return (
    <div className="animate-in" style={{ maxWidth: 580, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 14 }}>

      {/* Waveform — centered, no background */}
      <div onClick={handleWaveClick} style={{ cursor: 'pointer', display: 'flex', justifyContent: 'center', padding: '24px 0 8px' }}>
        <WaveformViz isListening={isListening} isSpeaking={isSpeaking} isThinking={isThinking} volume={vol} />
      </div>

      {/* Input row */}
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 8 }}>
        <input
          value={cmd}
          onChange={e => setCmd(e.target.value)}
          placeholder={isListening ? 'Listening... or type here' : 'Type a command or click the waveform to speak...'}
          className="nx-input"
          style={{ flex: 1, borderRadius: 10 }}
        />
        <button type="submit" disabled={!cmd.trim()} className="btn btn-primary btn-icon" style={{ width: 40, height: 40, borderRadius: 10 }}>
          <Icon path={ICON.send} size={15} />
        </button>
        {(isListening || isSpeaking) && (
          <button type="button" onClick={isListening ? toggleListening : stopSpeaking} className="btn btn-ghost" style={{ padding: '0 14px', height: 40, fontSize: 12, borderRadius: 10, color: isSpeaking ? 'var(--speaking)' : 'var(--voice)', borderColor: isSpeaking ? 'var(--peer-border)' : 'rgba(34,211,238,0.2)' }}>
            Stop
          </button>
        )}
      </form>

      {/* Transcript */}
      {(isListening || transcript) && (
        <div className="animate-in" style={{ padding: '12px 16px', borderRadius: 10, background: 'rgba(34,211,238,0.05)', border: '1px solid rgba(34,211,238,0.12)' }}>
          <div className="nx-label" style={{ color: 'rgba(34,211,238,0.45)', marginBottom: 6 }}>Voice Input</div>
          <div style={{ fontSize: 14, color: 'rgba(34,211,238,0.75)', fontStyle: 'italic' }}>
            {isListening && !transcript
              ? <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--voice)' }}>Listening... <TypingIndicator /></div>
              : transcript ? `"${transcript.replace(/^\[SENT\]:\s*"?|"?$/g, '')}"` : null
            }
          </div>
        </div>
      )}

      {/* Glass Brain telemetry */}
      {(thoughts.length > 0 || isThinking) && (
        <div className="animate-in" style={{ padding: '12px 16px', borderRadius: 10, background: 'var(--ai-bg)', border: '1px solid var(--ai-border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 8 }}>
            {isThinking ? <><Spinner size={12} /> <span className="nx-label" style={{ color: 'var(--ai)' }}>Agent reasoning...</span></> : <span className="nx-label" style={{ color: 'rgba(139,92,246,0.5)' }}>Process complete</span>}
          </div>
          {thoughts.slice(-3).map((t, i) => (
            <div key={i} style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--t3)', lineHeight: 1.6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t}</div>
          ))}
        </div>
      )}

      {/* Response card — max height, scrollable */}
      <div style={{ background: 'var(--bg-2)', border: `1px solid ${isSpeaking ? 'rgba(217,70,239,0.2)' : 'var(--b1)'}`, borderRadius: 12, display: 'flex', flexDirection: 'column', maxHeight: 320, transition: 'border-color var(--slow)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '11px 16px', borderBottom: '1px solid var(--b0)', flexShrink: 0 }}>
          <span className="nx-label">System Response</span>
          {isSpeaking && (
            <button onClick={stopSpeaking} className="btn btn-sm" style={{ fontSize: 10, background: 'rgba(217,70,239,0.08)', color: 'var(--speaking)', borderColor: 'rgba(217,70,239,0.2)' }}>
              <Icon path={ICON.stop} size={11} /> Stop
            </button>
          )}
        </div>
        <div ref={respRef} style={{ flex: 1, overflowY: 'auto', padding: '14px 16px', fontSize: 14, lineHeight: 1.75, color: isSpeaking ? 'var(--t1)' : 'var(--t2)', whiteSpace: 'pre-wrap', transition: 'color var(--slow)' }}>
          {isThinking
            ? <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--ai)' }}>Processing directive <TypingIndicator /></div>
            : safeStr(aiResponse) || <span style={{ color: 'var(--t4)', fontStyle: 'italic' }}>Nexus standing by. Speak or type a command.</span>
          }
        </div>
      </div>

    </div>
  );
}
