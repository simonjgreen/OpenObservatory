import { describe, expect, it } from 'vitest'
import { DEFAULT_ROUTE, readRoute, writeRoute } from './useAnalyticsRoute'

describe('the analytics URL', () => {
  it('is inactive without ?section=analytics and leaves ?view= alone', () => {
    expect(readRoute('?view=diagnose')).toBe(DEFAULT_ROUTE)
    expect(writeRoute('?view=diagnose', DEFAULT_ROUTE)).toBe('view=diagnose')
  })
  it('round-trips a question and a hand-built view', () => {
    const question = writeRoute('', {
      active: true,
      questionId: 'robin-year',
      reportId: null,
      view: 'series',
      params: { range: 'this-year', grain: 'week', group: 'bird', label: 'European Robin' },
    })
    const back = readRoute(`?${question}`)
    expect(back.active).toBe(true)
    expect(back.questionId).toBe('robin-year')
    expect(back.view).toBe('series')
    expect(back.params).toEqual({ range: 'this-year', grain: 'week', group: 'bird', label: 'European Robin' })
    expect(readRoute('?section=analytics&chart=nonsense').view).toBe('series')
    expect(readRoute('?section=analytics').params.range).toBe('last-90d')
  })
})
