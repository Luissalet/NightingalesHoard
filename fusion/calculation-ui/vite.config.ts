import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  base: '/calculation/',
  plugins: [react()],
  build: { outDir: '../calculation-static', emptyOutDir: true },
})
