import { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { useApi } from '../api.js'
import { Badge, copy, DecisionBadge, Empty, ErrorBox, fmtClock, fmtTime, friendly, Icon, Page, SeverityBadge, timeAgo } from '../ui.jsx'

const DECISION_WORD = { allow: 'Allowed', needs_approval: 'Asked a human about', deny: 'Blocked' }

/** Turn one recorded event into a card: icon, title, one-line body, highlight tone, scrubber tick tone. */
export function describe(e) {
  const p = e.payload || {}
  switch (e.type) {
    case 'model_call': return { icon: 'cpu', title: `Model call · ${p.model || 'model'}`, body: p.response || p.prompt, tick: 'acc' }
    case 'tool_call': return { icon: 'tool', title: `Called ${friendly(p.tool)}`, body: argsLine(p.args), tick: 'acc' }
    case 'tool_result': return p.ok
      ? { icon: 'check', title: `${friendly(p.tool)} finished`, body: `${p.duration_ms ?? '?'} ms`, tone: 'ok', tick: '' }
      : { icon: 'x', title: `${friendly(p.tool)} failed`, body: p.error, tone: 'bad', tick: 'bad' }
    case 'memory_write': return { icon: 'memory', title: `Wrote memory "${p.key}"`, body: String(p.value ?? ''), tone: 'warn', tick: 'warn', change: true }
    case 'policy_decision': {
      const tone = { allow: 'ok', needs_approval: 'warn', deny: 'bad' }[p.decision]
      return { icon: 'shield', title: `${DECISION_WORD[p.decision] || p.decision} ${friendly(p.tool)}`, body: p.reason, tone, tick: tone, decision: p.decision }
    }
    case 'approval_request': return { icon: 'approvals', title: 'Asked a human', body: `${p.summary || ''} Notified: ${p.notified || 'dashboard'}.`, tone: 'warn', tick: 'warn' }
    case 'approval_decision': {
      const ok = p.decision === 'approved'
      return { icon: ok ? 'check' : 'x', title: `${ok ? 'Approved' : p.decision === 'expired' ? 'Expired, blocked by default' : 'Blocked'} by ${p.decided_by}`,
        body: p.note || `via ${p.via}`, tone: ok ? 'ok' : 'bad', tick: ok ? 'ok' : 'bad' }
    }
    case 'error': return { icon: 'alert', title: 'Error', body: p.message, tone: 'bad', tick: 'bad' }
    default:
      if (p.kind === 'input') {
        const sev = p.scan?.severity
        const risky = sev === 'high' || sev === 'medium'
        return { icon: 'inbox', title: `Untrusted input from ${friendly(p.source)}`, body: p.text, tone: risky ? 'bad' : undefined, tick: risky ? 'bad' : '', severity: sev }
      }
      if (p.kind === 'policy_change' || p.kind === 'permission_change') return { icon: 'lock', title: 'Permission change', body: p.text, tone: 'warn', tick: 'warn', change: true }
      return { icon: 'note', title: 'Note', body: p.text, tick: '' }
  }
}
const argsLine = (a) => Object.entries(a || {}).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' · ')

function signature(e) {
  const p = e.payload || {}
  if (e.type === 'policy_decision') return `${p.tool} → ${p.decision}`
  if (e.type === 'memory_write') return `memory: ${p.key}`
  if (e.type === 'note' && p.kind === 'input') return `input: ${p.source}`
  if (e.type === 'model_call') return 'model call'
  if (e.type === 'approval_decision') return `human: ${p.decision}`
  if (e.type === 'error') return 'error'
  return null
}

/** LCS diff of the two runs' meaningful steps. */
function diffSteps(a, b) {
  const n = a.length, m = b.length, dp = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const out = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push(['same', a[i], b[j]]); i++; j++ } else if (dp[i + 1][j] >= dp[i][j + 1]) out.push(['del', a[i++], '']); else out.push(['add', '', b[j++]])
  }
  while (i < n) out.push(['del', a[i++], ''])
  while (j < m) out.push(['add', '', b[j++]])
  return out
}

function Compare({ prev, cur }) {
  const a = prev.events.map(signature).filter(Boolean)
  const b = cur.map(signature).filter(Boolean)
  const rows = diffSteps(a, b)
  const toolsA = new Set(a.filter((s) => s.includes('→')).map((s) => s.split(' → ')[0]))
  const toolsB = new Set(b.filter((s) => s.includes('→')).map((s) => s.split(' → ')[0]))
  const added = [...toolsB].filter((t) => !toolsA.has(t)), removed = [...toolsA].filter((t) => !toolsB.has(t))
  const changes = rows.filter((r) => r[0] !== 'same').length
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-head"><h3>Compared with "{prev.run.name}" · {fmtTime(prev.run.started)}</h3>
        <a href={`#/runs/${prev.run.id}`} style={{ fontSize: 12.5 }}>Open that run</a></div>
      <div className="row" style={{ marginBottom: 12 }}>
        <Badge tone={changes ? 'warn' : 'ok'} icon={changes ? 'compare' : 'check'}>{changes ? `${changes} step(s) differ` : 'Same steps'}</Badge>
        {added.map((t) => <Badge key={t} tone="warn" icon="tool">New tool: {friendly(t)}</Badge>)}
        {removed.map((t) => <Badge key={t} tone="mute" icon="tool">Not used now: {friendly(t)}</Badge>)}
      </div>
      <div className="diff-row muted" style={{ fontSize: 12 }}><span /><span>Previous run</span><span>This run</span></div>
      <div className="grid" style={{ gap: 4 }}>
        {rows.map(([kind, x, y], i) => (
          <div key={i} className={`diff-row ${kind === 'add' ? 'add' : kind === 'del' ? 'del' : ''}`}>
            <span aria-label={kind}>{kind === 'add' ? '+' : kind === 'del' ? '−' : '='}</span>
            <span className={kind === 'add' ? 'muted' : ''}>{x || '—'}</span><span className={kind === 'del' ? 'muted' : ''}>{y || '—'}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Timeline({ runId, focus, compareInit }) {
  const { data, error } = useApi(`/v1/runs/${runId}/timeline`, { every: 6000 })
  const [cur, setCur] = useState(0)
  const [open, setOpen] = useState({})
  const [compare, setCompare] = useState(Boolean(compareInit))
  const [playing, setPlaying] = useState(false)
  const refs = useRef({})
  const events = data?.events || []
  const flagged = useMemo(() => new Map((data?.flags || []).map((f) => [f.event_id, f])), [data])
  const cards = useMemo(() => events.map((e) => ({ e, d: describe(e) })), [events])
  const focused = useRef(false)
  useEffect(() => {
    if (focus && events.length && !focused.current) {
      const i = events.findIndex((e) => e.id === focus)
      if (i >= 0) { setCur(i); focused.current = true; setTimeout(() => refs.current[focus]?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 250) }
    }
  }, [focus, events])
  useEffect(() => {
    if (!playing) return
    if (cur >= events.length - 1) { setPlaying(false); return }
    const t = setTimeout(() => setCur((c) => c + 1), 650)
    return () => clearTimeout(t)
  }, [playing, cur, events.length])
  const go = (i) => {
    const k = Math.max(0, Math.min(events.length - 1, i))
    setCur(k)
    refs.current[events[k]?.id]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
  useEffect(() => { if (playing) go(cur) }, [cur]) // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <ErrorBox error={error} />
  if (!data) return <div className="card"><span className="muted">Loading run…</span></div>
  const { run, agent } = data
  const dur = run.ended ? Math.max(0, (new Date(run.ended) - new Date(run.started)) / 1000).toFixed(1) + ' s' : 'still running'
  const statusTone = { completed: 'ok', blocked: 'bad', failed: 'bad', running: 'info' }[run.status] || 'mute'
  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="spread" style={{ alignItems: 'flex-start' }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 650 }}>{run.name}</div>
            <div className="muted" style={{ fontSize: 12.5 }}>{agent.name} · {fmtTime(run.started)} · {dur} · {events.length} events</div>
            <div className="row" style={{ marginTop: 6, fontSize: 12.5 }}>
              {data.parent && <a href={`#/runs/${data.parent.id}`}><Icon name="left" size={12} /> parent: {data.parent.name}</a>}
              {data.children.map((c) => <a key={c.id} href={`#/runs/${c.id}`}>sub-agent: {c.name} <Icon name="right" size={12} /></a>)}
            </div>
          </div>
          <div className="row">
            <Badge tone={statusTone} icon={statusTone === 'ok' ? 'check' : statusTone === 'bad' ? 'x' : 'clock'}>{run.status}</Badge>
            {data.flags.length > 0 && <Badge tone="bad" icon="flag">{data.flags.length} flag{data.flags.length > 1 ? 's' : ''}</Badge>}
            {data.previous && <button className={`btn btn-sm ${compare ? 'btn-primary' : ''}`} onClick={() => setCompare(!compare)}><Icon name="compare" size={14} />Compare with previous run</button>}
          </div>
        </div>
        {events.length > 0 && (
          <>
            <div className="scrub" aria-hidden="false">
              <div className="scrub-track" />
              <div className="scrub-fill" style={{ width: `${events.length > 1 ? (cur / (events.length - 1)) * 100 : 100}%` }} />
              {cards.map(({ e, d }, i) => (
                <button key={e.id} className={`tick ${flagged.get(e.id)?.severity === 'high' ? 'bad' : d.tick || ''} ${i === cur ? 'cur' : ''}`} tabIndex={-1}
                  style={{ left: `${events.length > 1 ? (i / (events.length - 1)) * 100 : 50}%` }} title={`#${e.seq} ${d.title}`} onClick={() => go(i)} />
              ))}
              <input type="range" min={0} max={Math.max(0, events.length - 1)} value={cur} onChange={(ev) => go(Number(ev.target.value))}
                aria-label="Scrub through the run's events" />
            </div>
            <div className="spread" style={{ fontSize: 12.5 }}>
              <div className="row">
                <button className="btn btn-sm" onClick={() => go(cur - 1)} aria-label="Previous event"><Icon name="left" size={14} /></button>
                <button className="btn btn-sm" onClick={() => { if (cur >= events.length - 1) setCur(0); setPlaying(!playing) }}>
                  <Icon name={playing ? 'pause' : 'play'} size={14} />{playing ? 'Pause' : 'Replay'}</button>
                <button className="btn btn-sm" onClick={() => go(cur + 1)} aria-label="Next event"><Icon name="right" size={14} /></button>
              </div>
              <span className="muted">Event {cur + 1} of {events.length} · {fmtClock(events[cur]?.ts)}</span>
            </div>
          </>
        )}
      </div>
      {compare && data.previous && <Compare prev={data.previous} cur={events} />}
      <div className="events">
        {cards.map(({ e, d }, i) => {
          const flag = flagged.get(e.id)
          const tone = flag?.severity === 'high' ? 'bad' : flag?.severity === 'medium' ? 'warn' : d.tone
          return (
            <motion.div key={e.id} ref={(el) => { refs.current[e.id] = el }} onClick={() => setCur(i)}
              className={`ev ${i === cur ? 'cur' : ''} ${tone ? `hl-${tone}` : ''}`}
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 20) * 0.02 }}>
              <div className="ev-ic"><Icon name={d.icon} size={15} /></div>
              <div className="spread" style={{ alignItems: 'flex-start' }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="ev-title">{d.title}</div>
                  <div className="ev-meta"><span>#{e.seq}</span><span>{fmtClock(e.ts)}</span><span className="mono">{e.type}</span><span className="mono" title={e.hash}>hash {e.hash.slice(0, 10)}…</span></div>
                </div>
                <div className="row">
                  {d.decision && <DecisionBadge value={d.decision} />}
                  {d.severity && d.severity !== 'none' && <SeverityBadge value={d.severity} />}
                  {d.change && <Badge tone="warn" icon="alert">Change</Badge>}
                  <button className="btn btn-ghost btn-sm" title="Copy a link to this event" aria-label="Copy link to this event"
                    onClick={(ev) => { ev.stopPropagation(); copy(`${window.location.origin}/#/runs/${runId}?event=${e.id}`, 'Link to event copied') }}>
                    <Icon name="link" size={14} /></button>
                </div>
              </div>
              {d.body && <div className="ev-body">{String(d.body).slice(0, 400)}</div>}
              {flag && <div className="reason" style={{ color: { high: '#fca5a5', medium: '#fcd34d' }[flag.severity] || 'var(--accent)', marginTop: 6 }}><Icon name="flag" size={14} />{flag.message}</div>}
              <button className="btn btn-ghost btn-sm" style={{ marginTop: 6, paddingLeft: 0 }} onClick={(ev) => { ev.stopPropagation(); setOpen({ ...open, [e.id]: !open[e.id] }) }}>
                <Icon name={open[e.id] ? 'left' : 'right'} size={12} />{open[e.id] ? 'Hide' : 'Show'} recorded data</button>
              {open[e.id] && <pre className="pre">{JSON.stringify(e.payload, null, 2)}</pre>}
            </motion.div>
          )
        })}
      </div>
    </div>
  )
}

export default function Runs({ runId, query }) {
  const agentFilter = query.get('agent') || ''
  const agents = useApi('/v1/agents')
  const runs = useApi(`/v1/runs?limit=200${agentFilter ? `&agent_id=${agentFilter}` : ''}`, { every: 8000 })
  const names = Object.fromEntries((agents.data || []).map((a) => [a.id, a.name]))
  const list = runs.data || []
  const selected = runId || null
  return (
    <Page title="Flight recorder" sub="Every run, step by step: model calls, tool calls, decisions, approvals and memory writes.">
      <ErrorBox error={runs.error} />
      <div className="runs-layout">
        <div className="card" style={{ padding: 10 }}>
          <select className="select" value={agentFilter} aria-label="Filter by agent" style={{ marginBottom: 8 }}
            onChange={(e) => { window.location.hash = `#/runs${selected ? '/' + selected : ''}${e.target.value ? '?agent=' + e.target.value : ''}` }}>
            <option value="">All agents</option>
            {(agents.data || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          {list.length ? list.map((r) => (
            <a key={r.id} href={`#/runs/${r.id}${agentFilter ? '?agent=' + agentFilter : ''}`} className={`run-item ${r.id === selected ? 'active' : ''}`}>
              <div className="spread"><b style={{ fontWeight: 600, fontSize: 13 }}>{r.name}</b>
                <Icon name={r.status === 'completed' ? 'check' : r.status === 'running' ? 'clock' : 'x'} size={14}
                  style={{ color: r.status === 'completed' ? 'var(--ok)' : r.status === 'running' ? 'var(--accent)' : 'var(--bad)' }} aria-label={r.status} /></div>
              <div className="muted" style={{ fontSize: 12 }}>{names[r.agent_id] || r.agent_id} · {timeAgo(r.started)}{r.parent_run_id ? ' · sub-agent' : ''}</div>
            </a>
          )) : <Empty icon="runs" title="No runs yet">Runs appear when an agent calls POST /v1/runs or uses bb.run().</Empty>}
        </div>
        <div>
          {selected ? <Timeline key={selected + (query.get("compare") || "")} runId={selected} focus={query.get("event")} compareInit={query.get("compare") === "1"} />
            : <div className="card"><Empty icon="runs" title="Pick a run">Choose a run on the left to replay it event by event.</Empty></div>}
        </div>
      </div>
    </Page>
  )
}
