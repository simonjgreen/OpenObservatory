import { describe, expect, it } from 'vitest'
import { clock, compact, duration, groupColour, mix, niceMax, orderGroups, ramp, SURFACE, ticks } from './scale'

describe('group colours', () => {
  it('are the History timeline colours, and the order is the station order', () => {
    expect(groupColour('bird')).toBe('#5ce08a')
    expect(groupColour('bat')).toBe('#c39bff')
    expect(groupColour('something-new')).toBe('#6fb4ff')
    expect(orderGroups(['noise', 'zebra', 'bat', 'bird'])).toEqual(['bird', 'bat', 'noise', 'zebra'])
  })
})

describe('niceMax', () => {
  it('rounds up to 1, 2, 5 times a power of ten', () => {
    expect(niceMax(0)).toBe(1)
    expect(niceMax(3)).toBe(5)
    expect(niceMax(7)).toBe(10)
    expect(niceMax(1200)).toBe(2000)
    expect(niceMax(40706)).toBe(50000)
    expect(ticks(100, 4)).toEqual([0, 25, 50, 75, 100])
  })
})

describe('ramp', () => {
  it('is the surface at zero and the group colour at the maximum', () => {
    expect(ramp(0, 10, '#5ce08a')).toBe(SURFACE)
    expect(ramp(10, 10, '#5ce08a')).toBe('#5ce08a')
    expect(mix('#000000', '#ffffff', 0.5)).toBe('#808080')
  })
  it('is monotonic in lightness and lifts small values off the surface', () => {
    const low = ramp(1, 100, '#5ce08a')
    const mid = ramp(25, 100, '#5ce08a')
    const high = ramp(100, 100, '#5ce08a')
    const green = (hex: string) => parseInt(hex.slice(3, 5), 16)
    expect(green(low)).toBeGreaterThan(green(SURFACE) + 20)
    expect(green(mid)).toBeGreaterThan(green(low))
    expect(green(high)).toBeGreaterThan(green(mid))
  })
})

describe('formatting', () => {
  it('compacts counts and renders clock hours', () => {
    expect(compact(999)).toBe('999')
    expect(compact(1200)).toBe('1.2k')
    expect(compact(40706)).toBe('41k')
    expect(compact(1_500_000)).toBe('1.5M')
    expect(clock(6.5)).toBe('06:30')
    expect(clock(0.501)).toBe('00:30')
    expect(clock(null)).toBe('—')
    expect(duration(5400)).toBe('1h 30m')
    expect(duration(90)).toBe('2m')
  })
})
