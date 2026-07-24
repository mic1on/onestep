import { app } from "electron";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

export type DesktopPaths = {
  repoRoot: string;
  userDataDir: string;
  databasePath: string;
  packageStorageDir: string;
  backendLogPath: string;
  uiDistDir: string;
  alembicIniPath: string;
  alembicScriptLocation: string;
  packagedBackendPath: string;
};

export function resolveDesktopPaths(): DesktopPaths {
  const userDataDir = app.getPath("userData");
  const repoRoot = app.isPackaged ? process.resourcesPath : resolve(__dirname, "..", "..", "..");
  const resourceRoot = app.isPackaged ? process.resourcesPath : repoRoot;

  return {
    repoRoot,
    userDataDir,
    databasePath: join(userDataDir, "control-plane.db"),
    packageStorageDir: join(userDataDir, "worker-packages"),
    backendLogPath: join(userDataDir, "logs", "backend.log"),
    uiDistDir: app.isPackaged ? join(resourceRoot, "frontend", "dist") : join(repoRoot, "frontend", "dist"),
    alembicIniPath: app.isPackaged ? join(resourceRoot, "alembic.ini") : join(repoRoot, "alembic.ini"),
    alembicScriptLocation: app.isPackaged
      ? join(resourceRoot, "backend", "alembic")
      : join(repoRoot, "backend", "alembic"),
    packagedBackendPath: join(resourceRoot, "backend", "onestep-control-plane-api"),
  };
}

export function getBackendCommand(paths: DesktopPaths): { command: string; args: string[]; cwd: string } {
  if (app.isPackaged && existsSync(paths.packagedBackendPath)) {
    return { command: paths.packagedBackendPath, args: [], cwd: process.resourcesPath };
  }
  return {
    command: "uv",
    args: ["run", "python", "-m", "onestep_control_plane_api.desktop_entry"],
    cwd: paths.repoRoot,
  };
}
