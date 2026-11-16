import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/drum-studio/',
  plugins: [react()],
  server: {
    proxy: {
      '/drum-sheet-api': {
        target: 'https://stuserver.online',
        changeOrigin: true,
        secure: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          audio: ['tone'],
          midi: ['@tonejs/midi'],
          pdf: ['pdfjs-dist/legacy/build/pdf.mjs'],
        },
      },
    },
  },
});