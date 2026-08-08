import { describe, expect, it } from 'vitest'

import { applyAuthRequired, applyLoggedOut, applyMeResponse, initialAuthState } from './authState'

describe('authState', () => {
  it('starts in checking', () => {
    expect(initialAuthState.status).toBe('checking')
  })

  describe('applyMeResponse', () => {
    it('auth_enabled: false always yields anonymous, even if authenticated is somehow true', () => {
      const state = applyMeResponse({ authenticated: true, auth_enabled: false, username: 'operator' })
      expect(state).toEqual({ status: 'anonymous', username: null, mustChangePassword: false })
    })

    it('auth_enabled: true, authenticated: false yields login-required', () => {
      const state = applyMeResponse({ authenticated: false, auth_enabled: true })
      expect(state.status).toBe('login-required')
    })

    it('auth_enabled: true, authenticated: true carries the username and must-change flag', () => {
      const state = applyMeResponse({
        authenticated: true,
        auth_enabled: true,
        username: 'operator',
        method: 'session',
        must_change_password: true,
      })
      expect(state).toEqual({ status: 'authenticated', username: 'operator', mustChangePassword: true })
    })
  })

  describe('applyAuthRequired', () => {
    it('is ignored while still checking, so a stray 401 cannot flash the login form', () => {
      const state = applyAuthRequired({ status: 'checking', username: null, mustChangePassword: false })
      expect(state.status).toBe('checking')
    })

    it('drops an authenticated session back to login-required', () => {
      const state = applyAuthRequired({
        status: 'authenticated',
        username: 'operator',
        mustChangePassword: false,
      })
      expect(state).toEqual({ status: 'login-required', username: null, mustChangePassword: false })
    })
  })

  describe('applyLoggedOut', () => {
    it('always yields login-required', () => {
      expect(applyLoggedOut().status).toBe('login-required')
    })
  })
})
