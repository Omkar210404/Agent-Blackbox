// Static demo for GitHub Pages: answers the dashboard's API calls from a saved snapshot of `blackbox demo`.
// Actions (approve, pause, save policy) only change this browser tab. Built with `npm run build:demo`.
let state = null

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$/

// Move every timestamp forward so the snapshot always looks like it was recorded a few minutes ago.
function shift(value, ms) {
  if (typeof value === 'string') return ISO.test(value) ? new Date(new Date(value).getTime() + ms).toISOString().replace('Z', '+00:00') : value
  if (Array.isArray(value)) return value.map((v) => shift(v, ms))
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, shift(v, ms)]))
  return value
}

async function load() {
  if (state) return state
  const raw = (await import('./demo-data.json')).default
  const data = shift(raw, Date.now() - new Date(raw.generated).getTime() - 5 * 60 * 1000)
  data.overview.hours = Array.from({ length: 24 }, (_, i) => `${String(new Date(Date.now() - (23 - i) * 3600e3).getUTCHours()).padStart(2, '0')}:00`)
  state = data
  return state
}

const today = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10) }

export async function demoApi(path, method, body) {
  const s = await load()
  const url = new URL(path, 'http://demo')
  const p = url.pathname, q = url.searchParams
  const pending = () => s.approvals.filter((a) => a.status === 'pending')
  if (method === 'GET') {
    if (p === '/health') return { ...s.health, demo: true }
    if (p === '/v1/overview') {
      const pend = pending()
      return { ...s.overview, totals: { ...s.overview.totals, pending: pend.length },
        agents: s.overview.agents.map((a) => {
          const waiting = pend.filter((x) => x.agent_id === a.id).length
          return { ...a, pending_approvals: waiting, health: a.paused ? 'paused' : waiting || a.top_flag === 'high' || a.denied_24h ? 'attention' : 'ok' }
        }) }
    }
    if (p === '/v1/map') return s.map
    if (p === '/v1/agents') return s.agents
    if (p === '/v1/runs') return s.runs.filter((r) => !q.get('agent_id') || r.agent_id === q.get('agent_id'))
    if (p === '/v1/approvals') return s.approvals.filter((a) => !q.get('status') || a.status === q.get('status'))
    let m
    if ((m = p.match(/^\/v1\/runs\/([^/]+)\/timeline$/)) && s.timelines[m[1]]) return s.timelines[m[1]]
    if ((m = p.match(/^\/v1\/agents\/([^/]+)\/policy$/)) && s.policies[m[1]]) return s.policies[m[1]]
    if ((m = p.match(/^\/v1\/reports\/([^/]+)$/)) && s.reports[m[1]]) return s.reports[m[1]][q.get('format') === 'html' ? 'html' : 'md']
    if (p.startsWith('/v1/client/')) {
      const day = q.get('day')
      const c = { ...s.client, day: new Date().toLocaleDateString('en-GB', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' }) }
      return !day || day === today() ? c
        : { ...c, day, actions: 0, checked: 0, by_tool: {}, asked: [], blocked: [], warnings: [], memory_writes: 0 }
    }
  } else {
    let m
    if ((m = p.match(/^\/v1\/approvals\/([^/]+)\/decision$/))) {
      const a = s.approvals.find((x) => x.id === m[1])
      Object.assign(a, { status: body.approve ? 'approved' : 'denied', decided_by: `${body.by} (demo)`, decided_at: new Date().toISOString().replace('Z', '+00:00') })
      return a
    }
    if ((m = p.match(/^\/v1\/agents\/([^/]+)\/pause$/))) {
      for (const list of [s.agents, s.overview.agents]) { const a = list.find((x) => x.id === m[1]); if (a) a.paused = body.paused }
      return s.agents.find((x) => x.id === m[1])
    }
    if (p.match(/^\/v1\/agents\/[^/]+\/client-link$/)) return { path: '#/client', note: 'Demo link' }
    if ((m = p.match(/^\/v1\/agents\/([^/]+)\/policy$/))) {
      s.policies[m[1]] = { ...s.policies[m[1]], yaml: body.yaml, updated: new Date().toISOString(), updated_by: 'you (demo, this tab only)' }
      return s.policies[m[1]]
    }
    if (p === '/v1/policy/validate') return { ok: true, errors: [], demo: true }
    if (p === '/v1/policy/test') {
      const original = s.policies[body.agent_id]?.yaml
      const hit = s.policy_tests[`${body.agent_id}|${body.tool}|${JSON.stringify(sortKeys(body.args))}`]
      if (hit && body.yaml === original) return hit
      return { ok: false, demo: true, errors: ['This static demo can only test the example calls against the saved policy. Run Blackbox locally to test anything.'] }
    }
    if (p.match(/^\/v1\/client\/[^/]+\/pause$/)) return { status: 'requested' }
  }
  throw new Error('Not available in the static demo. Run Blackbox locally for the full app.')
}

function sortKeys(o) {
  return Object.fromEntries(Object.keys(o || {}).sort().map((k) => [k, o[k]]))
}
