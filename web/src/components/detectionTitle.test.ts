/** formatDetectionTitle is the only place any render site composes a detection's
 *  title, so a duplicated fallback bug (the bug this whole change fixes on the
 *  backend) cannot recur on the frontend. Each case below matches a rendered
 *  scenario a real render site hits.
 */

import { describe, expect, it } from 'vitest'

import { formatDetectionTitle, formatDetectionTitleText } from './detectionTitle'

describe('formatDetectionTitle', () => {
  it('leaves a bird with a common name unchanged, with no hint', () => {
    const result = formatDetectionTitle({
      display_name: 'European Robin',
      title_hint: null,
    })
    expect(result).toEqual({
      label: 'European Robin',
      hint: null,
      feedingBuzz: false,
      withdrawn: false,
    })
    expect(formatDetectionTitleText({ display_name: 'European Robin', title_hint: null })).toBe(
      'European Robin',
    )
  })

  it('shows the frequency and candidate for a bat pass', () => {
    const result = formatDetectionTitle({
      display_name: 'bat pass',
      title_hint: '45 kHz · common pipistrelle?',
    })
    expect(result.label).toBe('bat pass')
    expect(result.hint).toBe('45 kHz · common pipistrelle?')
    expect(result.hint).toContain('?')
    expect(result.feedingBuzz).toBe(false)
  })

  it('carries the ambiguity note for an ambiguous band', () => {
    const result = formatDetectionTitle({
      display_name: 'bat pass',
      title_hint: '21 kHz · noctule / serotine? (may be a bush-cricket)',
    })
    expect(result.hint).toBe('21 kHz · noctule / serotine? (may be a bush-cricket)')
    expect(result.hint).toContain('?')
    expect(result.hint).toContain('may be a bush-cricket')
  })

  it('flags a feeding buzz from flags.feeding_buzz', () => {
    const source = {
      display_name: 'bat pass',
      title_hint: '45 kHz · common pipistrelle?',
      flags: { feeding_buzz: true },
    }
    const result = formatDetectionTitle(source)
    expect(result.feedingBuzz).toBe(true)
    expect(formatDetectionTitleText(source)).toBe(
      'bat pass · 45 kHz · common pipistrelle? · feeding buzz',
    )
  })

  it('falls back to native_result.has_feeding_buzz when flags is absent', () => {
    const result = formatDetectionTitle({
      display_name: 'bat pass',
      title_hint: '45 kHz · common pipistrelle?',
      native_result: { has_feeding_buzz: true },
    })
    expect(result.feedingBuzz).toBe(true)
  })

  it('has no candidate when the frequency is outside every band', () => {
    const result = formatDetectionTitle({
      display_name: 'bat pass',
      title_hint: '150 kHz',
    })
    expect(result.hint).toBe('150 kHz')
    expect(result.hint).not.toContain('?')
  })

  it('is null for a bat pass with no title_hint at all', () => {
    const result = formatDetectionTitle({
      display_name: 'bat pass',
      title_hint: null,
    })
    expect(result.hint).toBeNull()
  })

  it('tolerates a null label, falling back to display_name', () => {
    const result = formatDetectionTitle({
      display_name: 'unknown',
      title_hint: null,
    })
    expect(result.label).toBe('unknown')
    expect(result.hint).toBeNull()
    expect(result.feedingBuzz).toBe(false)
  })

  it('tolerates a missing detection entirely', () => {
    expect(formatDetectionTitle(undefined)).toEqual({
      label: 'unknown',
      hint: null,
      feedingBuzz: false,
      withdrawn: false,
    })
  })

  // ---- ADR-044: a withdrawn identification --------------------------------
  //
  // The station keeps the row and marks it, so the UI must never print the
  // species name on its own. Three shapes reach these render sites and all
  // three carry the fact differently.

  it('marks a row the API reported as withdrawn', () => {
    const result = formatDetectionTitle({
      display_name: 'Western Screech-Owl',
      withdrawn: true,
    })
    expect(result.label).toBe('Western Screech-Owl')
    expect(result.withdrawn).toBe(true)
  })

  it('marks it from flags.withdrawn on a list row', () => {
    expect(
      formatDetectionTitle({
        display_name: 'Western Screech-Owl',
        flags: { feeding_buzz: false, withdrawn: true },
      }).withdrawn,
    ).toBe(true)
  })

  it('marks it from a raw native_result on a live WebSocket frame', () => {
    expect(
      formatDetectionTitle({
        display_name: 'Flammulated Owl',
        native_result: { plausibility_review: { implausible: true } },
      }).withdrawn,
    ).toBe(true)
  })

  it('does not mark a row that was reviewed and cleared', () => {
    // The block records that a review happened; the boolean records the
    // verdict. Confusing the two would withdraw every row anyone checked.
    expect(
      formatDetectionTitle({
        display_name: 'Tawny Owl',
        native_result: { plausibility_review: { implausible: false } },
      }).withdrawn,
    ).toBe(false)
  })

  it('does not mark an ordinary detection', () => {
    expect(formatDetectionTitle({ display_name: 'European Robin' }).withdrawn).toBe(false)
  })
})

describe('formatDetectionTitleText', () => {
  it('joins label, hint and buzz marker with the UI separator', () => {
    expect(
      formatDetectionTitleText({
        display_name: 'bat pass',
        title_hint: '45 kHz · common pipistrelle?',
        flags: { feeding_buzz: true },
      }),
    ).toBe('bat pass · 45 kHz · common pipistrelle? · feeding buzz')
  })

  it('omits the hint entirely when there is none', () => {
    expect(formatDetectionTitleText({ display_name: 'European Robin', title_hint: null })).toBe(
      'European Robin',
    )
  })
})
