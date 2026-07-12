import { BrowserWindow, shell } from "electron";
import { join } from "node:path";
import { is } from "@electron-toolkit/utils";

export function createMainWindow(apiBaseUrl: string): BrowserWindow {
  const win = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 1040,
    minHeight: 680,
    title: "OneStep",
    show: false,
    autoHideMenuBar: true,
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "hidden",
    backgroundColor: "#0b0c0f",
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--onestep-api-base-url=${apiBaseUrl}`],
    },
  });
  const rendererUrl = process.env.ELECTRON_RENDERER_URL;

  win.webContents.setWindowOpenHandler((details) => {
    void shell.openExternal(details.url);
    return { action: "deny" };
  });

  win.webContents.on("will-navigate", (event, url) => {
    if (url.startsWith(apiBaseUrl) || (rendererUrl && url.startsWith(rendererUrl)) || url.startsWith("file:")) {
      return;
    }
    event.preventDefault();
    void shell.openExternal(url);
  });

  if (is.dev && rendererUrl) {
    void win.loadURL(rendererUrl);
  } else {
    void win.loadFile(join(__dirname, "../renderer/index.html"));
  }

  win.once("ready-to-show", () => win.show());
  return win;
}
