import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The API is proxied in development so the browser sees a single origin and
// needs no CORS preflight on every plan request. In production Django serves
// this build from the same origin, so the same relative paths keep working.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    // mapbox-gl is large and changes rarely; keeping it in its own chunk means a
    // code change does not force the browser to re-download the map engine.
    rollupOptions: {
      output: {
        manualChunks: { mapbox: ['mapbox-gl'] },
      },
    },
  },
});
