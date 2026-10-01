import { existsSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import { randomUUID } from "node:crypto"
import { spawn } from "node:child_process"
import type { ChildProcessWithoutNullStreams } from "node:child_process"
import { Plugin } from "@opencode/plugin"

type Action = "run" | "put" | "get" | "terminal"
type Input = {
  action?: Action
  host?: string
  user?: string
  port?: number | string
  identity_file?: string
  env_file?: string
  password?: string
  command?: string
  src?: string
  dst?: string
  session_id?: string
  input?: string
  close?: boolean
  wait_ms?: number
}

type TerminalSession = {
  process: ChildProcessWithoutNullStreams
  output: string
  password?: string
  exitCode?: number | null
}

const terminals = new Map<string, TerminalSession>()

function findHelper(directory: string): string | undefined {
  const pluginDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    // The global installation bundles the helper next to this plugin.
    join(pluginDirectory, "ssh_exec.py"),
    // The project checkout reuses the shared generic helper.
    join(directory, "plugins", "ssh", "board_ssh_exec.py"),
  ]
  return candidates.find((candidate) => existsSync(candidate))
}

function invoke(
  helper: string,
  args: string[],
  env: NodeJS.ProcessEnv,
): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn("python3", [helper, ...args], {
      cwd: dirname(helper),
      env,
      stdio: ["ignore", "pipe", "pipe"],
    })
    let stdout = ""
    let stderr = ""
    child.stdout.on("data", (chunk) => { stdout += chunk.toString() })
    child.stderr.on("data", (chunk) => { stderr += chunk.toString() })
    child.on("error", (error) => resolve({ code: 1, stdout, stderr: `${stderr}${error.message}` }))
    child.on("close", (code) => resolve({ code: code ?? 1, stdout, stderr }))
  })
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function terminalOutput(session: TerminalSession, password?: string): string {
  const output = session.output
  session.output = ""
  const secret = session.password ?? password
  return secret ? output.replaceAll(secret, "[REDACTED]") : output
}

async function terminal(
  helper: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  input: Input,
): Promise<string> {
  const waitMs = Math.max(0, Math.min(input.wait_ms ?? 500, 5000))
  let sessionID = input.session_id
  let session = sessionID ? terminals.get(sessionID) : undefined

  if (sessionID && !session) return `ssh: terminal session not found: ${sessionID}`

  if (!session) {
    sessionID = randomUUID()
    const child = spawn("python3", [helper, ...args], {
      cwd: dirname(helper),
      env,
      stdio: ["pipe", "pipe", "pipe"],
    })
    session = {
      process: child,
      output: "",
      password: env.SSH_PASSWORD || env.BOARD_SSH_PASSWORD,
    }
    terminals.set(sessionID, session)
    child.stdout.on("data", (chunk) => {
      session!.output += chunk.toString()
      if (session!.output.length > 1_048_576) session!.output = session!.output.slice(-1_048_576)
    })
    child.stderr.on("data", (chunk) => {
      session!.output += chunk.toString()
      if (session!.output.length > 1_048_576) session!.output = session!.output.slice(-1_048_576)
    })
    child.on("error", (error) => {
      session!.output += `ssh: ${error.message}\n`
      session!.exitCode = 127
    })
    child.on("close", (code) => {
      session!.exitCode = code ?? 1
    })
  }

  if (input.input) session.process.stdin.write(input.input)
  if (input.close) session.process.stdin.end()
  if (waitMs) await wait(waitMs)

  const output = terminalOutput(session, input.password)
  const state = session.exitCode === undefined
    ? "running"
    : `exited=${session.exitCode}`
  if (session.exitCode !== undefined) terminals.delete(sessionID!)
  return `session_id=${sessionID}\n${state}\n${output}`
}

export default Plugin.define({
  id: "ssh",
  async setup(ctx) {
    const helper = findHelper(ctx.location.directory)
    if (!helper) return

    const registration = await ctx.tool.transform((editor) => {
      editor.add({
        name: "ssh",
        description:
          "Run commands, transfer files, or open a persistent interactive SSH terminal. " +
          "Use action=terminal to start a shell and reuse its session_id to exchange input and output.",
        input: {
          type: "object",
          properties: {
            action: { type: "string", enum: ["run", "put", "get", "terminal"] },
            host: { type: "string", description: "Hostname, IP, or user@host" },
            user: { type: "string", description: "SSH user when host has no user@ prefix" },
            port: { type: "integer", description: "SSH port; defaults to 22" },
            identity_file: { type: "string", description: "Private key path" },
            env_file: { type: "string", description: "Env file containing SSH_* settings" },
            password: {
              type: "string",
              format: "password",
              writeOnly: true,
              description: "SSH password for this call; never passed as a command-line argument",
            },
            command: { type: "string" },
            src: { type: "string" },
            dst: { type: "string" },
            session_id: { type: "string", description: "Existing action=terminal session to continue" },
            input: { type: "string", description: "Text or terminal control characters to send" },
            close: { type: "boolean", description: "Send EOF to an interactive terminal" },
            wait_ms: { type: "integer", description: "Time to collect output, 0-5000 ms (default 500)" },
          },
          additionalProperties: false,
        },
        async execute(value: unknown) {
          const raw = value as Input
          const action = raw.action ?? "run"
          const args: string[] = []
          const addOption = (name: string, value: string | number | undefined) => {
            if (value !== undefined && value !== "") args.push(name, String(value))
          }
          addOption("--host", raw.host)
          addOption("--user", raw.user)
          addOption("--port", raw.port)
          addOption("--identity-file", raw.identity_file)
          addOption("--env-file", raw.env_file)
          const childEnv = { ...process.env }
          if (raw.password !== undefined) {
            if (!raw.password || /[\0\r\n]/.test(raw.password)) {
              return { content: "ssh: password must be non-empty and must not contain NUL or line breaks" }
            }
            childEnv.SSH_PASSWORD = raw.password
          }
          if (action === "run") {
            if (!raw.command) return { content: "ssh: `command` is required for action=run" }
            args.push("run", raw.command)
          } else if (action === "put" || action === "get") {
            if (!raw.src || !raw.dst) {
              return { content: `ssh: \`src\` and \`dst\` are required for action=${action}` }
            }
            args.push(action, raw.src, raw.dst)
          } else if (action === "terminal") {
            if (raw.session_id) {
              return {
                content: await terminal(helper, [], childEnv, raw),
              }
            }
            if (raw.command) args.push("terminal", raw.command)
            else args.push("terminal")
            return {
              content: await terminal(helper, args, childEnv, raw),
            }
          } else {
            return { content: `ssh: unknown action ${String(action)}` }
          }

          const result = await invoke(helper, args, childEnv)
          const text = `exit=${result.code}\n${result.stdout}${result.stderr}`
          const secret = raw.password || childEnv.SSH_PASSWORD || childEnv.BOARD_SSH_PASSWORD
          return { content: secret ? text.replaceAll(secret, "[REDACTED]") : text }
        },
      })
    })
    return async () => {
      const sessions = [...terminals.values()]
      for (const session of sessions) session.process.stdin.end()
      await Promise.all(sessions.map((session) =>
        session.exitCode !== undefined
          ? Promise.resolve()
          : Promise.race([
              new Promise<void>((resolve) => session.process.once("close", () => resolve())),
              wait(1000),
            ]),
      ))
      for (const session of sessions) {
        if (session.exitCode === undefined) session.process.kill()
      }
      terminals.clear()
      await registration.dispose()
    }
  },
})
