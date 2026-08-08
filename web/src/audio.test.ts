/** Pure-logic tests for the live audio client.
 *
 *  There is no component testing library here, so these stick to the pure
 *  functions: the tuning-frequency clamp and the WebSocket URL builder. Both
 *  are exactly the kind of thing that silently rots — an off-by-one in a
 *  query string doesn't throw, it just quietly tunes to the wrong band.
 */

import { describe, expect, it } from 'vitest'

import {
  buildLiveAudioUrl,
  buildLiveAudioWavUrl,
  clampTuneHz,
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
