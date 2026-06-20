// OpenCode plugin: llm-statusline (toast variant)
//
// Multi-provider quota + cost toast for the OpenCode TUI. Self-contained:
// the Python statusline script is vendored inside this repo under
// ``python/`` — no dependency on the Claude Code install at all.
//
// Architecture
// ------------
// 1. ``message.part.updated`` events update a module-level ``currentProject`` so
//    the bar reflects where the agent is actually working (not the shell cwd).
// 2. ``session.idle`` fires → query session messages via client.session.messages()
//    and aggregate token totals from AssistantMessage payloads.
// 3. Write JSONL, spawn Python, show the rendered bar (with ANSI colors) as a
//    TUI toast with title "Quota".
//
// Environment
// -----------
// MINIMAX_API_KEY and/or OPENROUTER_API_KEY in shell.
//
// Installation
// ------------
// 1. Symlink this file into ``~/.config/opencode/plugins/``:
//    ``ln -sf <repo>/plugins/llm-statusline.toast.ts ~/.config/opencode/plugins/llm-statusline.toast.ts``
// 2. Add ``"plugin": ["llm-statusline.toast"]`` to ``~/.config/opencode/opencode.jsonc``.
// 3. Set ``MINIMAX_API_KEY`` and/or ``OPENROUTER_API_KEY`` in the shell that
//    launches OpenCode (the spawned Python script reads them).
// 4. Optional: ``LLM_STATUSLINE_PYTHON`` to override the ``python3`` interpreter.
//
// Gotchas
// -------
// - OpenCode's toast popup does not render ANSI escape codes; we strip them
//   before calling ``client.tui.showToast``. Original colors stay in the bar
//   when the same script runs under Claude Code.
// - Toast duration defaults to 30s. The toast is shown on every ``session.idle``,
//   so back-to-back model responses replace (not stack) the previous toast.
// - Latest rendered bar is cached at
//   ``~/.cache/opencode-llm-statusline/opencode-statusline.txt`` for external
//   tools that want to scrape it without re-running the Python script.
// - Set ``OPENCODE_PROJECT_DIR`` in the shell to pin the project folder shown
//   in the bar without changing the shell cwd (handy when launching from ~).
// - OpenCode 1.17.8 plugin SDK does not expose tool-call parts, so the bar
//   reflects the shell cwd (or ``OPENCODE_PROJECT_DIR``), not the live project
//   the agent is editing. See NOTE inside ``session.idle`` handler.

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

// Module-level state — shared across all events of all sessions.
let currentProject: string | null = null

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
}

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

function projectHash(dir: string): string {
  return dir.split(sep).join("-")
}

function stripAnsi(s: string): string {
  return s.replace(/\u001b\[[0-9;]*m/g, "")
}

// Extract a likely project directory from a tool call's arguments.
function extractProjectFromArgs(
  toolName: string,
  args: Record<string, unknown>
): string | null {
  const candidates = [
    args.filePath,
    args.path,
    args.file,
    args.directory,
    args.cwd,
  ]
  for (const c of candidates) {
    if (typeof c === "string" && c.trim().length > 0) {
      return c.startsWith("/") ? dirname(c) : c
    }
  }
  // bash: try to find a `cd <path>` or first absolute path mention.
  if (toolName === "bash" || toolName === "shell") {
    const cmd = String(args.command ?? args.cmd ?? "")
    const cdMatch = cmd.match(/(?:^|[\s;&|])(?:cd|chdir)\s+([^\s;&|]+)/)
    if (cdMatch && cdMatch[1].startsWith("/")) return cdMatch[1]
    const abs = cmd.match(/(\/(?:Users|home|tmp|var|etc|opt|root)\/[^\s'";&|]*)/)
    if (abs) return dirname(abs[1])
  }
  return null
}

function runPython(
  projectDir: string,
  sessionID: string,
  payload: Record<string, unknown>
): Promise<string> {
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
    proc.stdout.on("data", (c: Buffer) => out.push(c))
    proc.stderr.on("data", () => {
      /* ignore */
    })
    proc.on("error", () => resolve(""))
    proc.on("close", (code) => {
      resolve(code === 0 ? Buffer.concat(out).toString().trim() : "")
    })
    try {
      proc.stdin.write(JSON.stringify(payload))
      proc.stdin.end()
    } catch {
      resolve("")
    }
  })
}

export const LLMStatuslineToast: Plugin = async ({ client, directory }) => {
  // Priority: explicit env var > opencode directory > shell cwd.
  // Set OPENCODE_PROJECT_DIR in your shell rc or per-session to pin a
  // project without changing the shell cwd (e.g. when launching from ~).
  const fallbackCwd =
    process.env.OPENCODE_PROJECT_DIR ?? directory ?? process.cwd()

  return {
    event: async ({
      event,
    }: {
      event: { type?: string; properties?: Record<string, unknown> }
    }) => {
      try {
        // (eventos message.part.updated nao carregam tool parts no 1.17.8;
        // tracking acontece dentro de session.idle via client.session.messages())

        if (event?.type !== "session.idle") return
        const sessionID = (event.properties?.sessionID ?? "") as string
        if (!sessionID) return

        const c = client as unknown as Client
        const res = await c.session.messages({ path: { id: sessionID } })
        const msgs = (res?.data ?? []) as any[]

        // NOTE: OpenCode 1.17.8 plugin SDK does NOT expose tool-call parts
        // (no ``info.parts``, only ``text``/``reasoning``/``step-finish`` events).
        // The ``currentProject`` tracker relies on tool events that are server-side
        // only. Until OpenCode exposes tool parts in a future version, the project
        // folder in the toast reflects the shell cwd (``directory ?? process.cwd()``).

        let input = 0,
          output = 0,
          cacheRead = 0,
          cacheWrite = 0
        let modelID = "opencode/unknown"

        for (const m of msgs) {
          const info = m.info
          if (!info || (info as AssistantMsg).role !== "assistant") continue
          const t = (info as AssistantMsg).tokens
          input += t.input || 0
          output += t.output || 0
          cacheRead += t.cache?.read || 0
          cacheWrite += t.cache?.write || 0
          modelID = `${info.providerID || "?"}/${info.modelID || "?"}`
        }

        // Prefer the project the agent is actually working in over the shell cwd.
        const cwd = currentProject ?? fallbackCwd

        const dir = join(homedir(), ".claude", "projects", projectHash(cwd))
        mkdirSync(dir, { recursive: true })
        appendFileSync(
          join(dir, `${sessionID}.jsonl`),
          JSON.stringify({
            type: "assistant",
            sessionId: sessionID,
            timestamp: new Date().toISOString(),
            message: {
              role: "assistant",
              model: modelID,
              usage: {
                input_tokens: input,
                output_tokens: output,
                cache_creation_input_tokens: cacheWrite,
                cache_read_input_tokens: cacheRead,
              },
            },
          }) + "\n",
          "utf8"
        )

        const bar = await runPython(cwd, sessionID, {
          model: { id: modelID },
          workspace: { current_dir: cwd },
          version: "opencode",
          context_window: { used_percentage: 0 },
          cost: { total_duration_ms: 0 },
        })

        if (bar) {
          try {
            mkdirSync(CACHE_DIR, { recursive: true })
            writeFileSync(CACHE_FILE, bar + "\n", "utf8")
          } catch {
            /* noop */
          }
        }

        // Strip ANSI codes — OpenCode's toast popup does not render them and
        // shows them as literal escape characters. Cores ficam so no Claude Code.
        const lines = bar
          ? stripAnsi(bar).split("\n").filter((l: string) => l.trim())
          : []
        const message = lines.length > 0 ? lines.join("\n") : "no data yet"
        try {
          await c.tui.showToast({
            body: {
              message,
              variant: "info",
              title: "Quota",
              duration: 30000,
            },
          })
        } catch {
          /* noop */
        }
      } catch {
        // Never crash the TUI.
      }
    },
  }
}
