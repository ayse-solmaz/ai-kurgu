import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: true } },
  },
  build: {
    rollupOptions: {
      output: {
        // kütüphaneler ayrı dosyalarda: uygulama kodu değişince tarayıcı önbelleği korunur
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("@astryxdesign") || id.includes("@stylexjs")) return "astryx";
          if (id.includes("react-dom") || id.includes("/react/") || id.includes("scheduler")) return "react";
          if (id.includes("@heroicons")) return "icons";
          return "vendor";
        },
      },
    },
    // Astryx tek parça ~600 KB (gzip ~150 KB); yerel uygulama olduğu için uyarı sınırı buna göre
    chunkSizeWarningLimit: 900,
  },
});
