/** Regression cover for ADR-054: the controls must stay on the page.
 *
 *  The bug this exists to stop coming back: `.topbar-right` held the channel
 *  switch, GO LIVE and the clock and had no `flex-wrap`, so on a phone those
 *  controls overflowed to the right of the viewport — and a
 *  `html, body { overflow-x: hidden }` in the mobile block then *hid* the
 *  overflow, so GO LIVE could not even be scrolled to. The page reported a
 *  clean `scrollWidth` while the operator, standing in the garden, could not
 *  start the live stream.
 *
 *  jsdom has no layout engine, so nothing here can measure a bounding box —
 *  that is done with a real headless browser at 360/390/414/430 px and is
 *  recorded in ADR-054. What *is* checkable here, and is exactly what silently
 *  regressed before, is the stylesheet's own text and the DOM structure the
 *  stylesheet depends on. Both are cheap, and both would have failed on the
 *  broken code.
 */

// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Spectrogram } from './components/Spectrogram'
import type { SpectrogramSpec } from './types'

/** The stylesheet's own source text.
 *
 *  Read off disk rather than imported: vitest stubs CSS modules to the empty
 *  string by default, `?raw` included, and turning CSS processing on for the
 *  whole suite to satisfy one test would be a poor trade. `@types/node` is a
 *  dev dependency for exactly this (`vite.config.ts` already reads
 *  `process.env`, it simply is not in `tsconfig`'s `include`).
 */
const CSS = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8')

/** Body of the first rule whose selector list contains `selector`, with
 *  comments stripped. Deliberately naive: this is a stylesheet, not a
 *  language, and a parser here would be more code than the thing it tests. */
function rules(): Array<{ selectors: string[]; body: string }> {
  const stripped = CSS.replace(/\/\*[\s\S]*?\*\//g, '')
  return [...stripped.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, selector, body]) => ({
    selectors: selector.split(',').map((one: string) => one.trim()),
    body,
  }))
}

function ruleBody(selector: string): string {
  const found = rules().find((rule) => rule.selectors.includes(selector))
  if (!found) throw new Error(`no rule found for ${selector}`)
  return found.body
}

describe('the header control cluster wraps instead of overflowing', () => {
  it('gives .topbar-right flex-wrap — the outer .topbar wrapping is not enough', () => {
    // The overflow happened *inside* this box: `.topbar` already wrapped and
    // GO LIVE was still off-screen.
    expect(ruleBody('.topbar-right')).toMatch(/flex-wrap:\s*wrap/)
  })

  it('gives .listen flex-wrap at every width, not only under a breakpoint', () => {
    // `.listen` holds the channel switch, GO LIVE, the volume slider and the
    // playback telemetry. It ran out of room well above 640px on a narrow
    // desktop window too.
    expect(ruleBody('.listen')).toMatch(/flex-wrap:\s*wrap/)
  })

  it('lets .topbar-right and .listen shrink below their content width', () => {
    expect(ruleBody('.topbar-right')).toMatch(/min-width:\s*0/)
    expect(ruleBody('.listen')).toMatch(/min-width:\s*0/)
  })
})

describe('overflow is fixed at the source, never hidden', () => {
  it('does not clip horizontal overflow on html or body', () => {
    // `overflow-x: hidden` here is what turned an unreachable button into an
    // invisible one. If a control ever escapes the viewport again it must be
    // scrollable — and visible in the measurement — not silently swallowed.
    const offending = rules().filter(
      (rule) =>
        rule.selectors.some((one) => one === 'html' || one === 'body') &&
        /overflow(-x)?:\s*hidden/.test(rule.body),
    )
    expect(offending.map((rule) => rule.selectors.join(', '))).toEqual([])
  })
})

describe('the spectrogram badge strip can leave the plot', () => {
  const SPEC: SpectrogramSpec = {
    channel: 0,
    name: 'audible',
    sample_rate: 48000,
    bins: 192,
    min_hz: 80,
    max_hz: 15000,
    hop_s: 0.024,
    fft_size: 2048,
    floor_db: -95,
    ceiling_db: -15,
    columns_emitted: 0,
    history_columns: 1250,
  }

  function renderPlot() {
    return render(
      <Spectrogram
        spec={SPEC}
        register={vi.fn(() => vi.fn())}
        detections={[]}
        palette="observatory"
        windowSeconds={30}
        blackPoint={0.13}
        whitePoint={0.72}
        showDetections
        orientation="scroll"
        height={250}
      />,
    )
  }

  it('renders the badges as a sibling of the plot, not inside it', () => {
    // This is what lets the narrow breakpoint turn the strip into ordinary
    // flow beneath the plot. Nested inside `.spectrogram` it could only ever
    // be an overlay, where it was clipped by the plot width and drawn over by
    // detection labels.
    const { container } = renderPlot()
    const badges = container.querySelector('.spectrogram-badges')
    const plot = container.querySelector('.spectrogram')
    expect(badges).not.toBeNull()
    expect(plot).not.toBeNull()
    expect(plot!.contains(badges!)).toBe(false)
    expect(badges!.parentElement).toBe(plot!.parentElement)
    expect(badges!.parentElement!.className).toBe('spectrogram-figure')
  })

  it('keeps every badge — the numbers that say what the picture is', () => {
    // ADR-016/028: a stat that disappears at 400px is a stat the operator
    // cannot check from the garden. Moving the strip must never thin it.
    renderPlot()
    for (const text of ['audible', '80 Hz–15 kHz', '192 bins', '24 ms/col', 'FFT 2048', 'scroll']) {
      expect(screen.getByText(text)).toBeInTheDocument()
    }
  })

  it('drops the badge strip out of the overlay at the narrow breakpoint', () => {
    const narrow = CSS.slice(CSS.indexOf('@media (max-width: 640px)'))
    const block = narrow.slice(0, narrow.indexOf('\n}'))
    expect(block).toMatch(/\.spectrogram-badges\s*\{[^}]*position:\s*static/)
  })
})

describe('the history window picker keeps all six windows reachable', () => {
  it('uses the wrapping segmented variant rather than dropping options', () => {
    expect(ruleBody('.segmented-wrap')).toMatch(/flex-wrap:\s*wrap/)
  })
})
