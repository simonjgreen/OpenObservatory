/** Pure chart arithmetic for the analytics views: axis ticks, a sequential
 *  colour ramp built from one group hue, and the group palette itself.
 *  Kept out of the components so it can be tested without a DOM, as
 *  `geometry.ts` and `overlayLabels.ts` are for the spectrogram. */

/** The group colours, identical to the History timeline's: a bird must be
 *  the same green on every screen. Anything unlisted falls back to the
 *  accent blue. Noise is deliberately grey -- it is not wildlife. */
export const GROUP_COLOUR: Record<string, string> = {
  bird: '#5ce08a',
  bat: '#c39bff',
  acoustic_event: '#3f6ea8',
  noise: '#6b7280',
}
export const FALLBACK_COLOUR = '#6fb4ff'
/** The order series stack and legends list, matching the station's. */
export const GROUP_ORDER = ['bird', 'bat', 'acoustic_event', 'noise', 'unknown']

export function groupColour(group: string): string {
  return GROUP_COLOUR[group] ?? FALLBACK_COLOUR
}

export function groupLabel(group: string): string {
  return group.replace(/_/g, ' ')
}

/** Stable stacking order: known groups first in the station's order, then
 *  anything else alphabetically, so a filter that removes a group never
 *  repaints the survivors. */
export function orderGroups(groups: Iterable<string>): string[] {
  const set = new Set(groups)
  const known = GROUP_ORDER.filter((g) => set.has(g))
  const rest = [...set].filter((g) => !GROUP_ORDER.includes(g)).sort()
  return [...known, ...rest]
}

/** A "nice" axis maximum at or above `value`: 1, 2, 5 × 10^n. */
export function niceMax(value: number): number {
  if (!(value > 0)) return 1
  const exponent = Math.floor(Math.log10(value))
  const base = 10 ** exponent
  for (const step of [1, 2, 5, 10]) {
    if (step * base >= value) return step * base
  }
  return 10 * base
}

/** Evenly spaced ticks from 0 to `max` inclusive; `count` intervals. */
export function ticks(max: number, count = 4): number[] {
  const out: number[] = []
  for (let i = 0; i <= count; i += 1) out.push((max * i) / count)
  return out
}

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace('#', '')
  const n = parseInt(clean.length === 3 ? clean.replace(/(.)/g, '$1$1') : clean, 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function rgbToHex([r, g, b]: [number, number, number]): string {
  return `#${[r, g, b].map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join('')}`
}

/** Linear mix in sRGB: good enough for a ramp that only has to be
 *  monotonic in lightness against one dark surface. */
export function mix(a: string, b: string, t: number): string {
  const [ar, ag, ab] = hexToRgb(a)
  const [br, bg, bb] = hexToRgb(b)
  return rgbToHex([ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t])
}

/** The chart surface every ramp starts from. Matches `--bg-panel`. */
export const SURFACE = '#0d0f15'

/** Sequential ramp for a magnitude, one hue: the surface at zero, the
 *  group's own colour at the top, passing through a darker mid-step so
 *  small values are visible but unmistakably smaller. A square root keeps
 *  a heat-map from being one bright cell and a field of black when a single
 *  dawn hour holds a quarter of the day. Zero is the surface itself, so an
 *  empty cell is indistinguishable from the background -- which is the
 *  point: nothing there. */
export function ramp(value: number, max: number, colour: string): string {
  if (!(value > 0) || !(max > 0)) return SURFACE
  const t = Math.sqrt(Math.min(1, value / max))
  // Lift off the surface quickly so a value of 1 is a visible mark.
  const eased = 0.18 + 0.82 * t
  return mix(SURFACE, colour, eased)
}

/** Shorten a count for a cell or axis: 1200 -> 1.2k. */
export function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (n >= 10_000) return `${Math.round(n / 1000)}k`
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`
  return String(n)
}

/** Hours since midnight as a clock string: 6.5 -> "06:30". */
export function clock(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || Number.isNaN(hours)) return '—'
  const total = Math.round(hours * 60)
  const h = Math.floor(total / 60) % 24
  const m = total % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

/** `seconds` as a short duration: 3600 -> "1h", 5400 -> "1h 30m". */
export function duration(seconds: number): string {
  if (seconds >= 3600) {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.round((seconds % 3600) / 60)
    return minutes ? `${hours}h ${minutes}m` : `${hours}h`
  }
  if (seconds >= 60) return `${Math.round(seconds / 60)}m`
  return `${Math.round(seconds)}s`
}
