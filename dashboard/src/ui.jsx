import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { DEMO } from './api.js'

const PATHS = {
  overview: 'M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z',
  map: 'M6 6m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M18 6m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M12 18m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M8 7.5l3 8M16 7.5l-3 8M8.5 6h7',
  runs: 'M3 12h4l3-8 4 16 3-8h4',
  approvals: 'M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6zM8.5 12l2.5 2.5 4.5-5',
  policy: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M10 13l-2 2 2 2M14 13l2 2-2 2',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  x: 'M6 6l12 12M18 6L6 18',
  alert: 'M12 4l9 16H3zM12 10v4M12 17.2v.3',
  pause: 'M8 5v14M16 5v14',
  play: 'M7 5l12 7-12 7z',
  copy: 'M9 9h10v11H9zM5 15V4h10',
  link: 'M10 14a4 4 0 0 0 6 .4l3-3a4 4 0 0 0-5.7-5.7l-1.2 1.2M14 10a4 4 0 0 0-6-.4l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2',
  clock: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 7v5l3 2',
  user: 'M12 8m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0M4 21c1-4 4.5-6 8-6s7 2 8 6',
  tool: 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z',
  mail: 'M3 6h18v12H3zM3 7l9 6 9-6',
  memory: 'M12 5c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 8v8c0 1.7 3.6 3 8 3s8-1.3 8-3V8M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  inbox: 'M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5',
  cpu: 'M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4',
  note: 'M5 4h14v16H5zM9 9h6M9 13h6M9 17h3',
  flag: 'M5 21V4M5 4h11l-2 4 2 4H5',
  report: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11',
  left: 'M15 6l-6 6 6 6',
  right: 'M9 6l6 6-6 6',
  compare: 'M7 4v12M7 16a2 2 0 1 0 0 4 2 2 0 0 0 0-4M17 20V8M17 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4M7 6h6l-2-2M17 18h-6l2 2',
  shield: 'M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z',
  lock: 'M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4',
  globe: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M3 12h18M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18',
  bolt: 'M13 3L5 14h6l-1 7 8-11h-6z',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
}

export function Icon({ name, size = 16, stroke = 1.8, ...rest }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}>
      <path d={PATHS[name] || PATHS.note} />
    </svg>
  )
}

/** State badge: colour is never the only signal (always an icon and a word). */
const TONES = {
  ok: ['b-ok', 'check'], warn: ['b-warn', 'alert'], bad: ['b-bad', 'x'], info: ['b-info', 'bolt'], mute: ['b-mute', 'clock'],
}
export function Badge({ tone = 'mute', icon, children, title }) {
  const [cls, ic] = TONES[tone] || TONES.mute
  return <span className={`badge ${cls}`} title={title}><Icon name={icon || ic} size={13} stroke={2.2} />{children}</span>
}

export const DECISION = {
  allow: ['ok', 'check', 'Allowed'], needs_approval: ['warn', 'alert', 'Needs approval'], deny: ['bad', 'x', 'Blocked'],
  approved: ['ok', 'check', 'Approved'], denied: ['bad', 'x', 'Blocked'], expired: ['bad', 'clock', 'Expired (blocked)'],
  pending: ['warn', 'clock', 'Waiting'],
}
export function DecisionBadge({ value }) {
  const [tone, icon, label] = DECISION[value] || ['mute', 'note', value]
  return <Badge tone={tone} icon={icon}>{label}</Badge>
}

export const HEALTH = {
  ok: ['ok', 'check', 'Healthy'], attention: ['warn', 'alert', 'Needs attention'], paused: ['mute', 'pause', 'Paused'],
  broken: ['bad', 'x', 'Log tampered'],
}
export function HealthBadge({ value }) {
  const [tone, icon, label] = HEALTH[value] || HEALTH.ok
  return <Badge tone={tone} icon={icon}>{label}</Badge>
}

export const SEVERITY = { high: ['bad', 'alert', 'High'], medium: ['warn', 'alert', 'Medium'], low: ['info', 'flag', 'Low'], none: ['mute', 'check', 'None'] }
export function SeverityBadge({ value }) {
  const [tone, icon, label] = SEVERITY[value] || SEVERITY.none
  return <Badge tone={tone} icon={icon}>{label}</Badge>
}

export function Empty({ icon = 'inbox', title, children }) {
  return <div className="empty"><Icon name={icon} size={30} /><b>{title}</b>{children}</div>
}

export function ErrorBox({ error }) {
  return error ? <div className="err" role="alert">{error}</div> : null
}

export function Page({ title, sub, actions, children }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28, ease: 'easeOut' }}>
      <div className="topbar">
        <div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>
        {actions && <div className="row">{actions}</div>}
      </div>
      {children}
    </motion.div>
  )
}

export function Modal({ open, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={onClose}>
          <motion.div className="modal" role="dialog" aria-modal="true" initial={{ scale: 0.96, y: 10 }} animate={{ scale: 1, y: 0 }}
            exit={{ scale: 0.97, opacity: 0 }} onMouseDown={(e) => e.stopPropagation()}>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

let toastSetter = null
export function toast(msg) { toastSetter?.(msg) }
export function Toaster() {
  const [msg, setMsg] = useState(null)
  useEffect(() => { toastSetter = setMsg; return () => { toastSetter = null } }, [])
  useEffect(() => { if (msg) { const t = setTimeout(() => setMsg(null), 2200); return () => clearTimeout(t) } }, [msg])
  return (
    <AnimatePresence>
      {msg && <motion.div className="toast" role="status" initial={{ opacity: 0, y: 12, x: '-50%' }} animate={{ opacity: 1, y: 0, x: '-50%' }}
        exit={{ opacity: 0, y: 12, x: '-50%' }}><Icon name="check" />{msg}</motion.div>}
    </AnimatePresence>
  )
}

export async function copy(text, label = 'Copied') {
  try { await navigator.clipboard.writeText(text); toast(label) } catch { window.prompt('Copy this:', text) }
}

export function timeAgo(iso) {
  if (!iso) return 'never'
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}
export const fmtTime = (iso) => iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '-'
export const fmtClock = (iso) => iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '-'
export const friendly = (tool) => (tool || '').replace(/[_.:-]+/g, ' ')

/** 24 bars, single series: no legend (the card title names it), hover tooltip per bar, a hidden table for screen readers. */
export function BarChart({ values, labels, unit = 'events' }) {
  const max = Math.max(1, ...values)
  const [hover, setHover] = useState(null)
  return (
    <div>
      <div className="bars" role="img" aria-label={`${unit} per hour, last 24 hours`}>
        {values.map((v, i) => (
          <div key={i} className="bar-slot" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <motion.div className="bar" initial={{ height: 0 }} animate={{ height: `${(v / max) * 100}%` }}
              transition={{ duration: 0.5, delay: i * 0.012, ease: 'easeOut' }} />
            {hover === i && <div className="tip"><b>{v}</b> {unit} · {labels[i]}</div>}
          </div>
        ))}
      </div>
      <div className="bar-axis"><span>{labels[0]}</span><span>{labels[12]}</span><span>now</span></div>
      <table className="sr-only"><tbody>{values.map((v, i) => <tr key={i}><th>{labels[i]}</th><td>{v}</td></tr>)}</tbody></table>
    </div>
  )
}

export function Sparkline({ values }) {
  const w = 240, h = 34, max = Math.max(1, ...values)
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 3 - (v / max) * (h - 6)])
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('')
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="sg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#7dd3fc" stopOpacity="0.28" /><stop offset="1" stopColor="#7dd3fc" stopOpacity="0" /></linearGradient></defs>
      <path d={`${line}L${w},${h}L0,${h}Z`} fill="url(#sg)" />
      <path d={line} fill="none" stroke="#7dd3fc" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  )
}

export function DemoBanner() {
  if (!DEMO) return null
  return (
    <div className="demo-banner" role="note">
      <Icon name="alert" size={14} />
      <span>Live demo with sample data. Buttons only change this browser tab. <a href="https://github.com/Omkar210404/Agent-Blackbox#quickstart-5-minutes">Run Blackbox locally</a> for the real thing.</span>
    </div>
  )
}
