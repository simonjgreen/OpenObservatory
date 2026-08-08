/** Owns the login/session lifecycle: probes `/api/v1/auth/me` once on
 *  mount, listens for a 401 from anywhere else in the app
 *  (`api.ts`'s `onAuthRequired`) and drops back to the login view, and
 *  exposes `login`/`logout`/`changePassword` for the UI to call. */

import { useCallback, useEffect, useState } from 'react'

import {
  changePassword as apiChangePassword,
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
  onAuthRequired,
} from '../api'
import { applyAuthRequired, applyLoggedOut, applyMeResponse, initialAuthState, type AuthState } from '../state/authState'

export interface Auth extends AuthState {
  login: (username: string, password: string) => Promise<{ ok: boolean; error: string | null; rateLimited: boolean }>
  logout: () => Promise<void>
  changePassword: (current: string, next: string) => Promise<{ ok: boolean; error: string | null }>
}

export function useAuth(): Auth {
  const [state, setState] = useState<AuthState>(initialAuthState)

  useEffect(() => {
    let cancelled = false
    fetchMe()
      .then((me) => {
        if (!cancelled) setState(applyMeResponse(me))
      })
      .catch(() => {
        // Treat an unreachable /me as "anonymous" rather than stuck
        // 'checking' forever -- the rest of the app already handles a
        // disconnected station honestly (the connection banner), and a
        // login gate that can never resolve would just be one more thing
        // stuck loading.
        if (!cancelled) setState({ status: 'anonymous', username: null, mustChangePassword: false })
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => onAuthRequired(() => setState((current) => applyAuthRequired(current))), [])

  const login = useCallback(async (username: string, password: string) => {
    const result = await apiLogin(username, password)
    if (result.ok) {
      const me = await fetchMe()
      setState(applyMeResponse(me))
    }
    return { ok: result.ok, error: result.error, rateLimited: result.rateLimited }
  }, [])

  const logout = useCallback(async () => {
    await apiLogout()
    setState(applyLoggedOut())
  }, [])

  const changePassword = useCallback(async (current: string, next: string) => {
    const result = await apiChangePassword(current, next)
    if (result.ok) {
      const me = await fetchMe()
      setState(applyMeResponse(me))
    }
    return result
  }, [])

  return { ...state, login, logout, changePassword }
}
