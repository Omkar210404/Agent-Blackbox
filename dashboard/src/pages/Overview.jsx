import { useState } from 'react'
import { motion } from 'framer-motion'
import { api, useApi } from '../api.js'
import { Badge, BarChart, copy, Empty, ErrorBox, HealthBadge, Icon, Modal, Page, SeverityBadge, Sparkline, timeAgo, toast } from '../ui.jsx'

const today = () => new Date().toISOString().slice(0, 10)

function ReportModal({ agent, onClose }) {
  const [start, setStart] = useState(today())
  const [end, setEnd] = useState(today())
  const [error, setError] = useState(null)
  const get = async (format) => {
    setError(null)
    try {
      const text = await api(`/v1/reports/${agent.id}?start=${start}&end=${end}&format=${format}`, { text: true })
      const blob = new Blob([text], { type: format === 'html' ? 'text/html' : 'text/markdown' })
      const url = URL.createObjectURL(blob)
      if (format === 'html') window.open(url, '_blank', 'noopener')
      else Object.assign(document.createElement('a'), { href: url, download: `audit-${agent.id}-${start}_${end}.md` }).click()
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e) { setError(e.message) }
  }
  return (
    <Modal open={Boolean(agent)} onClose={onClose}>
      <h3>Audit report</h3>
      <p className="muted" style={{ marginTop: 0 }}>{agent?.name}: hash-chain check, decisions, approvals and flags (dates in UTC).</p>
      <div className="row" style={{ gap: 12, marginBottom: 14 }}>
        <div style={{ flex: 1 }}><label className="label">From</label><input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></div>
        <div style={{ flex: 1 }}><label className="label">To</label><input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></div>
      </div>
      <ErrorBox error={error} />
      <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
        <button className="btn" onClick={() => get('md')}><Icon name="report" />Markdown</button>
        <button className="btn btn-primary" onClick={() => get('html')}><Icon name="external" />Printable page (PDF)</button>
      </div>
    </Modal>
  )
}

function ClientLinkModal({ agent, onClose }) {
  const [link, setLink] = useState(null)
  const [error, setError] = useState(null)
  const make = async () => {
    try { const r = await api(`/v1/agents/${agent.id}/client-link`, { method: 'POST' }); setLink(window.location.origin + r.path) }
    catch (e) { setError(e.message) }
  }
  const close = () => { setLink(null); setError(null); onClose() }
  return (
    <Modal open={Boolean(agent)} onClose={close}>
      <h3>Client page for {agent?.name}</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        A read-only "What your AI did today" page for the business owner, opened through a long secret link.
        {agent?.has_client_link && ' Making a new link turns the old one off.'}
      </p>
      {link ? (
        <>
          <div className="pre" style={{ userSelect: 'all' }}>{link}</div>
          <p className="muted" style={{ fontSize: 12.5 }}>Shown once. Only its hash is stored, so copy it now.</p>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button className="btn" onClick={() => window.open(link, '_blank', 'noopener')}><Icon name="external" />Open</button>
            <button className="btn btn-primary" onClick={() => copy(link, 'Client link copied')}><Icon name="copy" />Copy link</button>
          </div>
        </>
      ) : (
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <ErrorBox error={error} />
          <button className="btn" onClick={close}>Cancel</button>
          <button className="btn btn-primary" onClick={make}><Icon name="link" />Create link</button>
        </div>
      )}
    </Modal>
  )
}

function AgentCard({ a, i, onReport, onLink, onChanged }) {
  const togglePause = async () => {
    await api(`/v1/agents/${a.id}/pause`, { method: 'POST', body: { paused: !a.paused, by: 'admin (dashboard)' } })
    toast(a.paused ? 'Agent resumed' : 'Agent paused: every tool call will be blocked')
    onChanged()
  }
  return (
    <motion.div className="card agent-card" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 * i }}>
      <div className="spread" style={{ alignItems: 'flex-start' }}>
        <div style={{ minWidth: 0 }}>
          <div className="agent-name">{a.name}</div>
          <div className="muted" style={{ fontSize: 12.5 }}>{a.owner || 'No owner label'}</div>
        </div>
        <HealthBadge value={a.health} />
      </div>
      <Sparkline values={a.activity} />
      <div className="kv">
        <div><b>{a.event_count}</b><span>events</span></div>
        <div><b style={{ color: a.pending_approvals ? 'var(--warn)' : undefined }}>{a.pending_approvals}</b><span>waiting</span></div>
        <div><b style={{ color: a.top_flag === 'high' ? 'var(--bad)' : undefined }}>{a.open_flags}</b><span>flags (24 h)</span></div>
      </div>
      <div className="spread muted" style={{ fontSize: 12.5 }}>
        <span className="row"><Icon name="clock" size={13} />Last activity {timeAgo(a.last_activity)}</span>
        <span className="row"><Icon name={a.chain_ok ? 'lock' : 'alert'} size={13} />{a.chain_ok ? 'Log verified' : 'Log broken'}</span>
      </div>
      <div className="row">
        <a className="btn btn-sm" href={`#/runs?agent=${a.id}`}><Icon name="runs" size={14} />Runs</a>
        <a className="btn btn-sm" href={`#/policies/${a.id}`}><Icon name="policy" size={14} />Policy</a>
        <button className="btn btn-sm" onClick={() => onReport(a)}><Icon name="report" size={14} />Report</button>
        <button className="btn btn-sm" onClick={() => onLink(a)}><Icon name="link" size={14} />Client page</button>
        <button className={`btn btn-sm ${a.paused ? 'btn-ok' : ''}`} onClick={togglePause}>
          <Icon name={a.paused ? 'play' : 'pause'} size={14} />{a.paused ? 'Resume' : 'Pause'}
        </button>
      </div>
    </motion.div>
  )
}

export default function Overview() {
  const { data, error, reload } = useApi('/v1/overview', { every: 8000 })
  const [reportFor, setReportFor] = useState(null)
  const [linkFor, setLinkFor] = useState(null)
  const t = data?.totals
  const stats = t && [
    ['Agents', t.agents, 'cpu'], ['Events (24 h)', t.events_24h, 'runs'],
    ['Waiting for you', t.pending, 'approvals', t.pending ? 'var(--warn)' : null],
    ['Blocked (24 h)', t.denied_24h, 'x', t.denied_24h ? 'var(--bad)' : null],
    ['Flags (24 h)', t.flags_24h, 'flag'],
  ]
  return (
    <Page title="Overview" sub="Every agent you watch, what it did in the last 24 hours, and what needs you."
      actions={t && <Badge tone={t.chains_ok ? 'ok' : 'bad'} icon={t.chains_ok ? 'lock' : 'alert'}>
        {t.chains_ok ? 'All logs verified' : 'A log failed verification'}</Badge>}>
      <ErrorBox error={error} />
      {stats && (
        <div className="stats">
          {stats.map(([label, value, icon, color], i) => (
            <motion.div key={label} className="card stat" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}>
              <div className="stat-label"><Icon name={icon} size={13} />{label}</div>
              <div className="stat-value" style={{ color: color || undefined }}>{value}</div>
            </motion.div>
          ))}
        </div>
      )}
      {data && (
        <div className="two-col" style={{ marginBottom: 16 }}>
          <div className="card">
            <div className="card-head"><h2>Activity, last 24 hours</h2><span className="muted" style={{ fontSize: 12 }}>events per hour, all agents</span></div>
            <BarChart values={data.activity} labels={data.hours} />
          </div>
          <div className="card">
            <div className="card-head"><h2>Latest flags</h2><a href="#/runs" style={{ fontSize: 12.5 }}>Flight recorder</a></div>
            {data.recent_flags.length ? (
              <div className="list">
                {data.recent_flags.slice(0, 5).map((f) => (
                  <a key={f.id} className="list-item" href={f.run_id ? `#/runs/${f.run_id}?event=${f.event_id}` : `#/runs?agent=${f.agent_id}`}
                    style={{ color: 'inherit', textDecoration: 'none' }}>
                    <SeverityBadge value={f.severity} />
                    <div className="grow"><div style={{ fontSize: 13 }}>{f.message}</div><div className="muted" style={{ fontSize: 11.5 }}>{f.agent_name} · {timeAgo(f.ts)}</div></div>
                  </a>
                ))}
              </div>
            ) : <Empty icon="shield" title="No flags in the last 24 hours">Nothing unusual was seen.</Empty>}
          </div>
        </div>
      )}
      {data && (data.agents.length ? (
        <div className="agents">
          {data.agents.map((a, i) => <AgentCard key={a.id} a={a} i={i} onReport={setReportFor} onLink={setLinkFor} onChanged={reload} />)}
        </div>
      ) : (
        <div className="card"><Empty icon="cpu" title="No agents yet">Run <span className="mono">blackbox demo</span> to add three scripted agents, or connect your own with the SDK.</Empty></div>
      ))}
      <ReportModal agent={reportFor} onClose={() => setReportFor(null)} />
      <ClientLinkModal agent={linkFor} onClose={() => { setLinkFor(null); reload() }} />
    </Page>
  )
}
