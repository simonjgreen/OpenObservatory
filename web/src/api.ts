/** Thin client for `/api/v1/auth/*`, plus the one piece of shared plumbing
 *  every other fetch in this app needs: noticing a 401 honestly.
 *
 *  The session credential is an `HttpOnly` cookie (ADR-034) -- this module
 *  never reads or stores a token itself. `apiFetch` is a drop-in
 *  replacement for `fetch` that reports a 401 to anyone listening
 *  (`onAuthRequired`) before returning the response to the caller, so a
 *  view that was mid-request when a session expired finds out rather than
 *  silently rendering an empty page, which is what happened before this
 *  existed: `useHistoryBrowser`'s `fetch(...).then(r => r.json())` never
 *  checked `r.ok`, so a 401's `{"detail": ...}` body just became an absent
 *  `detections` array with no explanation.
 */

export type AuthRequiredListener = () => void

let authRequiredListeners: AuthRequiredListener[] = []

/** Subscribe to "some request just got a 401". Returns an unsubscribe
 *  function. `useAuth` is the only intended subscriber -- it is what turns
 *  this into "show the login view" -- but the pub/sub is generic on
 *  purpose, so a fetch anywhere in the app (not just requests `useAuth`
 *  itself makes) can report the same signal. */
export function onAuthRequired(listener: AuthRequiredListener): () => void {
  authRequiredListeners.push(listener)
  return () => {
    authRequiredListeners = authRequiredListeners.filter((entry) => entry !== listener)
  }
}

/** Report the same "you are logged out" signal `apiFetch` reports on a 401,
 *  for callers that detect it some other way -- `live.ts`'s WebSocket close
 *  code 4401 is the one other place in this app that can. */
export function notifyAuthRequired(): void {
  authRequiredListeners.forEach((listener) => listener())
}

/** Test-only escape hatch: `onAuthRequired` subscriptions otherwise leak
 *  across test cases in the same module instance. */
export function _resetAuthRequiredListenersForTests(): void {
  authRequiredListeners = []
}

/** `fetch`, plus: cookies are always sent (same-origin default, made
 *  explicit rather than relied upon), and a 401 response notifies
 *  `onAuthRequired` subscribers before being handed back to the caller.
 *  Never throws on a 401 -- the caller still gets a `Response` and can
 *  decide what an empty/blocked view looks like; this only guarantees the
 *  "you are logged out" signal is not lost along the way. */
export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(input, { credentials: 'same-origin', ...init })
  if (response.status === 401) {
    authRequiredListeners.forEach((listener) => listener())
  }
  return response
}

export interface MeResponse {
  authenticated: boolean
  auth_enabled: boolean
  username?: string
  method?: 'session' | 'token'
  must_change_password?: boolean
}

export async function fetchMe(): Promise<MeResponse> {
  const response = await apiFetch('/api/v1/auth/me')
  if (!response.ok) {
    // Auth is enabled and there is no valid session -- the honest default
    // shape, since the server never even reaches the handler that would
    // otherwise report `auth_enabled` in this case (the blanket gate
    // answers first). See `api/app.py`'s `_enforce_auth`.
    return { authenticated: false, auth_enabled: true }
  }
  return (await response.json()) as MeResponse
}

export interface LoginResult {
  ok: boolean
  mustChangePassword: boolean
  error: string | null
  rateLimited: boolean
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const response = await fetch('/api/v1/auth/login', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (response.status === 429) {
    return { ok: false, mustChangePassword: false, error: 'too many attempts; try again shortly', rateLimited: true }
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: 'login failed' }))
    return { ok: false, mustChangePassword: false, error: body.detail ?? 'login failed', rateLimited: false }
  }
  const body = await response.json()
  return { ok: true, mustChangePassword: Boolean(body.must_change_password), error: null, rateLimited: false }
}

export async function logout(): Promise<void> {
  await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin' })
}

export interface ChangePasswordResult {
  ok: boolean
  error: string | null
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<ChangePasswordResult> {
  const response = await apiFetch('/api/v1/auth/password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: 'could not change password' }))
    return { ok: false, error: body.detail ?? 'could not change password' }
  }
  return { ok: true, error: null }
}
