import { test } from "@playwright/test";
import { _electron as electron } from "playwright";

test("desktop workbench opens", async () => {
  const app = await electron.launch({
    args: ["out/main/index.js"],
    cwd: process.cwd(),
  });
  try {
    const page = await app.firstWindow();
    await page.getByRole("heading", { name: "Command Center", level: 1 }).waitFor({ state: "visible" });
    await page.getByLabel("Services").waitFor({ state: "visible" });
    await page.getByLabel("Services").click();
    await page.getByRole("heading", { name: "Services", level: 1 }).waitFor({ state: "visible" });
  } finally {
    await app.close();
  }
});
