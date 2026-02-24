import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/static/',
  plugins: [react()],
  server: {
    // В dev-режиме /api/* проксируется на FastAPI
    proxy: {
      '/api': 'http://localhost:8765',
    },
  },

  build: {
    // npm run build → файлы попадают в ../static
    // FastAPI подхватит их автоматически
    outDir:     '../static',
    emptyOutDir: true,
  },
})
