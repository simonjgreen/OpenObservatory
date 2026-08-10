/** The privacy pause: a split button in the header (ADR-055).
 *
 *  Why a split button rather than a setting. Pausing is what an operator does
 *  when people who never consented are about to be in the garden — a birthday
 *  party, a barbecue, someone coming to fix the fence — and that decision is
 *  made in the ten seconds before it happens, not in a settings page. So the
 *  main action pauses for whatever duration is currently selected, and the
 *  caret is only for changing that. One click is the common case.
 *
 *  Two things it must never do:
 *
 *  - Look off when it is on. When paused this stops being a button that
 *    pauses and becomes a button that resumes, in the pause colour, with the
 *    time remaining on it, and `App` additionally renders an unmissable banner
 *    across the page. A control whose own state is ambiguous is worse than no
 *    control here, because the operator's conclusion from a wrong reading is
 *    "the garden is being recorded" or "the garden is not" and both are
 *    actionable.
 *  - Show a countdown it cannot stand behind. The remaining time is computed
 *    from the deadline against the ticking clock, so a tab left open overnight
 *    shows the truth or shows nothing.
 */

import { useEffect, useRef, useState } from 'react'

import { formatRemaining, type PauseControlState } from '../hooks/usePause'

interface Props {
  pause: PauseControlState
  /** The station's configured zone, so "resumes at 18:30" is the operator's
   *  18:30 and not the browser's. */
  timeZone: string
}

export function PauseControl({ pause, timeZone }: Props) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement | null>(null)

  // Close on an outside click or Escape. A menu that traps the page is a
  // nuisance anywhere and unacceptable on a control the operator may be
  // reaching for in a hurry.
  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const selectedLabel =
    pause.presets.find((preset) => preset.key === pause.selected)?.label ?? pause.selected

  if (pause.active) {
    const endsAt = pause.endsUtc
      ? new Intl.DateTimeFormat('en-GB', {
          hour: '2-digit',
          minute: '2-digit',
          timeZone,
        }).format(Date.parse(pause.endsUtc))
      : null
    return (
      <div className="pause-control paused" ref={root}>
        <button
          className="pause-main"
          onClick={pause.resume}
          disabled={pause.busy}
          title={
            endsAt
              ? `Recording is paused and will resume by itself at ${endsAt}. Press to resume now.`
              : 'Recording is paused. Press to resume now.'
          }
        >
          <span className="pause-dot" aria-hidden="true" />
          resume
          <span className="pause-remaining mono">{formatRemaining(pause.remainingMs)}</span>
        </button>
        {pause.error && <span className="pause-error warn-text">{pause.error}</span>}
      </div>
    )
  }

  return (
    <div className="pause-control" ref={root}>
      <button
        className="pause-main"
        onClick={pause.pause}
        disabled={pause.busy}
        title={`Stop recording for ${selectedLabel}. Nothing is detected, stored, published or listenable while paused; the microphone itself keeps running so it cannot fail to come back.`}
      >
        pause {selectedLabel}
      </button>
      <button
        className="pause-caret"
        aria-label="Choose how long to pause for"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="pause-menu" role="menu">
          {pause.presets.map((preset) => (
            <button
              key={preset.key}
              role="menuitem"
              className={preset.key === pause.selected ? 'on' : ''}
              onClick={() => {
                pause.select(preset.key)
                setOpen(false)
              }}
            >
              {preset.label}
            </button>
          ))}
          <p className="pause-menu-note dim">
            Sets what the button does. Nothing pauses until you press it.
          </p>
        </div>
      )}
      {pause.error && <span className="pause-error warn-text">{pause.error}</span>}
    </div>
  )
}

/** The page-wide banner. Separate from the button because the button says
 *  what you can do and this says what is currently true — and the second of
 *  those has to be readable from across the room, not found in a header. */
export function PauseBanner({ pause, timeZone }: Props) {
  if (!pause.active) return null
  const endsAt = pause.endsUtc
    ? new Intl.DateTimeFormat('en-GB', {
        hour: '2-digit',
        minute: '2-digit',
        timeZone,
      }).format(Date.parse(pause.endsUtc))
    : null
  return (
    <div className="pause-banner" role="status">
      <strong>RECORDING PAUSED</strong>
      <span>
        Nothing is being detected, stored, published or listened to
        {endsAt ? ` until ${endsAt}` : ''}. The microphone is still running, so capture
        recovers by itself when the pause ends.
      </span>
      <button className="linklike" onClick={pause.resume} disabled={pause.busy}>
        resume now
      </button>
    </div>
  )
}
