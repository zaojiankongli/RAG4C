import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const drawerPath = "/src/retrieval-quality/components/ExperimentDetailDrawer.tsx";
const judgmentStub = resolve(__dirname, "src/test/judgment-editor-lite.tsx");

export default defineConfig({
  plugins: [
    {
      name: "rag4c-experiment-drawer-test-isolation",
      enforce: "pre",
      resolveId(source, importer) {
        const normalizedImporter = importer?.replaceAll("\\", "/") ?? "";
        if (source === "./JudgmentEditor" && normalizedImporter.endsWith(drawerPath)) {
          return judgmentStub;
        }
        return null;
      },
    },
    react(),
  ],
  test: {
    environment: "jsdom",
  },
});
