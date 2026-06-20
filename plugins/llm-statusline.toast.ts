// OpenCode plugin: llm-statusline (toast variant)
//
// Multi-provider quota + cost toast for the OpenCode TUI. Self-contained:
// the Python statusline script is vendored inside this repo under
// ``python/`` — no dependency on the Claude Code install at all.
//
// Architecture
// ------------
// 1. OpenCode fires ``session.idle`` after each model response.
// 2. The plugin queries ``client.session.messages()`` and aggregates token
//    totals from AssistantMessage payloads in a module-level
//    ``Map<sessionId, TokenState>`` (same pattern as the log-panel variant).
// 3. It writes a Claude-Code-compatible JSONL entry so the Python script
//    can read it like a real Claude Code session.
// 4. It spawns ``python/session_tokens.py`` with the project cwd, session
//    id, and a synthesised stdin payload.
// 5. The script's rendered 3-line bar is delivered via ``client.tui.showToast``
//    (default 5 min duration, configurable via ``LLM_STATUSLINE_TOAST_MS``)
//    and also logged to ``:open-logs`` as a fallback.
//    If the bar text is identical to the previous one for the same session,
//    the call is skipped (dedupe) to avoid triggering the TUI's internal
//    rate limit.
//
// Environment
// -----------
// MINIMAX_API_KEY and/or OPENROUTER_API_KEY in shell.
//
// Installation
// ------------
// 1. Symlink this file into ``~/.config/opencode/plugins/``:
//    ``ln -sf <repo>/plugins/llm-statusline.toast.ts ~/.config/opencode/plugins/llm-statusline.ts``
// 2. Add ``"plugin": ["./plugins/llm-statusline.ts"]`` (or ``"llm-statusline.toast"``)
//    to ``~/.config/opencode/opencode.jsonc``.
// 3. Set ``MINIMAX_API_KEY`` and/or ``OPENROUTER_API_KEY`` in the shell that
//    launches OpenCode (the spawned Python script reads them).
// 4. Optional env vars:
//    - ``LLM_STATUSLINE_PYTHON``: override the ``python3`` interpreter.
//    - ``LLM_STATUSLINE_TOAST_MS``: override the toast duration in ms
//      (default 300000 = 5 min).
//    - ``OPENCODE_PROJECT_DIR``: pin the folder shown in the toast.
//
// Gotchas
// -------
// - OpenCode's toast popup does not render ANSI escape codes; we strip them
//   before calling ``client.tui.showToast``. Original colors stay in the bar
//   when the same script runs under Claude Code.
// - The toast auto-dismisses after ``TOAST_MS`` (default 5 min). Increase
//   ``LLM_STATUSLINE_TOAST_MS`` if you want it even longer. The bar is also
//   logged to ``:open-logs`` on every update so you can always find it there.
// - OpenCode version is detected via ``opencode --version`` at plugin init
//   and shown as ``📟 v1.17.8`` in the bar.
// - Failures in showToast, mkdir, writeFile, or Python spawn are logged via
//   ``client.app.log({ level: "warn" })`` instead of being silently dropped,
//   so they show up in OpenCode's log panel (``:open-logs``).
// - OpenCode 1.17.8 plugin SDK does not expose tool-call parts, so the bar
//   reflects the shell cwd (or ``OPENCODE_PROJECT_DIR``), not the live
//   project the agent is editing.

import type { Plugin } from "@opencode-ai/plugin"
import { spawn } from "node:child_process"
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const PLUGIN_ROOT = resolve(HERE, "..")
const SCRIPT = join(PLUGIN_ROOT, "python", "session_tokens.py")
const PYTHON =
  process.env.LLM_STATUSLINE_PYTHON ??
  process.env.MINIMAX_STATUSLINE_PYTHON ??
  "python3"

const CACHE_DIR = join(homedir(), ".cache", "opencode-llm-statusline")
const CACHE_FILE = join(CACHE_DIR, "opencode-statusline.txt")

const PLUGIN_NAME = "llm-statusline.toast"
const TOAST_MS_DEFAULT = 300_000
const TOAST_MS = (() => {
  const raw = process.env.LLM_STATUSLINE_TOAST_MS
  const n = raw == null ? NaN : Number(raw)
  return Number.isFinite(n) && n > 0 ? n : TOAST_MS_DEFAULT
})()

// Detect the real OpenCode version at module init time (runs once per
// process). Falls back to "opencode" if the subprocess fails or times out.
let OPENCODE_VERSION = "opencode"
function detectOpenCodeVersion(): Promise<string> {
  return new Promise((resolve) => {
    const proc = spawn("opencode", ["--version"], {
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 5_000,
    })
    const out: Buffer[] = []
    proc.stdout.on("data", (chunk: Buffer) => out.push(chunk))
    proc.on("error", () => resolve("opencode"))
    proc.on("close", (code: number | null) => {
      if (code !== 0) { resolve("opencode"); return }
      const raw = Buffer.concat(out).toString().trim()
      // Typical output: "1.17.8" or "opencode 1.17.8" — extract the version.
      const m = raw.match(/(\d+\.\d+\.\d+)/)
      resolve(m ? m[1] : raw.slice(0, 40) || "opencode")
    })
  })
}
// Kick off detection eagerly; the promise settles by the time the first
// session.idle fires.
const _versionPromise = detectOpenCodeVersion().then((v) => { OPENCODE_VERSION = v })

// Module-level state — shared across all events of all sessions.
const sessionState = new Map<string, TokenState>()
const lastShownBar = new Map<string, string>()

interface TokenCache {
  read: number
  write: number
}

interface Tokens {
  input: number
  output: number
  cache: TokenCache
}

interface AssistantMsg {
  role: string
  providerID: string
  modelID: string
  tokens: Tokens
}

interface TokenState {
  input: number
  output: number
  cache_read: number
  cache_creation: number
  model: string
  lastTimestamp: string
  requests: number
}

interface ExtractedUsage {
  input: number
  output: number
  cache_read: number
  cache_creation: number
  model: string
  session_id: string
  timestamp: string
}

interface RunResult {
  ok: boolean
  output: string
}

interface Client {
  session: {
    messages(opts: { path: { id: string } }): Promise<{
      data?: Array<{ info: AssistantMsg }>
    }>
  }
  tui: {
    showToast(opts: {
      body: {
        message: string
        variant: string
        title?: string
        duration?: number
      }
    }): Promise<unknown>
  }
  app: {
    log(input: { body: Record<string, unknown> }): Promise<unknown>
  }
}

function pickNumber(...candidates: unknown[]): number {
  for (const c of candidates) {
    if (typeof c === "number" && Number.isFinite(c)) return c
    if (typeof c === "string" && c.trim() !== "" && !Number.isNaN(Number(c))) {
      return Number(c)
    }
  }
  return 0
}

function projectHash(dir: string): string {
  return dir.split(sep).join("-")
}

// Strip ANSI CSI SGR sequences (ESC [ ... m). Uses String.fromCharCode to
// avoid encoding pitfalls when the source file is round-tripped through
// editors that strip the ESC byte.
const ESC = String.fromCharCode(0x1b)
const ANSI_SGR_RE = new RegExp(`${ESC}\\[[0-9;]*m`, "g")
function stripAnsi(s: string): string {
  return s.replace(ANSI_SGR_RE, "")
}

function summarize(text: string): string {
  const clean = stripAnsi(text)
  const lines = clean
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0)
  return lines.slice(0, 3).join(" • ") || "(empty)"
}

function extractUsage(
  msgs: Array<{ info?: AssistantMsg }>,
  sessionID: string,
): ExtractedUsage {
  let input = 0
  let output = 0
  let cacheRead = 0
  let cacheWrite = 0
  let modelID = "opencode/unknown"
  for (const m of msgs) {
    const info = m?.info
    if (!info || info.role !== "assistant") continue
    input += info.tokens?.input || 0
    output += info.tokens?.output || 0
    cacheRead += info.tokens?.cache?.read || 0
    cacheWrite += info.tokens?.cache?.write || 0
    modelID = `${info.providerID || "?"}/${info.modelID || "?"}`
  }
  return {
    input: pickNumber(input),
    output: pickNumber(output),
    cache_read: pickNumber(cacheRead),
    cache_creation: pickNumber(cacheWrite),
    model: modelID,
    session_id: sessionID,
    timestamp: new Date().toISOString(),
  }
}

function runStatusline(
  projectDir: string,
  sessionID: string,
  stdinPayload: Record<string, unknown>,
): Promise<RunResult> {
  return new Promise((resolve) => {
    const proc = spawn(PYTHON, [SCRIPT], {
      env: {
        ...process.env,
        CLAUDE_PROJECT_DIR: projectDir,
        CLAUDE_SESSION_ID: sessionID,
      },
      stdio: ["pipe", "pipe", "pipe"],
    })

    const out: Buffer[] = []
    const err: Buffer[] = []
    let settled = false
    const finish = (result: RunResult) => {
      if (settled) return
      settled = true
      resolve(result)
    }

    proc.stdout.on("data", (chunk: Buffer) => out.push(chunk))
    proc.stderr.on("data", (chunk: Buffer) => err.push(chunk))
    proc.on("error", (e: Error) =>
      finish({
        ok: false,
        output: `[${PLUGIN_NAME}] spawn error: ${e.message}`,
      }),
    )
    proc.on("close", (code: number | null) => {
      const stdout = Buffer.concat(out).toString().trim()
      const stderr = Buffer.concat(err).toString().trim()
      if (code !== 0 || !stdout) {
        finish({
          ok: false,
          output: `[${PLUGIN_NAME}] exit=${code ?? "?"} stderr=${stderr.slice(0, 200) || "(empty)"}`,
        })
        return
      }
      finish({ ok: true, output: stdout })
    })

    try {
      proc.stdin.write(JSON.stringify(stdinPayload))
      proc.stdin.end()
    } catch (e) {
      finish({
        ok: false,
        output: `[${PLUGIN_NAME}] stdin write failed: ${(e as Error).message}`,
      })
    }
  })
}

async function logWarn(
  client: Client,
  message: string,
  extra: Record<string, unknown> = {},
): Promise<void> {
  try {
    await client.app.log({
      body: { service: PLUGIN_NAME, level: "warn", message, extra },
    })
  } catch {
    /* nothing else we can do; the log panel itself is unavailable */
  }
}

async function logInfo(
  client: Client,
  message: string,
  extra: Record<string, unknown> = {},
): Promise<void> {
  try {
    await client.app.log({
      body: { service: PLUGIN_NAME, level: "info", message, extra },
    })
  } catch {
    /* noop */
  }
}

async function handleSessionIdle(
  client: Client,
  projectDir: string,
  event: { properties?: Record<string, unknown> },
): Promise<void> {
  const sessionID = (event?.properties?.sessionID ?? "") as string
  if (!sessionID) return

  let msgs: Array<{ info?: AssistantMsg }> = []
  try {
    const res = await client.session.messages({ path: { id: sessionID } })
    msgs = (res?.data ?? []) as Array<{ info?: AssistantMsg }>
  } catch (e) {
    await logWarn(client, "session.messages() failed", {
      session_id: sessionID,
      error: (e as Error).message,
    })
    return
  }

  const usage = extractUsage(msgs, sessionID)
  const now = usage.timestamp

  // Accumulate per-session totals across multiple events so the Python
  // script's running aggregation matches what Claude Code would feed it.
  const prev = sessionState.get(sessionID)
  const acc: TokenState = {
    input: (prev?.input ?? 0) + usage.input,
    output: (prev?.output ?? 0) + usage.output,
    cache_read: (prev?.cache_read ?? 0) + usage.cache_read,
    cache_creation: (prev?.cache_creation ?? 0) + usage.cache_creation,
    model: usage.model || prev?.model || "opencode/unknown",
    lastTimestamp: now,
    requests: (prev?.requests ?? 0) + 1,
  }
  sessionState.set(sessionID, acc)

  // Write JSONL entry using ACCUMULATED totals so the Python parser sees
  // the same running total it would see under Claude Code.
  const dir = join(homedir(), ".claude", "projects", projectHash(projectDir))
  try {
    mkdirSync(dir, { recursive: true })
    appendFileSync(
      join(dir, `${sessionID}.jsonl`),
      JSON.stringify({
        type: "assistant",
        sessionId: sessionID,
        timestamp: now,
        message: {
          role: "assistant",
          model: acc.model,
          usage: {
            input_tokens: acc.input,
            output_tokens: acc.output,
            cache_creation_input_tokens: acc.cache_creation,
            cache_read_input_tokens: acc.cache_read,
          },
        },
      }) + "\n",
      "utf8",
    )
  } catch (e) {
    await logWarn(client, "jsonl write failed", {
      session_id: sessionID,
      error: (e as Error).message,
    })
  }

  // Synthesise a stdin payload so the Python script can render the model
  // + cwd + version lines even if OpenCode does not provide them.
  const stdinPayload: Record<string, unknown> = {
    model: { id: acc.model },
    workspace: { current_dir: projectDir },
    version: OPENCODE_VERSION,
    context_window: { used_percentage: 0 },
    cost: { total_duration_ms: 0 },
  }

  const result = await runStatusline(projectDir, sessionID, stdinPayload)
  if (!result.ok) {
    await logWarn(client, summarize(result.output), {
      session_id: sessionID,
      ok: false,
      requests: acc.requests,
    })
    return
  }

  // Cache the rendered bar (after strip) for external scrapers.
  const barClean = stripAnsi(result.output)
  try {
    mkdirSync(CACHE_DIR, { recursive: true })
    writeFileSync(CACHE_FILE, barClean + "\n", "utf8")
  } catch (e) {
    await logWarn(client, "cache write failed", {
      session_id: sessionID,
      error: (e as Error).message,
    })
  }

  // Dedupe: skip showToast if the bar text is identical to the previous
  // one for the same session. Avoids hammering the TUI toast queue with
  // identical updates, which can trigger an internal rate limit.
  const prevBar = lastShownBar.get(sessionID)
  if (prevBar === barClean) {
    await logInfo(client, "toast deduplicated (unchanged bar)", {
      session_id: sessionID,
      requests: acc.requests,
    })
    return
  }

  const lines = barClean.split("\n").filter((l) => l.trim().length > 0)
  const message = lines.length > 0 ? lines.join("\n") : "no data yet"

  try {
    await client.tui.showToast({
      body: {
        message,
        variant: "info",
        title: "Quota",
        duration: TOAST_MS,
      },
    })
    lastShownBar.set(sessionID, barClean)
    // Also log the bar to the app log so it's visible in :open-logs after
    // the toast auto-dismisses.
    await logInfo(client, summarize(result.output), {
      session_id: sessionID,
      ok: true,
      requests: acc.requests,
    })
  } catch (e) {
    await logWarn(client, "showToast failed", {
      session_id: sessionID,
      error: (e as Error).message,
    })
  }
}

export const LLMStatuslineToast: Plugin = async ({ client, directory }) => {
  // Priority: explicit env var > opencode directory > shell cwd.
  const fallbackCwd =
    process.env.OPENCODE_PROJECT_DIR ?? directory ?? process.cwd()

  const c = client as unknown as Client

  await logInfo(c, "plugin loaded", {
    project: fallbackCwd,
    toast_ms: TOAST_MS,
    python: PYTHON,
    script: SCRIPT,
  })

  return {
    "session.idle": (event: unknown) =>
      handleSessionIdle(
        c,
        fallbackCwd,
        event as { properties?: Record<string, unknown> },
      ),

    // Generic catch-all for OpenCode versions that emit a generic
    // ``event`` handler instead of typed ``session.idle``.
    event: ({ event }: { event: { type?: string; properties?: Record<string, unknown> } }) => {
      if (event?.type !== "session.idle") return Promise.resolve()
      return handleSessionIdle(c, fallbackCwd, event)
    },
  }
}
