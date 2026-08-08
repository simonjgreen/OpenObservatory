/** Pure-logic tests for the live audio client, plus behavioural tests for
 *  `LiveAudioPlayer.setTuneHz`'s throttling (the fix for the live-sweep
 *  regression: retuning must never reconnect the audio.wav stream, and must
 *  not flood the server with a request per slider tick).
 *
 *  Most of this file sticks to pure functions and needs no DOM. The
 *  `setTuneHz` behavioural tests below need `window.setTimeout`/`fetch`, so
 *  this file runs under jsdom (`@vitest-environment` below) rather than the
 *  project's node default.
 */

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  buildLiveAudioUrl,
  buildLiveAudioWavUrl,
  buildLiveTuneUrl,
  clampTuneHz,
  LiveAudioPlayer,
  ULTRASONIC_TUNE_MAX_HZ,
  ULTRASONIC_TUNE_MIN_HZ,
} from './audio'

describe('clampTuneHz', () => {
  it('leaves an in-range value unchanged', () => {
    expect(clampTuneHz(45000)).toBe(45000)
  })

  it('clamps below the minimum up to it', () => {
    expect(clampTuneHz(1000)).toBe(ULTRASONIC_TUNE_MIN_HZ)
  })

  it('clamps above the maximum down to it', () => {
    expect(clampTuneHz(500000)).toBe(ULTRASONIC_TUNE_MAX_HZ)
  })

  it('falls back to the minimum for non-finite input, rather than propagating NaN', () => {
    expect(clampTuneHz(NaN)).toBe(ULTRASONIC_TUNE_MIN_HZ)
    expect(clampTuneHz(Infinity)).toBe(ULTRASONIC_TUNE_MIN_HZ)
  })
})

describe('buildLiveAudioUrl', () => {
  const httpLocation = { protocol: 'http:', host: 'station.example:8080' }
  const httpsLocation = { protocol: 'https:', host: 'station.local' }

  it('defaults to the audible channel with no query string', () => {
    expect(buildLiveAudioUrl(httpLocation, 'audible')).toBe(
      'ws://station.example:8080/api/v1/live/audio',
    )
  })

  it('adds channel=ultrasonic and a rounded tune_hz for the ultrasonic channel', () => {
    const url = buildLiveAudioUrl(httpLocation, 'ultrasonic', 44999.6)
    expect(url).toBe('ws://station.example:8080/api/v1/live/audio?channel=ultrasonic&tune_hz=45000')
  })

  it('clamps an out-of-range tuning frequency before it reaches the query string', () => {
    const url = buildLiveAudioUrl(httpLocation, 'ultrasonic', 999999)
    expect(url).toContain(`tune_hz=${ULTRASONIC_TUNE_MAX_HZ}`)
  })

  it('omits tune_hz when none was given, so the server default applies', () => {
    const url = buildLiveAudioUrl(httpLocation, 'ultrasonic')
    expect(url).toBe('ws://station.example:8080/api/v1/live/audio?channel=ultrasonic')
  })

  it('uses wss over an https origin', () => {
    expect(buildLiveAudioUrl(httpsLocation, 'audible')).toBe(
      'wss://station.local/api/v1/live/audio',
    )
  })
})

describe('buildLiveAudioWavUrl', () => {
  const httpLocation = { protocol: 'http:', host: 'station.example:8080' }
  const httpsLocation = { protocol: 'https:', host: 'station.local' }

  it('defaults to the audible channel with no query string', () => {
    expect(buildLiveAudioWavUrl(httpLocation, 'audible')).toBe(
      'http://station.example:8080/api/v1/live/audio.wav',
    )
  })

  it('adds channel=ultrasonic and a rounded tune_hz for the ultrasonic channel', () => {
    const url = buildLiveAudioWavUrl(httpLocation, 'ultrasonic', 44999.6)
    expect(url).toBe(
      'http://station.example:8080/api/v1/live/audio.wav?channel=ultrasonic&tune_hz=45000',
    )
  })

  it('clamps an out-of-range tuning frequency before it reaches the query string', () => {
    const url = buildLiveAudioWavUrl(httpLocation, 'ultrasonic', 999999)
    expect(url).toContain(`tune_hz=${ULTRASONIC_TUNE_MAX_HZ}`)
  })

  it('omits tune_hz when none was given, so the server default applies', () => {
    const url = buildLiveAudioWavUrl(httpLocation, 'ultrasonic')
    expect(url).toBe('http://station.example:8080/api/v1/live/audio.wav?channel=ultrasonic')
  })

  it('uses https over an https origin, never wss — this is a plain HTTP stream', () => {
    expect(buildLiveAudioWavUrl(httpsLocation, 'audible')).toBe(
      'https://station.local/api/v1/live/audio.wav',
    )
  })
})

describe('buildLiveTuneUrl', () => {
  const httpLocation = { protocol: 'http:', host: 'station.example:8080' }
  const httpsLocation = { protocol: 'https:', host: 'station.local' }

  it('builds the control endpoint with a rounded tune_hz, never a socket scheme', () => {
    expect(buildLiveTuneUrl(httpLocation, 44999.6)).toBe(
      'http://station.example:8080/api/v1/live/tune?tune_hz=45000',
    )
  })

  it('clamps an out-of-range tuning frequency before it reaches the query string', () => {
    expect(buildLiveTuneUrl(httpLocation, 999999)).toContain(`tune_hz=${ULTRASONIC_TUNE_MAX_HZ}`)
  })

  it('uses https, never wss — this is a plain HTTP control call', () => {
    expect(buildLiveTuneUrl(httpsLocation, 45000)).toBe(
      'https://station.local/api/v1/live/tune?tune_hz=45000',
    )
  })
})

describe('LiveAudioPlayer.setTuneHz — in-place retune over the WAV path', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.useFakeTimers()
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ tune_hz: 45000, bandwidth_hz: 5000, available: true }),
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  function ultrasonicPlayer(): LiveAudioPlayer {
    const player = new LiveAudioPlayer(
      () => {},
      () => {},
    )
    // Reach into private state via a cast rather than going through a real
    // `start()` (which needs a live `<audio>` element / network probe): the
    // throttling logic under test only depends on `channel`, not on being
    // "playing".
    ;(player as unknown as { channel: string }).channel = 'ultrasonic'
    return player
  }

  it('never opens a WebSocket or reconnects the audio element — it only calls fetch', () => {
    const player = ultrasonicPlayer()
    player.setTuneHz(42000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toContain('/api/v1/live/tune')
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' })
  })

  it('is a no-op on the audible channel', () => {
    const player = new LiveAudioPlayer(
      () => {},
      () => {},
    )
    player.setTuneHz(42000)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends the first tick immediately, then throttles a rapid sweep to one in-flight request per window', () => {
    const player = ultrasonicPlayer()
    // A sweep: many ticks in quick succession, as a range input fires while dragging.
    for (let hz = 20000; hz <= 40000; hz += 2000) player.setTuneHz(hz)
    // Only the leading tick has gone out so far; the rest are coalesced.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toContain('tune_hz=20000')
  })

  it('always sends the final settled value once the sweep stops, even though intermediate ticks were coalesced', async () => {
    const player = ultrasonicPlayer()
    for (let hz = 20000; hz <= 40000; hz += 2000) player.setTuneHz(hz)
    await vi.advanceTimersByTimeAsync(80)
    // The trailing edge of the throttle window fires the last pending value.
    const urls = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(urls.at(-1)).toContain('tune_hz=40000')
  })

  it('reflects a server-clamped tuning frequency back through onHello', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ tune_hz: 38000, bandwidth_hz: 5000, available: true }),
    })
    const onHello = vi.fn()
    const player = new LiveAudioPlayer(
      () => {},
      () => {},
      onHello,
    )
    ;(player as unknown as { channel: string }).channel = 'ultrasonic'
    player.setTuneHz(999999)
    await vi.advanceTimersByTimeAsync(0)
    expect(onHello).toHaveBeenCalledWith(
      expect.objectContaining({ channel: 'ultrasonic', tuneHz: 38000, available: true }),
    )
  })

  it('stop() cancels a pending throttled tune so it is never sent after teardown', async () => {
    const player = ultrasonicPlayer()
    player.setTuneHz(20000)
    player.setTuneHz(30000) // queued, pending the throttle window
    await player.stop()
    await vi.advanceTimersByTimeAsync(200)
    // Only the leading-edge send from the first tick went out; the queued
    // second value never fires because stop() cleared it.
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
