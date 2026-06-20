// OpenCode plugin: llm-statusline
//
// Reuses the multi-provider statusline Python script that powers fcc-claude's
// statusline bar:
//   ~/.claude/statusline/session_tokens.py
//
// On every ``session.idle`` event (after each model response) the plugin:
//
//   1. Accumulates token usage in an in-memory ``Map<sessionId, totals>``.
//   2. Writes a single JSONL entry to ``~/.claude/projects/<hash>/<sessionId>.jsonl``
//      so the Python script can read it like a real Claude Code session.
//   3. Spawns the Python script with ``CLAUDE_PROJECT_DIR`` and
//      ``CLAUDE_SESSION_ID`` set, then logs the rendered bar through
//      ``client.app.log()`` so the user can see it in OpenCode's logs.
//
// The Python script understands gateway model IDs from fcc-claude, the
// MiniMax Token Plan quota, the OpenRouter credits endpoint, cache R/W
// split, and burn-rate emojis. We reuse the same data pipeline — no
// provider-specific logic lives in this plugin.
//
// To render the full bar on demand, type in the TUI:
//   !python3 ~/.claude/statusline/session_tokens.py
//
// Environment:
//   MINIMAX_API_KEY and/or OPENROUTER_API_KEY should be set in the shell so
//   the script can query the relevant provider quota endpoint when the
//   matching provider is active.

import type { Plugin } from "@opencode-ai/plugin"
import { spawn } from "node:child_process"
import { appendFileSync, mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { join, sep } from "node:path"

const SCRIPT = join(homedir(), ".claude", "statusline", "session_tokens.py")
const PYTHON = process.env.LLM_STATUSLINE_PYTHON ?? process.env.MINIMAX_STATUSLINE_PYTHON ?? "python3"
const PLUGIN_NAME = "llm-statusline"

interface TokenState {
  input: number
  output: number
  cache_read: number
  cache_creation: number
  model: string
  lastTimestamp: string
  requests: number
}

const sessionState = new Map<string, TokenState>()

function stripAnsi(s: string): string {
  return s.replace(/\u001b\[[0-9;]*m/g, "")
}

function summarize(text: string): string {
  const clean = stripAnsi(text)
  const lines = clean
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0)
  return lines.slice(0, 2).join(" \u2022 ") || "(empty)"
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

function pickString(...candidates: unknown[]): string {
  for (const c of candidates) {
    if (typeof c === "string" && c.trim() !== "") return c
  }
  return ""
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

function extractUsage(event: unknown): ExtractedUsage {
  const ev = (event ?? {}) as Record<string, unknown>
  const modelField = ev.model
  const modelObj =
    typeof modelField === "object" && modelField !== null
      ? (modelField as Record<string, unknown>)
      : null
  const model =
    pickString(modelObj?.id, modelObj?.name, modelField) || "opencode/unknown"

  const sessionId =
    pickString(ev.sessionID, ev.session_id, ev.id) ||
    `opencode-${Date.now()}`

  const tokens =
    (ev.tokens as Record<string, unknown> | undefined) ??
    (ev.usage as Record<string, unknown> | undefined) ??
    (ev.message as Record<string, unknown> | undefined)?.usage ??
    {}

  return {
    input: pickNumber(
      (tokens as Record<string, unknown>).input,
      (tokens as Record<string, unknown>).input_tokens,
      (tokens as Record<string, unknown>).prompt_tokens,
      (tokens as Record<string, unknown>).prompt,
    ),
    output: pickNumber(
      (tokens as Record<string, unknown>).output,
      (tokens as Record<string, unknown>).output_tokens,
      (tokens as Record<string, unknown>).completion_tokens,
      (tokens as Record<string, unknown>).completion,
    ),
    cache_read: pickNumber(
      (tokens as Record<string, unknown>).cache_read,
      (tokens as Record<string, unknown>).cache_read_input_tokens,
      (tokens as Record<string, unknown>).cached_tokens,
    ),
    cache_creation: pickNumber(
      (tokens as Record<string, unknown>).cache_creation,
      (tokens as Record<string, unknown>).cache_creation_input_tokens,
    ),
    model,
    session_id: sessionId,
    timestamp: new Date().toISOString(),
  }
}

function projectHash(projectDir: string): string {
  // Match ``lib/parser.py::project_dir_to_hash`` — replace path separators
  // with dashes so ``/Users/luiz/foo`` becomes ``-Users-luiz-foo``.
  return projectDir.split(sep).join("-")
}

function jsonlPathFor(projectDir: string, sessionId: string): string {
  const dir = join(homedir(), ".claude", "projects", projectHash(projectDir))
  mkdirSync(dir, { recursive: true })
  return join(dir, `${sessionId}.jsonl`)
}

function writeJsonlEntry(
  projectDir: string,
  entry: {
    type: string
    sessionId: string
    timestamp: string
    message: {
      role: string
      model: string
      usage: {
        input_tokens: number
        output_tokens: number
        cache_creation_input_tokens: number
        cache_read_input_tokens: number
      }
    }
  },
): string {
  const path = jsonlPathFor(projectDir, entry.sessionId)
  appendFileSync(path, JSON.stringify(entry) + "\n", "utf8")
  return path
}

interface RunResult {
  ok: boolean
  output: string
}

async function runStatusline(
  projectDir: string,
  sessionId: string,
  stdinPayload: Record<string, unknown>,
): Promise<RunResult> {
  return new Promise((resolve) => {
    const proc = spawn(PYTHON, [SCRIPT], {
      env: {
        ...process.env,
        CLAUDE_PROJECT_DIR: projectDir,
        CLAUDE_SESSION_ID: sessionId,
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
      finish({ ok: false, output: `[${PLUGIN_NAME}] spawn error: ${e.message}` }),
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
      finish({ ok: false, output: `[${PLUGIN_NAME}] stdin write failed: ${(e as Error).message}` })
    }
  })
}

async function handleSessionIdle(
  client: { app: { log: (input: { body: Record<string, unknown> }) => Promise<void> } },
  projectDir: string,
  event: unknown,
): Promise<void> {
  const usage = extractUsage(event)
  const now = usage.timestamp

  // Accumulate per-session totals across multiple events.
  const prev = sessionState.get(usage.session_id)
  const acc: TokenState = {
    input: (prev?.input ?? 0) + usage.input,
    output: (prev?.output ?? 0) + usage.output,
    cache_read: (prev?.cache_read ?? 0) + usage.cache_read,
    cache_creation: (prev?.cache_creation ?? 0) + usage.cache_creation,
    model: usage.model || prev?.model || "opencode/unknown",
    lastTimestamp: now,
    requests: (prev?.requests ?? 0) + 1,
  }
  sessionState.set(usage.session_id, acc)

  // Write JSONL entry using ACCUMULATED totals (not per-request delta) so
  // the Python parser's running aggregation works correctly even if the
  // plugin only sees ``session.idle`` events (not per-message events).
  const jsonlFile = writeJsonlEntry(projectDir, {
    type: "assistant",
    sessionId: usage.session_id,
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
  })

  // Synthesise a stdin payload so the Python script can still render the
  // model + cwd + version lines even if OpenCode does not provide them
  // through the event payload.
  const stdinPayload: Record<string, unknown> = {
    model: { id: acc.model },
    workspace: { current_dir: projectDir },
    version: "opencode",
    context_window: {
      used_percentage: 0,
    },
    cost: { total_duration_ms: 0 },
  }

  const result = await runStatusline(projectDir, usage.session_id, stdinPayload)
  const summary = summarize(result.output)
  await client.app.log({
    body: {
      service: PLUGIN_NAME,
      level: result.ok ? "info" : "warn",
      message: summary,
      extra: {
        ok: result.ok,
        jsonl_file: jsonlFile,
        session_id: usage.session_id,
        requests: acc.requests,
        tokens: {
          input: acc.input,
          output: acc.output,
          cache_read: acc.cache_read,
          cache_creation: acc.cache_creation,
        },
      },
    },
  })
}

export const MiniMaxStatusline: Plugin = async ({ client, directory }) => {
  const projectDir = directory ?? process.cwd()

  await client.app.log({
    body: {
      service: PLUGIN_NAME,
      level: "info",
      message: `Plugin loaded (project: ${projectDir})`,
    },
  })

  return {
    "session.idle": (event: unknown) =>
      handleSessionIdle(
        client as unknown as {
          app: { log: (input: { body: Record<string, unknown> }) => Promise<void> }
        },
        projectDir,
        event,
      ),

    // Generic event catch-all for OpenCode versions that emit a generic
    // ``event`` handler instead of a typed ``session.idle``.
    event: ({ event }: { event: { type?: string } }) => {
      if (event?.type !== "session.idle") return Promise.resolve()
      return handleSessionIdle(
        client as unknown as {
          app: { log: (input: { body: Record<string, unknown> }) => Promise<void> }
        },
        projectDir,
        event,
      )
    },
  }
}
