import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'Vinted Monitor',
        short_name: 'Vinted',
        description: 'Dashboard privado para monitorizar oportunidades de Vinted.',
        theme_color: '#0f766e',
        background_color: '#f7faf9',
        display: 'standalone',
        icons: [
          {
            src: '/pwa-192.png',
            sizes: '192x192',
            type: 'image/png'
          },
          {
            src: '/pwa-512.png',
            sizes: '512x512',
            type: 'image/png'
          }
        ]
      }
    })
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://api:8000',
      '/health': 'http://api:8000'
    }
  }
});
