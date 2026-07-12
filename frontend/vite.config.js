import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Standard SPA config. Works for `vite dev` locally and `vite build` on
// Render/Railway static hosting. Env vars prefixed VITE_ are exposed to the app.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
  },
  preview: {
    port: Number(process.env.PORT) || 4173,
    host: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
