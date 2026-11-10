#!/usr/bin/env node
// Запуск MCP-сервера «Стратега»: находит рабочий Python 3.9+ (на Windows "python3" — часто заглушка Store).
import { spawn, spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const candidates = process.platform === "win32" ? [["python"], ["py", "-3"], ["python3"]] : [["python3"], ["python"]];
const ok = candidates.find(([cmd, ...pre]) =>
  spawnSync(cmd, [...pre, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"], { stdio: "ignore" }).status === 0);
if (!ok) { console.error("Стратег MCP: не найден Python 3.9+"); process.exit(1); }
const [cmd, ...pre] = ok;
const child = spawn(cmd, [...pre, join(ROOT, "scripts", "mcp_server.py")], { stdio: "inherit", cwd: ROOT });
child.on("exit", (code) => process.exit(code ?? 0));
