#!/usr/bin/env node
// Запуск Playwright MCP кроссплатформенно (Windows / macOS / Linux).
// - На Windows npx — это .cmd-файл, его нужно запускать через оболочку.
// - Браузер: на Windows берём Microsoft Edge (есть всегда), иначе Chrome; можно переопределить STRATEG_BROWSER.
// - Скриншоты сохраняются в inputs/ux активного проекта, чтобы сразу стать источниками.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const VERSION = "0.0.83"; // закреплённая версия: воспроизводимость важнее новизны
let out = join(ROOT, "projects", "_screenshots");
const active = join(ROOT, "projects", ".active");
if (existsSync(active)) {
  const slug = readFileSync(active, "utf8").trim();
  if (slug && existsSync(join(ROOT, "projects", slug))) out = join(ROOT, "projects", slug, "inputs", "ux");
}
mkdirSync(out, { recursive: true });
const browser = process.env.STRATEG_BROWSER || (process.platform === "win32" ? "msedge" : "chrome");
const args = ["-y", `@playwright/mcp@${VERSION}`, "--headless", "--isolated", "--browser", browser,
  "--output-dir", out, ...(process.env.STRATEG_BROWSER_PATH ? ["--executable-path", process.env.STRATEG_BROWSER_PATH] : [])];
const child = spawn(process.platform === "win32" ? "npx.cmd" : "npx", args, {
  stdio: "inherit", shell: process.platform === "win32",
});
child.on("exit", (code) => process.exit(code ?? 0));
child.on("error", (e) => { console.error("Playwright MCP не запустился:", e.message); process.exit(1); });
