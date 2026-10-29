import { useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Background, Controls, Handle, MarkerType, Position, ReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useApi } from '../api.js'
import { Badge, Empty, ErrorBox, friendly, Icon, Page, SeverityBadge, timeAgo } from '../ui.jsx'

const KIND = {
  agent: ['cpu', 'Agent'], tool: ['tool', 'Tool'], source: ['inbox', 'Data source'], memory: ['memory', 'Memory'], human: ['user', 'Human'],
}

function FlowNode({ data, selected }) {
  const [icon, kind] = KIND[data.kind]
  const risky = data.kind === 'source' && ['high', 'medium'].includes(data.meta?.severity)
  const blocked = data.kind === 'tool' && data.meta?.deny
  return (
    <div className={`flow-node ${data.kind} ${selected ? 'sel' : ''} ${risky || blocked ? 'risk' : ''}`}>
      <Handle type="target" position={Position.Left} />
      <div className="ic"><Icon name={icon} size={16} /></div>
      <div style={{ minWidth: 0 }}>
        <div className="t">{data.kind === 'tool' ? friendly(data.label) : data.label}</div>
        <div className="s">{kind} · {data.count} {data.kind === 'agent' ? 'events' : 'times'}{blocked ? ` · ${data.meta.deny} blocked` : ''}</div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}
const nodeTypes = { bb: FlowNode }

function layout(nodes, edges) {
  // Columns: humans | data sources | agents | tools and memory. One row band per agent.
  const ROW = 64, GAP = 44
  const agents = nodes.filter((n) => n.kind === 'agent')
  const humans = nodes.filter((n) => n.kind === 'human')
  const pos = {}
  let y = 0
  for (const a of agents) {
    const left = nodes.filter((n) => n.agent_id === a.id && n.kind === 'source')
    const right = nodes.filter((n) => n.agent_id === a.id && (n.kind === 'tool' || n.kind === 'memory'))
    const h = Math.max(left.length, right.length, 1) * ROW
    left.forEach((n, i) => { pos[n.id] = { x: 0, y: y + i * ROW + (h - left.length * ROW) / 2 } })
    right.forEach((n, i) => { pos[n.id] = { x: 640, y: y + i * ROW + (h - right.length * ROW) / 2 } })
    pos[a.id] = { x: 300, y: y + h / 2 - 28 }
    y += h + GAP
  }
  humans.forEach((n, i) => { pos[n.id] = { x: -300, y: (y - GAP) / 2 - (humans.length * ROW) / 2 + i * ROW } })
  const now = Date.now()
  return {
    nodes: nodes.map((n) => ({ id: n.id, type: 'bb', position: pos[n.id] || { x: 0, y: 0 }, data: n })),
    edges: edges.map((e, i) => {
      const color = e.denied ? '#f87171' : e.risk === 'high' ? '#fbbf24' : '#7dd3fc'
      const width = 1.2 + Math.log2(e.count + 1) * 1.1
      const recent = e.last && now - new Date(e.last).getTime() < 24 * 3600 * 1000
      return {
        id: `e${i}`, source: e.source, target: e.target, animated: Boolean(recent),
        label: e.denied ? `${e.denied} blocked` : e.sub ? 'sub-agent' : undefined,
        labelStyle: { fill: '#fca5a5', fontSize: 11, fontWeight: 600 }, labelBgStyle: { fill: '#0e1424' },
        style: { stroke: color, strokeWidth: width, opacity: 0.85, filter: `drop-shadow(0 0 ${Math.min(8, 2 + e.count)}px ${color}66)` },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
      }
    }),
  }
}

function Details({ node, onClose }) {
  const [, kind] = KIND[node.kind]
  const m = node.meta || {}
  return (
    <motion.aside className="drawer" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }}>
      <div className="spread" style={{ marginBottom: 10 }}>
        <Badge tone="info" icon={KIND[node.kind][0]}>{kind}</Badge>
        <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close details"><Icon name="x" /></button>
      </div>
      <h3 style={{ margin: '0 0 4px', fontSize: 17 }}>{node.kind === 'tool' ? friendly(node.label) : node.label}</h3>
      {node.kind === 'agent' && (
        <>
          <p className="muted" style={{ marginTop: 0 }}>{m.owner || 'No owner label'}</p>
          <div className="list">
            <div className="list-item"><Icon name="runs" /><div className="grow">{node.count} events recorded</div></div>
            {m.paused && <div className="list-item"><Icon name="pause" /><div className="grow">Paused: every tool call is blocked</div></div>}
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <a className="btn btn-sm" href={`#/runs?agent=${node.id}`}><Icon name="runs" size={14} />Runs</a>
            <a className="btn btn-sm" href={`#/policies/${node.id}`}><Icon name="policy" size={14} />Policy</a>
          </div>
        </>
      )}
      {node.kind === 'tool' && (
        <div className="list" style={{ marginTop: 10 }}>
          <div className="list-item"><Badge tone="ok" icon="check">Allowed</Badge><div className="grow" style={{ textAlign: 'right' }}>{m.allow || 0}</div></div>
          <div className="list-item"><Badge tone="warn" icon="alert">Asked a human</Badge><div className="grow" style={{ textAlign: 'right' }}>{m.needs_approval || 0}</div></div>
          <div className="list-item"><Badge tone="bad" icon="x">Blocked</Badge><div className="grow" style={{ textAlign: 'right' }}>{m.deny || 0}</div></div>
          <div className="muted" style={{ fontSize: 12.5 }}>Last checked {timeAgo(m.last)}</div>
        </div>
      )}
      {node.kind === 'source' && (
        <div className="list" style={{ marginTop: 10 }}>
          <div className="list-item"><Icon name="inbox" /><div className="grow">{node.count} untrusted inputs</div></div>
          <div className="list-item"><div className="grow">Worst injection scan</div><SeverityBadge value={m.severity} /></div>
          <div className="muted" style={{ fontSize: 12.5 }}>Last seen {timeAgo(m.last)}</div>
        </div>
      )}
      {node.kind === 'memory' && <p className="ink2">{node.count} memory writes. Last {timeAgo(m.last)}.</p>}
      {node.kind === 'human' && <p className="ink2">Answered {node.count} approval request{node.count === 1 ? '' : 's'}.</p>}
    </motion.aside>
  )
}

export default function AgentMap() {
  const { data, error } = useApi('/v1/map', { every: 15000 })
  const [selected, setSelected] = useState(null)
  const flow = useMemo(() => (data ? layout(data.nodes, data.edges) : null), [data])
  return (
    <Page title="Agent map" sub="Agents, the tools they call, where their input comes from, and the humans who answer them. Thicker lines mean more use.">
      <ErrorBox error={error} />
      {flow && (flow.nodes.length ? (
        <div className="map-wrap">
          <div className="legend" aria-label="Legend">
            <span><Icon name="cpu" size={13} />Agent</span><span><Icon name="tool" size={13} />Tool</span>
            <span><Icon name="inbox" size={13} />Data source</span><span><Icon name="user" size={13} />Human</span>
            <span style={{ color: 'var(--bad)' }}><Icon name="x" size={13} />Red line: calls were blocked</span>
          </div>
          <ReactFlow nodes={flow.nodes} edges={flow.edges} nodeTypes={nodeTypes} colorMode="dark" fitView fitViewOptions={{ padding: 0.08, maxZoom: 1.1 }}
            minZoom={0.2} nodesConnectable={false} onNodeClick={(_, n) => setSelected(n.data)} onPaneClick={() => setSelected(null)}
            proOptions={{ hideAttribution: true }}>
            <Background color="rgba(148,163,196,0.18)" gap={28} size={1.2} />
            <Controls showInteractive={false} />
          </ReactFlow>
          <AnimatePresence>{selected && <Details key={selected.id} node={selected} onClose={() => setSelected(null)} />}</AnimatePresence>
        </div>
      ) : <div className="card"><Empty icon="map" title="Nothing to map yet">The map fills in as agents record tool calls and inputs.</Empty></div>)}
    </Page>
  )
}
