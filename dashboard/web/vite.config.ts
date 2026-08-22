import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import http from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { fileURLToPath, URL } from "node:url";

const FALLBACK_PORTS = [4090, 8787];

function candidatePorts(): number[] {
  const envPort = Number(process.env.DASHBOARD_PORT || 0);
  const ports = [envPort, ...FALLBACK_PORTS].filter((port) => port > 0);
  return [...new Set(ports)];
}

function probe(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.request(
      { host: "127.0.0.1", port, path: "/healthz", method: "GET", timeout: 400 },
      (res) => {
        res.resume();
        resolve((res.statusCode || 500) < 500);
      },
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

async function detectPort(preferred = 0): Promise<number> {
  const ports = preferred ? [preferred, ...candidatePorts()] : candidatePorts();
  const unique = [...new Set(ports)];
  for (const port of unique) {
    if (await probe(port)) return port;
  }
  return unique[0] || 4090;
}

function pipeTo(req: IncomingMessage, res: ServerResponse, port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const headers = { ...req.headers, host: `127.0.0.1:${port}` };
    const upstream = http.request(
      {
        hostname: "127.0.0.1",
        port,
        path: req.url,
        method: req.method,
        headers,
      },
      (pres) => {
        res.writeHead(pres.statusCode || 502, pres.headers);
        pres.pipe(res);
        pres.on("end", resolve);
      },
    );
    upstream.on("error", reject);
    req.pipe(upstream);
  });
}

function dashboardApiProxy(): Plugin {
  let livePort = 0;
  let lastProbe = 0;

  return {
    name: "dashboard-api-proxy",
    configureServer(server) {
      server.httpServer?.once("listening", async () => {
        livePort = await detectPort();
        console.log(
          `[dashboard-proxy] API → http://127.0.0.1:${livePort} (tries ${candidatePorts().join(", ")})`,
        );
      });
      server.middlewares.use(async (req, res, next) => {
        const url = req.url || "";
        if (!url.startsWith("/api") && !url.startsWith("/invite")) {
          next();
          return;
        }
        const now = Date.now();
        if (!livePort || now - lastProbe > 4000) {
          livePort = await detectPort(livePort);
          lastProbe = now;
        }
        try {
          await pipeTo(req, res, livePort);
        } catch {
          livePort = await detectPort();
          lastProbe = Date.now();
          try {
            await pipeTo(req, res, livePort);
          } catch {
            res.statusCode = 502;
            res.setHeader("Content-Type", "application/json");
            res.end(JSON.stringify({
              detail:
                "Dashboard API is not running. Start scripts/start_dashboard_stack.sh (port 4090) or scripts/run_dashboard.sh (port 8787).",
            }));
          }
        }
      });
    },
  };
}

export default defineConfig({
  base: "/next/",
  plugins: [react(), dashboardApiProxy()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "dist",
    assetsDir: "assets",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
  },
});
