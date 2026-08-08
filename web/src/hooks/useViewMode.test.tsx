// @vitest-environment jsdom

import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { useViewMode } from './useViewMode'

describe('useViewMode', () => {
  afterEach(() => {
    window.history.replaceState(null, '', '/')
  })

  it('defaults to operate when there is no ?view= param', () => {
    window.history.replaceState(null, '', '/')
    const { result } = renderHook(() => useViewMode())
    expect(result.current.depth).toBe('operate')
  })

  it('restores diagnose from the URL on mount', () => {
    window.history.replaceState(null, '', '/?view=diagnose')
    const { result } = renderHook(() => useViewMode())
    expect(result.current.depth).toBe('diagnose')
  })

  it('writes ?view=diagnose to the URL without adding a history entry', () => {
    window.history.replaceState(null, '', '/')
    const { result } = renderHook(() => useViewMode())
    act(() => result.current.setDepth('diagnose'))
    expect(window.location.search).toBe('?view=diagnose')
  })

  it('removes the param when returning to operate, the default', () => {
    window.history.replaceState(null, '', '/?view=diagnose')
    const { result } = renderHook(() => useViewMode())
    act(() => result.current.toggle())
    expect(window.location.search).toBe('')
  })
})
