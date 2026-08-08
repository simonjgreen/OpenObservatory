import { useEffect, useState } from 'react'

/** A `Date` that updates every `intervalMs`, for the header clock and
 *  anything else that needs "now" to actually tick. Its own hook so the
 *  interval lifecycle isn't duplicated, and so it's trivial to test in
 *  isolation with fake timers. */
export function useClock(intervalMs = 500): Date {
  const [clock, setClock] = useState(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return clock
}
