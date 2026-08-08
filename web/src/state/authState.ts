/** Pure state transitions for the login/session lifecycle, kept separate
 *  from `hooks/useAuth.ts` so they are testable without a socket, a fetch,
 *  or a DOM -- same split as `state/liveState.ts` and
 *  `state/operatorHealth.ts` use for the same reason. */

import type { MeResponse } from '../api'

export type AuthStatus = 'checking' | 'anonymous' | 'login-required' | 'authenticated'

export interface AuthState {
  status: AuthStatus
  username: string | null
  mustChangePassword: boolean
}

export const initialAuthState: AuthState = {
  status: 'checking',
  username: null,
  mustChangePassword: false,
}

/** `GET /api/v1/auth/me`'s response, folded into state.
 *
 *  `auth_enabled: false` always yields `'anonymous'` regardless of whether a
 *  session happens to exist -- ADR-034's point is that turning auth off
 *  means no login gate at all, not a gate that happens to already be open.
 */
export function applyMeResponse(response: MeResponse): AuthState {
  if (!response.auth_enabled) {
    return { status: 'anonymous', username: null, mustChangePassword: false }
  }
  if (!response.authenticated) {
    return { status: 'login-required', username: null, mustChangePassword: false }
  }
  return {
    status: 'authenticated',
    username: response.username ?? null,
    mustChangePassword: Boolean(response.must_change_password),
  }
}

/** A 401 arrived from some unrelated request (`api.ts`'s `onAuthRequired`).
 *  Ignored while still `'checking'`: a request racing the initial `/me`
 *  call must not flash a login form on a station where auth turns out to
 *  be off entirely. */
export function applyAuthRequired(state: AuthState): AuthState {
  if (state.status === 'checking') return state
  return { status: 'login-required', username: null, mustChangePassword: false }
}

export function applyLoggedOut(): AuthState {
  return { status: 'login-required', username: null, mustChangePassword: false }
}
