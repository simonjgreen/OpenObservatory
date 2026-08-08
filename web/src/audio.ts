/** Live audio playback via a plain `<audio>` element, pointed at the
 *  chunked-WAV endpoint (`/api/v1/live/audio.wav`).
 *
 *  This replaced a Web Audio implementation (buffers scheduled on an explicit
 *  playback cursor, or an AudioWorklet) after diagnosing it dead on a real
 *  machine: Chrome/Ubuntu, hardware output at 44.1 kHz, page served over plain
 *  HTTP from a LAN address so `window.isSecureContext` is false. On that
 *  machine an oscillator through `context.destination` was inaudible, and so
 *  was the same graph routed via `createMediaStreamDestination()` into an
 *  `<audio srcObject>` — Web Audio produced no audible output by any route. A
 *  generated WAV through a plain `<audio>` element *was* audible on the same
 *  machine, and so is YouTube: media-element playback works, Web Audio does
 *  not. So no node in the output path may be a Web Audio node — that includes
 *  a `GainNode` for monitor make-up gain, which is why that control is gone
 *  rather than reimplemented on `context.destination`.
 *
 *  The trade-off against the old WebSocket/AudioWorklet path: this buffers
 *  what the browser's media pipeline decides to buffer, which is coarser and
 *  less controllable than an explicit jitter buffer. That is an acceptable
 *  trade for actually being audible. The WebSocket endpoint
 *  (`/api/v1/live/audio`) is untouched and still serves other clients (e.g. a
 *  phone) that never had this problem.
 *
 *  Because there is no explicit jitter buffer or Web Audio analyser here, the
 *  old telemetry (`out dBFS`, `buffer ms`, `underruns`, ...) has no equivalent
 *  and is not fabricated. `AudioTelemetry` below reports only what
 *  `HTMLMediaElement` genuinely exposes: `readyState`/`networkState`, how much
 *  is buffered ahead of the play cursor, and counts of `stalled`/`waiting`
 *  events. The server-side level meters elsewhere in the UI already show
 *  signal level; nothing essential is lost.
 */

export type LiveAudioChannel = 'audible' | 'ultrasonic'

/** What the server actually gave us for this connection. Over HTTP there is
 *  no JSON hello frame, so this is assembled from the response's status and a
 *  few small `X-Live-*` headers — real values from the broadcaster/heterodyne,
 *  never fabricated. */
export interface AudioHelloInfo {
  channel: LiveAudioChannel
  sampleRate: number
  available: boolean
  reason?: string
  tuneHz?: number
  bandwidthHz?: number
}

//: Roughly what a handheld heterodyne detector covers: above this most
//: adults hear nothing to expand on, below it native gear already used for
//: bush-crickets and the lowest bat calls starts to overlap the audible band.
export const ULTRASONIC_TUNE_MIN_HZ = 15000
export const ULTRASONIC_TUNE_MAX_HZ = 125000

/** Keep a requested tuning frequency inside the range the live monitor
 *  supports. Pure so it can be unit tested without a socket or an AudioContext. */
export function clampTuneHz(hz: number): number {
  if (!Number.isFinite(hz)) return ULTRASONIC_TUNE_MIN_HZ
  return Math.min(ULTRASONIC_TUNE_MAX_HZ, Math.max(ULTRASONIC_TUNE_MIN_HZ, hz))
}

/** The `channel`/`tune_hz` query string shared by both live-audio transports.
 *  Pure and shared so the two URL builders below cannot drift apart. */
function liveAudioQuery(channel: LiveAudioChannel, tuneHz?: number): string {
  const params = new URLSearchParams()
  if (channel === 'ultrasonic') {
    params.set('channel', 'ultrasonic')
    if (tuneHz !== undefined) params.set('tune_hz', String(Math.round(clampTuneHz(tuneHz))))
  }
  return params.toString()
}

/** Build the `/api/v1/live/audio` WebSocket URL for a given channel. Pure so
 *  the query string logic — the part most likely to silently rot — is unit
 *  tested without needing a real `window.location` or WebSocket. Kept around
 *  even though the debug UI no longer uses it for playback: other clients
 *  (e.g. a phone, where Web Audio works fine) still connect to it directly. */
export function buildLiveAudioUrl(
  location: { protocol: string; host: string },
  channel: LiveAudioChannel,
  tuneHz?: number,
): string {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const query = liveAudioQuery(channel, tuneHz)
  return `${scheme}//${location.host}/api/v1/live/audio${query ? `?${query}` : ''}`
}

/** Build the `/api/v1/live/audio.wav` HTTP URL for a given channel — what a
 *  plain `<audio>` element is pointed at. Same query-string shape as the
 *  WebSocket URL, different scheme and path. */
export function buildLiveAudioWavUrl(
  location: { protocol: string; host: string },
  channel: LiveAudioChannel,
  tuneHz?: number,
): string {
  const scheme = location.protocol === 'https:' ? 'https:' : 'http:'
  const query = liveAudioQuery(channel, tuneHz)
  return `${scheme}//${location.host}/api/v1/live/audio.wav${query ? `?${query}` : ''}`
}

/** Build the `/api/v1/live/tune` control URL — the in-place retune added
 *  alongside the chunked-WAV stream (see `LiveAudioPlayer.setTuneHz`). Pure
 *  and unit tested for the same reason the two URL builders above are. */
export function buildLiveTuneUrl(
  location: { protocol: string; host: string },
  tuneHz: number,
): string {
  const scheme = location.protocol === 'https:' ? 'https:' : 'http:'
  const params = new URLSearchParams({ tune_hz: String(Math.round(clampTuneHz(tuneHz))) })
  return `${scheme}//${location.host}/api/v1/live/tune?${params.toString()}`
}

//: Coalesces rapid slider ticks into at most one in-flight request per
//: window, while still sending immediately on the first tick so the dial
//: feels responsive. See `LiveAudioPlayer.setTuneHz`.
const TUNE_THROTTLE_MS = 80

/** What `HTMLMediaElement` genuinely exposes, sampled on an interval. Nothing
 *  here is inferred or invented — see the module comment for why the previous
 *  Web-Audio-derived telemetry (output level, jitter-buffer depth, underrun
 *  count) has no replacement. */
export interface AudioTelemetry {
  /** `HTMLMediaElement.readyState`, 0..4. >=3 means playback can continue
   *  without further buffering right now. */
  readyState: number
  /** `HTMLMediaElement.networkState`, 0..3. */
  networkState: number
  /** Seconds already buffered ahead of the current play position. */
  bufferedAheadS: number
  /** Times the `stalled` event fired: the browser was fetching data but none
   *  arrived. */
  stalls: number
  /** Times the `waiting` event fired: playback paused for lack of data. */
  waits: number
  paused: boolean
}

export type AudioStatus = 'idle' | 'starting' | 'playing' | 'error'

export class LiveAudioPlayer {
  private audioEl: HTMLAudioElement | null = null
  private channel: LiveAudioChannel = 'audible'
  private tuneHz = 45000
  private sampleRate = 48000
  private volume = 1
  private stalls = 0
  private waits = 0
  private reportTimer: number | null = null
  //: Trailing-edge throttle state for `setTuneHz`. `tuneThrottleTimer` is
  //: non-null exactly while a cooldown is running; `pendingTuneHz` holds the
  //: latest value requested during that cooldown, sent when it elapses.
  private tuneThrottleTimer: number | null = null
  private pendingTuneHz: number | null = null

  telemetry: AudioTelemetry | null = null

  constructor(
    private readonly onStatus: (status: AudioStatus, detail?: string) => void,
    private readonly onTelemetry: (telemetry: AudioTelemetry) => void,
    private readonly onHello?: (info: AudioHelloInfo) => void,
  ) {}

  get playing(): boolean {
    return this.audioEl !== null
  }

  async start(volume = 1, channel: LiveAudioChannel = 'audible', tuneHz = 45000): Promise<void> {
    if (this.audioEl) return
    this.volume = volume
    this.channel = channel
    this.tuneHz = clampTuneHz(tuneHz)
    this.onStatus('starting')

    const url = buildLiveAudioWavUrl(window.location, this.channel, this.tuneHz)
    try {
      // A plain probe first: it reads the status and the `X-Live-*` headers
      // (real values — sample rate, and for ultrasonic the tuning/bandwidth
      // the server actually landed on) without asking the `<audio>` element
      // to render an error page as if it were audio. The body is cancelled
      // immediately rather than consumed, so this does not itself count as
      // "a listener" for longer than it takes to read the headers, and the
      // `<audio>` element below opens its own connection.
      const probe = await fetch(url)
      if (!probe.ok) {
        let reason: string | undefined
        try {
          const payload = await probe.json()
          reason = typeof payload?.detail === 'string' ? payload.detail : undefined
        } catch {
          reason = undefined
        }
        this.onHello?.({
          channel: this.channel,
          sampleRate: 0,
          available: false,
          reason: reason ?? `HTTP ${probe.status}`,
        })
        this.onStatus('error', reason ?? `live audio unavailable (HTTP ${probe.status})`)
        return
      }
      void probe.body?.cancel()

      this.sampleRate = Number(probe.headers.get('X-Live-Sample-Rate') ?? 0) || this.sampleRate
      this.onHello?.({
        channel: this.channel,
        sampleRate: this.sampleRate,
        available: true,
        tuneHz: this.channel === 'ultrasonic'
          ? Number(probe.headers.get('X-Live-Tune-Hz') ?? this.tuneHz)
          : undefined,
        bandwidthHz: this.channel === 'ultrasonic'
          ? Number(probe.headers.get('X-Live-Bandwidth-Hz') ?? 0) || undefined
          : undefined,
      })

      const audio = new Audio()
      audio.preload = 'auto'
      audio.volume = Math.max(0, Math.min(1, this.volume))
      this.audioEl = audio
      this.stalls = 0
      this.waits = 0

      audio.oncanplay = () => this.onStatus('playing')
      audio.onplaying = () => this.onStatus('playing')
      audio.onstalled = () => {
        this.stalls += 1
      }
      audio.onwaiting = () => {
        this.waits += 1
      }
      audio.onerror = () => {
        this.onStatus('error', audio.error?.message ?? 'live audio stream failed')
      }
      audio.onended = () => {
        if (this.audioEl) void this.stop()
      }

      audio.src = url
      audio.play().catch((error) => {
        this.onStatus('error', error instanceof Error ? error.message : String(error))
      })

      this.reportTimer = window.setInterval(() => this.report(), 250)
    } catch (error) {
      this.onStatus('error', error instanceof Error ? error.message : String(error))
      await this.stop()
    }
  }

  /** Retune the live heterodyne monitor in place, over `POST
   *  /api/v1/live/tune` — the control path added alongside the chunked-WAV
   *  stream (`docs/api/DEBUG_UI_TRANSPORT.md`, ADR-022) because the WAV
   *  response has no socket to carry the old WebSocket's `{"type": "tune",
   *  ...}` message. The audio.wav connection itself is never touched: no
   *  stop, no reconnect, no gap. The server's heterodyne oscillator is
   *  shared by the whole station, so retuning it is enough — every
   *  ultrasonic listener, including this one, hears the new frequency on its
   *  next chunk.
   *
   *  A range slider fires this on every tick while dragging — dozens of
   *  times a second — so calls are throttled to at most one in-flight
   *  request per `TUNE_THROTTLE_MS`, trailing-edge, so the final value the
   *  slider settles on is always the last one sent even if intermediate
   *  ticks are coalesced. A no-op on the audible channel. */
  setTuneHz(hz: number): void {
    const clamped = clampTuneHz(hz)
    this.tuneHz = clamped
    if (this.channel !== 'ultrasonic') return
    this.pendingTuneHz = clamped
    if (this.tuneThrottleTimer !== null) return // a send is already scheduled/cooling down
    this.flushPendingTune()
  }

  private flushPendingTune(): void {
    const hz = this.pendingTuneHz
    this.pendingTuneHz = null
    if (hz !== null) void this.postTuneHz(hz)
    this.tuneThrottleTimer = window.setTimeout(() => {
      this.tuneThrottleTimer = null
      if (this.pendingTuneHz !== null) this.flushPendingTune()
    }, TUNE_THROTTLE_MS)
  }

  private async postTuneHz(hz: number): Promise<void> {
    try {
      const response = await fetch(buildLiveTuneUrl(window.location, hz), { method: 'POST' })
      if (!response.ok) return
      const payload = (await response.json()) as {
        tune_hz?: number
        bandwidth_hz?: number
        available?: boolean
      }
      // The server may have clamped this further (e.g. against the native
      // stream's Nyquist); reflect what it actually landed on, same as the
      // initial hello does.
      if (typeof payload.tune_hz === 'number') {
        this.tuneHz = payload.tune_hz
        this.onHello?.({
          channel: 'ultrasonic',
          sampleRate: this.sampleRate,
          available: payload.available ?? true,
          tuneHz: payload.tune_hz,
          bandwidthHz: payload.bandwidth_hz,
        })
      }
    } catch {
      // Best-effort: a dropped tune request just leaves the previous
      // frequency in place; the next slider tick retries.
    }
  }

  private report(): void {
    const audio = this.audioEl
    if (!audio) return
    let bufferedAheadS = 0
    if (audio.buffered.length > 0) {
      bufferedAheadS = Math.max(0, audio.buffered.end(audio.buffered.length - 1) - audio.currentTime)
    }
    const telemetry: AudioTelemetry = {
      readyState: audio.readyState,
      networkState: audio.networkState,
      bufferedAheadS,
      stalls: this.stalls,
      waits: this.waits,
      paused: audio.paused,
    }
    this.telemetry = telemetry
    this.onTelemetry(telemetry)
  }

  setVolume(value: number): void {
    this.volume = value
    if (this.audioEl) this.audioEl.volume = Math.max(0, Math.min(1, value))
  }

  async stop(): Promise<void> {
    if (this.reportTimer !== null) {
      window.clearInterval(this.reportTimer)
      this.reportTimer = null
    }
    if (this.tuneThrottleTimer !== null) {
      window.clearTimeout(this.tuneThrottleTimer)
      this.tuneThrottleTimer = null
    }
    this.pendingTuneHz = null
    const audio = this.audioEl
    this.audioEl = null
    this.telemetry = null
    if (audio) {
      audio.onerror = null
      audio.pause()
      // Clearing `src` and calling `load()` aborts the in-flight network
      // request, which is what lets the server notice the disconnect and
      // release its listener promptly (tested server-side) rather than
      // leaving the connection to time out on its own.
      audio.removeAttribute('src')
      audio.load()
    }
    this.onStatus('idle')
  }
}
