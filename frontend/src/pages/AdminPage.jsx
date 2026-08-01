import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useNexus } from '../context/NexusContext';
import axios from 'axios';

import { BACKEND_URL, WS_BASE } from '../config';

export default function AdminPage() {
  const { currentUser } = useNexus();

  const [agents,      setAgents]      = useState([]);
  const [metrics,     setMetrics]     = useState(null);
  const [events,      setEvents]      = useState([]);
  const [pulses,      setPulses]      = useState([]);
  const [activeNodes, setActiveNodes] = useState(new Set());
  const [errorNodes,  setErrorNodes]  = useState(new Set());
  const [wsConnected, setWsConnected] = useState(false);

  const wsRef         = useRef(null);
  const pulseIdRef    = useRef(0);
  const activeFadeRef = useRef({});
  const errorFadeRef  = useRef({});

  const lightNode = useCallback((id, ms = 1200) => {
    setActiveNodes(p => new Set([...p, id]));
    if (activeFadeRef.current[id]) clearTimeout(activeFadeRef.current[id]);
    activeFadeRef.current[id] = setTimeout(() => {
      setActiveNodes(p => { const n = new Set(p); n.delete(id); return n; });
      delete activeFadeRef.current[id];
    }, ms);
  }, []);

  const flashError = useCallback((id) => {
    if (!id) return;
    setErrorNodes(p => new Set([...p, id]));
    if (errorFadeRef.current[id]) clearTimeout(errorFadeRef.current[id]);
    errorFadeRef.current[id] = setTimeout(() => {
      setErrorNodes(p => { const n = new Set(p); n.delete(id); return n; });
      delete errorFadeRef.current[id];
    }, 2500);
  }, []);

  const addPulse = useCallback((from, to, color, ms = 1100) => {
    const id = ++pulseIdRef.current;
    setPulses(p => [...p, { id, from, to, color, ms }]);
    setTimeout(() => setPulses(p => p.filter(x => x.id !== id)), ms + 120);
  }, []);

  const handleEvent = useCallback((ev) => {
    setEvents(p => [ev, ...p].slice(0, 80));
    const actor   = ev.actor;
    const isAgent = actor?.startsWith('Manager_') || actor?.startsWith('Employee_');
    const agentId = isAgent ? actor : null;

    switch (ev.type) {
      case 'agent_thinking':
        if (agentId) {
          lightNode('input_text'); lightNode('ai_router');
          lightNode(agentId);     lightNode('orchestrator');
          addPulse('input_text', 'ai_router',    '#22d3ee', 800);
          addPulse('ai_router',  agentId,        '#8b5cf6', 1100);
          addPulse(agentId,      'orchestrator', '#8b5cf6', 1100);
        }
        break;
      case 'agent_idle':    if (agentId) lightNode(agentId, 400); break;
      case 'tool_called': {
        const c = toolToCluster(ev.data?.tool);
        lightNode('orchestrator');
        if (c) { lightNode(`cluster_${c}`); addPulse('orchestrator', `cluster_${c}`, '#f59e0b', 1000); }
        break;
      }
      case 'db_query':
        lightNode('database');
        addPulse('orchestrator', 'database', '#10b981', 900);
        break;
      case 'negotiation_start':
        lightNode('negotiation');
        addPulse('orchestrator', 'negotiation', '#ec4899', 900);
        break;
      case 'negotiation_step': {
        const f = ev.data?.from_agent, t = ev.data?.to_agent;
        if (f && t) {
          lightNode(f); lightNode(t); lightNode('negotiation');
          addPulse(f, t, '#ec4899', 1300);
          addPulse(t, f, '#ec4899', 1300);
        }
        break;
      }
      case 'negotiation_done': lightNode('negotiation', 600); break;
      case 'message_sent':
        if (agentId) addPulse(agentId, 'ws_broadcast', '#6366f1', 900);
        lightNode('ws_broadcast');
        break;
      case 'cost_recorded': lightNode('ai_router', 800); break;
      case 'error':         flashError(agentId || 'orchestrator'); break;
      default: break;
    }
  }, [lightNode, flashError, addPulse]);

  const loadMetrics = useCallback(async () => {
    try { const r = await axios.get(`${BACKEND_URL}/api/v1/admin/metrics`); setMetrics(r.data); }
    catch (_) {}
  }, []);

  const connectStream = useCallback(() => {
    const token = sessionStorage.getItem('nexus_access_token');
    if (!token) return;
    // Token in the subprotocol, not the query string — proxies log URLs.
    const ws = new WebSocket(`${WS_BASE}/api/v1/admin/stream`, ['nexus-auth', token]);
    wsRef.current = ws;
    ws.onopen    = () => setWsConnected(true);
    ws.onerror   = () => setWsConnected(false);
    ws.onclose   = () => { setWsConnected(false); setTimeout(connectStream, 2000); };
    ws.onmessage = (msg) => {
      try { const ev = JSON.parse(msg.data); if (ev.type !== 'ping') handleEvent(ev); }
      catch (_) {}
    };
  }, [handleEvent]);

  const layout = useMemo(() => buildLayout(agents), [agents]);

  useEffect(() => {
    const init = async () => {
      try {
        const [a, m] = await Promise.all([
          axios.get(`${BACKEND_URL}/api/v1/admin/agents`),
          axios.get(`${BACKEND_URL}/api/v1/admin/metrics`),
        ]);
        setAgents(a.data); setMetrics(m.data);
      } catch (e) { console.error('Admin load failed', e); }
    };
    init();
    connectStream();
    const t = setInterval(loadMetrics, 5000);
    return () => {
      clearInterval(t);
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); }
      Object.values(activeFadeRef.current).forEach(clearTimeout);
      Object.values(errorFadeRef.current).forEach(clearTimeout);
    };
  }, [connectStream, loadMetrics]);

  // Guard AFTER all hooks
  if (currentUser?.role !== 'Manager') {
    return <div style={{ padding: 40, color: '#8f8fa0' }}>Admin access requires manager role.</div>;
  }

  return (
    <div style={{ padding: '14px 18px', background: '#06060a', color: '#e4e4eb', minHeight: '100%' }}>
      <TopBar metrics={metrics} wsConnected={wsConnected} />

      {/* Full-width diagram */}
      <div style={{ ...panelStyle, marginTop: 14 }}>
        <PanelHeader
          title="System Architecture — Live Circuit"
          subtitle={`${activeNodes.size} active nodes · ${pulses.length} signals in flight`}
        />
        <div style={{ overflowX: 'auto' }}>
          <CircuitBoard layout={layout} pulses={pulses}
                        activeNodes={activeNodes} errorNodes={errorNodes} />
        </div>
      </div>

      {/* Event log below, full width */}
      <div style={{ marginTop: 14 }}>
        <EventLog events={events} />
      </div>
    </div>
  );
}


/* ═══════════════════════════════════════════════════════════════
 * Layout
 * ═══════════════════════════════════════════════════════════════ */
function buildLayout(agents) {
  const W = 1580;

  // Column centre X
  const CX = {
    inputs:  88,
    routing: 248,
    agents:  418,
    intel:   620,
    tools:   888,
    data:    1168,
    output:  1432,
  };

  const N = (id, x, y, label, kind, icon = null, meta = null) =>
    ({ id, x, y, label, kind, icon, meta });

  const nodes = {
    // ── INPUTS ──────────────────────────────────────────
    input_voice: N('input_voice', CX.inputs,  72,  'Voice',     'input',   '🎙'),
    input_text:  N('input_text',  CX.inputs,  152, 'Text',      'input',   '⌨'),
    input_ws:    N('input_ws',    CX.inputs,  232, 'WebSocket', 'input',   '⚡'),
    input_file:  N('input_file',  CX.inputs,  312, 'File',      'input',   '📎'),
    input_slack: N('input_slack', CX.inputs,  392, 'Slack',     'input',   '#'),
    input_gmail: N('input_gmail', CX.inputs,  472, 'Gmail',     'input',   '✉'),

    // ── ROUTING ─────────────────────────────────────────
    auth:        N('auth',        CX.routing, 72,  'Auth',        'service', '🔐'),
    rate_limit:  N('rate_limit',  CX.routing, 172, 'Rate Limit',  'service', '🚦'),
    ai_router:   N('ai_router',   CX.routing, 288, 'AI Router',   'core',    '◆'),
    company_ctx: N('company_ctx', CX.routing, 392, 'Company',     'service', '🏢'),
    preference:  N('preference',  CX.routing, 476, 'Preferences', 'service', '🧠'),

    // ── INTELLIGENCE ────────────────────────────────────
    orchestrator: N('orchestrator', CX.intel, 220, 'Orchestrator', 'brain',   '◉'),
    negotiation:  N('negotiation',  CX.intel, 370, 'Negotiation',  'core',    '⇄'),
    glass_brain:  N('glass_brain',  CX.intel, 488, 'Glass Brain',  'service', '◍'),

    // ── TOOL CLUSTERS ────────────────────────────────────
    cluster_task:         N('cluster_task',         CX.tools,  56,  'Task',      'cluster', '✓'),
    cluster_project:      N('cluster_project',      CX.tools,  144, 'Project',   'cluster', '▣'),
    cluster_people:       N('cluster_people',       CX.tools,  232, 'People',    'cluster', '◯'),
    cluster_meeting:      N('cluster_meeting',      CX.tools,  320, 'Meeting',   'cluster', '◷'),
    cluster_google:       N('cluster_google',       CX.tools,  408, 'Google',    'cluster', 'G'),
    cluster_file:         N('cluster_file',         CX.tools,  496, 'File',      'cluster', '▤'),
    cluster_negotiation:  N('cluster_negotiation',  CX.tools,  584, 'Negotiate', 'cluster', '⇄'),
    cluster_notification: N('cluster_notification', CX.tools,  672, 'Notify',    'cluster', '◔'),

    // ── DATA ─────────────────────────────────────────────
    database:     N('database',     CX.data, 144, 'PostgreSQL', 'data', '⛁'),
    memory:       N('memory',       CX.data, 280, 'Memory',     'data', '⌬'),
    audit:        N('audit',        CX.data, 416, 'Audit Log',  'data', '◫'),
    file_storage: N('file_storage', CX.data, 552, 'Files',      'data', '◳'),

    // ── OUTPUTS ──────────────────────────────────────────
    ws_broadcast:  N('ws_broadcast',  CX.output, 172, 'WS Out',        'output', '⇶'),
    tts:           N('tts',           CX.output, 300, 'TTS',            'output', '◐'),
    notifications: N('notifications', CX.output, 428, 'Notifications',  'output', '◉'),
    out_slack:     N('out_slack',     CX.output, 556, 'Slack Out',      'output', '#'),
  };

  // Agent nodes — centred vertically
  const agentCount  = Math.max(agents.length, 3);
  const agentSpan   = (agentCount - 1) * 96;
  const agentStartY = Math.max(60, (740 - agentSpan) / 2);
  agents.forEach((a, i) => {
    nodes[a.agent_id] = {
      id:    a.agent_id,
      x:     CX.agents,
      y:     agentStartY + i * 96,
      label: a.name,
      kind:  a.system_role === 'manager' ? 'manager' : 'employee',
      meta:  `${a.message_count || 0} msgs`,
      icon:  a.system_role === 'manager' ? '★' : '●',
    };
  });

  // Dynamic height — at least 760, grows if many agents or tool clusters go deep
  const maxY = Math.max(
    agentStartY + agentSpan,
    672 + 40, // cluster_notification + label clearance
    556 + 40, // file_storage
  );
  const H = Math.max(760, maxY + 80);

  // ── Edges ────────────────────────────────────────────
  const edges = [];

  ['input_voice','input_text','input_ws','input_file','input_slack','input_gmail']
    .forEach(id => edges.push([id, 'auth']));
  edges.push(['auth', 'rate_limit']);
  edges.push(['rate_limit', 'ai_router']);
  edges.push(['ai_router', 'company_ctx']);
  edges.push(['ai_router', 'preference']);

  agents.forEach(a => {
    edges.push(['ai_router', a.agent_id]);
    edges.push([a.agent_id, 'orchestrator']);
  });

  edges.push(['orchestrator', 'negotiation']);
  edges.push(['orchestrator', 'glass_brain']);

  ['cluster_task','cluster_project','cluster_people','cluster_meeting',
   'cluster_google','cluster_file','cluster_negotiation','cluster_notification']
    .forEach(c => edges.push(['orchestrator', c]));

  edges.push(['cluster_task',         'database']);
  edges.push(['cluster_project',      'database']);
  edges.push(['cluster_people',       'database']);
  edges.push(['cluster_meeting',      'database']);
  edges.push(['cluster_file',         'file_storage']);
  edges.push(['cluster_notification', 'notifications']);
  edges.push(['orchestrator',         'memory']);
  edges.push(['orchestrator',         'audit']);
  edges.push(['glass_brain',          'ws_broadcast']);
  edges.push(['notifications',        'ws_broadcast']);
  edges.push(['orchestrator',         'tts']);

  return { nodes, edges, W, H, CX };
}


/* ── Orthogonal circuit path ─────────────────────────────────── */
// Returns SVG path string: exit right → vertical → arrive right (like a PCB trace)
function tracePath(x1, y1, x2, y2) {
  if (Math.abs(y1 - y2) < 3) return `M ${x1} ${y1} H ${x2}`;       // purely horizontal
  // Bend at 42% of horizontal distance
  const bx = Math.round(x1 + (x2 - x1) * 0.42);
  return `M ${x1} ${y1} H ${bx} V ${y2} H ${x2}`;
}

// Proportional keyTimes based on Manhattan segment lengths
function pulseKeyTimes(x1, y1, x2, y2) {
  if (Math.abs(y1 - y2) < 3) {
    return { cx: `${x1};${x2}`, cy: `${y1};${y2}`, kt: '0;1' };
  }
  const bx   = x1 + (x2 - x1) * 0.42;
  const h1   = Math.abs(bx - x1);
  const v    = Math.abs(y2 - y1);
  const h2   = Math.abs(x2 - bx);
  const tot  = h1 + v + h2;
  const t1   = (h1 / tot).toFixed(3);
  const t2   = ((h1 + v) / tot).toFixed(3);
  return {
    cx: `${x1};${bx};${bx};${x2}`,
    cy: `${y1};${y1};${y2};${y2}`,
    kt: `0;${t1};${t2};1`,
  };
}


/* ── Tool name → cluster id ──────────────────────────────────── */
function toolToCluster(name) {
  if (!name) return null;
  const t = name.toLowerCase();
  if (t.includes('task') || t.includes('subtask') || t.includes('assign'))      return 'task';
  if (t.includes('project'))                                                     return 'project';
  if (t.includes('employee') || t.includes('people') || t.includes('user'))    return 'people';
  if (t.includes('meeting') || t.includes('schedule'))                          return 'meeting';
  if (t.includes('email') || t.includes('gmail') || t.includes('calendar'))    return 'google';
  if (t.includes('file') || t.includes('upload'))                               return 'file';
  if (t.includes('negotiat') || t.includes('rebalance') || t.includes('peer')) return 'negotiation';
  if (t.includes('notif') || t.includes('briefing'))                            return 'notification';
  return null;
}


/* ── Circuit board SVG ───────────────────────────────────────── */
function CircuitBoard({ layout, pulses, activeNodes, errorNodes }) {
  const { nodes, edges, W, H, CX } = layout;

  // Column label positions from CX
  const colLabels = [
    { x: CX.inputs,  label: 'INPUT' },
    { x: CX.routing, label: 'ROUTING' },
    { x: CX.agents,  label: 'AGENTS' },
    { x: CX.intel,   label: 'INTELLIGENCE' },
    { x: CX.tools,   label: 'TOOLS' },
    { x: CX.data,    label: 'DATA' },
    { x: CX.output,  label: 'OUTPUT' },
  ];

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      style={{ width: '100%', minWidth: 1200, height: 'auto', display: 'block' }}
    >
      <defs>
        {/* Fine dot grid */}
        <pattern id="dotgrid" width="24" height="24" patternUnits="userSpaceOnUse">
          <circle cx="1" cy="1" r="0.8" fill="#0f0f1a" />
        </pattern>
        {/* Glow filter */}
        <filter id="cglow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="4" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        {/* Subtle node glow */}
        <filter id="nglow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2.5" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        {/* Arrow marker for edges */}
        <marker id="arr" markerWidth="5" markerHeight="5"
                refX="4" refY="2.5" orient="auto">
          <path d="M0,0 L5,2.5 L0,5 Z" fill="#1e1e30" />
        </marker>
        <marker id="arr-active" markerWidth="5" markerHeight="5"
                refX="4" refY="2.5" orient="auto">
          <path d="M0,0 L5,2.5 L0,5 Z" fill="#8b5cf6" opacity="0.5" />
        </marker>
      </defs>

      {/* Background */}
      <rect width={W} height={H} fill="#07070f" />
      <rect width={W} height={H} fill="url(#dotgrid)" />

      {/* Thin vertical dividers between columns */}
      {[CX.routing - 80, CX.agents - 80, CX.intel - 80,
        CX.tools - 80, CX.data - 80, CX.output - 80].map((x, i) => (
        <line key={i} x1={x} y1={32} x2={x} y2={H - 16}
              stroke="#111120" strokeWidth="1" />
      ))}

      {/* Column labels */}
      {colLabels.map(cl => (
        <text key={cl.label} x={cl.x} y={26}
              fill="#252538" fontSize="9" fontWeight="700"
              textAnchor="middle" letterSpacing="2">
          {cl.label}
        </text>
      ))}

      {/* ── Edges (circuit traces) ── */}
      {edges.map(([f, t], i) => {
        const a = nodes[f], b = nodes[t];
        if (!a || !b) return null;
        const d = tracePath(a.x, a.y, b.x, b.y);
        return (
          <path key={`e-${i}`} d={d}
                fill="none" stroke="#181828" strokeWidth="1.2"
                strokeLinecap="square" opacity="0.9"
                markerEnd="url(#arr)" />
        );
      })}

      {/* ── Pulses ── */}
      {pulses.map(p => {
        const a = nodes[p.from], b = nodes[p.to];
        if (!a || !b) return null;
        return (
          <CircuitPulse key={p.id}
            from={a} to={b} color={p.color} duration={p.ms / 1000} />
        );
      })}

      {/* ── Nodes ── */}
      {Object.values(nodes).map(n => (
        <CircuitNode key={n.id} node={n}
                     active={activeNodes.has(n.id)}
                     error={errorNodes.has(n.id)} />
      ))}
    </svg>
  );
}


/* ── Node ────────────────────────────────────────────────────── */
function CircuitNode({ node, active, error }) {
  const k = NODE_KINDS[node.kind] || NODE_KINDS.default;
  const r = k.radius;
  const col = error ? '#ef4444' : (active ? k.glow : k.idle);

  return (
    <g transform={`translate(${node.x},${node.y})`}>
      {/* Active ripple */}
      {active && !error && (
        <circle r={r} fill="none" stroke={k.glow} strokeWidth="1" opacity="0">
          <animate attributeName="r"       values={`${r};${r + 14}`}
                   dur="1.3s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.5;0"
                   dur="1.3s" repeatCount="indefinite" />
        </circle>
      )}

      {/* Square pad (PCB-style) */}
      <rect x={-r - 3} y={-r - 3} width={(r + 3) * 2} height={(r + 3) * 2}
            rx="4" fill="none"
            stroke={col} strokeWidth={active || error ? 1 : 0.6}
            opacity={active || error ? 0.55 : 0.2}
            style={{ transition: 'stroke 0.2s, opacity 0.2s' }} />

      {/* Body */}
      <rect x={-r} y={-r} width={r * 2} height={r * 2}
            rx="3"
            fill={error ? 'rgba(239,68,68,0.1)' : (active ? k.activeFill : k.fill)}
            stroke={col} strokeWidth="1.5"
            filter={active || error ? 'url(#nglow)' : undefined}
            style={{ transition: 'fill 0.2s, stroke 0.2s' }} />

      {/* Icon */}
      {node.icon && (
        <text x={0} y={r * 0.28} textAnchor="middle"
              fontSize={r * 0.78}
              fill={error ? '#ef4444' : (active ? k.glow : k.iconColor)}
              style={{ userSelect: 'none', transition: 'fill 0.2s' }}>
          {node.icon}
        </text>
      )}

      {/* Label */}
      <text x={0} y={r + 14} textAnchor="middle" fontSize="10" fontWeight="500"
            fill={error ? '#ef4444' : (active ? k.glow : '#555568')}
            style={{ userSelect: 'none', transition: 'fill 0.2s' }}>
        {node.label}
      </text>

      {node.meta && (
        <text x={0} y={r + 25} textAnchor="middle" fontSize="8" fill="#2e2e48"
              style={{ userSelect: 'none' }}>
          {node.meta}
        </text>
      )}
    </g>
  );
}

const NODE_KINDS = {
  default:  { fill: '#0a0a16', activeFill: '#12103a', glow: '#8b5cf6', idle: '#1e1e32', radius: 18, iconColor: '#303048' },
  input:    { fill: '#0a0a16', activeFill: '#06243a', glow: '#22d3ee', idle: '#1e1e32', radius: 16, iconColor: '#303048' },
  service:  { fill: '#0a0a16', activeFill: '#062a20', glow: '#10b981', idle: '#1e1e32', radius: 16, iconColor: '#303048' },
  core:     { fill: '#0e0c2a', activeFill: '#1a1650', glow: '#8b5cf6', idle: '#2a2650', radius: 20, iconColor: '#8b5cf6' },
  brain:    { fill: '#0e0c2a', activeFill: '#1a1650', glow: '#a78bfa', idle: '#2a2650', radius: 26, iconColor: '#a78bfa' },
  manager:  { fill: '#0e0c2a', activeFill: '#131550', glow: '#6366f1', idle: '#242650', radius: 22, iconColor: '#6366f1' },
  employee: { fill: '#0a0a16', activeFill: '#06243a', glow: '#22d3ee', idle: '#1e2232', radius: 18, iconColor: '#22d3ee' },
  cluster:  { fill: '#0a0a16', activeFill: '#2a1e08', glow: '#f59e0b', idle: '#1e1e32', radius: 18, iconColor: '#303048' },
  data:     { fill: '#0a0a16', activeFill: '#062a20', glow: '#10b981', idle: '#1e2a22', radius: 18, iconColor: '#303048' },
  output:   { fill: '#0a0a16', activeFill: '#131550', glow: '#6366f1', idle: '#1e1e3a', radius: 16, iconColor: '#303048' },
};


/* ── Animated circuit pulse ──────────────────────────────────── */
function CircuitPulse({ from, to, color, duration }) {
  const dur = `${duration}s`;
  const traceDPath = tracePath(from.x, from.y, to.x, to.y);
  const { cx: cxVals, cy: cyVals, kt } = pulseKeyTimes(from.x, from.y, to.x, to.y);

  return (
    <g>
      {/* Lit trace — fades along the circuit path */}
      <path d={traceDPath} fill="none" stroke={color} strokeWidth="2" strokeLinecap="square">
        <animate attributeName="opacity" values="0.6;0" dur={dur} fill="remove" />
        <animate attributeName="stroke-width" values="2;0.5" dur={dur} fill="remove" />
      </path>

      {/* Glow orb following the circuit path */}
      <circle r="8" cx={from.x} cy={from.y}
              fill={color} opacity="0" filter="url(#cglow)">
        <animate attributeName="cx"      values={cxVals} keyTimes={kt} dur={dur} fill="remove" calcMode="linear" />
        <animate attributeName="cy"      values={cyVals} keyTimes={kt} dur={dur} fill="remove" calcMode="linear" />
        <animate attributeName="opacity" values="0.35;0"               dur={dur} fill="remove" />
      </circle>

      {/* Core dot following the circuit path */}
      <circle r="3.5" cx={from.x} cy={from.y} fill={color}>
        <animate attributeName="cx"      values={cxVals} keyTimes={kt} dur={dur} fill="remove" calcMode="linear" />
        <animate attributeName="cy"      values={cyVals} keyTimes={kt} dur={dur} fill="remove" calcMode="linear" />
        <animate attributeName="opacity" values="1;0.1"                dur={dur} fill="remove" />
      </circle>

      {/* Trailing spark dot */}
      <circle r="2" cx={from.x} cy={from.y} fill={color} opacity="0.5">
        <animate attributeName="cx"      values={cxVals} keyTimes={kt} dur={dur} fill="remove" calcMode="linear" begin="0.08s" />
        <animate attributeName="cy"      values={cyVals} keyTimes={kt} dur={dur} fill="remove" calcMode="linear" begin="0.08s" />
        <animate attributeName="opacity" values="0.5;0"                dur={dur} fill="remove" />
      </circle>
    </g>
  );
}


/* ── Top metrics ─────────────────────────────────────────────── */
function TopBar({ metrics, wsConnected }) {
  const c  = metrics?.counters || {};
  const db = metrics?.db || {};
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      <Metric label="Status"       value={wsConnected ? 'LIVE' : 'OFFLINE'}
              color={wsConnected ? '#10b981' : '#ef4444'} />
      <Metric label="AI Calls"     value={c.total_ai_calls     || 0} />
      <Metric label="Tools Used"   value={c.total_tool_calls   || 0} />
      <Metric label="DB Writes"    value={c.total_db_queries   || 0} />
      <Metric label="Negotiations" value={c.total_negotiations || 0} />
      <Metric label="Errors"       value={c.total_errors       || 0}
              color={c.total_errors ? '#ef4444' : undefined} />
      <Metric label="Cost"         value={`$${(c.total_cost_usd || 0).toFixed(4)}`} />
      <Metric label="Active Tasks" value={db.active_tasks      || 0} />
      <Metric label="WS Conns"     value={metrics?.ws?.active_connections || 0} />
    </div>
  );
}

function Metric({ label, value, color }) {
  return (
    <div style={{
      flex: '1 1 100px', minWidth: 100, padding: '9px 13px',
      background: '#0c0c14', border: '1px solid #181828', borderRadius: 6,
    }}>
      <div style={{ fontSize: 9, color: '#303048', textTransform: 'uppercase',
                    letterSpacing: '0.09em', marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600, color: color || '#c8c8d8',
                    fontFamily: 'var(--font-mono, monospace)' }}>{value}</div>
    </div>
  );
}


/* ── Event log ───────────────────────────────────────────────── */
function EventLog({ events }) {
  const TC = {
    agent_thinking:    '#8b5cf6',
    agent_idle:        '#252538',
    tool_called:       '#f59e0b',
    tool_completed:    '#252538',
    db_query:          '#10b981',
    negotiation_start: '#ec4899',
    negotiation_step:  '#ec4899',
    negotiation_done:  '#ec4899',
    error:             '#ef4444',
    cost_recorded:     '#f59e0b',
    message_sent:      '#6366f1',
  };
  return (
    <div style={{ ...panelStyle }}>
      <PanelHeader title="Live Events" subtitle={`${events.length} / 80`} />
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(min(480px, 100%), 1fr))',
        maxHeight: 280, overflowY: 'auto',
        fontFamily: 'var(--font-mono, monospace)', fontSize: 11,
      }}>
        {events.length === 0 ? (
          <div style={{ color: '#252538', padding: '20px 14px' }}>Waiting for activity...</div>
        ) : events.map((e, i) => (
          <div key={i} style={{
            padding: '5px 12px', borderBottom: '1px solid #0d0d1a',
            display: 'flex', gap: 8, alignItems: 'baseline',
            background: i === 0 ? 'rgba(99,102,241,0.04)' : 'transparent',
          }}>
            <span style={{ color: '#252538', fontSize: 9, minWidth: 52, flexShrink: 0 }}>
              {new Date(e.ts * 1000).toLocaleTimeString('en-US', { hour12: false })}
            </span>
            <span style={{ color: TC[e.type] || '#555568', fontWeight: 700,
                           fontSize: 9, minWidth: 112, flexShrink: 0 }}>
              {e.type}
            </span>
            <span style={{ color: '#555568', fontSize: 10, overflow: 'hidden',
                           textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
              {e.actor}
              {e.data?.tool       && ` → ${e.data.tool}`}
              {e.data?.from_agent && ` ${e.data.from_agent}→${e.data.to_agent}`}
              {e.data?.message    && ` ${String(e.data.message).slice(0, 48)}`}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}


/* ── Shared ──────────────────────────────────────────────────── */
const panelStyle = {
  background: '#08080f',
  border: '1px solid #181828',
  borderRadius: 10,
  overflow: 'hidden',
};

function PanelHeader({ title, subtitle }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '9px 14px', borderBottom: '1px solid #111120',
    }}>
      <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
                    letterSpacing: '0.1em', color: '#555568' }}>{title}</div>
      <div style={{ fontSize: 10, color: '#252538' }}>{subtitle}</div>
    </div>
  );
}
