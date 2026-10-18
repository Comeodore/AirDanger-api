import { build } from "esbuild";
import { fileURLToPath } from "node:url";

const stub = (name) => fileURLToPath(new URL(`./stubs/${name}`, import.meta.url));
const STUBS = {
  "cloudflare:workers": stub("workers.mjs"),
  "@mtcute/web": stub("mtcute.mjs"),
  "./node_modules/@mtcute/convert/telethon/convert.js": stub("mtcute.mjs"),
  "./mtcute.wasm": stub("mtcute.mjs"),
};

await build({
  entryPoints: [fileURLToPath(new URL("../edge.js", import.meta.url))],
  bundle: true,
  format: "esm",
  platform: "node",
  target: "node22",
  outfile: fileURLToPath(new URL("../dist/edge.test.mjs", import.meta.url)),
  logLevel: "warning",
  plugins: [{
    name: "stubs",
    setup(b) {
      b.onResolve({ filter: /.*/ }, (args) => (STUBS[args.path] ? { path: STUBS[args.path] } : undefined));
    },
  }],
});
