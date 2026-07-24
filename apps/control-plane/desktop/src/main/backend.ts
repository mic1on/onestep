import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { spawn } from "node:child_process";
import { createWriteStream, mkdirSync } from "node:fs";
import { request } from "node:http";
import { dirname } from "node:path";
import { getBackendCommand, resolveDesktopPaths } from "./paths";
import { allocateLocalPort } from "./ports";

export type BackendRuntime = {
  baseUrl: string;
  stop: () => Promise<void>;
};

function waitForHealth(baseUrl: string, timeoutMs: number): Promise<void> {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const retry = (): void => {
      if (Date.now() - startedAt > timeoutMs) {
        reject(new Error(`backend did not become ready within ${timeoutMs}ms`));
        return;
      }
      setTimeout(poll, 250);
    };

    const poll = (): void => {
      const req = request(`${baseUrl}/healthz`, { method: "GET", timeout: 1000 }, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 500) {
          resolve();
          return;
        }
        retry();
      });
      req.on("error", retry);
      req.on("timeout", () => {
        req.destroy();
        retry();
      });
      req.end();
    };

    poll();
  });
}

function stopProcess(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      resolve();
    }, 3000);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
    child.kill("SIGTERM");
  });
}

export async function startBackend(): Promise<BackendRuntime> {
  const paths = resolveDesktopPaths();
  mkdirSync(dirname(paths.backendLogPath), { recursive: true });
  mkdirSync(paths.packageStorageDir, { recursive: true });

  const port = await allocateLocalPort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const command = getBackendCommand(paths);
  const log = createWriteStream(paths.backendLogPath, { flags: "a" });

  const child = spawn(command.command, command.args, {
    cwd: command.cwd,
    env: {
      ...process.env,
      ONESTEP_CP_HOST: "127.0.0.1",
      ONESTEP_CP_PORT: String(port),
      ONESTEP_CP_DATABASE_URL: `sqlite:///${paths.databasePath}`,
      ONESTEP_CP_INGEST_TOKENS: process.env.ONESTEP_CP_INGEST_TOKENS || "desktop-dev-token",
      ONESTEP_CP_CORS_ALLOW_ORIGINS: "",
      ONESTEP_CP_UI_DIST_DIR: paths.uiDistDir,
      ONESTEP_CP_UI_API_BASE_URL: "/",
      ONESTEP_CP_WORKER_PACKAGE_STORAGE_DIR: paths.packageStorageDir,
      ONESTEP_CP_ALEMBIC_INI: paths.alembicIniPath,
      ONESTEP_CP_ALEMBIC_SCRIPT_LOCATION: paths.alembicScriptLocation,
    },
  });

  child.stdout.pipe(log);
  child.stderr.pipe(log);

  await waitForHealth(baseUrl, 30_000);
  return { baseUrl, stop: () => stopProcess(child) };
}
