import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";

const versionSource = readFileSync(new URL("../skills/codemap/scripts/codemap_lib/__init__.py", import.meta.url), "utf8");
const appVersion = versionSource.match(/^VERSION = "([0-9A-Za-z.+-]+)"/m)?.[1];
if (!appVersion) throw new Error("Codemap application version is missing");
const packageVersion = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")).version;
if (packageVersion !== appVersion) throw new Error("Dashboard package version differs from the application version");

const noticeFiles = [
  ["react", "LICENSE"], ["react-dom", "LICENSE"], ["scheduler", "LICENSE"],
  ["echarts", "LICENSE"], ["echarts", "NOTICE"], ["zrender", "LICENSE"],
  ["tslib", "LICENSE.txt"], ["tslib", "CopyrightNotice.txt"],
];
const licenses = noticeFiles
  .map(([name, file]) => {
    const license = readFileSync(
      new URL(`./node_modules/${name}/${file}`, import.meta.url),
      "utf8",
    );
    return `Bundled dependency: ${name} (${file})\n${license}`;
  })
  .join("\n\n");

export default defineConfig({
  plugins: [
    {
      name: "codemap-dependency-notices",
      generateBundle(_options, bundle) {
        for (const output of Object.values(bundle)) {
          if (output.type === "chunk")
            output.code = `/*!\n${licenses.replace(/\*\//g, "* /")}\n*/\n${output.code}`;
        }
      },
    },
  ],
  define: { "process.env.NODE_ENV": JSON.stringify("production"), "__CODEMAP_APP_VERSION__": JSON.stringify(appVersion) },
  build: {
    outDir: "../skills/codemap/scripts/codemap_lib/assets",
    emptyOutDir: false,
    lib: {
      entry: fileURLToPath(new URL("./src/main.jsx", import.meta.url)),
      name: "CodemapDashboard",
      formats: ["iife"],
      fileName: () => "history.bundle.js",
      cssFileName: "history",
    },
    target: "es2020",
    sourcemap: false,
    minify: "esbuild",
  },
});
