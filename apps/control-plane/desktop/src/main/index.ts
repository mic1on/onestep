import { app, BrowserWindow, dialog } from "electron";
import { startBackend, type BackendRuntime } from "./backend";
import { createMainWindow } from "./window";

let backend: BackendRuntime | null = null;
let mainWindow: BrowserWindow | null = null;
let quitting = false;

async function boot(): Promise<void> {
  backend = await startBackend();
  mainWindow = createMainWindow(backend.baseUrl);
}

app.whenReady().then(() => {
  boot().catch((error) => {
    dialog.showErrorBox("OneStep failed to start", error instanceof Error ? error.message : String(error));
    app.quit();
  });
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0 && backend) {
    mainWindow = createMainWindow(backend.baseUrl);
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", (event) => {
  if (quitting || !backend) {
    return;
  }
  event.preventDefault();
  quitting = true;
  const runtime = backend;
  backend = null;
  runtime.stop().finally(() => app.quit());
});

void mainWindow;
