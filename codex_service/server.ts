import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import readline from "node:readline";

const PORT = Number(process.env.PORT || 3000);
const MAX_BODY = 1_000_000;
const CODEX = process.env.CODEX_BIN || join(process.cwd(), "node_modules", ".bin", "codex");
const BILLING_ENV = [
  "OPENAI_API_KEY",
  "CODEX_API_KEY",
  "CODEX_ACCESS_TOKEN",
  "OPENAI_IDENTITY_TOKEN_FILE",
  "OPENAI_FEDERATION_RULE_ID",
];

function sealKey(): Buffer {
  const secret =
    process.env.YOWAYOWA_CODEX_CREDENTIAL_KEY ||
    process.env.VERCEL_TOKEN ||
    process.env.YOWAYOWA_API_TOKEN ||
    process.env.CRON_SECRET;
  if (!secret) {
    throw new Error(
      "No credential-sealing secret is configured. Set YOWAYOWA_CODEX_CREDENTIAL_KEY."
    );
  }
  return createHash("sha256").update("yowayowa-codex-credential-v1\0").update(secret).digest();
}

function sealCredential(raw: string, sessionId: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", sealKey(), iv);
  cipher.setAAD(Buffer.from(sessionId, "utf8"));
  const ciphertext = Buffer.concat([cipher.update(raw, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, ciphertext]).toString("base64url");
}

function openCredential(envelope: string, sessionId: string): string {
  const packed = Buffer.from(envelope, "base64url");
  if (packed.length < 29) throw new Error("Credential envelope is malformed");
  const iv = packed.subarray(0, 12);
  const tag = packed.subarray(12, 28);
  const ciphertext = packed.subarray(28);
  const decipher = createDecipheriv("aes-256-gcm", sealKey(), iv);
  decipher.setAAD(Buffer.from(sessionId, "utf8"));
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString("utf8");
}

function codexEnv(home: string): NodeJS.ProcessEnv {
  const env = { ...process.env, CODEX_HOME: home };
  for (const key of BILLING_ENV) delete env[key];
  return env;
}

async function createHome(credential?: string, sessionId?: string): Promise<string> {
  const home = await mkdtemp(join(tmpdir(), "yowayowa-codex-"));
  await writeFile(
    join(home, "config.toml"),
    [
      'cli_auth_credentials_store = "file"',
      'forced_login_method = "chatgpt"',
      'approval_policy = "never"',
      'sandbox_mode = "read-only"',
      "",
    ].join("\n"),
    "utf8"
  );
  if (credential) {
    if (!sessionId) throw new Error("Missing Codex session");
    await writeFile(join(home, "auth.json"), openCredential(credential, sessionId), {
      encoding: "utf8",
      mode: 0o600,
    });
  }
  return home;
}

async function updatedCredential(home: string, sessionId: string): Promise<string> {
  const raw = await readFile(join(home, "auth.json"), "utf8");
  return sealCredential(raw, sessionId);
}

function writeJson(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "content-length": Buffer.byteLength(payload).toString(),
  });
  res.end(payload);
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > MAX_BODY) throw new Error("Request body is too large");
    chunks.push(buffer);
  }
  if (!chunks.length) return {};
  const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON object body required");
  }
  return parsed as Record<string, unknown>;
}

function sessionId(req: IncomingMessage): string {
  const value = req.headers["x-yowayowa-codex-session"];
  const resolved = Array.isArray(value) ? value[0] : value;
  if (!resolved || resolved.length < 20 || resolved.length > 200) {
    throw new Error("Missing or invalid Codex browser session");
  }
  return resolved;
}

function sendRpc(proc: ReturnType<typeof spawn>, message: unknown): void {
  proc.stdin.write(JSON.stringify(message) + "\n");
}

async function deviceAuth(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const sid = sessionId(req);
  sealKey();
  const home = await createHome();
  const proc = spawn(CODEX, ["app-server"], {
    cwd: home,
    env: codexEnv(home),
    stdio: ["pipe", "pipe", "pipe"],
  });
  const lines = readline.createInterface({ input: proc.stdout });
  let loginId: string | null = null;
  let settled = false;
  let account: Record<string, unknown> | null = null;

  res.writeHead(200, {
    "content-type": "application/x-ndjson; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });

  const emit = (value: unknown) => res.write(JSON.stringify(value) + "\n");
  const cleanup = async () => {
    if (!proc.killed) proc.kill("SIGTERM");
    lines.close();
    await rm(home, { recursive: true, force: true });
  };
  const fail = async (message: string) => {
    if (settled) return;
    settled = true;
    emit({ type: "error", message });
    res.end();
    await cleanup();
  };
  const timer = setTimeout(() => {
    void fail("ChatGPT device login timed out. Start the connection again.");
  }, 270_000);

  req.on("close", () => {
    if (!settled) {
      clearTimeout(timer);
      settled = true;
      void cleanup();
    }
  });

  proc.on("error", (error) => void fail(`Could not start Codex: ${error.message}`));
  proc.stderr.on("data", () => {
    // Codex diagnostics intentionally stay server-side and are never streamed to the browser.
  });

  lines.on("line", (line) => {
    void (async () => {
      let msg: any;
      try {
        msg = JSON.parse(line);
      } catch {
        return;
      }
      if (msg.id === 0 && msg.result) {
        sendRpc(proc, { method: "initialized", params: {} });
        sendRpc(proc, {
          method: "account/login/start",
          id: 1,
          params: { type: "chatgptDeviceCode" },
        });
        return;
      }
      if (msg.id === 1 && msg.result?.type === "chatgptDeviceCode") {
        loginId = String(msg.result.loginId || "");
        emit({
          type: "instructions",
          login_id: loginId,
          verification_url: msg.result.verificationUrl,
          user_code: msg.result.userCode,
        });
        return;
      }
      if (
        msg.method === "account/login/completed" &&
        (!loginId || msg.params?.loginId === loginId)
      ) {
        if (!msg.params?.success) {
          await fail(String(msg.params?.error || "ChatGPT login failed"));
          return;
        }
        sendRpc(proc, {
          method: "account/read",
          id: 2,
          params: { refreshToken: true },
        });
        return;
      }
      if (msg.id === 2 && msg.result) {
        account =
          msg.result.account && typeof msg.result.account === "object"
            ? msg.result.account
            : null;
        try {
          const credential = await updatedCredential(home, sid);
          settled = true;
          clearTimeout(timer);
          emit({
            type: "complete",
            credential,
            account_type: account?.type || null,
            plan_type: account?.planType || null,
            email: account?.email || null,
          });
          res.end();
          await cleanup();
        } catch (error) {
          await fail(
            `ChatGPT login completed but credentials could not be sealed: ${
              error instanceof Error ? error.message : String(error)
            }`
          );
        }
      }
    })();
  });

  sendRpc(proc, {
    method: "initialize",
    id: 0,
    params: {
      clientInfo: {
        name: "yowayowa_investor",
        title: "Yowayowa-Investor",
        version: "0.1.0",
      },
    },
  });
}

async function structured(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const sid = sessionId(req);
  const body = await readJson(req);
  const credential = String(body.credential || "");
  const prompt = String(body.prompt || "");
  const schema = body.schema;
  const model = String(body.model || "").trim();
  if (!credential || !prompt || !schema || typeof schema !== "object") {
    writeJson(res, 422, { error: "credential, prompt and schema are required" });
    return;
  }

  const home = await createHome(credential, sid);
  const schemaPath = join(home, "schema.json");
  const outputPath = join(home, "result.json");
  await writeFile(schemaPath, JSON.stringify(schema), "utf8");

  const args = [
    "exec",
    "--ephemeral",
    "--sandbox",
    "read-only",
    "--ask-for-approval",
    "never",
    "--skip-git-repo-check",
    "--output-schema",
    schemaPath,
    "--output-last-message",
    outputPath,
  ];
  if (model && !["default", "auto"].includes(model.toLowerCase())) {
    args.push("--model", model);
  }
  args.push("-");

  try {
    const proc = spawn(CODEX, args, {
      cwd: home,
      env: codexEnv(home),
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stderr = "";
    proc.stderr.on("data", (chunk) => {
      stderr += String(chunk);
      if (stderr.length > 12_000) stderr = stderr.slice(-12_000);
    });
    proc.stdin.end(prompt);
    const exitCode = await new Promise<number>((resolve, reject) => {
      const timer = setTimeout(() => {
        proc.kill("SIGTERM");
        reject(new Error("Codex execution timed out"));
      }, 280_000);
      proc.on("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      proc.on("close", (code) => {
        clearTimeout(timer);
        resolve(code ?? 1);
      });
    });
    if (exitCode !== 0) {
      throw new Error(stderr.trim() || `Codex exited with code ${exitCode}`);
    }
    const result = JSON.parse(await readFile(outputPath, "utf8"));
    const refreshed = await updatedCredential(home, sid);
    writeJson(res, 200, { result, credential: refreshed });
  } finally {
    await rm(home, { recursive: true, force: true });
  }
}

async function sessionStatus(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const sid = sessionId(req);
  const body = await readJson(req);
  const credential = String(body.credential || "");
  if (!credential) {
    writeJson(res, 200, {
      enabled: true,
      installed: true,
      authenticated: false,
      mode: "hosted_bridge",
      reason: "ChatGPT login required.",
    });
    return;
  }
  const home = await createHome(credential, sid);
  try {
    const proc = spawn(CODEX, ["login", "status"], {
      cwd: home,
      env: codexEnv(home),
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (chunk) => (stdout += String(chunk)));
    proc.stderr.on("data", (chunk) => (stderr += String(chunk)));
    const code = await new Promise<number>((resolve, reject) => {
      proc.on("error", reject);
      proc.on("close", (value) => resolve(value ?? 1));
    });
    const summary = [stdout.trim(), stderr.trim()].filter(Boolean).join(" ");
    const authenticated = code === 0 && summary.toLowerCase().includes("chatgpt");
    const refreshed = authenticated ? await updatedCredential(home, sid) : null;
    writeJson(res, 200, {
      enabled: true,
      installed: true,
      authenticated,
      mode: "hosted_bridge",
      auth_summary: summary || null,
      reason: authenticated ? null : "Stored ChatGPT session is no longer valid.",
      credential: refreshed,
    });
  } finally {
    await rm(home, { recursive: true, force: true });
  }
}

const server = createServer((req, res) => {
  void (async () => {
    try {
      const url = new URL(req.url || "/", "http://internal");
      if (req.method === "GET" && url.pathname === "/health") {
        let sealConfigured = true;
        try {
          sealKey();
        } catch {
          sealConfigured = false;
        }
        writeJson(res, 200, {
          ok: true,
          codex_binary: CODEX,
          credential_seal_configured: sealConfigured,
        });
        return;
      }
      if (req.method === "POST" && url.pathname === "/device-auth") {
        await deviceAuth(req, res);
        return;
      }
      if (req.method === "POST" && url.pathname === "/structured") {
        await structured(req, res);
        return;
      }
      if (req.method === "POST" && url.pathname === "/session-status") {
        await sessionStatus(req, res);
        return;
      }
      writeJson(res, 404, { error: "Not found" });
    } catch (error) {
      if (!res.headersSent) {
        writeJson(res, 500, {
          error: error instanceof Error ? error.message : String(error),
        });
      } else {
        res.end(
          JSON.stringify({
            type: "error",
            message: error instanceof Error ? error.message : String(error),
          }) + "\n"
        );
      }
    }
  })();
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Yowayowa Codex bridge listening on ${PORT}`);
});
