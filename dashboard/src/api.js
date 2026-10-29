import { useCallback, useEffect, useRef, useState } from 'react'

const KEY = 'bb_admin_token'

export const auth = {
  get() { try { return localStorage.getItem(KEY) } catch { return null } },
  set(t) { try { localStorage.setItem(KEY, t) } catch { /* private mode: token lives for this tab only */ } },
  clear() { try { localStorage.removeItem(KEY) } catch { /* ignore */ } },
}

let memoryToken = null
export const setToken = (t) => { memoryToken = t; auth.set(t) }
const token = () => memoryToken || auth.get()

export async function api(path, { method = 'GET', body, text = false, anon = false } = {}) {
  const headers = {}
  if (!anon) headers.Authorization = `Bearer ${token()}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  if (r.status === 401 && !anon) {
    auth.clear(); memoryToken = null
    window.dispatchEvent(new Event('bb-logout'))
    throw new Error('Signed out: the admin token was not accepted.')
  }
  if (!r.ok) {
    let detail = r.statusText
    try { const j = await r.json(); detail = j.detail?.errors?.join('; ') || (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) } catch { /* not JSON */ }
    throw new Error(detail || `Request failed (${r.status})`)
  }
  return text ? r.text() : r.json()
}

/** Fetch a path, optionally refreshing every `every` ms. */
export function useApi(path, { every = 0, anon = false } = {}) {
  const [state, set] = useState({ data: null, error: null, loading: true })
  const alive = useRef(true)
  const load = useCallback(async () => {
    if (!path) return
    try {
      const data = await api(path, { anon })
      if (alive.current) set({ data, error: null, loading: false })
    } catch (e) {
      if (alive.current) set((s) => ({ ...s, error: e.message, loading: false }))
    }
  }, [path, anon])
  useEffect(() => {
    alive.current = true
    set((s) => ({ ...s, loading: true }))
    load()
    const id = every ? setInterval(load, every) : null
    return () => { alive.current = false; if (id) clearInterval(id) }
  }, [load, every])
  return { ...state, reload: load }
}
