/** Vitest setup: extends `expect` with the jest-dom matchers used by the
 *  component tests (e.g. `toBeInTheDocument`, `toHaveTextContent`). Loaded
 *  globally via `vite.config.ts`'s `test.setupFiles` rather than per-file,
 *  since every component test wants it and forgetting it produces a
 *  confusing "not a function" failure rather than a useful one. */
import '@testing-library/jest-dom/vitest'
