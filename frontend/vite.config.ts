import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Dev server (optional, `npm run dev`) proxies to a backend on localhost:8000.
// In compose, nginx serves the build and proxies /api and /ws (see nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
});
