import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AnimatePresence, motion } from 'framer-motion'
import './styles.css'
import { api, auth, setToken, useApi } from './api.js'
import { Icon, Toaster } from './ui.jsx'
import Overview from './pages/Overview.jsx'
import AgentMap from './pages/AgentMap.jsx'
import Runs from './pages/Runs.jsx'
import Approvals from './pages/Approvals.jsx'
import Policies from './pages/Policies.jsx'
import Client from './pages/Client.jsx'

// `blackbox serve` opens /#token=...: keep it in this browser and drop it from the address bar right away.
function takeTokenFromHash() {
  const m = window.location.hash.match(/token=([^&]+)/)
  if (!m) return false
  setToken(decodeURIComponent(m[1]))
  history.replaceState(null, '', window.location.pathname + '#/overview')
  window.dispatchEvent(new HashChangeEvent('hashchange'))
  return true
}
takeTokenFromHash()

function useRoute() {
  const read = () => {
    const raw = window.location.hash.replace(/^#/, '') || '/overview'
    const [path, query = ''] = raw.split('?')
    return { parts: path.split('/').filter(Boolean), query: new URLSearchParams(query) }
  }
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const on = () => setRoute(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

const NAV = [
  ['overview', 'Overview', 'overview'], ['map', 'Agent map', 'map'], ['runs', 'Flight recorder', 'runs'],
  ['approvals', 'Approvals', 'approvals'], ['policies', 'Policies', 'policy'],
]

function Login({ onDone }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState(null)
  const submit = async (e) => {
    e.preventDefault()
    setToken(value.trim())
    try { await api('/v1/agents'); onDone() } catch (err) { setError('That token was not accepted. ' + err.message) }
  }
  return (
    <div className="login">
      <motion.form className="card" onSubmit={submit} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
        <div className="brand" style={{ padding: '0 0 18px' }}>
          <div className="brand-mark"><Icon name="runs" /></div>
          <div>Blackbox<small>FLIGHT RECORDER FOR AI AGENTS</small></div>
        </div>
        <label className="label" htmlFor="tok">Admin token</label>
        <input id="tok" className="input mono" type="password" autoComplete="off" value={value}
          onChange={(e) => setValue(e.target.value)} placeholder="bba_..." />
        <p className="muted" style={{ fontSize: 12.5 }}>
          Run <span className="mono">blackbox serve</span> to open this page signed in, or paste
          <span className="mono"> BLACKBOX_ADMIN_TOKEN</span> from your <span className="mono">.env</span> file.
        </p>
        {error && <div className="err" style={{ marginBottom: 12 }}>{error}</div>}
        <button className="btn btn-primary btn-big" style={{ width: '100%' }} disabled={!value.trim()}>
          <Icon name="lock" />Sign in
        </button>
      </motion.form>
    </div>
  )
}

function Shell() {
  const route = useRoute()
  const page = route.parts[0] || 'overview'
  const pending = useApi('/v1/approvals?status=pending&limit=100', { every: 5000 })
  const health = useApi('/health', { every: 15000, anon: true })
  const count = pending.data?.length || 0
  const view = {
    overview: <Overview />, map: <AgentMap />, runs: <Runs runId={route.parts[1]} query={route.query} />,
    approvals: <Approvals onChange={pending.reload} />, policies: <Policies agentId={route.parts[1]} />,
  }[page] || <Overview />
  return (
    <div className="shell">
      <nav className="side" aria-label="Main">
        <div className="brand">
          <div className="brand-mark"><Icon name="runs" /></div>
          <div>Blackbox<small>MISSION CONTROL</small></div>
        </div>
        {NAV.map(([id, label, icon]) => (
          <a key={id} href={`#/${id}`} className={`nav-link ${page === id ? 'active' : ''}`} aria-current={page === id ? 'page' : undefined}>
            <Icon name={icon} size={18} /><span>{label}</span>
            {id === 'approvals' && count > 0 && <span className="nav-count" aria-label={`${count} waiting`}>{count}</span>}
          </a>
        ))}
        <div className="side-foot">
          <span className="row">
            <Icon name={health.data ? 'check' : 'alert'} size={14} style={{ color: health.data ? 'var(--ok)' : 'var(--warn)' }} />
            {health.data ? `Recorder online · v${health.data.version}` : 'Recorder unreachable'}
          </span>
          <button className="btn btn-ghost btn-sm" style={{ justifyContent: 'flex-start' }}
            onClick={() => { auth.clear(); window.dispatchEvent(new Event('bb-logout')) }}>
            <Icon name="logout" size={14} />Sign out
          </button>
        </div>
      </nav>
      <main className="main">
        <AnimatePresence mode="wait"><div key={page}>{view}</div></AnimatePresence>
      </main>
    </div>
  )
}

function App() {
  const clientToken = window.location.pathname.match(/^\/client\/([A-Za-z0-9_-]{20,})/)?.[1]
  const [signedIn, setSignedIn] = useState(Boolean(auth.get()))
  useEffect(() => {
    const out = () => setSignedIn(false)
    const onHash = () => { if (takeTokenFromHash()) setSignedIn(true) }
    window.addEventListener('bb-logout', out)
    window.addEventListener('hashchange', onHash)
    return () => { window.removeEventListener('bb-logout', out); window.removeEventListener('hashchange', onHash) }
  }, [])
  if (clientToken) return <><Client token={clientToken} /><Toaster /></>
  return <>{signedIn ? <Shell /> : <Login onDone={() => setSignedIn(true)} />}<Toaster /></>
}

createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>)
