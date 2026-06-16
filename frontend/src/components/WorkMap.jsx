import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import ForceGraph3D from 'react-force-graph-3d';
import SpriteText from 'three-spritetext';
import * as THREE from 'three';

const NODE_CONFIG = {
  database: { radius: 22, color: '#6366f1', label: 'Nexus Database'    },
  core:     { radius: 18, color: '#ffffff', label: 'Nexus Orchestrator' },
  project:  { radius: 14, color: '#10b981', label: 'Projects'           },
  team:     { radius: 12, color: '#8b5cf6', label: 'Teams'              },
  employee: { radius: 9,  color: '#a5b4fc', label: 'Employees'          },
  pai:      { radius: 6,  color: '#d946ef', label: 'Personal AI Agents' },
  pdb:      { radius: 5,  color: '#475569', label: 'Private Databases'  },
  task:     { radius: 5,  color: '#f59e0b', label: 'Tasks'              },
  subtask:  { radius: 3,  color: '#78716c', label: 'Subtasks'           },
};

const LINK_COLOR    = 'rgba(99,102,241,0.35)';
const LINK_WIDTH    = 1;
const CANVAS_HEIGHT = 620;

export default function WorkMap({ employees, tasks }) {
  const containerRef = useRef(null);
  const graphRef     = useRef(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: CANVAS_HEIGHT });
  const rotateRef    = useRef(null);

  useEffect(() => {
    const measure = () => {
      if (containerRef.current) {
        setDimensions({ width: containerRef.current.offsetWidth, height: CANVAS_HEIGHT });
      }
    };
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);

  const startAutoRotate = useCallback(() => {
    if (!graphRef.current) return;
    const SPEED = 0.0015;
    let angle = 0;
    const animate = () => {
      if (!graphRef.current) return;
      angle += SPEED;
      const dist = 400;
      graphRef.current.cameraPosition({
        x: dist * Math.sin(angle),
        z: dist * Math.cos(angle),
      });
      rotateRef.current = requestAnimationFrame(animate);
    };
    animate();
  }, []);

  const stopAutoRotate = useCallback(() => {
    if (rotateRef.current) {
      cancelAnimationFrame(rotateRef.current);
      rotateRef.current = null;
    }
  }, []);

  const graphData = useMemo(() => {
    const nodes = [];
    const links = [];
    const added = new Set();

    const addNode = (id, group, name) => {
      if (!added.has(id)) {
        nodes.push({ id, group, name });
        added.add(id);
      }
    };

    const safeEmps  = Array.isArray(employees) ? employees : [];
    const safeTasks = Array.isArray(tasks)     ? tasks     : [];

    addNode('db',   'database', 'Nexus DB');
    addNode('core', 'core',     'Nexus AI');
    links.push({ source: 'db', target: 'core' });

    const teams = [...new Set(safeEmps.map(e => e.team || 'Unassigned'))];
    teams.forEach(t => {
      const tid = `team-${t}`;
      addNode(tid, 'team', t.toLowerCase().startsWith('team') ? t : `Team ${t}`);
      links.push({ source: 'core', target: tid });
    });

    safeEmps.forEach(emp => {
      const empId = `emp-${emp.id}`;
      const paiId = `pai-${emp.id}`;
      const pdbId = `pdb-${emp.id}`;
      const tid   = `team-${emp.team || 'Unassigned'}`;

      addNode(empId, 'employee', emp.name);
      addNode(paiId, 'pai',      `${emp.name}'s AI`);
      addNode(pdbId, 'pdb',      `${emp.name}'s DB`);

      links.push({ source: tid,   target: empId });
      links.push({ source: empId, target: paiId });
      links.push({ source: paiId, target: pdbId });
    });

    safeTasks.forEach(task => {
      let projName = 'General';
      if (task.title.includes(':')) projName = task.title.split(':')[0].trim();

      const projId = `proj-${projName}`;
      addNode(projId, 'project', projName.substring(0, 20));
      links.push({ source: 'core', target: projId });

      const taskId   = `task-${task.id}`;
      const taskName = task.title.includes(':')
        ? task.title.split(':')[1].trim().substring(0, 22)
        : task.title.substring(0, 22);

      addNode(taskId, 'task', taskName);
      links.push({ source: projId, target: taskId });

      const owner = safeEmps.find(e => e.id === task.owner_id);
      if (owner) {
        links.push({ source: `emp-${owner.id}`, target: taskId });
        links.push({ source: `pai-${owner.id}`, target: taskId });
      }

      (task.subtasks || []).forEach(sub => {
        const subId = `sub-${sub.id}`;
        addNode(subId, 'subtask', sub.title.substring(0, 18));
        links.push({ source: taskId, target: subId });
      });
    });

    return { nodes, links };
  }, [employees, tasks]);

  const renderNode = useCallback((node) => {
    const cfg = NODE_CONFIG[node.group] || { radius: 5, color: '#64748b' };

    const geo = new THREE.SphereGeometry(cfg.radius, 32, 32);
    const mat = new THREE.MeshStandardMaterial({
      color:             cfg.color,
      emissive:          cfg.color,
      emissiveIntensity: node.group === 'core' ? 0.6 : 0.4,
      roughness:         0.3,
      metalness:         0.2,
      transparent:       true,
      opacity:           node.group === 'subtask' ? 0.6 : 0.85,
    });
    const sphere = new THREE.Mesh(geo, mat);

    const label = new SpriteText(node.name);
    label.color           = node.group === 'core' ? '#ffffff' : 'rgba(248,250,252,0.75)';
    label.textHeight      = node.group === 'core' ? 10 : 7;
    label.fontWeight      = node.group === 'core' ? 'bold' : 'normal';
    label.fontFace        = 'Inter, ui-sans-serif, system-ui, sans-serif';
    label.backgroundColor = 'rgba(9,9,15,0.6)';
    label.padding         = 2;
    label.borderRadius    = 3;
    label.position.set(0, -(cfg.radius + 10), 0);
    label.visible   = node.group === 'core' || node.group === 'database';
    label.userData  = { isLabel: true, group: node.group };

    const group = new THREE.Group();
    group.add(sphere);
    group.add(label);
    return group;
  }, []);

  useEffect(() => {
    let raf;
    const tmp = new THREE.Vector3();
    const tick = () => {
      if (graphRef.current) {
        const cam   = graphRef.current.camera();
        const scene = graphRef.current.scene();
        if (cam && scene) {
          scene.traverse(obj => {
            if (
              obj.userData?.isLabel &&
              obj.userData.group !== 'core' &&
              obj.userData.group !== 'database'
            ) {
              obj.getWorldPosition(tmp);
              obj.visible = tmp.distanceToSquared(cam.position) < 55000;
            }
          });
        }
      }
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [graphData]);

  if (graphData.nodes.length === 0) return null;

  return (
    <div style={{
      // FIX: --border, --bg-card, --text-3 don't exist in the design system.
      // Replaced with the correct CSS variables from index.css.
      borderRadius: '0.875rem',
      border:       '1px solid var(--b1)',      // was: var(--border)
      background:   'var(--bg-2)',              // was: var(--bg-card)
      overflow:     'hidden',
      position:     'relative',
      marginTop:    '1.25rem',
    }}>
      {/* Header overlay */}
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10,
        display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
        padding: '1.25rem', pointerEvents: 'none',
      }}>
        <div>
          <p style={{
            fontSize: '0.6875rem', fontWeight: 700, textTransform: 'uppercase',
            letterSpacing: '0.08em',
            color: 'var(--t3)',   // was: var(--text-3)
            marginBottom: '0.25rem',
          }}>
            Nexus 3D Brain
          </p>
          <p style={{ fontSize: '0.5625rem', color: 'var(--t3)', opacity: 0.6 }}>
            Drag to rotate · Scroll to zoom · Zoom in to reveal labels
          </p>
        </div>

        {/* Legend */}
        <div style={{
          display: 'flex', flexDirection: 'column', gap: '0.375rem',
          background: 'rgba(9,9,15,0.75)', backdropFilter: 'blur(8px)',
          border: '1px solid var(--b1)', borderRadius: '0.625rem',
          padding: '0.75rem 1rem',
        }}>
          {Object.entries(NODE_CONFIG).map(([key, cfg]) => (
            <div key={key} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <div style={{
                width:        `${Math.max(cfg.radius * 0.55, 5)}px`,
                height:       `${Math.max(cfg.radius * 0.55, 5)}px`,
                borderRadius: '999px',
                background:   cfg.color,
                boxShadow:    `0 0 6px ${cfg.color}66`,
                flexShrink:   0,
              }} />
              <span style={{
                fontSize:      '0.5625rem',
                color:         'rgba(248,250,252,0.5)',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                whiteSpace:    'nowrap',
              }}>
                {cfg.label}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Graph canvas */}
      <div
        ref={containerRef}
        style={{
          width: '100%', height: `${CANVAS_HEIGHT}px`, cursor: 'grab',
          background: 'radial-gradient(ellipse at center, rgba(99,102,241,0.07) 0%, rgba(9,9,15,0) 65%)',
        }}
        onMouseDown={stopAutoRotate}
      >
        {dimensions.width > 0 && (
          <ForceGraph3D
            ref={graphRef}
            width={dimensions.width}
            height={CANVAS_HEIGHT}
            graphData={graphData}
            nodeLabel={() => ''}
            linkColor={() => LINK_COLOR}
            linkWidth={LINK_WIDTH}
            linkOpacity={0.5}
            nodeThreeObject={renderNode}
            d3VelocityDecay={0.25}
            d3AlphaDecay={0.02}
            showNavInfo={false}
            enableNavigationControls={true}
            backgroundColor="rgba(0,0,0,0)"
            onEngineStop={startAutoRotate}
            onBackgroundClick={stopAutoRotate}
          />
        )}
      </div>
    </div>
  );
}
