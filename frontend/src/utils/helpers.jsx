import React from 'react';

// --- ERROR BOUNDARY ---
export class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { hasError: false, error: null, errorInfo: null }; }
  static getDerivedStateFromError(error) { return { hasError: true, error }; }
  componentDidCatch(error, errorInfo) { console.error("Error:", error, errorInfo); this.setState({ errorInfo }); }
  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col min-h-screen items-center justify-center bg-[#020817] text-red-400 p-10 font-mono">
          <h1 className="text-3xl font-bold mb-4 tracking-widest uppercase">CRITICAL SYSTEM FAILURE</h1>
          <p className="mb-6 text-white tracking-wider">The Execution Terminal encountered a fatal rendering error.</p>
          <div className="bg-black/50 p-6 rounded-2xl w-full max-w-4xl overflow-auto border border-red-500/30 shadow-[0_0_30px_rgba(248,113,113,0.1)]">
            <p className="font-bold text-red-300 mb-2">{this.state.error?.toString()}</p>
            <pre className="text-[10px] text-red-400/70 whitespace-pre-wrap">{this.state.errorInfo?.componentStack}</pre>
          </div>
          <button onClick={() => { sessionStorage.clear(); window.location.reload(); }} className="mt-10 px-8 py-4 bg-red-900/30 border border-red-500/50 rounded-xl text-white font-bold tracking-widest uppercase transition-all hover:bg-red-800/40">Hard Reboot System</button>
        </div>
      );
    }
    return this.props.children;
  }
}

// --- UTILITIES ---
export function formatDueDate(raw) {
  if (!raw || raw === 'None' || raw === 'none') return '—';
  const lower = String(raw).trim().toLowerCase();
  const now = new Date();
  let date;
  if (lower === 'today') date = now;
  else if (lower === 'tomorrow') { date = new Date(now); date.setDate(now.getDate() + 1); }
  else if (lower === 'yesterday') { date = new Date(now); date.setDate(now.getDate() - 1); }
  else {
    // A date-only value ("2026-08-05") is parsed by new Date() as UTC
    // midnight, which renders as the PREVIOUS day in any timezone behind
    // UTC — a task due the 5th showed "04 Aug". Build it as a local
    // calendar day instead. Values carrying a time are left alone; those
    // are real instants and parse correctly.
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(String(raw).trim());
    if (dateOnly) {
      const [y, m, d] = String(raw).trim().split('-').map(Number);
      date = new Date(y, m - 1, d);
    } else {
      date = new Date(raw);
    }
  }
  if (isNaN(date.getTime())) return String(raw);
  const hasTime = String(raw).includes('T') || String(raw).includes(':') || lower === 'today' || lower === 'tomorrow';
  const dateStr = date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  if (hasTime && (String(raw).includes('T') || String(raw).includes(':'))) return `${dateStr} ${date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false })}`;
  return dateStr;
}

export const safeStr = (val) => (val === null || val === undefined ? '' : String(val));

/* A "YYYY-MM-DD" day parsed as a LOCAL calendar day. new Date() would treat
   it as UTC midnight, which renders as the previous day anywhere behind UTC. */
export function parseLocalDay(raw) {
  if (!raw) return null;
  const [y, m, d] = String(raw).trim().split('-').map(Number);
  if (!y || !m || !d) return null;
  const dt = new Date(y, m - 1, d);
  return isNaN(dt) ? null : dt;
}

export const startOfToday = () => { const t = new Date(); t.setHours(0, 0, 0, 0); return t; };

/* A day is past only once it is fully over, so today's items stay current. */
export function isPastDay(raw) {
  const d = parseLocalDay(raw);
  return d ? d < startOfToday() : false;
}
