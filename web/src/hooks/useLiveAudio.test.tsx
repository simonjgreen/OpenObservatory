/** Regression coverage for ADR-022: dragging the ultrasonic tune slider must
 *  retune in place via `LiveAudioPlayer.setTuneHz` and must NEVER stop/start
 *  the player. A prior version reconnected the `audio.wav` element on every
 *  slider tick, audible as a gap on every drag; the fix was a throttled
 *  `POST /api/v1/live/tune`. This test fails if that regression is
 *  reintroduced by a future refactor of this hook.
 */

// @vitest-environment jsdom

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLiveAudio } from './useLiveAudio'
import * as audioModule from '../audio'

describe('useLiveAudio', () => {
  let startSpy: ReturnType<typeof vi.spyOn>
  let stopSpy: ReturnType<typeof vi.spyOn>
  let setTuneHzSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    startSpy = vi
      .spyOn(audioModule.LiveAudioPlayer.prototype, 'start')
      .mockImplementation(async function (this: audioModule.LiveAudioPlayer) {
        // Simulate a playing player without a real <audio> element.
        Object.defineProperty(this, 'playing', { value: true, configurable: true })
      })
    stopSpy = vi
      .spyOn(audioModule.LiveAudioPlayer.prototype, 'stop')
      .mockImplementation(async () => {})
    setTuneHzSpy = vi
      .spyOn(audioModule.LiveAudioPlayer.prototype, 'setTuneHz')
      .mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('changeTuneHz calls setTuneHz, never start or stop, while playing on the ultrasonic channel', async () => {
    const { result } = renderHook(() => useLiveAudio())

    await act(async () => {
      result.current.changeChannel('ultrasonic')
      result.current.toggle() // start()
    })
    startSpy.mockClear()
    stopSpy.mockClear()

    act(() => {
      result.current.changeTuneHz(60000)
    })
    act(() => {
      result.current.changeTuneHz(61000)
    })
    act(() => {
      result.current.changeTuneHz(62000)
    })

    expect(setTuneHzSpy).toHaveBeenCalledWith(62000)
    expect(startSpy).not.toHaveBeenCalled()
    expect(stopSpy).not.toHaveBeenCalled()
  })

  it('does not retune the player while not playing (no channel reconnect either)', () => {
    const { result } = renderHook(() => useLiveAudio())

    act(() => {
      result.current.changeTuneHz(70000)
    })

    expect(setTuneHzSpy).not.toHaveBeenCalled()
    expect(startSpy).not.toHaveBeenCalled()
    expect(stopSpy).not.toHaveBeenCalled()
  })
})
