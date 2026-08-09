/** Where in the picture the sound currently leaving the speakers actually is.
 *
 *  The scrolling spectrogram draws audio the instant a column arrives; the
 *  `<audio>` element (ADR-019) plays the same audio some seconds later, after
 *  the browser's own media buffering. So the picture and the sound disagree,
 *  and until this module existed nothing on screen said by how much.
 *
 *  This is a measurement, not a decoration. A marker that is confidently in the
 *  wrong place is worse than no marker at all, because the operator will use it
 *  to decide whether the call he just heard is the shape he is looking at. So
 *  everything here is explicit about which parts are read from the media
 *  element and which are estimated, and the result carries the width of the
 *  interval it is willing to claim rather than a bare number.
 *
 *  ## The model
 *
 *  Everything is in station UTC, the same clock the spectrogram's columns and
 *  the detection overlay are timestamped in, so the marker lands on the same
 *  timeline as everything else drawn on that canvas. `clockSkewS` (server_utc
 *  minus browser clock, measured at the live socket's `hello`) converts.
 *
 *  Two independent estimators of the station-UTC of the sample at the speakers:
 *
 *  **B, buffer-anchored** — the newest sample the element holds arrived from
 *  the station a moment ago, so it is roughly `now`; the play cursor sits
 *  `bufferedAhead` seconds behind that:
 *
 *      T_play = now − bufferedAheadS − outputLatency
 *
 *  **A, epoch-anchored** — media time 0 is the first sample the server sent,
 *  which (the handler drains its queue before streaming, see
 *  `live_audio_wav`) is the audio that was live when the element opened the
 *  stream:
 *
 *      T_play = streamOpened + currentTime − outputLatency
 *
 *  On a stream that behaves perfectly these are algebraically identical: the
 *  source is real time, so `bufferedEnd = now − streamOpened`, and
 *  `currentTime = bufferedEnd − bufferedAhead`. They part company for exactly
 *  two reasons, and both push the same way:
 *
 *  - the browser reports `buffered` staler than the data it has actually
 *    received, which makes B too new;
 *  - the server dropped chunks for this listener (the bounded queue sheds the
 *    oldest), which shortens the media timeline and makes A too old.
 *
 *  **From inside the browser those two are indistinguishable** — both appear
 *  as the same gap, with the same sign. That is the real limit on this
 *  measurement, and it is why the pair is treated as a *bracket* rather than a
 *  preferred estimate with a warning attached: the answer is reported as the
 *  midpoint, and the whole bracket is claimed as uncertainty. Neither
 *  estimator is ever trusted alone.
 *
 *  This is not an assumption. Measured against a ground-truth harness — a
 *  local server streaming the same endless-WAV shape and recording the instant
 *  it wrote every chunk, played by real Chromium — the bracket contained the
 *  true playhead in **209 of 209** samples, with A within +0.02 s of truth and
 *  B, on that browser, consistently **0.17–0.46 s** newer than truth. The
 *  midpoint's residual error was +0.02 s median. See ADR-051 for the full
 *  numbers and for what that harness could *not* measure.
 *
 *  Note what needs no modelling: an output device whose clock drifts against
 *  the 48 kHz source simply accumulates or loses buffer, and both estimators
 *  see it, because `bufferedAhead` is measured against `currentTime` rather
 *  than assumed.
 *
 *  ## What is measured and what is estimated
 *
 *  Measured, from `HTMLMediaElement` and the live socket:
 *  `bufferedAheadS`, `currentTimeS`, `paused`, `readyState`, whether
 *  `currentTime` advanced since the last sample, and `clockSkewS`. The gap
 *  between the two estimators is measured too, per sample, rather than being
 *  a constant somebody guessed.
 *
 *  Estimated, with the bound declared below and folded into `uncertaintyS`:
 *  only the browser/OS output buffer between `currentTime` and the speaker. A
 *  media element exposes no equivalent of `AudioContext.outputLatency`, and
 *  Web Audio is not available to this UI at all (ADR-019), so this one term
 *  cannot be read from anywhere and is carried as an interval.
 */

/** The browser/OS output buffer between the play cursor and the speaker.
 *  Not exposed for a media element; this is the range a media-element output
 *  path plausibly sits in, and half its width is charged to `uncertaintyS`. */
export const OUTPUT_LATENCY_RANGE_S = { min: 0.02, max: 0.2 }

/** `clockSkewS` is measured from a single `hello` without correcting for the
 *  round trip, so it carries at most half an RTT of error, plus whatever the
 *  browser's own clock is doing. Generous for a LAN. */
export const CLOCK_SKEW_UNCERTAINTY_S = 0.05

/** `HTMLMediaElement.HAVE_FUTURE_DATA`. Below this the element cannot play the
 *  current position forward, i.e. it is rebuffering. */
const HAVE_FUTURE_DATA = 3

const midpoint = (range: { min: number; max: number }) => (range.min + range.max) / 2
const halfWidth = (range: { min: number; max: number }) => (range.max - range.min) / 2

/** The base half-width, before any estimator disagreement is added: everything
 *  we know we do not know about a healthy stream. */
export const BASE_UNCERTAINTY_S =
  halfWidth(OUTPUT_LATENCY_RANGE_S) + CLOCK_SKEW_UNCERTAINTY_S

/** Past this bound a single line stops being a fair summary of the interval,
 *  and only the band is drawn: "somewhere in here" is the honest picture of an
 *  estimate this loose, and a hairline through the middle of it is not. */
export const CENTRE_LINE_MAX_UNCERTAINTY_S = 1.0

/** One sample of the media element's state. Structurally a subset of
 *  `AudioTelemetry` (`web/src/audio.ts`), which is where the real readings come
 *  from; kept as its own type so this module stays pure and testable without
 *  an `<audio>` element. */
export interface PlayheadSample {
  /** Seconds decoded/received ahead of the play cursor. */
  bufferedAheadS: number
  /** `HTMLMediaElement.currentTime`. */
  currentTimeS: number
  /** Browser wall clock (`Date.now() / 1000`) when the element was pointed at
   *  the stream, i.e. when media time 0 started arriving. */
  streamOpenedEpochS: number
  /** Browser wall clock when this sample was taken. */
  sampledEpochS: number
  /** `performance.now()` when this sample was taken. Used to extrapolate
   *  between samples without depending on a wall clock that may step. */
  sampledPerfMs: number
  paused: boolean
  readyState: number
  /** Whether `currentTime` advanced since the previous sample. */
  advancing: boolean
}

export interface PlayheadEstimate {
  /** Station UTC of the audio leaving the speakers at `atPerfMs`. */
  utcS: number
  /** Half-width of the interval this estimate is willing to claim, seconds. */
  uncertaintyS: number
  /** How far the two independent estimators disagreed, seconds. Surfaced so a
   *  misbehaving stream is visible as a number rather than only as a wider
   *  band. */
  disagreementS: number
  /** `performance.now()` this estimate was anchored at. */
  atPerfMs: number
}

/** Station UTC of the sound at the speakers, or `null` when there is nothing
 *  honest to say.
 *
 *  `null` — meaning *draw no marker at all*, never a stale one — when the
 *  element is paused, has not started, is rebuffering (`readyState` below
 *  `HAVE_FUTURE_DATA`), or when `currentTime` did not advance since the
 *  previous sample. A frozen playhead is not where the sound is; it is where
 *  the sound stopped.
 */
export function estimatePlayhead(
  sample: PlayheadSample,
  clockSkewS: number,
): PlayheadEstimate | null {
  if (sample.paused) return null
  if (sample.readyState < HAVE_FUTURE_DATA) return null
  if (!sample.advancing) return null
  if (!Number.isFinite(sample.currentTimeS) || sample.currentTimeS <= 0) return null

  // The one term neither estimator can read is charged identically to both, so
  // that the gap below is purely what the *measurements* disagree about.
  const outputLatencyS = midpoint(OUTPUT_LATENCY_RANGE_S)
  const nowStationS = sample.sampledEpochS + clockSkewS

  const bufferAnchoredS = nowStationS - sample.bufferedAheadS - outputLatencyS
  const epochAnchoredS =
    sample.streamOpenedEpochS + clockSkewS + sample.currentTimeS - outputLatencyS

  const disagreementS = Math.abs(bufferAnchoredS - epochAnchoredS)
  return {
    // The midpoint of the bracket, not one end of it. Which end is closer to
    // the truth depends on whether the gap came from a lazily-reported
    // `buffered` or from dropped chunks, and nothing visible from in here can
    // tell those apart.
    utcS: (bufferAnchoredS + epochAnchoredS) / 2,
    // Half the gap reaches either end of the bracket; the base covers the
    // output buffer and the clock skew on top of that.
    uncertaintyS: BASE_UNCERTAINTY_S + disagreementS / 2,
    disagreementS,
    atPerfMs: sample.sampledPerfMs,
  }
}

/** How far behind the newest spectrogram column the sound at the speakers is,
 *  at `nowPerfMs`.
 *
 *  Both the estimate and `newestColumnUtcS` are station UTC, so this is a
 *  subtraction of two quantities on the same timeline rather than a parallel
 *  wall-clock guess — the visual pipeline's own lag cancels out exactly instead
 *  of being modelled.
 *
 *  The estimate is advanced by real elapsed time since it was taken, because
 *  audio plays at one second per second: telemetry is sampled four times a
 *  second and the overlay draws sixty, and holding the playhead still between
 *  samples while the live edge glides on produces a visible 250 ms sawtooth.
 */
export function playheadSecondsAgo(
  estimate: PlayheadEstimate,
  newestColumnUtcS: number,
  nowPerfMs: number,
): number {
  const advancedUtcS = estimate.utcS + Math.max(0, nowPerfMs - estimate.atPerfMs) / 1000
  return newestColumnUtcS - advancedUtcS
}

export interface PlayheadBand {
  /** Seconds ago of the centre of the estimate, unclamped — what the label
   *  should say. */
  centreSecondsAgo: number
  /** Drawable band edges, clamped into the visible window. `oldest` is the
   *  larger seconds-ago (further from the live edge). */
  oldestSecondsAgo: number
  newestSecondsAgo: number
}

/** The band to draw, or `null` when the whole interval lies outside the
 *  selected history window.
 *
 *  Off-scale draws nothing rather than pinning the marker to an edge: a line at
 *  the edge of the window asserts a position, and the position it asserts is
 *  wrong. The label still reports the number, which is the honest half of the
 *  answer — "the sound you are hearing is older than everything on screen".
 */
export function playheadBand(
  secondsAgo: number,
  uncertaintyS: number,
  windowSeconds: number,
): PlayheadBand | null {
  const oldest = secondsAgo + uncertaintyS
  const newest = secondsAgo - uncertaintyS
  if (oldest < 0 || newest > windowSeconds) return null
  return {
    centreSecondsAgo: secondsAgo,
    oldestSecondsAgo: Math.min(oldest, windowSeconds),
    newestSecondsAgo: Math.max(newest, 0),
  }
}

/** The words next to the marker. Says what is claimed and how well it is
 *  known, in that order, and never rounds the uncertainty away. */
export function formatPlayheadLabel(
  secondsAgo: number,
  uncertaintyS: number,
  windowSeconds: number,
): string {
  const tolerance = `±${uncertaintyS.toFixed(1)} s`
  if (secondsAgo < -uncertaintyS) {
    return `hearing ${Math.abs(secondsAgo).toFixed(1)} s ahead of the newest column ${tolerance}`
  }
  if (secondsAgo - uncertaintyS > windowSeconds) {
    return `hearing ${secondsAgo.toFixed(1)} s ago — older than this window ${tolerance}`
  }
  return `hearing ${Math.max(0, secondsAgo).toFixed(1)} s ago ${tolerance}`
}
