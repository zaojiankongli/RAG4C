import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Tauri 约定：固定端口 + 禁止自动打开浏览器（webview 自己开）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      // 忽略 src-tauri，避免 Rust 侧文件变更触发前端热重载
      ignored: ["**/src-tauri/**"],
    },
  },
  build: {
    // 页面已按路由懒加载；只固定真正独立的重型能力，避免把整套 Ant Design
    // 强行合并成一个首屏共享大包。
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-react": ["react", "react-dom", "scheduler"],
          "vendor-echarts": ["echarts"],
          "vendor-markdown": ["react-markdown", "remark-gfm"],
        },
        onlyExplicitManualChunks: true,
      },
    },
    chunkSizeWarningLimit: 900,
  },
});
