// @vitest-environment jsdom

/** The privacy pause control (ADR-055).
 *
 *  What is worth testing here is not that a button renders. It is the three
 *  ways this control could lie to an operator: showing "off" while the station
 *  is paused, showing a countdown from a stale number rather than the
 *  deadline, and pausing for a different duration from the one on its face.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderHook, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PauseBanner, PauseControl } from './PauseControl'
import { formatRemaining, usePause } from '../hooks/usePause'
import type { PausePayload } from '../types'

const IDLE: PausePayload = {
  active: false,
  ends_utc: null,
  started_utc: null,
  remaining_s: 0,
  preset: null,
  label: null,
  actor: null,
  detections_suppressed: 0,
  pauses_started: 0,
  presets: [
    { key: '15m', label: '15 minutes', seconds: 900 },
    { key: '1h', label: '1 hour', seconds: 3600 },
    { key: '6h', label: '6 hours', seconds: 21600 },
    { key: 'until-midnight', label: 'until midnight', seconds: null },
  ],
  default_preset: '1h',
  timezone: 'Europe/London',
  banner: '',
}

function stubFetch(payload: PausePayload = IDLE) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => payload })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('usePause', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('offers the station default until this browser has chosen otherwise', async () => {
    stubFetch()
    const { result } = renderHook(() => usePause(null, new Date()))
    await waitFor(() => expect(result.current.presets).toHaveLength(4))
    expect(result.current.selected).toBe('1h')

    act(() => result.current.select('6h'))
    expect(result.current.selected).toBe('6h')
    // Remembered for next time, which is the whole point of the drop-down
    // being a *selection* rather than four separate buttons.
    expect(window.localStorage.getItem('oo.pause.preset')).toBe('6h')
  })

  it('discards a remembered choice the station no longer offers', async () => {
    window.localStorage.setItem('oo.pause.preset', '3h')
    stubFetch()
    const { result } = renderHook(() => usePause(null, new Date()))
    await waitFor(() => expect(result.current.presets).toHaveLength(4))
    // '3h' is not in this station's menu, so it falls back rather than showing
    // a selection the drop-down does not contain.
    expect(result.current.selected).toBe('1h')
  })

  it('posts the selected preset, and only the selected preset', async () => {
    const fetchMock = stubFetch()
    const { result } = renderHook(() => usePause(null, new Date()))
    await waitFor(() => expect(result.current.presets).toHaveLength(4))

    act(() => result.current.select('until-midnight'))
    act(() => result.current.pause())

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/pause',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ preset: 'until-midnight' }),
        }),
      ),
    )
  })

  it('does not flip back to "pause" while the live frame catches up', async () => {
    /** The live status frame is only every 2 s. Letting a stale frame win
     *  immediately after the operator pressed pause would make the control
     *  visibly bounce back to the off state — on a privacy control, the worst
     *  possible moment to look like it did not work. */
    const active: PausePayload = {
      ...IDLE,
      active: true,
      ends_utc: '2026-08-08T16:00:00Z',
      remaining_s: 3600,
      preset: '1h',
      label: '1 hour',
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => IDLE })
      .mockResolvedValue({ ok: true, status: 200, json: async () => active })
    vi.stubGlobal('fetch', fetchMock)

    const stale = { ...IDLE, active: false } as const
    const { result, rerender } = renderHook(
      ({ live, now }) => usePause(live, now),
      { initialProps: { live: stale, now: new Date() } },
    )
    await waitFor(() => expect(result.current.presets).toHaveLength(4))

    act(() => result.current.pause())
    await waitFor(() => expect(result.current.active).toBe(true))

    // A stale "not paused" frame arrives; the control must hold.
    rerender({ live: stale, now: new Date() })
    expect(result.current.active).toBe(true)
  })

  it('computes the countdown from the deadline, never from remaining_s', async () => {
    /** The failure this catches: a tab left open for an hour confidently
     *  showing the remaining time it was told about an hour ago. */
    const now = new Date('2026-08-08T15:00:00Z')
    stubFetch()
    const { result } = renderHook(() =>
      usePause(
        {
          active: true,
          ends_utc: '2026-08-08T16:30:00Z',
          started_utc: '2026-08-08T14:00:00Z',
          // Deliberately wrong, and deliberately ignored.
          remaining_s: 99999,
          preset: '3h',
          label: '3 hours',
          actor: 'operator',
          detections_suppressed: 4,
          pauses_started: 1,
        },
        now,
      ),
    )
    expect(result.current.active).toBe(true)
    expect(result.current.remainingMs).toBe(90 * 60 * 1000)
  })

  it('falls back to a menu rather than nothing when the station cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    const { result } = renderHook(() => usePause(null, new Date()))
    // An empty privacy control reads as "you cannot do this", which is the one
    // thing it must never say by accident.
    await waitFor(() => expect(result.current.presets.length).toBeGreaterThan(0))
  })
})

describe('formatRemaining', () => {
  it('drops seconds above a minute, so the control is not an animation', () => {
    expect(formatRemaining(3 * 3600_000 + 4 * 60_000)).toBe('3h 04m')
    expect(formatRemaining(12 * 60_000 + 30_000)).toBe('12m')
    expect(formatRemaining(48_000)).toBe('48s')
    expect(formatRemaining(-5)).toBe('0s')
  })
})

describe('PauseControl', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const base = {
    presets: IDLE.presets,
    selected: '1h',
    select: vi.fn(),
    busy: false,
    error: null,
    pause: vi.fn(),
    resume: vi.fn(),
  }

  it('names the duration it will use, on its face', () => {
    render(
      <PauseControl
        pause={{ ...base, active: false, remainingMs: 0, endsUtc: null }}
        timeZone="Europe/London"
      />,
    )
    expect(screen.getByRole('button', { name: /pause 1 hour/i })).toBeInTheDocument()
  })

  it('becomes a resume button showing the time left once paused', () => {
    render(
      <PauseControl
        pause={{
          ...base,
          active: true,
          remainingMs: 64 * 60_000,
          endsUtc: '2026-08-08T17:30:00Z',
        }}
        timeZone="Europe/London"
      />,
    )
    expect(screen.getByRole('button', { name: /resume/i })).toBeInTheDocument()
    expect(screen.getByText('1h 04m')).toBeInTheDocument()
    // Not still offering to pause: a control that shows both states at once is
    // one an operator can read wrong.
    expect(screen.queryByText(/pause 1 hour/i)).not.toBeInTheDocument()
  })

  it('the caret selects a duration without pausing anything', async () => {
    const select = vi.fn()
    const pause = vi.fn()
    const user = userEvent.setup()
    render(
      <PauseControl
        pause={{ ...base, select, pause, active: false, remainingMs: 0, endsUtc: null }}
        timeZone="Europe/London"
      />,
    )

    await user.click(screen.getByRole('button', { name: /choose how long/i }))
    await user.click(screen.getByRole('menuitem', { name: '6 hours' }))

    expect(select).toHaveBeenCalledWith('6h')
    expect(pause).not.toHaveBeenCalled()
  })

  it('resumes in one click', async () => {
    const resume = vi.fn()
    const user = userEvent.setup()
    render(
      <PauseControl
        pause={{ ...base, resume, active: true, remainingMs: 60_000, endsUtc: null }}
        timeZone="Europe/London"
      />,
    )
    await user.click(screen.getByRole('button', { name: /resume/i }))
    expect(resume).toHaveBeenCalledTimes(1)
  })
})

describe('PauseBanner', () => {
  const base = {
    presets: IDLE.presets,
    selected: '1h',
    select: vi.fn(),
    busy: false,
    error: null,
    pause: vi.fn(),
    resume: vi.fn(),
  }

  it('renders nothing at all when the station is recording', () => {
    const { container } = render(
      <PauseBanner
        pause={{ ...base, active: false, remainingMs: 0, endsUtc: null }}
        timeZone="Europe/London"
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('says what is suppressed, and that the microphone is still running', () => {
    render(
      <PauseBanner
        pause={{ ...base, active: true, remainingMs: 60_000, endsUtc: '2026-08-08T17:30:00Z' }}
        timeZone="Europe/London"
      />,
    )
    expect(screen.getByText('RECORDING PAUSED')).toBeInTheDocument()
    // 17:30 UTC is 18:30 in London -- the station's zone, not the browser's.
    expect(screen.getByText(/until 18:30/)).toBeInTheDocument()
    expect(screen.getByText(/microphone is still running/i)).toBeInTheDocument()
  })
})
