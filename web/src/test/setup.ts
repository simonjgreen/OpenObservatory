/** Vitest setup, loaded globally via `vite.config.ts`'s `test.setupFiles`.
 *
 *  Two things:
 *
 *  1. Extends `expect` with the jest-dom matchers the component tests use
 *     (`toBeInTheDocument`, `toHaveTextContent`, ...). Loaded globally rather
 *     than per-file, since every component test wants it and forgetting it
 *     produces a confusing "not a function" failure rather than a useful one.
 *
 *  2. Unmounts everything after each test. React Testing Library only
 *     auto-cleans when it can find a global `afterEach`, which depends on the
 *     runner's `globals` setting -- so relying on that is a silent trap.
 *     Without it, each test inherits the DOM left by the previous one and
 *     `getByText` can match an element another test rendered. That produces
 *     false passes as readily as false failures, and it bit a real assertion
 *     on 2026-08-09: a component correctly rendering nothing appeared to
 *     render a leftover card from the test before it.
 */
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})
