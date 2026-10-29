import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: `npm run dev` proxies the API to `blackbox serve` on 8377. Build output is served by the backend.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/v1': 'http://127.0.0.1:8377', '/health': 'http://127.0.0.1:8377' } },
  build: { outDir: 'dist', chunkSizeWarningLimit: 900 },
})
