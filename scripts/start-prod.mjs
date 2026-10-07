import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const apiPort = 8000;
const apiOrigin = `http://127.0.0.1:${apiPort}`;
const python = process.env.API_PYTHON || resolve(root, '.venv/bin/python');
const standaloneDirectory = resolve(root, process.env.WEB_STANDALONE_DIR || 'apps/web');
const webEntry = resolve(standaloneDirectory, 'server.js');
const webPort = Number(process.env.PORT || 10000);
const startupDeadlineMs = 45_000;
const shutdownGraceMs = 8_000;
const children = new Set();
let stopping = false;
let shutdownPromise;

function shutdown(code, message) {
  if (shutdownPromise) return shutdownPromise;
  stopping = true;
  if (message) console.error(message);
  process.exitCode = code;
  shutdownPromise = (async () => {
    const remaining = [...children].filter(child => child.exitCode === null && child.signalCode === null);
    const closed = remaining.map(child => new Promise(done => child.once('close', done)));
    for (const child of remaining) child.kill('SIGTERM');
    const force = setTimeout(() => {
      for (const child of remaining) {
        if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
      }
    }, shutdownGraceMs);
    await Promise.all(closed);
    clearTimeout(force);
    process.exit(code);
  })();
  return shutdownPromise;
}

process.on('SIGTERM', () => void shutdown(0));
process.on('SIGINT', () => void shutdown(0));

function launch(name, command, args, cwd, environment) {
  const child = spawn(command, args, { cwd, env: environment, shell: false, stdio: 'inherit' });
  children.add(child);
  child.on('error', () => {
    // An upstream error or executable argument may contain sensitive text.
    // Use only fixed messages; never log process environments or exceptions.
    if (!stopping) void shutdown(1, `${name} process could not start.`);
  });
  child.on('exit', () => {
    if (!stopping) void shutdown(1, `${name} process stopped unexpectedly.`);
  });
  child.on('close', () => children.delete(child));
  return child;
}

async function requireUnusedApiPort() {
  // Production always owns its API. An unrelated or stale API is never reused.
  await new Promise((done, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen({ host: '127.0.0.1', port: apiPort, exclusive: true }, () => server.close(done));
  });
}

async function waitForApi(child) {
  const deadline = Date.now() + startupDeadlineMs;
  while (!stopping && Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) return false;
    try {
      const response = await fetch(`${apiOrigin}/api/health`, { signal: AbortSignal.timeout(1000) });
      if (response.ok) {
        const health = await response.json();
        if (health.status === 'ok' && child.exitCode === null && child.signalCode === null) return true;
      }
    } catch {
      // Startup polling is local and read-only; it performs no broker requests.
    }
    await new Promise(done => setTimeout(done, 250));
  }
  return false;
}

async function main() {
  if (!Number.isInteger(webPort) || webPort < 1 || webPort > 65535 || webPort === apiPort) {
    await shutdown(1, 'The public PORT must be a valid port distinct from the internal API port.');
    return;
  }
  const executablePath = isAbsolute(python) ? python : resolve(root, python);
  if (python.includes('/') && !existsSync(executablePath)) {
    await shutdown(1, 'The configured API executable is missing.');
    return;
  }
  if (!existsSync(webEntry)) {
    await shutdown(1, 'The standalone web server is missing. Build the production image first.');
    return;
  }
  try {
    await requireUnusedApiPort();
  } catch {
    await shutdown(1, 'The internal API port is unavailable. Stop any existing API before production startup.');
    return;
  }
  if (stopping) return;
  const api = launch('API', python, [
    '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(apiPort),
  ], root, {
    ...process.env,
    PYTHONPATH: resolve(root, 'apps/api'),
    PYTHONUNBUFFERED: '1',
  });
  if (!(await waitForApi(api))) {
    if (!stopping) await shutdown(1, 'The API did not become healthy within the startup deadline.');
    return;
  }
  if (stopping) return;
  const webEnvironment = {
    ...process.env,
    NODE_ENV: 'production',
    HOSTNAME: '0.0.0.0',
    PORT: String(webPort),
    API_ORIGIN: apiOrigin,
  };
  for (const name of ['TOSS_CLIENT_ID', 'TOSS_CLIENT_SECRET', 'TOSS_ACCESS_TOKEN', 'TOSS_API_MANIFEST', 'DATABASE_URL', 'REDIS_URL']) {
    delete webEnvironment[name];
  }
  launch('Web', process.execPath, [webEntry], standaloneDirectory, webEnvironment);
}

main().catch(() => void shutdown(1, 'Production startup failed.'));
