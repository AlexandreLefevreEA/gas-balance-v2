import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Read the monorepo's single root .env (Vite only exposes VITE_-prefixed vars to the browser).
  envDir: '..',
  test: { environment: 'node' },
})
