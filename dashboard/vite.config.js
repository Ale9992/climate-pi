import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// In dev (npm run dev) le chiamate /api vengono proxate al backend FastAPI.
// In produzione la dist viene servita direttamente da main.py (stessa origine).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,            // accessibile da altri device sulla LAN in dev
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
