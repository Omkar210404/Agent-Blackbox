import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { api, useApi } from '../api.js'
import { Badge, DecisionBadge, Empty, ErrorBox, fmtTime, Icon, Page, timeAgo, toast } from '../ui.jsx'

const NAME_KEY = 'bb_decider_name'
const readName = () => { try { return localStorage.getItem(NAME_KEY) || '' } catch { return '' } }

function Countdown({ until }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  const s = Math.max(0, Math.round((new Date(until) - now) / 1000))
  const text = s > 3600 ? `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  return <Badge tone={s < 60 ? 'bad' : 'warn'} icon="clock">{s ? `${text} left, then blocked` : 'Time is up'}</Badge>
}

function PendingCard({ a, name, onDone }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [showArgs, setShowArgs] = useState(false)
  const decide = async (approve) => {
    setBusy(true); setError(null)
    try {
      await api(`/v1/approvals/${a.id}/decision`, { method: 'POST', body: { approve, by: name.trim() || 'admin (dashboard)' } })
      toast(approve ? 'Approved: the agent will go ahead' : 'Blocked: the agent will not do this')
      onDone()
    } catch (e) { setError(e.message); setBusy(false) }
  }
  const pause = a.kind === 'pause_request'
  return (
    <motion.div layout className="card" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, x: 40, transition: { duration: 0.25 } }}>
      <div className="spread">
        <Badge tone={pause ? 'info' : 'warn'} icon={pause ? 'pause' : 'approvals'}>{pause ? 'Pause request from the owner' : a.agent_name}</Badge>
        <Countdown until={a.expires} />
      </div>
      <div className="appr-summary">{a.summary}</div>
      <div className="label">Why you are asked</div>
      {a.reasons.map((r, i) => (
        <div key={i} className="reason">
          <Icon name={r.level === 'deny' ? 'x' : 'alert'} size={15} style={{ color: r.level === 'deny' ? 'var(--bad)' : 'var(--warn)' }} />
          <span>{r.text}</span>
        </div>
      ))}
      {!pause && Object.keys(a.args || {}).length > 0 && (
        <>
          <button className="btn btn-ghost btn-sm" style={{ paddingLeft: 0, marginTop: 6 }} onClick={() => setShowArgs(!showArgs)}>
            <Icon name={showArgs ? 'left' : 'right'} size={12} />{showArgs ? 'Hide' : 'Show'} exact details</button>
          {showArgs && <pre className="pre">{JSON.stringify(a.args, null, 2)}</pre>}
        </>
      )}
      <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>Asked {timeAgo(a.created)} · {a.run_id ? <a href={`#/runs/${a.run_id}`}>open the run</a> : 'no run'}</div>
      <ErrorBox error={error} />
      <div className="appr-actions">
        <motion.button whileTap={{ scale: 0.97 }} className="btn btn-big btn-bad" disabled={busy} onClick={() => decide(false)}>
          <Icon name="x" size={18} stroke={2.4} />Block</motion.button>
        <motion.button whileTap={{ scale: 0.97 }} className="btn btn-big btn-ok" disabled={busy} onClick={() => decide(true)}>
          <Icon name="check" size={18} stroke={2.4} />{pause ? 'Pause agent' : 'Approve'}</motion.button>
      </div>
    </motion.div>
  )
}

export default function Approvals({ onChange }) {
  const [tab, setTab] = useState('pending')
  const [name, setName] = useState(readName)
  const pending = useApi('/v1/approvals?status=pending', { every: 3000 })
  const all = useApi('/v1/approvals?limit=200', { every: tab === 'history' ? 5000 : 0 })
  const decided = (all.data || []).filter((a) => a.status !== 'pending')
  const saveName = (v) => { setName(v); try { localStorage.setItem(NAME_KEY, v) } catch { /* fine */ } }
  const done = () => { pending.reload(); all.reload(); onChange?.() }
  return (
    <Page title="Approvals" sub="When a policy says a human must decide, the agent waits here (and on Telegram, if set up). No answer in time means blocked.">
      <div className="spread" style={{ marginBottom: 16 }}>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'pending'} className={`tab ${tab === 'pending' ? 'active' : ''}`} onClick={() => setTab('pending')}>
            Waiting {pending.data ? `(${pending.data.length})` : ''}</button>
          <button role="tab" aria-selected={tab === 'history'} className={`tab ${tab === 'history' ? 'active' : ''}`} onClick={() => setTab('history')}>History</button>
        </div>
        <div className="row" style={{ maxWidth: 320, flex: '1 1 220px' }}>
          <label className="label" htmlFor="decider" style={{ margin: 0, whiteSpace: 'nowrap' }}>Decide as</label>
          <input id="decider" className="input" value={name} placeholder="Your name (recorded)" onChange={(e) => saveName(e.target.value)} style={{ flex: 1 }} />
        </div>
      </div>
      <ErrorBox error={pending.error || all.error} />
      {tab === 'pending' ? (
        <div className="appr">
          <AnimatePresence mode="popLayout">
            {(pending.data || []).map((a) => <PendingCard key={a.id} a={a} name={name} onDone={done} />)}
          </AnimatePresence>
          {pending.data && !pending.data.length && (
            <div className="card"><Empty icon="approvals" title="Nothing is waiting for you">Agents are working within their policies.</Empty></div>
          )}
        </div>
      ) : (
        <div className="card">
          {decided.length ? (
            <div className="list">
              {decided.map((a) => (
                <div key={a.id} className="list-item">
                  <DecisionBadge value={a.status} />
                  <div className="grow">
                    <div style={{ fontSize: 13.5 }}>{a.summary}</div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {a.agent_name} · decided by <b className="ink2">{a.decided_by}</b> · {fmtTime(a.decided_at)}{a.note ? ` · "${a.note}"` : ''}
                    </div>
                  </div>
                  {a.run_id && <a className="btn btn-sm" href={`#/runs/${a.run_id}`}>Run</a>}
                </div>
              ))}
            </div>
          ) : <Empty icon="clock" title="No decisions yet" />}
        </div>
      )}
    </Page>
  )
}
