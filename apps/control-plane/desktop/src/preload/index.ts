import { contextBridge } from "electron";

contextBridge.exposeInMainWorld("onestepDesktop", {
  platform: process.platform,
});
