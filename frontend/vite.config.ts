import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

// https://vite.dev/config/
const backendOrigin = process.env.BACKEND_ORIGIN ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Default unchanged; BACKEND_ORIGIN lets the e2e harness point the same dev
  // server at a throwaway API without taking port 8000 from a running backend.
  server: {
    hmr: {
      overlay: true,
    },
    watch: {
      usePolling: true,
      interval: 250,
    },
    proxy: {
      '/backend': {
        target: backendOrigin,
        changeOrigin: true,
        rewrite: (requestPath) => requestPath.replace(/^\/backend/, ''),
      },
      '/v1': {
        target: backendOrigin,
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/react') || id.includes('node_modules/react-dom')) {
            return 'react-vendor'
          }
          if (id.includes('node_modules/lucide-react')) {
            return 'icons-vendor'
          }
        },
      },
    },
  },
})
