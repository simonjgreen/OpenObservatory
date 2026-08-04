/** WebSocket client for the station's live channel.
 *
 *  Deliberately not a React hook: spectrogram columns arrive ~40 times a second
 *  and must reach a canvas without triggering a render each time. This class owns
 *  the socket and hands columns straight to a callback; only the low-rate JSON
 *  (status, events, detections) is surfaced as state.
 */

import type { ColumnBatch, Detection, Envelope, StationStatus } from './types'

const HEADER_BYTES = 16
const FRAME_SPECTROGRAM = 1

export type ConnectionState = 'connecting' | 'open' | 'closed' | 'error'

export interface LiveHandlers {
  onColumns(batch: ColumnBatch): void
  onStatus(status: StationStatus): void
  onDetection(detection: Detection): void
  onEvent(event: Envelope): void
  onConnectionChange(state: ConnectionState, detail?: string): void
  onHello(payload: HelloPayload): void
}

export interface HelloPayload {
  server_utc: number
  station: StationStatus
  spectrograms: StationStatus['spectrograms']
  recent_detections: Detection[]
  recent_events: Envelope[]
}

export function decodeColumns(buffer: ArrayBuffer): ColumnBatch | null {
  if (buffer.byteLength < HEADER_BYTES) return null
  const view = new DataView(buffer)
  if (view.getUint8(0) !== FRAME_SPECTROGRAM) return null
  const channel = view.getUint8(1)
  const bins = view.getUint16(2, true)
  const columns = view.getUint16(4, true)
  const firstUtcS = view.getFloat64(8, true)
  const expected = bins * columns
  const available = buffer.byteLength - HEADER_BYTES
  if (expected === 0 || available < expected) return null
  return {
    channel,
    bins,
    columns,
    firstUtcS,
    data: new Uint8Array(buffer, HEADER_BYTES, expected),
  }
}

export function apiBase(): string {
  return '/api/v1'
}

function socketUrl(path: string): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}${path}`
}

export class LiveConnection {
  private socket: WebSocket | null = null
  private reconnectDelay = 500
  private keepAlive: number | null = null
  private closedByUs = false
  /** Server clock minus browser clock, so column times line up with our own. */
  clockSkewS = 0

  constructor(private readonly handlers: LiveHandlers) {}

  connect(): void {
    this.closedByUs = false
    this.handlers.onConnectionChange('connecting')
    const socket = new WebSocket(socketUrl(`${apiBase()}/live`))
    socket.binaryType = 'arraybuffer'
    this.socket = socket

    socket.onopen = () => {
      this.reconnectDelay = 500
      this.handlers.onConnectionChange('open')
      // The server reads from the socket to notice disconnects promptly, so give
      // it something to read on a slow interval.
      this.keepAlive = window.setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send('ping')
      }, 5000)
    }

    socket.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== 'string') {
        const batch = decodeColumns(event.data as ArrayBuffer)
        if (batch) this.handlers.onColumns(batch)
        return
      }
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(event.data)
      } catch {
        return
      }
      switch (payload.type) {
        case 'hello': {
          const hello = payload as unknown as HelloPayload
          this.clockSkewS = hello.server_utc - Date.now() / 1000
          this.handlers.onHello(hello)
          this.handlers.onStatus(hello.station)
          hello.recent_detections?.forEach((d) => this.handlers.onDetection(d))
          hello.recent_events?.forEach((e) => this.handlers.onEvent(e))
          break
        }
        case 'status':
          this.handlers.onStatus(payload.station as StationStatus)
          break
        case 'event': {
          const envelope = payload.event as Envelope
          this.handlers.onEvent(envelope)
          if (envelope.event_type === 'detection.created') {
            this.handlers.onDetection(envelope.data as unknown as Detection)
          }
          break
        }
      }
    }

    socket.onerror = () => this.handlers.onConnectionChange('error')

    socket.onclose = () => {
      if (this.keepAlive !== null) {
        window.clearInterval(this.keepAlive)
        this.keepAlive = null
      }
      this.handlers.onConnectionChange('closed')
      if (this.closedByUs) return
      // Reconnect with backoff. A station restart or a Wi-Fi blip should heal on
      // its own rather than needing a page refresh.
      window.setTimeout(() => this.connect(), this.reconnectDelay)
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 10000)
    }
  }

  close(): void {
    this.closedByUs = true
    if (this.keepAlive !== null) window.clearInterval(this.keepAlive)
    this.socket?.close()
    this.socket = null
  }
}
