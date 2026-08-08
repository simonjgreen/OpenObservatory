/** Pure reducer-shaped helpers behind the live WebSocket state.
 *
 *  These used to be inline closures inside `App.tsx`'s single `useEffect` —
 *  correct, but untestable without standing up a real `LiveConnection` and a
 *  socket. Pulling the *decision* each callback makes (keep this array or
 *  replace it? which events are noise?) out as plain functions means the
 *  policy is unit tested directly; `useLiveConnection` just wires these to
 *  the connection's callbacks and holds the resulting state.
 */

import type { ColumnBatch, Detection, Envelope, SpectrogramSpec, StationStatus } from '../types'

/** Only replace the spectrogram spec array when the channel set actually
 *  changed, so re-anchoring the canvases does not throw away scroll history
 *  on every status tick (most status ticks do not change the channel set). */
export function reconcileSpecs(
  current: SpectrogramSpec[],
  next: SpectrogramSpec[],
): SpectrogramSpec[] {
  const same =
    current.length === next.length &&
    current.every(
      (spec, index) =>
        spec.channel === next[index].channel &&
        spec.bins === next[index].bins &&
        spec.sample_rate === next[index].sample_rate,
    )
  return same ? current : next
}

/** Append a detection, deduplicated by id (the socket can redeliver on
 *  reconnect), bounded to `max` — oldest dropped first. */
export function appendDetection(
  current: Detection[],
  detection: Detection,
  max: number,
): Detection[] {
  if (current.some((existing) => existing.id === detection.id)) return current
  const next = [...current, detection]
  return next.length > max ? next.slice(-max) : next
}

/** Per-second level telemetry and the full status snapshot would swamp the
 *  event log — they drive the meters and header instead — so they never
 *  enter the log. Everything else is prepended (newest first) and bounded. */
export function appendEvent(current: Envelope[], event: Envelope, max: number): Envelope[] {
  if (event.event_type === 'capture.levels' || event.event_type === 'station.status') {
    return current
  }
  const next = [event, ...current]
  return next.length > max ? next.slice(0, max) : next
}

/** Route an incoming column batch to whichever spectrogram canvases have
 *  registered a sink for its channel. Pulled out so the routing logic (as
 *  opposed to the ref bookkeeping around it) is directly testable. */
export function routeColumns(
  sinks: Map<number, Set<(batch: ColumnBatch) => void>>,
  batch: ColumnBatch,
): void {
  const channelSinks = sinks.get(batch.channel)
  if (!channelSinks) return
  for (const sink of channelSinks) sink(batch)
}

export interface CaptureHealth {
  listening: boolean
  synthetic: boolean
  label: string
}

/** Reduce `StationStatus` down to the one fact the operator view leads
 *  with: is this real audio, and is it flowing. Everything else in the
 *  header/diagnostics is detail underneath this. */
export function captureHealth(status: StationStatus | null): CaptureHealth {
  const capture = status?.capture
  if (!capture) return { listening: false, synthetic: false, label: 'no station connection' }
  const synthetic = !capture.is_live_hardware
  const listening = capture.state === 'capturing'
  if (synthetic) {
    return { listening, synthetic: true, label: `NOT LIVE AUDIO — ${capture.source_kind ?? 'synthetic source'}` }
  }
  if (!listening) {
    return { listening: false, synthetic: false, label: capture.detail || capture.state }
  }
  return { listening: true, synthetic: false, label: 'listening on the microphone' }
}
