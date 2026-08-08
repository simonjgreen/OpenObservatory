/** The GO LIVE listen control: player lifecycle, volume/channel/tune state
 *  and the callbacks the `ListenControl` component drives.
 *
 *  `changeTuneHz` is the one function in this file with a documented
 *  regression behind it (ADR-022): retuning the ultrasonic heterodyne must
 *  call `player.setTuneHz`, a throttled in-place `POST /api/v1/live/tune`,
 *  and must NEVER go through `player.start`/`stop` — a previous version
 *  reconnected the `audio.wav` stream on every slider tick, which is audible
 *  as a gap on every drag. `tuneHz` must also never appear in the dependency
 *  array of the effect that calls `player.start` for the same reason. There
 *  is no such effect here — `start`/`stop` are only ever called from
 *  `toggleAudio` and `changeChannel`, both of which read `tuneHz` fresh via
 *  the closure over current state rather than re-running on every tune
 *  change — see `useLiveAudio.test.tsx` for the regression test.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  LiveAudioPlayer,
  clampTuneHz,
  type AudioHelloInfo,
  type AudioStatus,
  type AudioTelemetry,
  type LiveAudioChannel,
} from '../audio'

export interface LiveAudio {
  status: AudioStatus
  telemetry: AudioTelemetry | null
  detail: string | undefined
  volume: number
  channel: LiveAudioChannel
  tuneHz: number
  hello: AudioHelloInfo | null
  playing: boolean
  toggle: () => void
  changeChannel: (value: LiveAudioChannel) => void
  changeTuneHz: (value: number) => void
  changeVolume: (value: number) => void
}

export function useLiveAudio(): LiveAudio {
  const [status, setStatus] = useState<AudioStatus>('idle')
  const [telemetry, setTelemetry] = useState<AudioTelemetry | null>(null)
  const [detail, setDetail] = useState<string | undefined>()
  const [volume, setVolume] = useState(0.9)
  // Audible is the unconditional default: pressing GO LIVE must always
  // listen to the audible mix unless the operator explicitly switches.
  const [channel, setChannel] = useState<LiveAudioChannel>('audible')
  const [tuneHz, setTuneHz] = useState(45000)
  const [hello, setHello] = useState<AudioHelloInfo | null>(null)

  const player = useMemo(
    () =>
      new LiveAudioPlayer(
        (state, d) => {
          setStatus(state)
          setDetail(d)
        },
        (t) => setTelemetry(t),
        (h) => {
          setHello(h)
          // The server may have clamped an out-of-range request; reflect
          // what it actually landed on rather than what was asked for.
          if (h.tuneHz !== undefined) setTuneHz(h.tuneHz)
        },
      ),
    [],
  )

  useEffect(() => () => void player.stop(), [player])

  const toggle = useCallback(() => {
    if (player.playing) void player.stop()
    else void player.start(volume, channel, tuneHz)
  }, [player, volume, channel, tuneHz])

  // Switching channel means pointing the `<audio>` element at a new URL, so
  // this always reconnects while playing — there is no in-place channel
  // switch over a chunked-WAV stream.
  const changeChannel = useCallback(
    (value: LiveAudioChannel) => {
      setChannel(value)
      setHello(null)
      if (player.playing) {
        void player.stop().then(() => void player.start(volume, value, tuneHz))
      }
    },
    [player, volume, tuneHz],
  )

  const changeTuneHz = useCallback(
    (value: number) => {
      const clamped = clampTuneHz(value)
      setTuneHz(clamped)
      // Retunes the server-side heterodyne in place over `POST
      // /api/v1/live/tune` — the audio.wav stream itself is never touched,
      // so sweeping the slider does not reconnect or gap. See
      // `LiveAudioPlayer.setTuneHz`, which also throttles this: a range
      // input fires on every drag tick.
      if (player.playing && channel === 'ultrasonic') player.setTuneHz(clamped)
    },
    [player, channel],
  )

  const changeVolume = useCallback(
    (value: number) => {
      setVolume(value)
      player.setVolume(value)
    },
    [player],
  )

  return {
    status,
    telemetry,
    detail,
    volume,
    channel,
    tuneHz,
    hello,
    playing: player.playing,
    toggle,
    changeChannel,
    changeTuneHz,
    changeVolume,
  }
}
