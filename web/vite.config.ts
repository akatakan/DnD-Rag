import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    // Three is an optional lazy-loaded 524 KiB ESM vendor chunk. Keep the main
    // application and cannon physics separate while warning on larger regressions.
    chunkSizeWarningLimit: 550,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "dice-three",
              test: /node_modules[\\/]three[\\/]/,
            },
            {
              name: "dice-physics",
              test: /node_modules[\\/]cannon-es[\\/]/,
            },
          ],
        },
      },
    },
  },
  server: {
    allowedHosts: ['33f2-78-190-50-152.ngrok-free.app'],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
})
