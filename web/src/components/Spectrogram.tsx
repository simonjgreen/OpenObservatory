/** Scrolling live spectrogram, Merlin-style: newest audio at the right edge,
 *  history flowing left.
 *
 *  Rendering strategy: columns are written into an offscreen canvas used as a ring
 *  buffer, one pixel column each, and every animation frame blits at most two
 *  slices of that ring onto the visible canvas. The alternative — rebuilding an
 *  ImageData of the whole viewport each frame — is hundreds of thousands of JS
 *  pixel writes per frame and drops the display to a crawl on a modest client.
 *  Here the per-frame cost is two GPU-accelerated `drawImage` calls regardless of
 *  how much history is on screen.
 *
 *  The frequency axis is logarithmic because the server's bins are, and gridlines
 *  are placed by looking up each label frequency in the bin centre table rather
 *  than by assuming a scale.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { formatDetectionTitleText } from './detectionTitle'
import type { ColumnBatch, Detection, SpectrogramSpec } from '../types'
import {
  frequencyToPixel,
  pixelToFrequency,
  pixelToSecondsAgo,
  ringTransform,
  spanRect,
  type Band,
  type Orientation,
} from './geometry'

export type Palette = 'observatory' | 'merlin' | 'ice'

const GRID_HZ = [50, 100, 200, 500, 1000, 2000, 5000, 10_000, 20_000, 50_000, 100_000]

/** Colour ramp anchors, as [position, r, g, b].
 *
 *  Every ramp must start at (near) black and rise monotonically in perceived
 *  brightness. The first version of this file used a hand-rolled sinusoidal ramp
 *  whose blue channel was 64 at zero and whose red channel saturated by a third of
 *  the way up; against a real garden noise floor that painted the entire display
 *  a flat magenta and hid everything. Perceptually ordered ramps are not a
 *  cosmetic preference — the picture is the diagnostic.
 */
const RAMPS: Record<Palette, Array<[number, number, number, number]>> = {
  // Inferno: the usual choice for spectrograms, and hard to beat for showing
  // faint structure just above the floor.
  observatory: [
    [0.0, 0, 0, 4],
    [0.1, 22, 11, 57],
    [0.2, 66, 10, 104],
    [0.3, 106, 23, 110],
    [0.4, 147, 38, 103],
    [0.5, 188, 55, 84],
    [0.6, 221, 81, 58],
    [0.7, 243, 120, 25],
    [0.8, 252, 165, 10],
    [0.9, 246, 215, 70],
    [1.0, 252, 255, 164],
  ],
  // Merlin's own look: dark ink on near-white paper.
  merlin: [
    [0.0, 250, 250, 248],
    [0.35, 190, 190, 190],
    [0.7, 90, 90, 92],
    [1.0, 12, 12, 16],
  ],
  ice: [
    [0.0, 2, 4, 10],
    [0.25, 12, 44, 92],
    [0.5, 20, 110, 158],
    [0.75, 96, 194, 210],
    [1.0, 240, 253, 255],
  ],
}

/** 256-entry RGB lookup, built once per palette. */
function buildPalette(name: Palette): Uint8ClampedArray {
  const ramp = RAMPS[name]
  const table = new Uint8ClampedArray(256 * 3)
  for (let i = 0; i < 256; i++) {
    const t = i / 255
    let upper = 1
    while (upper < ramp.length - 1 && ramp[upper][0] < t) upper++
    const [t0, r0, g0, b0] = ramp[upper - 1]
    const [t1, r1, g1, b1] = ramp[upper]
    const f = t1 > t0 ? (t - t0) / (t1 - t0) : 0
    table[i * 3] = r0 + (r1 - r0) * f
    table[i * 3 + 1] = g0 + (g1 - g0) * f
    table[i * 3 + 2] = b0 + (b1 - b0) * f
  }
  return table
}

/** Should this detection be drawn over this frequency band?
 *
 *  It must not simply be drawn on every channel. A jackdaw call at 2 kHz labelled
 *  across the 15-150 kHz ultrasonic spectrogram asserts evidence that is not there
 *  — the audio in that panel physically cannot contain it — and with ultrasound
 *  stacked on top that mislabelling is the first thing you read.
 *
 *  Peak frequency decides it when the detector reported one. When it did not
 *  (BirdNET reports a species, not a frequency) fall back to the taxonomic group,
 *  which encodes which stream the detector ran on: birds and unidentified acoustic
 *  events come from the audible stream, bats from the native one.
 */
function belongsOnChannel(detection: Detection, spec: SpectrogramSpec): boolean {
  const peak = detection.peak_frequency_hz
  if (peak) return peak >= spec.min_hz && peak <= spec.max_hz
  const ultrasonicChannel = spec.min_hz >= 15_000
  return detection.taxonomic_group === 'bat' ? ultrasonicChannel : !ultrasonicChannel
}

interface Props {
  spec: SpectrogramSpec
  /** Registers a sink the parent calls for every batch on this channel. */
  register: (channel: number, sink: (batch: ColumnBatch) => void) => () => void
  detections: Detection[]
  palette: Palette
  /** Seconds of history to show. */
  windowSeconds: number
  /** Display range within the server's 0-255 encoding, as fractions. */
  blackPoint: number
  whitePoint: number
  showDetections: boolean
  orientation: Orientation
  height: number
}

export function Spectrogram({
  spec,
  register,
  detections,
  palette,
  windowSeconds,
  blackPoint,
  whitePoint,
  showDetections,
  orientation,
  height,
}: Props) {
  const visibleRef = useRef<HTMLCanvasElement | null>(null)
  const overlayRef = useRef<HTMLCanvasElement | null>(null)
  const ringRef = useRef<HTMLCanvasElement | null>(null)
  const writeXRef = useRef(0)
  const totalColumnsRef = useRef(0)
  /** UTC seconds of the newest column written. */
  const newestUtcRef = useRef<number | null>(null)
  /** When the most recent batch arrived, for sub-column scroll interpolation. */
  const lastBatchRef = useRef<{ at: number; columns: number } | null>(null)
  const [ready, setReady] = useState(false)
  const [hover, setHover] = useState<{ hz: number; secondsAgo: number } | null>(null)
  const band: Band = { minHz: spec.min_hz, maxHz: spec.max_hz, bins: spec.bins }

  // Read by the overlay's animation loop rather than closed over by it. Detections
  // arrive several times a second, and having them in the effect's dependencies
  // tore down and restarted the requestAnimationFrame loop on every one — which
  // cost frames and left the boxes visibly rougher than the plot they sit on.
  const detectionsRef = useRef(detections)
  detectionsRef.current = detections

  // Compose the display range into the palette so the hot loop stays a single
  // table lookup per bin rather than an arithmetic rescale per bin.
  const paletteTable = useMemo(() => {
    const base = buildPalette(palette)
    const low = Math.round(Math.min(blackPoint, whitePoint - 0.02) * 255)
    const high = Math.round(Math.max(whitePoint, blackPoint + 0.02) * 255)
    const composed = new Uint8ClampedArray(256 * 3)
    for (let value = 0; value < 256; value++) {
      const scaled = Math.max(0, Math.min(255, Math.round(((value - low) / (high - low)) * 255)))
      composed[value * 3] = base[scaled * 3]
      composed[value * 3 + 1] = base[scaled * 3 + 1]
      composed[value * 3 + 2] = base[scaled * 3 + 2]
    }
    return composed
  }, [palette, blackPoint, whitePoint])
  const ringColumns = Math.max(2048, Math.ceil(windowSeconds / spec.hop_s) * 2)

  // Columns of scroll owed since the last batch landed, shared by the spectrogram
  // and the overlay. It has to be one function used by both: interpolating the plot
  // alone left the detection boxes stepping in 100 ms jumps over a smoothly gliding
  // background, which reads worse than when both juddered together. Clamped so a
  // stalled feed parks rather than drifting away from its own audio.
  const elapsedColumns = () => {
    const batch = lastBatchRef.current
    if (!batch) return 0
    return Math.min(((performance.now() - batch.at) / 1000) / spec.hop_s, 6)
  }

  // The offscreen ring is sized in *columns*, one pixel each; the visible canvas
  // stretches it horizontally, so history length is independent of viewport width.
  useLayoutEffect(() => {
    const ring = document.createElement('canvas')
    ring.width = ringColumns
    ring.height = spec.bins
    const context = ring.getContext('2d', { willReadFrequently: false })
    if (context) {
      context.fillStyle = palette === 'merlin' ? '#f7f7f5' : '#05060a'
      context.fillRect(0, 0, ring.width, ring.height)
    }
    ringRef.current = ring
    writeXRef.current = 0
    totalColumnsRef.current = 0
    newestUtcRef.current = null
    lastBatchRef.current = null
    setReady(true)
  }, [ringColumns, spec.bins, palette])

  // Column ingest. Kept out of React state entirely: at ~40 columns a second a
  // re-render per batch would dominate the frame budget.
  useEffect(() => {
    const sink = (batch: ColumnBatch) => {
      const ring = ringRef.current
      if (!ring || batch.bins !== spec.bins) return
      const context = ring.getContext('2d')
      if (!context) return

      const image = context.createImageData(1, spec.bins)
      const pixels = image.data
      for (let column = 0; column < batch.columns; column++) {
        const offset = column * batch.bins
        for (let bin = 0; bin < batch.bins; bin++) {
          const value = batch.data[offset + bin]
          // Bin 0 is the lowest frequency; canvas row 0 is the top, so flip.
          const row = spec.bins - 1 - bin
          const index = row * 4
          const p = value * 3
          pixels[index] = paletteTable[p]
          pixels[index + 1] = paletteTable[p + 1]
          pixels[index + 2] = paletteTable[p + 2]
          pixels[index + 3] = 255
        }
        context.putImageData(image, writeXRef.current, 0)
        writeXRef.current = (writeXRef.current + 1) % ring.width
        totalColumnsRef.current += 1
      }
      newestUtcRef.current = batch.firstUtcS + (batch.columns - 1) * spec.hop_s
      // Note when this batch landed, so the draw loop can interpolate between
      // batches instead of jumping. Columns arrive four at a time every 100 ms
      // (one capture block), which without this reads as a visible lurch ten
      // times a second rather than a smooth scroll.
      lastBatchRef.current = { at: performance.now(), columns: totalColumnsRef.current }
    }
    return register(spec.channel, sink)
  }, [register, spec.channel, spec.bins, spec.hop_s, paletteTable])

  // Draw loop.
  useEffect(() => {
    if (!ready) return
    let frame = 0
    const draw = () => {
      frame = requestAnimationFrame(draw)
      const canvas = visibleRef.current
      const ring = ringRef.current
      if (!canvas || !ring) return
      const context = canvas.getContext('2d')
      if (!context) return

      const dpr = window.devicePixelRatio || 1
      const cssWidth = canvas.clientWidth
      const cssHeight = canvas.clientHeight
      if (canvas.width !== Math.round(cssWidth * dpr) || canvas.height !== Math.round(cssHeight * dpr)) {
        canvas.width = Math.round(cssWidth * dpr)
        canvas.height = Math.round(cssHeight * dpr)
      }

      // windowColumns is the number of columns the *selected window* implies —
      // it sets the pixels-per-column scale and never changes with how much
      // history has actually arrived. columnsDrawn is how many columns actually
      // exist to blit right now, which is smaller right after a window change or
      // a fresh connection. Rescaling the scale term to columnsDrawn (as this
      // used to do) is exactly the reported bug: a partially-filled window would
      // render stretched to fill the canvas, then visibly "bunch up" to true
      // scale as more data arrived.
      const windowColumns = Math.max(1, Math.round(windowSeconds / spec.hop_s))
      const columnsDrawn = Math.min(windowColumns, ring.width, totalColumnsRef.current)
      const start = (writeXRef.current - columnsDrawn + ring.width * 2) % ring.width

      context.setTransform(1, 0, 0, 1, 0, 0)
      context.imageSmoothingEnabled = false
      context.fillStyle = palette === 'merlin' ? '#f7f7f5' : '#05060a'
      context.fillRect(0, 0, canvas.width, canvas.height)

      if (columnsDrawn > 0) {
        // One affine matrix presents the ring in whichever orientation is
        // selected — `waterfall` needs a transpose, which is a reflection about
        // the diagonal and so still a single GPU blit rather than a second copy
        // of the history. Because the scale is fixed by windowColumns rather
        // than columnsDrawn, a partially-filled window draws at true scale
        // anchored to the live edge, with the unfilled remainder left as
        // background instead of being stretched to hide it.
        //
        // It also carries the sub-column interpolation: columns arrive in
        // bursts of ~4 every 100 ms, and drawing them flush to the live edge
        // makes the image lurch ten times a second instead of gliding.
        context.setTransform(
          ...ringTransform({
            orientation,
            deviceWidth: canvas.width,
            deviceHeight: canvas.height,
            windowColumns,
            columnsDrawn,
            bins: ring.height,
            shiftColumns: elapsedColumns(),
          }),
        )

        // Two blits at most: the ring may wrap between `start` and the write head.
        // Destination coordinates are in source units, so the matrix does the work.
        if (start + columnsDrawn <= ring.width) {
          context.drawImage(
            ring, start, 0, columnsDrawn, ring.height, 0, 0, columnsDrawn, ring.height,
          )
        } else {
          const firstRun = ring.width - start
          context.drawImage(ring, start, 0, firstRun, ring.height, 0, 0, firstRun, ring.height)
          context.drawImage(
            ring, 0, 0, columnsDrawn - firstRun, ring.height,
            firstRun, 0, columnsDrawn - firstRun, ring.height,
          )
        }
        context.setTransform(1, 0, 0, 1, 0, 0)
      }
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [ready, windowSeconds, spec.hop_s, palette, orientation])

  // Overlay: gridlines, labels and detection boxes. Separate canvas so the
  // spectrogram blit never has to be redrawn to update a label.
  useEffect(() => {
    let frame = 0
    const draw = () => {
      frame = requestAnimationFrame(draw)
      const canvas = overlayRef.current
      if (!canvas) return
      const dpr = window.devicePixelRatio || 1
      const cssWidth = canvas.clientWidth
      const cssHeight = canvas.clientHeight
      if (canvas.width !== Math.round(cssWidth * dpr) || canvas.height !== Math.round(cssHeight * dpr)) {
        canvas.width = Math.round(cssWidth * dpr)
        canvas.height = Math.round(cssHeight * dpr)
      }
      const context = canvas.getContext('2d')
      if (!context) return
      context.setTransform(dpr, 0, 0, dpr, 0, 0)
      context.clearRect(0, 0, cssWidth, cssHeight)

      const dark = palette !== 'merlin'
      const viewport = { width: cssWidth, height: cssHeight }

      // Frequency gridlines run across the frequency axis, whichever that is:
      // horizontal in scroll, vertical in waterfall.
      context.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace'
      context.textBaseline = 'middle'
      for (const hz of GRID_HZ) {
        if (hz < spec.min_hz * 1.05 || hz > spec.max_hz * 0.98) continue
        const at = frequencyToPixel(hz, band, viewport, orientation)
        context.strokeStyle = dark ? 'rgba(255,255,255,0.10)' : 'rgba(0,0,0,0.12)'
        context.lineWidth = 1
        context.beginPath()
        if (orientation === 'scroll') {
          context.moveTo(0, at)
          context.lineTo(cssWidth, at)
        } else {
          context.moveTo(at, 0)
          context.lineTo(at, cssHeight)
        }
        context.stroke()
        const label = hz >= 1000 ? `${hz / 1000}k` : `${hz}`
        context.fillStyle = dark ? 'rgba(233,238,248,0.62)' : 'rgba(20,20,24,0.6)'
        if (orientation === 'scroll') {
          context.fillText(label, 4, at - 7)
        } else {
          // Along the bottom, nudged inside the edges so the extremes stay legible.
          const metrics = context.measureText(label)
          context.fillText(
            label,
            Math.max(2, Math.min(at + 3, cssWidth - metrics.width - 2)),
            cssHeight - 8,
          )
        }
      }

      // Detection boxes, positioned from the newest column's timestamp advanced by
      // the same interpolation the spectrogram uses, so the two stay locked.
      const newest =
        newestUtcRef.current === null
          ? null
          : newestUtcRef.current + elapsedColumns() * spec.hop_s
      if (showDetections && newest !== null) {
        for (const detection of detectionsRef.current) {
          if (!belongsOnChannel(detection, spec)) continue
          const startAgo = newest - Date.parse(detection.event_start_utc) / 1000
          const endAgo = newest - Date.parse(detection.event_end_utc) / 1000
          // Off-screen either way, with a margin for the label.
          if (endAgo < -windowSeconds * 0.1 || startAgo > windowSeconds * 1.1) continue

          const group = detection.taxonomic_group
          const colour =
            group === 'bird' ? '#5ce08a' : group === 'bat' ? '#c39bff' : '#6fb4ff'
          const peak = detection.peak_frequency_hz
          const rect = spanRect(
            {
              startSecondsAgo: startAgo,
              endSecondsAgo: endAgo,
              // A peak frequency marks a band around itself; without one the box
              // spans everything, which states ignorance rather than bandwidth.
              lowHz: peak ? Math.max(spec.min_hz, peak / 1.6) : null,
              highHz: peak ? Math.min(spec.max_hz, peak * 1.6) : null,
            },
            band,
            viewport,
            windowSeconds,
            orientation,
          )

          if (group === 'acoustic_event') {
            // The activity detector fires several times a second. Drawing each as a
            // full box turned the display into a wall of rectangles and hid the
            // audio underneath, so unidentified events get a tick on the edge
            // furthest from the live edge: present and countable, but not shouting.
            context.strokeStyle = colour
            context.globalAlpha = 0.5
            context.lineWidth = 2
            context.beginPath()
            if (orientation === 'scroll') {
              context.moveTo(rect.x, cssHeight - 2)
              context.lineTo(rect.x + rect.width, cssHeight - 2)
            } else {
              context.moveTo(2, rect.y)
              context.lineTo(2, rect.y + rect.height)
            }
            context.stroke()
            context.globalAlpha = 1
            continue
          }

          context.strokeStyle = colour
          context.lineWidth = 1.5
          context.strokeRect(rect.x, rect.y, rect.width, rect.height)

          const text = `${formatDetectionTitleText(detection)} ${(detection.score * 100).toFixed(0)}%`
          context.font = '600 11px ui-sans-serif, system-ui, sans-serif'
          const metrics = context.measureText(text)
          const labelX = Math.max(2, Math.min(rect.x, cssWidth - metrics.width - 10))
          const labelY = Math.max(11, rect.y - 9)
          context.fillStyle = dark ? 'rgba(6,8,14,0.82)' : 'rgba(255,255,255,0.88)'
          context.fillRect(labelX, labelY - 8, metrics.width + 8, 16)
          context.fillStyle = colour
          context.fillText(text, labelX + 4, labelY)
        }
      }

      // Live edge marker.
      context.strokeStyle = dark ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.45)'
      context.lineWidth = 1
      context.beginPath()
      if (orientation === 'scroll') {
        context.moveTo(cssWidth - 0.5, 0)
        context.lineTo(cssWidth - 0.5, cssHeight)
      } else {
        context.moveTo(0, 0.5)
        context.lineTo(cssWidth, 0.5)
      }
      context.stroke()
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
    // Deliberately excludes `detections`: it is read through a ref so that a new
    // detection never restarts this loop.
  }, [palette, spec.min_hz, spec.max_hz, spec.bins, spec.hop_s, windowSeconds, showDetections, orientation])

  const onMove = (event: React.MouseEvent<HTMLDivElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect()
    const viewport = { width: bounds.width, height: bounds.height }
    const x = event.clientX - bounds.left
    const y = event.clientY - bounds.top
    // Same functions the plot and overlay use, so the readout cannot disagree with
    // what is drawn under the cursor.
    setHover({
      hz: pixelToFrequency(orientation === 'scroll' ? y : x, band, viewport, orientation),
      secondsAgo: pixelToSecondsAgo(
        orientation === 'scroll' ? x : y, windowSeconds, viewport, orientation,
      ),
    })
  }

  return (
    <div
      className="spectrogram"
      style={{ height }}
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
    >
      <canvas ref={visibleRef} className="spectrogram-canvas" />
      <canvas ref={overlayRef} className="spectrogram-overlay" />
      <div className="spectrogram-badges">
        <span className="badge">{spec.name}</span>
        <span className="badge dim">
          {formatHz(spec.min_hz)}–{formatHz(spec.max_hz)}
        </span>
        <span className="badge dim">{spec.bins} bins</span>
        <span className="badge dim">{(spec.hop_s * 1000).toFixed(0)} ms/col</span>
        <span className="badge dim">FFT {spec.fft_size}</span>
        <span className="badge dim">{orientation}</span>
      </div>
      {hover && (
        <div className="spectrogram-readout">
          {formatHz(hover.hz)} · {hover.secondsAgo < 1 ? 'now' : `${hover.secondsAgo.toFixed(1)} s ago`}
        </div>
      )}
    </div>
  )
}

export function formatHz(hz: number): string {
  if (hz >= 1000) return `${(hz / 1000).toFixed(hz >= 10_000 ? 0 : 1)} kHz`
  return `${Math.round(hz)} Hz`
}
