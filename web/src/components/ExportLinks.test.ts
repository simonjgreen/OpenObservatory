import { describe, expect, it } from 'vitest'
import { buildExportUrl } from './ExportLinks'

describe('buildExportUrl', () => {
  it('uses the named window when there is no focus', () => {
    const url = buildExportUrl('csv', { windowName: 'last-night', focus: null })
    expect(url).toBe('/api/v1/detections/export?format=csv&window=last-night')
  })

  it('uses since/until when a slice is focused, dropping the window', () => {
    const url = buildExportUrl('json', {
      windowName: 'last-night',
      focus: { fromUtc: '2026-08-08T00:00:00Z', toUtc: '2026-08-08T01:00:00Z' },
    })
    expect(url).toBe(
      '/api/v1/detections/export?format=json&since=2026-08-08T00%3A00%3A00Z&until=2026-08-08T01%3A00%3A00Z',
    )
  })
})
