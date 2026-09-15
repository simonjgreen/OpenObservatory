/** Labels for the calendar buckets the station returns. Pure, so a week
 *  key like `2026-W32` renders the same everywhere and is tested once. */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function parseDate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1))
}

/** Short label for a bucket: `5 Aug`, `w/c 3 Aug`, `Aug 2026`, `night of 5 Aug`. */
export function bucketLabel(startDate: string, grain: string): string {
  const d = parseDate(startDate)
  const day = d.getUTCDate()
  const month = MONTHS[d.getUTCMonth()]
  if (grain === 'month') return `${month} ${d.getUTCFullYear()}`
  if (grain === 'week') return `w/c ${day} ${month}`
  if (grain === 'night') return `night of ${day} ${month}`
  return `${day} ${month}`
}

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const LONG_MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/** A full date for a tooltip: `Wednesday 5 August 2026`. Built by hand so
 *  it reads the same on every Node and browser ICU build. */
export function longDate(iso: string): string {
  const d = parseDate(iso)
  return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${LONG_MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

/** Which of `count` evenly spread column indices get an axis label, so a
 *  365-column heat-map shows a dozen dates rather than a black smear. */
export function labelledIndices(count: number, wanted = 8): number[] {
  if (count <= wanted) return [...Array(count).keys()]
  const step = Math.ceil(count / wanted)
  const out: number[] = []
  for (let i = 0; i < count; i += step) out.push(i)
  return out
}

/** The first column index of each month within a run of day/week
 *  columns, for month rules on a long axis. */
export function monthStarts(startDates: string[]): Array<{ index: number; label: string }> {
  const out: Array<{ index: number; label: string }> = []
  let previous = ''
  startDates.forEach((iso, index) => {
    const key = iso.slice(0, 7)
    if (key !== previous) {
      const d = parseDate(iso)
      out.push({ index, label: `${MONTHS[d.getUTCMonth()]}${d.getUTCMonth() === 0 || index === 0 ? ` ${d.getUTCFullYear()}` : ''}` })
      previous = key
    }
  })
  return out
}
