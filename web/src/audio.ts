/** Low-latency live audio playback.
 *
 *  Why not `<audio src=...>`: a media element buffers seconds before it starts,
 *  which is useless when the point is to hear what the spectrogram is showing
 *  *now*.
 *
 *  Why not an AudioWorklet, which would be the modern answer: `AudioWorklet` is
 *  only exposed in a **secure context**, and this page is served over plain HTTP
 *  from a LAN address (`http://station.example:8080`). Only `localhost` gets the
 *  secure-context exemption, so on the real deployment `context.audioWorklet` is
 *  `undefined` and a worklet implementation cannot start at all. Putting the debug
 *  UI behind TLS to gain a worklet would trade a working feature for a certificate
 *  warning on every visit.
 *
 *  So: incoming int16 chunks are turned into short `AudioBuffer`s and scheduled
 *  back-to-back on an explicit playback cursor. This is the pre-worklet technique
 *  and it works everywhere, with latency we control directly rather than infer.
 *
 *  The latency budget is explicit:
 *   - `targetLatencyMs` is how far ahead of the audio clock the cursor is placed
 *     when playback starts or recovers. Too small and every network hiccup is an
 *     audible gap; too large and it stops being live. ~150 ms suits LAN Wi-Fi.
 *   - If the cursor falls behind the clock, audio arrived too late to schedule:
 *     that is an underrun, and the cursor is re-seated ahead of the clock.
 *   - If the cursor drifts more than `maxLatencyMs` ahead, this listener is
 *     accumulating delay rather than tracking live, so chunks are dropped until it
 *     catches up. Both are counted and surfaced, because silently growing latency
 *     is the failure mode that makes a "live" feed quietly useless.
 */

export type LiveAudioChannel = 'audible' | 'ultrasonic'

/** What the server actually gave us, echoed back from the `audio-hello` frame
 *  so the UI never has to guess the tuning it landed on (a request near the
 *  band edge gets clamped server-side) or whether the channel even works for
 *  this stream's native rate. */
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

/** Build the `/api/v1/live/audio` URL for a given channel. Pure so the query
 *  string logic — the part most likely to silently rot — is unit tested
 *  without needing a real `window.location` or WebSocket. */
export function buildLiveAudioUrl(
  location: { protocol: string; host: string },
  channel: LiveAudioChannel,
  tuneHz?: number,
): string {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const params = new URLSearchParams()
  if (channel === 'ultrasonic') {
    params.set('channel', 'ultrasonic')
    if (tuneHz !== undefined) params.set('tune_hz', String(Math.round(clampTuneHz(tuneHz))))
  }
  const query = params.toString()
  return `${scheme}//${location.host}/api/v1/live/audio${query ? `?${query}` : ''}`
}

export interface AudioTelemetry {
  bufferedFrames: number
  bufferedMs: number
  underruns: number
  overflows: number
  resyncs: number
  framesPlayed: number
  bytesReceived: number
  sampleRate: number
  contextLatencyMs: number
  /** AudioContext state. 'suspended' means the browser blocked playback. */
  contextState: string
  /** RMS of what is actually reaching the speakers, in dBFS. */
  outputRmsDbfs: number
  outputPeakDbfs: number
}

export type AudioStatus = 'idle' | 'starting' | 'playing' | 'error'

export class LiveAudioPlayer {
  private context: AudioContext | null = null
  private gain: GainNode | null = null
  private limiter: DynamicsCompressorNode | null = null
  private analyser: AnalyserNode | null = null
  private analyserBuffer: Float32Array<ArrayBuffer> | null = null
  /** Monitor make-up gain in dB, on top of the volume control. */
  private monitorGainDb = 24
  private socket: WebSocket | null = null
  /** Playback cursor in AudioContext time. */
  private nextTime = 0
  private started = false
  private bytesReceived = 0
  private framesPlayed = 0
  private underruns = 0
  private overflows = 0
  private resyncs = 0
  private sampleRate = 48000
  private reportTimer: number | null = null
  private channel: LiveAudioChannel = 'audible'
  private tuneHz = 45000

  telemetry: AudioTelemetry | null = null

  constructor(
    private readonly onStatus: (status: AudioStatus, detail?: string) => void,
    private readonly onTelemetry: (telemetry: AudioTelemetry) => void,
    private volume = 1,
    private readonly targetLatencyMs = 120,
    /** Called once per connection with what the server actually gave us —
     *  see `AudioHelloInfo`. Optional so existing callers are unaffected. */
    private readonly onHello?: (info: AudioHelloInfo) => void,
    /** Chunks are dropped above this, so the steady state sits just under it.
     *
     *  This must be only a little above the target, not a generous ceiling: each
     *  dropped chunk reduces the lead by one chunk duration, so whatever this
     *  threshold is *becomes* the resting latency. Set to 600 ms initially, the
     *  feed settled at 575 ms — technically playing, but no longer live. */
    private readonly dropAboveMs = 200,
  ) {}

  get playing(): boolean {
    return this.context !== null
  }

  async start(volume = 1, channel: LiveAudioChannel = 'audible', tuneHz = 45000): Promise<void> {
    if (this.context) return
    this.volume = volume
    this.channel = channel
    this.tuneHz = clampTuneHz(tuneHz)
    this.onStatus('starting')
    try {
      // 48 kHz matches the station's derived stream exactly, so no browser-side
      // resampling sits between us and the audio being inspected.
      const context = new AudioContext({ sampleRate: 48000, latencyHint: 'interactive' })
      this.context = context
      this.sampleRate = context.sampleRate
      this.nextTime = 0
      this.started = false
      this.bytesReceived = 0
      this.framesPlayed = 0
      this.underruns = 0
      this.overflows = 0
      this.resyncs = 0

      // Signal chain: buffers -> make-up gain -> limiter -> analyser -> speakers.
      //
      // The make-up gain is not a nicety. A quiet garden sits around -45 dBFS, so
      // at unity the live feed is inaudible on laptop speakers and looks broken
      // even though every counter says it is working. The limiter then stops a
      // sudden close call being amplified into something painful.
      const gain = context.createGain()
      gain.gain.value = volume * Math.pow(10, this.monitorGainDb / 20)
      this.gain = gain

      const limiter = context.createDynamicsCompressor()
      limiter.threshold.value = -6
      limiter.knee.value = 6
      limiter.ratio.value = 12
      limiter.attack.value = 0.003
      limiter.release.value = 0.15
      this.limiter = limiter

      const analyser = context.createAnalyser()
      analyser.fftSize = 2048
      this.analyser = analyser
      this.analyserBuffer = new Float32Array(new ArrayBuffer(analyser.fftSize * 4))

      gain.connect(limiter)
      limiter.connect(analyser)
      analyser.connect(context.destination)

      // A user gesture triggered this call, so resume() is permitted.
      await context.resume()

      const url = buildLiveAudioUrl(window.location, this.channel, this.tuneHz)
      const socket = new WebSocket(url)
      socket.binaryType = 'arraybuffer'
      this.socket = socket

      socket.onopen = () => this.onStatus('playing')
      socket.onerror = () => this.onStatus('error', 'live audio socket failed')
      socket.onclose = () => {
        if (this.context) void this.stop()
      }
      socket.onmessage = (event) => {
        if (typeof event.data === 'string') {
          this.handleHello(event.data)
          return
        }
        this.enqueue(event.data as ArrayBuffer)
      }

      this.reportTimer = window.setInterval(() => this.report(), 250)
    } catch (error) {
      this.onStatus('error', error instanceof Error ? error.message : String(error))
      await this.stop()
    }
  }

  private handleHello(raw: string): void {
    let parsed: unknown
    try {
      parsed = JSON.parse(raw)
    } catch {
      return
    }
    if (!parsed || typeof parsed !== 'object' || (parsed as { type?: string }).type !== 'audio-hello') {
      return
    }
    const payload = parsed as {
      channel?: LiveAudioChannel
      sample_rate?: number
      available?: boolean
      reason?: string
      tune_hz?: number
      bandwidth_hz?: number
    }
    this.onHello?.({
      channel: payload.channel ?? this.channel,
      sampleRate: payload.sample_rate ?? this.sampleRate,
      available: payload.available ?? true,
      reason: payload.reason,
      tuneHz: payload.tune_hz,
      bandwidthHz: payload.bandwidth_hz,
    })
  }

  /** Retune the live heterodyne monitor without reconnecting. A no-op on the
   *  audible channel or before the socket is open. */
  setTuneHz(hz: number): void {
    this.tuneHz = clampTuneHz(hz)
    if (this.channel !== 'ultrasonic') return
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return
    this.socket.send(JSON.stringify({ type: 'tune', tune_hz: this.tuneHz }))
  }

  private enqueue(payload: ArrayBuffer): void {
    const context = this.context
    const gain = this.gain
    if (!context || !gain) return
    this.bytesReceived += payload.byteLength

    const samples = new Int16Array(payload)
    if (samples.length === 0) return

    const buffer = context.createBuffer(1, samples.length, this.sampleRate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768

    const now = context.currentTime
    const targetLead = this.targetLatencyMs / 1000
    const dropLead = Math.max(this.dropAboveMs / 1000, targetLead + buffer.duration)
    // Far beyond the drop threshold there is no point trickling chunks away one at
    // a time — the connection handed us a backlog (the station backfills
    // spectrogram history on connect, which can briefly delay the audio consumer).
    // Re-seat straight to the target and skip the stale audio.
    const resyncLead = Math.max(dropLead * 2, targetLead + 0.25)

    if (!this.started || this.nextTime < now + 0.002) {
      // Either the first chunk, or audio arrived too late to schedule where the
      // cursor had reached. Re-seat the cursor ahead of the clock.
      if (this.started) this.underruns += 1
      this.nextTime = now + targetLead
      this.started = true
    } else if (this.nextTime > now + resyncLead) {
      this.resyncs += 1
      this.nextTime = now + targetLead
    } else if (this.nextTime > now + dropLead) {
      // Slightly ahead: drop this chunk so the lead converges back to the target
      // one chunk duration at a time.
      this.overflows += 1
      return
    }

    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(gain)
    source.start(this.nextTime)
    this.nextTime += buffer.duration
    this.framesPlayed += samples.length
  }

  private report(): void {
    const context = this.context
    if (!context) return
    const bufferedS = Math.max(0, this.nextTime - context.currentTime)

    let rms = 0
    let peak = 0
    if (this.analyser && this.analyserBuffer) {
      this.analyser.getFloatTimeDomainData(this.analyserBuffer)
      let sum = 0
      for (const sample of this.analyserBuffer) {
        sum += sample * sample
        peak = Math.max(peak, Math.abs(sample))
      }
      rms = Math.sqrt(sum / this.analyserBuffer.length)
    }
    const toDb = (value: number) => 20 * Math.log10(Math.max(value, 1e-7))
    const telemetry: AudioTelemetry = {
      bufferedFrames: Math.round(bufferedS * this.sampleRate),
      bufferedMs: bufferedS * 1000,
      underruns: this.underruns,
      overflows: this.overflows,
      resyncs: this.resyncs,
      framesPlayed: this.framesPlayed,
      bytesReceived: this.bytesReceived,
      sampleRate: this.sampleRate,
      contextLatencyMs: ((context.baseLatency ?? 0) + (context.outputLatency ?? 0)) * 1000,
      contextState: context.state,
      outputRmsDbfs: toDb(rms),
      outputPeakDbfs: toDb(peak),
    }
    this.telemetry = telemetry
    this.onTelemetry(telemetry)
  }

  setVolume(value: number): void {
    this.volume = value
    this.applyGain()
  }

  setMonitorGainDb(db: number): void {
    this.monitorGainDb = db
    this.applyGain()
  }

  get monitorGain(): number {
    return this.monitorGainDb
  }

  private applyGain(): void {
    if (!this.gain || !this.context) return
    const target = this.volume * Math.pow(10, this.monitorGainDb / 20)
    // Ramp rather than jump, so a slider drag does not click.
    this.gain.gain.setTargetAtTime(target, this.context.currentTime, 0.02)
  }

  async stop(): Promise<void> {
    if (this.reportTimer !== null) {
      window.clearInterval(this.reportTimer)
      this.reportTimer = null
    }
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.onclose = null
      socket.close()
    }
    this.gain?.disconnect()
    this.gain = null
    this.limiter?.disconnect()
    this.limiter = null
    this.analyser?.disconnect()
    this.analyser = null
    this.analyserBuffer = null
    const context = this.context
    this.context = null
    this.telemetry = null
    this.started = false
    if (context) await context.close().catch(() => undefined)
    this.onStatus('idle')
  }
}
