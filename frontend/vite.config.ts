import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

const devApiProxyTarget = process.env.VITE_DEV_API_PROXY_TARGET ?? 'http://api:8000';
const isQa = process.env.APP_ENV === 'test';

export default defineConfig({
  plugins: [
    react(),
    ...(
      isQa
        ? []
        : [
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
          ]
    )
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': devApiProxyTarget,
      '/health': devApiProxyTarget
    }
  },
  preview: {
    proxy: {
      '/api': devApiProxyTarget,
      '/health': devApiProxyTarget
    }
  }
});
