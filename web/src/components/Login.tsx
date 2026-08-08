/** Login gate and forced-password-change form.
 *
 *  Shown instead of the whole app when `useAuth().status` is
 *  `'login-required'`, or when it is `'authenticated'` but
 *  `mustChangePassword` is true (the bootstrap account, or one an operator
 *  reset) -- see `App.tsx`. Deliberately minimal: this is a local appliance
 *  login form, not a product marketing page.
 */

import { useState, type FormEvent } from 'react'
import type { Auth } from '../hooks/useAuth'

export function Login({ auth }: { auth: Auth }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const result = await auth.login(username, password)
    setBusy(false)
    if (!result.ok) {
      setError(result.error ?? 'login failed')
      return
    }
    setPassword('')
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>Open Observatory</h1>
        <p className="dim">Sign in to this station.</p>
        <label>
          username
          <input
            autoFocus
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
          />
        </label>
        <label>
          password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
          />
        </label>
        {error && (
          <p role="alert" className="login-error">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy || !username || !password}>
          {busy ? 'signing in…' : 'sign in'}
        </button>
        <p className="dim login-note">
          Served over plain HTTP on the local network: this login protects against another
          device on the LAN, not against anything on the wire between here and this browser.
        </p>
      </form>
    </div>
  )
}

export function ChangePasswordGate({ auth }: { auth: Auth }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (next !== confirm) {
      setError('passwords do not match')
      return
    }
    setBusy(true)
    setError(null)
    const result = await auth.changePassword(current, next)
    setBusy(false)
    if (!result.ok) {
      setError(result.error ?? 'could not change password')
      return
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>Choose a password</h1>
        <p className="dim">
          Signed in as <strong>{auth.username}</strong> with a generated password. Set a
          password only you know before continuing.
        </p>
        <label>
          current (generated) password
          <input
            type="password"
            autoFocus
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            autoComplete="current-password"
          />
        </label>
        <label>
          new password
          <input
            type="password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            autoComplete="new-password"
          />
        </label>
        <label>
          confirm new password
          <input
            type="password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            autoComplete="new-password"
          />
        </label>
        {error && (
          <p role="alert" className="login-error">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy || !current || !next || !confirm}>
          {busy ? 'saving…' : 'set password'}
        </button>
      </form>
    </div>
  )
}
