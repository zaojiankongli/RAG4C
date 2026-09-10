import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Tauri 约定：固定端口 + 禁止自动打开浏览器（webview 自己开）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  test: {
    // 为什么把 testTimeout 从默认 5000ms 提到 15000ms：
    // 本项目有一批 jsdom + TDesign 的重型 UI 测试，**单机空闲时**实测最慢约 2.9s
    // （NotificationDrawer / EnterpriseIdentityCenter.stage9）。5s 只留 1.7x 余量，
    // 机器稍有负载就整批 "Test timed out in 5000ms" —— 实测在并行跑后端 pytest 时
    // 会有 3 个文件转红，而**同样这 3 个文件单独跑是 12/12 全绿**（448–2897ms）。
    // 也就是说 5s 制造的是假红灯，不是真缺陷。
    // 15s ≈ 5x 余量：能吸收 CI 抖动，又仍然拦得住真正的挂死。
    testTimeout: 15000,
  },
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
