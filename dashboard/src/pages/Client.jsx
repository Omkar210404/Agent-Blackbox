import { useState } from 'react'
import { motion } from 'framer-motion'
import { api, useApi } from '../api.js'
import { Badge, DemoBanner, Empty, ErrorBox, friendly, Icon, Modal } from '../ui.jsx'

const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

export default function Client({ token }) {
  const [day, setDay] = useState(() => iso(new Date()))
  const offset = -new Date().getTimezoneOffset()
  const { data, error, reload } = useApi(`/v1/client/${token}?day=${day}&tz_offset_min=${offset}`, { anon: true, every: 30000 })
  const [confirm, setConfirm] = useState(false)
  const [sent, setSent] = useState(null)
  const shift = (n) => { const d = new Date(day + 'T12:00:00'); d.setDate(d.getDate() + n); setDay(iso(d)) }
  const isToday = day === iso(new Date())
  const requestPause = async () => {
    try { const r = await api(`/v1/client/${token}/pause`, { method: 'POST', anon: true }); setSent(r.status); reload() }
    catch (e) { setSent('error: ' + e.message) }
    setConfirm(false)
  }
  if (error) return <div className="client"><ErrorBox error={error.includes('not found') ? 'This link is not valid any more. Ask the person who manages your AI agent for a new one.' : error} /></div>
  if (!data) return <div className="client muted">Loading…</div>
  const tools = Object.entries(data.by_tool)
  return (
    <motion.div className="client" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
      <DemoBanner />
      <div className="row muted" style={{ fontSize: 12.5 }}><Icon name="lock" size={13} />Private page · read-only · recorded by Blackbox</div>
      <h1>What your AI did {isToday ? 'today' : 'on this day'}</h1>
      <div className="ink2" style={{ fontSize: 15 }}>{data.agent.name}{data.agent.owner ? ` · ${data.agent.owner}` : ''}</div>
      <div className="spread" style={{ marginTop: 14 }}>
        <div className="row">
          <button className="btn btn-sm" onClick={() => shift(-1)} aria-label="Previous day"><Icon name="left" size={14} /></button>
          <b>{data.day}</b>
          <button className="btn btn-sm" onClick={() => shift(1)} disabled={isToday} aria-label="Next day"><Icon name="right" size={14} /></button>
        </div>
        {data.agent.paused ? <Badge tone="mute" icon="pause">Paused: it cannot act</Badge> : <Badge tone="ok" icon="check">Working</Badge>}
      </div>

      <div className="client-stats">
        <div className="card stat"><div className="stat-label"><Icon name="check" size={13} />Things it did</div><div className="stat-value">{data.actions}</div></div>
        <div className="card stat"><div className="stat-label"><Icon name="approvals" size={13} />Asked permission</div><div className="stat-value" style={{ color: data.asked.length ? 'var(--warn)' : undefined }}>{data.asked.length}</div></div>
        <div className="card stat"><div className="stat-label"><Icon name="x" size={13} />Stopped</div><div className="stat-value" style={{ color: data.blocked.length ? 'var(--bad)' : undefined }}>{data.blocked.length}</div></div>
      </div>

      {!data.checked && !data.actions ? (
        <div className="card"><Empty icon="clock" title={`Your AI did nothing ${isToday ? 'yet today' : 'on this day'}`}>When it works, a summary appears here.</Empty></div>
      ) : (
        <>
          <section className="card">
            <h2>What it did</h2>
            {tools.length ? <ul>{tools.map(([t, n]) => <li key={t}><Icon name="check" size={14} style={{ color: 'var(--ok)', verticalAlign: -2 }} /> {friendly(t)}: {plural(n, 'time', 'times')}</li>)}</ul>
              : <p className="muted">It did not complete any actions.</p>}
            {data.memory_writes > 0 && <p className="muted" style={{ marginBottom: 0 }}>It also saved {plural(data.memory_writes, 'note', 'notes')} to its memory.</p>}
          </section>
          <section className="card">
            <h2>When it asked for permission</h2>
            {data.asked.length ? <ul>{data.asked.map((a, i) => (
              <li key={i}><div style={{ color: 'var(--ink)' }}>{a.what}</div><div className="row" style={{ marginTop: 3 }}>
                <Badge tone={a.status === 'approved' ? 'ok' : a.status === 'pending' ? 'warn' : 'bad'} icon={a.status === 'approved' ? 'check' : a.status === 'pending' ? 'clock' : 'x'}>{a.outcome}</Badge>
                {a.by && <span className="muted" style={{ fontSize: 12 }}>by {a.by}</span>}</div></li>
            ))}</ul> : <p className="muted">It did not need to ask for anything.</p>}
          </section>
          <section className="card">
            <h2>What was stopped, and why</h2>
            {data.blocked.length ? <ul>{data.blocked.map((b, i) => (
              <li key={i}><div style={{ color: 'var(--ink)' }}><Icon name="x" size={14} style={{ color: 'var(--bad)', verticalAlign: -2 }} /> {b.what}</div>
                <div className="muted" style={{ fontSize: 13 }}>{b.why}</div></li>
            ))}</ul> : <p className="muted">Nothing was stopped.</p>}
          </section>
          {data.warnings.length > 0 && (
            <section className="card" style={{ borderColor: 'rgba(248,113,113,0.35)' }}>
              <h2><Icon name="alert" size={15} style={{ color: 'var(--bad)', verticalAlign: -2 }} /> Things worth a look</h2>
              <ul>{data.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </section>
          )}
        </>
      )}

      <section className="card" style={{ marginTop: 22 }}>
        <h2>Want it to stop?</h2>
        <p className="ink2" style={{ marginTop: 0 }}>This sends a request to the person who manages your AI. Once they accept, the agent cannot do anything until it is turned back on.</p>
        {sent === 'requested' || sent === 'already requested' || data.pause_pending ? (
          <Badge tone="info" icon="check">Pause requested. You will see "Paused" above once it is done.</Badge>
        ) : (
          <button className="btn btn-big btn-bad" style={{ width: '100%' }} disabled={data.agent.paused} onClick={() => setConfirm(true)}>
            <Icon name="pause" size={18} stroke={2.4} />{data.agent.paused ? 'Already paused' : 'Pause this agent'}
          </button>
        )}
        {sent?.startsWith('error') && <ErrorBox error={sent} />}
      </section>
      <Modal open={confirm} onClose={() => setConfirm(false)}>
        <h3>Pause {data.agent.name}?</h3>
        <p className="ink2">We will ask the person who manages it to pause it now.</p>
        <div className="appr-actions">
          <button className="btn btn-big" onClick={() => setConfirm(false)}>Cancel</button>
          <button className="btn btn-big btn-bad" onClick={requestPause}><Icon name="pause" size={18} />Yes, pause it</button>
        </div>
      </Modal>
    </motion.div>
  )
}
