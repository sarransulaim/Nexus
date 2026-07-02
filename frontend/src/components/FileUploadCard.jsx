import { useState, useRef } from 'react';
import { useNexus } from '../context/NexusContext';
import axios from 'axios';

import { BACKEND_URL } from '../config';

/**
 * FileUploadCard
 * ----------------
 * Lives inside AI Commands page. Two modes:
 *   1. Idle  → paperclip icon + drop zone overlay
 *   2. Card  → shows AI analysis with editable owner picker per task
 *              Manager confirms each owner explicitly before execute.
 *
 * Use it like:
 *   <FileUploadCard employees={teamList} onComplete={() => refreshDashboard()} />
 */
export default function FileUploadCard({ employees = [], onComplete }) {
  const { currentUser } = useNexus();
  const [stage, setStage] = useState('idle');        // idle | uploading | analyzing | preview | executing | done
  const [error, setError] = useState(null);
  const [activeFile, setActiveFile] = useState(null);
  const [actions,    setActions]    = useState([]);
  const [dragActive, setDragActive] = useState(false);
  const inputRef = useRef(null);

  if (currentUser?.role !== 'Manager') return null;

  // ── Drag & drop ────────────────────────────────────────────
  const handleDrag = (e) => {
    e.preventDefault(); e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') setDragActive(true);
    else if (e.type === 'dragleave') setDragActive(false);
  };

  const handleDrop = (e) => {
    e.preventDefault(); e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files?.[0]) handleUpload(e.dataTransfer.files[0]);
  };

  const handleFileSelect = (e) => {
    if (e.target.files?.[0]) handleUpload(e.target.files[0]);
  };

  // ── Upload & analyze ──────────────────────────────────────
  const handleUpload = async (file) => {
    setError(null);
    setStage('uploading');

    const formData = new FormData();
    formData.append('file', file);

    try {
      setStage('analyzing');
      const res = await axios.post(
        `${BACKEND_URL}/api/v1/files/upload`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      );
      setActiveFile(res.data);
      setActions(res.data.analysis?.proposed_actions || []);
      setStage('preview');
    } catch (e) {
      setError(e.response?.data?.detail || 'Upload failed');
      setStage('idle');
    }
  };

  // ── Owner picker for nested tasks ─────────────────────────
  const updateNestedTaskOwner = (actionIdx, taskIdx, ownerId) => {
    const next = [...actions];
    const a    = { ...next[actionIdx] };
    a.details  = { ...a.details };
    a.details.tasks = a.details.tasks.map((t, i) =>
      i === taskIdx ? { ...t, owner_id: ownerId || null } : t
    );
    next[actionIdx] = a;
    setActions(next);
  };

  // ── Owner picker for standalone task ──────────────────────
  const updateOwner = (actionIdx, ownerId) => {
    const next = [...actions];
    next[actionIdx] = {
      ...next[actionIdx],
      details: { ...next[actionIdx].details, owner_id: ownerId || null },
    };
    setActions(next);
  };

  const removeAction = (idx) => {
    setActions(actions.filter((_, i) => i !== idx));
  };

  // ── Execute ───────────────────────────────────────────────
  const handleExecute = async () => {
    setStage('executing');
    try {
      const res = await axios.post(
        `${BACKEND_URL}/api/v1/files/${activeFile.file_id}/execute`,
        { edited_actions: actions }
      );
      setStage('done');
      if (onComplete) onComplete(res.data.created);

      // Auto-reset after 5 seconds
      setTimeout(() => {
        setStage('idle');
        setActiveFile(null);
        setActions([]);
      }, 5000);
    } catch (e) {
      setError(e.response?.data?.detail || 'Execute failed');
      setStage('preview');
    }
  };

  const handleCancel = () => {
    setStage('idle');
    setActiveFile(null);
    setActions([]);
    setError(null);
  };

  // ═══════════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════════

  // ── Idle state — just the paperclip icon ────────────────
  if (stage === 'idle') {
    return (
      <div
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        title="Upload a file (PDF, DOCX, XLSX, image)"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '6px',
          padding: '6px 10px',
          background: dragActive ? 'rgba(99,102,241,0.15)' : 'transparent',
          border: `1px solid ${dragActive ? 'var(--p)' : 'var(--b1)'}`,
          borderRadius: '8px',
          color: 'var(--t2)',
          cursor: 'pointer',
          fontSize: '12px',
          transition: 'all 0.15s',
        }}
      >
        <input
          ref={inputRef}
          type="file"
          onChange={handleFileSelect}
          style={{ display: 'none' }}
          accept=".pdf,.docx,.doc,.xlsx,.xls,.csv,.txt,.md,.png,.jpg,.jpeg,.gif,.webp"
        />
        <span style={{ fontSize: '14px' }}>📎</span>
        <span>{dragActive ? 'Drop file' : 'Attach file'}</span>
      </div>
    );
  }

  // ── Uploading / analyzing ──────────────────────────────
  if (stage === 'uploading' || stage === 'analyzing') {
    return (
      <div style={cardStyle}>
        <div style={{ color: 'var(--ai)', fontSize: '14px', fontWeight: 500 }}>
          {stage === 'uploading' ? 'Uploading file...' : 'Nexus is reading the file...'}
        </div>
        <div style={{ color: 'var(--t3)', fontSize: '12px', marginTop: '6px' }}>
          {stage === 'uploading' ? 'Please wait' : 'Usually takes 5-15 seconds'}
        </div>
      </div>
    );
  }

  // ── Executing ──────────────────────────────────────────
  if (stage === 'executing') {
    return (
      <div style={cardStyle}>
        <div style={{ color: 'var(--ai)', fontSize: '14px', fontWeight: 500 }}>
          Creating entities...
        </div>
      </div>
    );
  }

  // ── Done ───────────────────────────────────────────────
  if (stage === 'done') {
    return (
      <div style={cardStyle}>
        <div style={{ color: 'var(--ai)', fontSize: '14px', fontWeight: 500 }}>
          ✓ Done. Dashboard updated.
        </div>
      </div>
    );
  }

  // ── Preview — the main interaction ─────────────────────
  if (stage === 'preview') {
    const analysis = activeFile?.analysis || {};

    return (
      <div style={cardStyle}>
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
                      marginBottom: '12px' }}>
          <div>
            <div style={{ color: 'var(--ai)', fontSize: '10px', textTransform: 'uppercase',
                          letterSpacing: '0.08em', fontWeight: 600 }}>
              {analysis.type} — {analysis.confidence ?? 0}% confident
            </div>
            <h3 style={{ color: 'var(--t1)', marginTop: '4px', fontSize: '16px',
                         fontFamily: 'inherit', fontWeight: 600 }}>
              {analysis.title || activeFile.filename}
            </h3>
          </div>
          <button onClick={handleCancel} style={btnGhost}>Cancel</button>
        </div>

        {/* Summary */}
        <p style={{ color: 'var(--t2)', fontSize: '13px', lineHeight: 1.5, marginBottom: '14px' }}>
          {analysis.summary}
        </p>

        {analysis.needs_review && (
          <div style={warningBox}>
            Nexus was not fully confident. Review each action carefully before executing.
          </div>
        )}

        {error && <div style={errorBox}>{error}</div>}

        {/* Actions */}
        {actions.length === 0 ? (
          <p style={{ color: 'var(--t3)', fontSize: '13px', fontStyle: 'italic' }}>
            No actions to execute. The file was understood but Nexus didn't suggest creating anything.
          </p>
        ) : (
          <div style={{ marginBottom: '14px' }}>
            {actions.map((a, idx) => (
              <ActionRow
                key={idx}
                action={a}
                idx={idx}
                employees={employees}
                onOwnerChange={(taskIdx, ownerId) =>
                  taskIdx === null
                    ? updateOwner(idx, ownerId)
                    : updateNestedTaskOwner(idx, taskIdx, ownerId)
                }
                onRemove={() => removeAction(idx)}
              />
            ))}
          </div>
        )}

        {/* Assignment summary + execute */}
        {actions.length > 0 && (
          <>
            <AssignmentSummary actions={actions} employees={employees} />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '12px' }}>
              <button onClick={handleCancel} style={btnGhost}>Cancel</button>
              <button onClick={handleExecute} style={btnPrimary}>
                Confirm & Execute
              </button>
            </div>
          </>
        )}
      </div>
    );
  }

  return null;
}


// ═══════════════════════════════════════════════════════════════
// Action Row — one action with editable details
// ═══════════════════════════════════════════════════════════════

function ActionRow({ action, idx, employees, onOwnerChange, onRemove }) {
  const [expanded, setExpanded] = useState(true);
  const d = action.details || {};
  const isProject = action.action === 'create_project';
  const isTask    = action.action === 'create_task';

  return (
    <div style={actionCard}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div onClick={() => setExpanded(!expanded)} style={{ cursor: 'pointer', flex: 1 }}>
          <div style={{ color: 'var(--ai)', fontSize: '10px', fontWeight: 600,
                        textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {(action.action || 'unknown').replace(/_/g, ' ')}
          </div>
          <div style={{ color: 'var(--t1)', fontSize: '14px', marginTop: '2px', fontWeight: 500 }}>
            {d.name || d.title || d.topic || 'Untitled'}
          </div>
          {action.action === 'add_employee' && (
            <div style={{ color: 'var(--t3)', fontSize: '11px', marginTop: '2px' }}>
              {d.role || 'Employee'}
              {d.experience ? ` · ${d.experience}y exp` : ''}
              {d.email ? ` · ${d.email}` : ''}
            </div>
          )}
        </div>
        <button onClick={onRemove} style={{ ...btnGhost, padding: '2px 8px', fontSize: '11px' }}>
          ✕
        </button>
      </div>

      {expanded && (
        <div style={{ marginTop: '10px', paddingTop: '10px', borderTop: '1px solid var(--b1)' }}>

          {/* Description */}
          {(d.description || d.summary) && (
            <p style={{ color: 'var(--t2)', fontSize: '12px', lineHeight: 1.5, marginBottom: '8px' }}>
              {d.description || d.summary}
            </p>
          )}

          {/* Standalone task — owner picker right here */}
          {isTask && (
            <div style={{ marginTop: '8px' }}>
              <label style={lbl}>Assign to</label>
              <OwnerPicker
                employees={employees}
                value={d.owner_id}
                suggestion={d.suggested_owner_id}
                suggestionReason={d.suggested_owner_reason}
                onChange={(id) => onOwnerChange(null, id)}
              />
              {d.due_date && (
                <div style={{ color: 'var(--t3)', fontSize: '11px', marginTop: '6px' }}>
                  Due: {d.due_date}
                </div>
              )}
            </div>
          )}

          {/* Project — list of tasks each with its own owner picker */}
          {isProject && d.tasks && d.tasks.length > 0 && (
            <div style={{ marginTop: '8px' }}>
              <label style={lbl}>Tasks in project ({d.tasks.length})</label>
              <div style={{ display: 'grid', gap: '6px' }}>
                {d.tasks.map((t, i) => (
                  <div key={i} style={nestedTask}>
                    <div style={{ display: 'flex', justifyContent: 'space-between',
                                  alignItems: 'center', marginBottom: '4px' }}>
                      <div style={{ color: 'var(--t1)', fontSize: '12px', fontWeight: 500 }}>
                        {t.title}
                      </div>
                      <div style={{ color: 'var(--t3)', fontSize: '10px' }}>
                        {t.priority || 'Medium'}
                        {t.due_date && ` · ${t.due_date}`}
                      </div>
                    </div>
                    {t.skill_hint && (
                      <div style={{ color: 'var(--t3)', fontSize: '10px', marginBottom: '4px' }}>
                        Skills: {t.skill_hint}
                      </div>
                    )}
                    <OwnerPicker
                      employees={employees}
                      value={t.owner_id}
                      suggestion={t.suggested_owner_id}
                      suggestionReason={t.suggested_owner_reason}
                      onChange={(id) => onOwnerChange(i, id)}
                      compact
                    />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════
// Owner Picker — dropdown with Nexus's suggestion shown
// ═══════════════════════════════════════════════════════════════

function OwnerPicker({ employees, value, suggestion, suggestionReason, onChange, compact = false }) {
  const list = (employees || []).filter(e => e?.role !== 'Manager' && e?.role !== 'manager');

  return (
    <div>
      <select
        value={value || ''}
        onChange={(e) => onChange(e.target.value ? parseInt(e.target.value, 10) : null)}
        style={{
          ...inp,
          padding: compact ? '5px 8px' : '8px 10px',
          fontSize: compact ? '11px' : '13px',
        }}
      >
        <option value="">Unassigned</option>
        {list.map(emp => (
          <option key={emp.id} value={emp.id}>
            {emp.name} {emp.role ? `— ${emp.role}` : ''}
          </option>
        ))}
      </select>
      {suggestion && suggestion !== value && (
        <div
          style={{ color: 'var(--ai)', fontSize: '10px', marginTop: '3px', cursor: 'pointer' }}
          onClick={() => onChange(suggestion)}
        >
          Nexus suggests: {(list.find(e => e.id === suggestion)?.name) || `#${suggestion}`}
          {suggestionReason && <span style={{ color: 'var(--t3)' }}> — {suggestionReason}</span>}
        </div>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════
// Assignment summary footer
// ═══════════════════════════════════════════════════════════════

function AssignmentSummary({ actions, employees }) {
  // Count who gets what
  const counts = {};
  let unassigned = 0;
  let total      = 0;

  for (const a of actions) {
    const d = a.details || {};
    if (a.action === 'create_task') {
      total++;
      if (d.owner_id) counts[d.owner_id] = (counts[d.owner_id] || 0) + 1;
      else unassigned++;
    } else if (a.action === 'create_project' && d.tasks) {
      for (const t of d.tasks) {
        total++;
        if (t.owner_id) counts[t.owner_id] = (counts[t.owner_id] || 0) + 1;
        else unassigned++;
      }
    }
  }

  if (total === 0) return null;

  const lines = Object.entries(counts).map(([id, n]) => {
    const emp = (employees || []).find(e => e.id === parseInt(id, 10));
    return `${n} → ${emp ? emp.name : '#' + id}`;
  });

  return (
    <div style={{ background: 'var(--bg-0)', border: '1px solid var(--b1)',
                  borderRadius: '6px', padding: '8px 12px', fontSize: '11px',
                  color: 'var(--t2)' }}>
      <strong style={{ color: 'var(--t1)' }}>Assignment: </strong>
      {lines.length > 0 ? lines.join(' · ') : null}
      {unassigned > 0 && (
        <span style={{ color: '#f59e0b', marginLeft: lines.length > 0 ? '8px' : 0 }}>
          {unassigned} unassigned
        </span>
      )}
    </div>
  );
}


// ── Styles ─────────────────────────────────────────────────
const cardStyle = {
  background: 'var(--bg-2)',
  border: '1px solid var(--b1)',
  borderRadius: '10px',
  padding: '16px',
  marginTop: '12px',
};

const actionCard = {
  background: 'var(--bg-0)',
  border: '1px solid var(--b1)',
  borderRadius: '8px',
  padding: '10px 12px',
  marginBottom: '8px',
};

const nestedTask = {
  background: 'var(--bg-2)',
  border: '1px solid var(--b1)',
  borderRadius: '6px',
  padding: '8px 10px',
};

const warningBox = {
  color: '#f59e0b',
  background: 'rgba(245,158,11,0.1)',
  padding: '8px 12px',
  borderRadius: '6px',
  fontSize: '12px',
  marginBottom: '12px',
};

const errorBox = {
  color: '#ef4444',
  background: 'rgba(239,68,68,0.1)',
  padding: '8px 12px',
  borderRadius: '6px',
  fontSize: '12px',
  marginBottom: '12px',
};

const btnPrimary = {
  background: 'var(--p)',
  color: '#fff',
  border: 'none',
  borderRadius: '6px',
  padding: '8px 16px',
  fontSize: '12px',
  fontWeight: 500,
  cursor: 'pointer',
};

const btnGhost = {
  background: 'transparent',
  color: 'var(--t2)',
  border: '1px solid var(--b1)',
  borderRadius: '6px',
  padding: '6px 12px',
  fontSize: '12px',
  cursor: 'pointer',
};

const lbl = {
  display: 'block',
  color: 'var(--t3)',
  fontSize: '10px',
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  marginBottom: '4px',
};

const inp = {
  width: '100%',
  background: 'var(--bg-2)',
  color: 'var(--t1)',
  border: '1px solid var(--b1)',
  borderRadius: '6px',
  padding: '8px 10px',
  fontSize: '13px',
  fontFamily: 'inherit',
};