// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ChangePasswordGate, Login } from './Login'
import type { Auth } from '../hooks/useAuth'

afterEach(() => {
  cleanup()
})

function makeAuth(overrides: Partial<Auth> = {}): Auth {
  return {
    status: 'login-required',
    username: null,
    mustChangePassword: false,
    login: vi.fn().mockResolvedValue({ ok: true, error: null, rateLimited: false }),
    logout: vi.fn().mockResolvedValue(undefined),
    changePassword: vi.fn().mockResolvedValue({ ok: true, error: null }),
    ...overrides,
  }
}

describe('Login', () => {
  it('submits the typed username and password', async () => {
    const user = userEvent.setup()
    const auth = makeAuth()
    render(<Login auth={auth} />)

    await user.type(screen.getByLabelText('username'), 'operator')
    await user.type(screen.getByLabelText('password'), 'hunter2hunter2')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(auth.login).toHaveBeenCalledWith('operator', 'hunter2hunter2')
  })

  it('shows the error message from a failed login', async () => {
    const user = userEvent.setup()
    const auth = makeAuth({
      login: vi.fn().mockResolvedValue({ ok: false, error: 'invalid username or password', rateLimited: false }),
    })
    render(<Login auth={auth} />)

    await user.type(screen.getByLabelText('username'), 'operator')
    await user.type(screen.getByLabelText('password'), 'wrong')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('invalid username or password')
  })

  it('disables submit until both fields are filled', () => {
    render(<Login auth={makeAuth()} />)
    expect(screen.getByRole('button', { name: /sign in/i })).toBeDisabled()
  })
})

describe('ChangePasswordGate', () => {
  it('rejects a mismatched confirmation without calling changePassword', async () => {
    const user = userEvent.setup()
    const auth = makeAuth({ status: 'authenticated', username: 'operator', mustChangePassword: true })
    render(<ChangePasswordGate auth={auth} />)

    await user.type(screen.getByLabelText(/current \(generated\) password/i), 'generated-pw')
    await user.type(screen.getByLabelText(/^new password$/i), 'a-new-password')
    await user.type(screen.getByLabelText(/confirm new password/i), 'does-not-match')
    await user.click(screen.getByRole('button', { name: /set password/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('do not match')
    expect(auth.changePassword).not.toHaveBeenCalled()
  })

  it('calls changePassword with current and new values when they match', async () => {
    const user = userEvent.setup()
    const auth = makeAuth({ status: 'authenticated', username: 'operator', mustChangePassword: true })
    render(<ChangePasswordGate auth={auth} />)

    await user.type(screen.getByLabelText(/current \(generated\) password/i), 'generated-pw')
    await user.type(screen.getByLabelText(/^new password$/i), 'a-new-password')
    await user.type(screen.getByLabelText(/confirm new password/i), 'a-new-password')
    await user.click(screen.getByRole('button', { name: /set password/i }))

    expect(auth.changePassword).toHaveBeenCalledWith('generated-pw', 'a-new-password')
  })
})
