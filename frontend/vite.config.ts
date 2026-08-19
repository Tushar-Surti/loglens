import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// Dev server proxies /api and /ws to the API container so the browser sees a
// single origin — identical to how nginx serves the production build.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': { target: process.env.API_ORIGIN ?? 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: process.env.API_ORIGIN ?? 'http://localhost:8000', ws: true, changeOrigin: true },
    },
  },
  build: {
    target: 'es2020',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // Three.js and the charting layer are heavy and change rarely: keeping
        // them in their own chunks keeps the main bundle cacheable.
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts'],
          three: ['three'],
          motion: ['framer-motion', 'gsap', 'lenis'],
        },
      },
    },
  },
})
