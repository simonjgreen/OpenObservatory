/** Coordinate mapping for the two spectrogram orientations.
 *
 *  `scroll` puts time on the horizontal axis with now at the right edge and
 *  frequency on the vertical axis, high at the top. It is the better view for
 *  reading rhythm and the shape of a call over time.
 *
 *  `waterfall` puts frequency on the horizontal axis, low at the left, and time on
 *  the vertical axis with now at the top flowing downwards — the classic radio
 *  waterfall. It is the better view for reading where energy sits across the band
 *  and for comparing one band against another.
 *
 *  Everything here is pure, and deliberately so: the plot, the detection overlay
 *  and the hover readout must agree exactly, in both orientations, or the display
 *  lies about where a sound was. Keeping the maths in one testable place is what
 *  makes that checkable rather than hoped for.
 */

export type Orientation = 'scroll' | 'waterfall'

export interface Band {
  minHz: number
  maxHz: number
  bins: number
}

export interface Viewport {
  /** CSS pixels. */
  width: number
  height: number
}

/** Where a frequency sits on its own axis, 0 at the axis origin to 1 at the end.
 *
 *  Logarithmic, because the server's bins are: a linear scale would waste most of
 *  the display on 8-24 kHz, where little bird song lives, and squash 1-5 kHz, where
 *  almost all of it does.
 */
export function frequencyFraction(hz: number, band: Band): number {
  const low = Math.log(band.minHz)
  const high = Math.log(band.maxHz)
  if (!(high > low)) return 0
  return (Math.log(Math.max(hz, 1e-6)) - low) / (high - low)
}

export function fractionToFrequency(fraction: number, band: Band): number {
  const low = Math.log(band.minHz)
  const high = Math.log(band.maxHz)
  return Math.exp(low + fraction * (high - low))
}

/** Pixel position of a frequency along whichever axis carries frequency.
 *
 *  `scroll` returns a y coordinate (high frequency at the top); `waterfall` returns
 *  an x coordinate (high frequency at the right).
 */
export function frequencyToPixel(
  hz: number,
  band: Band,
  viewport: Viewport,
  orientation: Orientation,
): number {
  const fraction = frequencyFraction(hz, band)
  return orientation === 'scroll'
    ? viewport.height * (1 - fraction)
    : viewport.width * fraction
}

export function pixelToFrequency(
  pixel: number,
  band: Band,
  viewport: Viewport,
  orientation: Orientation,
): number {
  const fraction =
    orientation === 'scroll'
      ? 1 - pixel / Math.max(1, viewport.height)
      : pixel / Math.max(1, viewport.width)
  return fractionToFrequency(fraction, band)
}

/** Pixel position of an age along whichever axis carries time.
 *
 *  `scroll` returns x, with zero seconds ago at the right edge; `waterfall` returns
 *  y, with zero seconds ago at the top.
 */
export function secondsAgoToPixel(
  secondsAgo: number,
  windowSeconds: number,
  viewport: Viewport,
  orientation: Orientation,
): number {
  const fraction = secondsAgo / Math.max(1e-6, windowSeconds)
  return orientation === 'scroll'
    ? viewport.width * (1 - fraction)
    : viewport.height * fraction
}

export function pixelToSecondsAgo(
  pixel: number,
  windowSeconds: number,
  viewport: Viewport,
  orientation: Orientation,
): number {
  const fraction =
    orientation === 'scroll'
      ? 1 - pixel / Math.max(1, viewport.width)
      : pixel / Math.max(1, viewport.height)
  return fraction * windowSeconds
}

/** How wide, in pixels, one column of history is along the time axis. */
export function pixelsPerColumn(
  columns: number,
  viewport: Viewport,
  orientation: Orientation,
): number {
  const extent = orientation === 'scroll' ? viewport.width : viewport.height
  return extent / Math.max(1, columns)
}

/** A canvas 2D affine matrix, in `setTransform` order. */
export type Matrix = [number, number, number, number, number, number]

export interface RingTransformInput {
  orientation: Orientation
  /** Device pixels — this matrix is applied instead of a devicePixelRatio scale. */
  deviceWidth: number
  deviceHeight: number
  /** Columns implied by the *selected window* (windowSeconds / hop_s) — this, and
   *  only this, sets the pixels-per-column scale. It must never be derived from how
   *  many columns happen to be buffered: doing that was the bug where a
   *  partially-filled window rendered stretched to fill the canvas, then visibly
   *  "bunched up" to true scale as the buffer filled. A column's width is a
   *  property of its duration and the selected window, not of how much history has
   *  arrived yet.
   */
  windowColumns: number
  /** Columns actually being blitted this frame. Equal to `windowColumns` once the
   *  ring has filled; smaller right after a window change or a fresh connection,
   *  in which case the undrawn remainder of the window is left as background —
   *  audio that has not arrived, not audio stretched to hide that fact.
   */
  columnsDrawn: number
  /** Frequency bins per column, i.e. the ring canvas's height. */
  bins: number
  /** Sub-column interpolation: columns of scroll owed since the last batch. */
  shiftColumns: number
}

/** Matrix that blits the `(columnsDrawn × bins)` slice of the ring canvas for one
 *  orientation, at the fixed scale implied by `windowColumns`.
 *
 *  The ring is stored once, in one orientation — column index increasing with time,
 *  row 0 holding the highest frequency — and presented either way by transform
 *  rather than being kept twice. `waterfall` needs a transpose, which is a
 *  reflection about the diagonal and expressible as an affine matrix, so a single
 *  `drawImage` still does the work on the GPU.
 *
 *  Source pixel `(u, row)` maps to the screen, where `u` counts from the oldest
 *  *drawn* column (0) up to `columnsDrawn`, and `row` counts down from the highest
 *  frequency. The drawn slice is anchored to the live edge (the right in `scroll`,
 *  the top in `waterfall`); when `columnsDrawn < windowColumns` the far end is left
 *  blank rather than the slice being rescaled to cover the whole axis.
 */
export function ringTransform(input: RingTransformInput): Matrix {
  const { orientation, deviceWidth, deviceHeight, windowColumns, columnsDrawn, bins, shiftColumns } =
    input
  const perColumnX = deviceWidth / Math.max(1, windowColumns)
  const perColumnY = deviceHeight / Math.max(1, windowColumns)
  const perBinX = deviceWidth / Math.max(1, bins)
  const perBinY = deviceHeight / Math.max(1, bins)

  if (orientation === 'scroll') {
    // x grows with time, y grows downwards with the ring's rows, so the highest
    // frequency (row 0) lands at the top. The drawn slice's newest edge
    // (source x = columnsDrawn) is anchored at the right, minus whatever
    // interpolation is owed; the few pixels that uncovers at the right edge are
    // time for which no audio has arrived yet, which is the honest thing to show
    // there. If the buffer isn't full yet, the slice's oldest edge simply lands
    // short of the left edge, leaving the true gap blank instead of stretching to
    // fill it.
    return [perColumnX, 0, 0, perBinY, deviceWidth - (shiftColumns + columnsDrawn) * perColumnX, 0]
  }
  // Transpose: the ring's time axis drives screen y (newest at the top, older
  // downwards) and its row axis drives screen x (row 0, the highest frequency, at
  // the right). Interpolation slides content down, uncovering a sliver at the top;
  // an unfilled buffer leaves a gap at the bottom rather than being stretched to
  // reach it.
  return [
    0,
    -perColumnY,
    -perBinX,
    0,
    deviceWidth,
    (shiftColumns + columnsDrawn) * perColumnY,
  ]
}

/** Apply a matrix to a point, for tests and for reasoning about the mapping. */
export function applyMatrix(matrix: Matrix, x: number, y: number): { x: number; y: number } {
  const [a, b, c, d, e, f] = matrix
  return { x: a * x + c * y + e, y: b * x + d * y + f }
}

/** Axis-aligned rectangle in CSS pixels. */
export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

/** The rectangle covering a time span and frequency span, in either orientation.
 *
 *  Used for detection overlays, so a box marks the same audio in both views.
 */
export function spanRect(
  span: {
    startSecondsAgo: number
    endSecondsAgo: number
    lowHz: number | null
    highHz: number | null
  },
  band: Band,
  viewport: Viewport,
  windowSeconds: number,
  orientation: Orientation,
): Rect {
  const timeA = secondsAgoToPixel(span.startSecondsAgo, windowSeconds, viewport, orientation)
  const timeB = secondsAgoToPixel(span.endSecondsAgo, windowSeconds, viewport, orientation)
  const timeLow = Math.min(timeA, timeB)
  const timeExtent = Math.max(3, Math.abs(timeB - timeA))

  // A detection with no frequency information spans the whole band; that is a
  // statement about ignorance, not about the sound filling the spectrum.
  const hasBand = span.lowHz !== null && span.highHz !== null
  const freqA = hasBand
    ? frequencyToPixel(span.lowHz as number, band, viewport, orientation)
    : orientation === 'scroll'
      ? viewport.height
      : 0
  const freqB = hasBand
    ? frequencyToPixel(span.highHz as number, band, viewport, orientation)
    : orientation === 'scroll'
      ? 0
      : viewport.width
  const freqLow = Math.min(freqA, freqB)
  const freqExtent = Math.max(3, Math.abs(freqB - freqA))

  return orientation === 'scroll'
    ? { x: timeLow, y: freqLow, width: timeExtent, height: freqExtent }
    : { x: freqLow, y: timeLow, width: freqExtent, height: timeExtent }
}


/** Order the band panels so their frequency axes form one continuous run.
 *
 *  Which end "high" belongs at depends on the orientation, because the two views
 *  put frequency on different axes:
 *
 *  `scroll` has frequency vertical with high at the top of each panel, so the
 *  highest band goes first and the page reads 150 kHz at the top down to 80 Hz at
 *  the bottom.
 *
 *  `waterfall` has frequency horizontal with high at the right of each panel, so the
 *  highest band goes last and the page reads 80 Hz on the left across to 150 kHz on
 *  the right.
 *
 *  Ordered by the band's own low edge rather than by channel id: an id is a protocol
 *  identifier, not a display order, so a future third band lands in the right place
 *  without another edit here.
 */
export function orderPanels<T extends { min_hz: number }>(
  panels: readonly T[],
  orientation: Orientation,
): T[] {
  const ascending = orientation === 'waterfall'
  return [...panels].sort((a, b) =>
    ascending ? a.min_hz - b.min_hz : b.min_hz - a.min_hz,
  )
}
