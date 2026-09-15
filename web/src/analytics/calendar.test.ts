import { describe, expect, it } from 'vitest'
import { bucketLabel, labelledIndices, longDate, monthStarts } from './calendar'

describe('bucket labels', () => {
  it('names each grain the way a person would', () => {
    expect(bucketLabel('2026-08-05', 'day')).toBe('5 Aug')
    expect(bucketLabel('2026-08-03', 'week')).toBe('w/c 3 Aug')
    expect(bucketLabel('2026-08-01', 'month')).toBe('Aug 2026')
    expect(bucketLabel('2026-08-05', 'night')).toBe('night of 5 Aug')
    expect(longDate('2026-08-05')).toBe('Wednesday 5 August 2026')
  })
  it('thins a long axis to a handful of labels', () => {
    expect(labelledIndices(5)).toEqual([0, 1, 2, 3, 4])
    expect(labelledIndices(365, 8).length).toBeLessThanOrEqual(9)
    expect(labelledIndices(365, 8)[0]).toBe(0)
  })
  it('marks where each month starts', () => {
    const starts = monthStarts(['2026-08-30', '2026-08-31', '2026-09-01', '2026-09-02', '2026-10-01'])
    expect(starts).toEqual([
      { index: 0, label: 'Aug 2026' },
      { index: 2, label: 'Sep' },
      { index: 4, label: 'Oct' },
    ])
  })
})
