import { existsSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import { spawn } from "node:child_process"
import { Plugin } from "@opencode/plugin"

type Action = "run" | "put" | "get"
type Input = {
  action?: Action
  host?: string
  user?: string
  port?: number | string
  identity_file?: string
  env_file?: string
  command?: string
  src?: string
  dst?: string
}

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

function invoke(helper: string, args: string[]): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn("python3", [helper, ...args], {
      cwd: dirname(helper),
      env: process.env,
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

export default Plugin.define({
  id: "ssh",
  async setup(ctx) {
    const helper = findHelper(ctx.location.directory)
    if (!helper) return

    await ctx.tool.transform((editor) => {
      editor.add({
        name: "ssh",
        description:
          "Run a shell command on any remote host, or transfer files over SSH. " +
          "Use action=run for commands, action=put to upload, and action=get to download.",
        input: {
          type: "object",
          properties: {
            action: { type: "string", enum: ["run", "put", "get"] },
            host: { type: "string", description: "Hostname, IP, or user@host" },
            user: { type: "string", description: "SSH user when host has no user@ prefix" },
            port: { type: "integer", description: "SSH port; defaults to 22" },
            identity_file: { type: "string", description: "Private key path" },
            env_file: { type: "string", description: "Env file containing SSH_* settings" },
            command: { type: "string" },
            src: { type: "string" },
            dst: { type: "string" },
          },
          additionalProperties: false,
        },
        async execute(raw: Input) {
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
          if (action === "run") {
            if (!raw.command) return { content: "ssh: `command` is required for action=run" }
            args.push("run", raw.command)
          } else if (action === "put" || action === "get") {
            if (!raw.src || !raw.dst) {
              return { content: `ssh: \`src\` and \`dst\` are required for action=${action}` }
            }
            args.push(action, raw.src, raw.dst)
          } else {
            return { content: `ssh: unknown action ${String(action)}` }
          }

          const result = await invoke(helper, args)
          return { content: `exit=${result.code}\n${result.stdout}${result.stderr}` }
        },
      })
    })
  },
})
