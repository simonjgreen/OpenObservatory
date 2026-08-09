import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The built bundle is served by the station's FastAPI process from web/dist,
// so asset paths must be relative to wherever that is mounted.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  test: {
    // Pure-logic files opt into `jsdom` per-file with a `@vitest-environment`
    // docblock; the default stays `node` so pure-function tests don't pay for
    // a DOM they never touch. Component tests declare the docblock instead of
    // flipping the default, matching the existing convention in audio.test.ts.
    setupFiles: ['./src/test/setup.ts'],
  },
  server: {
    // `npm run dev`: proxy both REST and WebSocket traffic to the station.
    // Point OO_TARGET at a live Pi (e.g. OO_TARGET=http://<station-host>:8080);
    // the default assumes a station running locally. No real station address
    // is committed here, deliberately (ADR-047).
    proxy: {
      '/api': {
        target: process.env.OO_TARGET ?? 'http://localhost:8080',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
