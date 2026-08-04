import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The built bundle is served by the station's FastAPI process from web/dist,
// so asset paths must be relative to wherever that is mounted.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  server: {
    // `npm run dev` against a live Pi: proxy both REST and WebSocket traffic.
    proxy: {
      '/api': {
        target: process.env.OO_TARGET ?? 'http://station.example:8080',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
