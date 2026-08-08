/** Owns the `/api/v1/live` WebSocket and the state it produces: station
 *  status, spectrogram specs, detections, events and connection state.
 *
 *  Extracted from `App.tsx` (ADR-024/Milestone 4's state-extraction
 *  prerequisite) with its behaviour unchanged — this is the same single
 *  `LiveConnection` instance, the same effect lifecycle, the same bounds —
 *  just relocated, with the per-frame decisions delegated to the pure
 *  functions in `state/liveState.ts` so they can be tested without a socket.
 *
 *  Spectrogram column batches still bypass React state entirely (`register`
 *  below hands a canvas a direct callback via a ref-held sink map): they
 *  arrive dozens of times a second and a render per batch would not survive
 *  contact with a Raspberry Pi.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { LiveConnection, type ConnectionState, type HelloPayload } from '../live'
import type { ColumnBatch, Detection, Envelope, SpectrogramSpec, StationStatus } from '../types'
import { appendDetection, appendEvent, reconcileSpecs, routeColumns } from '../state/liveState'

const MAX_DETECTIONS = 600
const MAX_EVENTS = 400

export interface LiveConnectionState {
  status: StationStatus | null
  specs: SpectrogramSpec[]
  detections: Detection[]
  events: Envelope[]
  connection: ConnectionState
  /** Register a sink for one spectrogram channel's column batches. Returns
   *  an unregister function; call it from the consuming effect's cleanup. */
  register: (channel: number, sink: (batch: ColumnBatch) => void) => () => void
  /** Clock skew between this browser and the station, seconds, as measured
   *  at the last `hello` frame. Exposed for diagnostics, not the operator
   *  view. */
  clockSkewS: number
}

export function useLiveConnection(): LiveConnectionState {
  const [status, setStatus] = useState<StationStatus | null>(null)
  const [specs, setSpecs] = useState<SpectrogramSpec[]>([])
  const [detections, setDetections] = useState<Detection[]>([])
  const [events, setEvents] = useState<Envelope[]>([])
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [clockSkewS, setClockSkewS] = useState(0)

  const sinksRef = useRef(new Map<number, Set<(batch: ColumnBatch) => void>>())

  const register = useCallback((channel: number, sink: (batch: ColumnBatch) => void) => {
    const sinks = sinksRef.current
    if (!sinks.has(channel)) sinks.set(channel, new Set())
    sinks.get(channel)!.add(sink)
    return () => {
      sinks.get(channel)?.delete(sink)
    }
  }, [])

  useEffect(() => {
    const live = new LiveConnection({
      onColumns: (batch: ColumnBatch) => routeColumns(sinksRef.current, batch),
      onStatus: (next) => {
        setStatus(next)
        setSpecs((current) => reconcileSpecs(current, next.spectrograms))
      },
      onDetection: (detection) =>
        setDetections((current) => appendDetection(current, detection, MAX_DETECTIONS)),
      onEvent: (event) => setEvents((current) => appendEvent(current, event, MAX_EVENTS)),
      onConnectionChange: (state) => setConnection(state),
      onHello: (hello: HelloPayload) => {
        setClockSkewS(live.clockSkewS)
        setSpecs(hello.spectrograms)
      },
    })
    live.connect()
    return () => live.close()
  }, [])

  return useMemo(
    () => ({ status, specs, detections, events, connection, register, clockSkewS }),
    [status, specs, detections, events, connection, register, clockSkewS],
  )
}
