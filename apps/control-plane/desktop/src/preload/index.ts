import { contextBridge } from "electron";

const apiArgPrefix = "--onestep-api-base-url=";
const apiBaseUrl =
  process.argv.find((arg) => arg.startsWith(apiArgPrefix))?.slice(apiArgPrefix.length) ?? "";

contextBridge.exposeInMainWorld("onestepDesktop", {
  platform: process.platform,
  apiBaseUrl,
});
