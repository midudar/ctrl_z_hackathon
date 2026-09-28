import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// В разработке API — на backend (порт 8080); собранный интерфейс раздаёт сам backend
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://localhost:8080' } },
  build: { outDir: 'dist', chunkSizeWarningLimit: 800 },
});
