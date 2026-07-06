import path from 'node:path';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src')
    }
  },
  server: {
    proxy: {
      '/trpc': {
        target: 'http://localhost:4000',
        changeOrigin: true
      },
      // Bull Board -- proxied too so its auth cookie is same-origin in dev, matching prod
      // where the backend serves everything from one origin.
      '/admin': {
        target: 'http://localhost:4000',
        changeOrigin: true
      }
    }
  }
});
