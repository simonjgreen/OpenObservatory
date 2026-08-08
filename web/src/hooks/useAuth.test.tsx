// @vitest-environment jsdom

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from './useAuth'
import * as api from '../api'

describe('useAuth', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    api._resetAuthRequiredListenersForTests()
  })

  it('starts checking, then anonymous when auth_enabled is false', async () => {
    vi.spyOn(api, 'fetchMe').mockResolvedValue({ authenticated: false, auth_enabled: false })
    const { result } = renderHook(() => useAuth())
    expect(result.current.status).toBe('checking')
    await waitFor(() => expect(result.current.status).toBe('anonymous'))
  })

  it('goes to login-required when auth is enabled and there is no session', async () => {
    vi.spyOn(api, 'fetchMe').mockResolvedValue({ authenticated: false, auth_enabled: true })
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.status).toBe('login-required'))
  })

  it('falls back to anonymous rather than hanging forever if /me is unreachable', async () => {
    vi.spyOn(api, 'fetchMe').mockRejectedValue(new Error('network error'))
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.status).toBe('anonymous'))
  })

  it('login() success re-probes /me and lands on authenticated', async () => {
    vi.spyOn(api, 'fetchMe')
      .mockResolvedValueOnce({ authenticated: false, auth_enabled: true })
      .mockResolvedValueOnce({ authenticated: true, auth_enabled: true, username: 'operator', method: 'session' })
    vi.spyOn(api, 'login').mockResolvedValue({ ok: true, mustChangePassword: false, error: null, rateLimited: false })

    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.status).toBe('login-required'))

    await act(async () => {
      await result.current.login('operator', 'correct-password')
    })

    expect(result.current.status).toBe('authenticated')
    expect(result.current.username).toBe('operator')
  })

  it('login() failure stays on login-required and surfaces the error', async () => {
    vi.spyOn(api, 'fetchMe').mockResolvedValue({ authenticated: false, auth_enabled: true })
    vi.spyOn(api, 'login').mockResolvedValue({
      ok: false,
      mustChangePassword: false,
      error: 'invalid username or password',
      rateLimited: false,
    })

    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.status).toBe('login-required'))

    let outcome
    await act(async () => {
      outcome = await result.current.login('operator', 'wrong')
    })

    expect(outcome).toEqual({ ok: false, error: 'invalid username or password', rateLimited: false })
    expect(result.current.status).toBe('login-required')
  })

  it('a 401 reported elsewhere (onAuthRequired) drops an authenticated session back to login-required', async () => {
    vi.spyOn(api, 'fetchMe').mockResolvedValue({
      authenticated: true,
      auth_enabled: true,
      username: 'operator',
      method: 'session',
    })
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.status).toBe('authenticated'))

    act(() => api.notifyAuthRequired())

    expect(result.current.status).toBe('login-required')
  })

  it('logout() clears the session and returns to login-required', async () => {
    vi.spyOn(api, 'fetchMe').mockResolvedValue({
      authenticated: true,
      auth_enabled: true,
      username: 'operator',
      method: 'session',
    })
    const logoutSpy = vi.spyOn(api, 'logout').mockResolvedValue(undefined)
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.status).toBe('authenticated'))

    await act(async () => {
      await result.current.logout()
    })

    expect(logoutSpy).toHaveBeenCalled()
    expect(result.current.status).toBe('login-required')
  })
})
