import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const apiPort = process.env.API_PORT || '8000';
const apiOrigin = process.env.API_ORIGIN || `http://127.0.0.1:${apiPort}`;
const python = resolve(root, '.venv/bin/python');
const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill('SIGTERM');
  setTimeout(() => process.exit(code), 250).unref();
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
function run(command, args, env) {
  const child = spawn(command, args, { cwd: root, env: { ...process.env, ...env }, stdio: 'inherit' });
  children.push(child);
  child.on('error', error => { console.error(error.message); stop(1); });
  child.on('exit', code => { if (!stopping) stop(code || 0); });
  return child;
}
async function apiReady() {
  try { const response = await fetch(`${apiOrigin}/api/health`, { signal: AbortSignal.timeout(1500) }); return response.ok && (await response.json()).status === 'ok'; }
  catch { return false; }
}
if (!(await apiReady())) {
  if (!existsSync(python)) { console.error('Run bash scripts/setup.sh first to install the Python environment.'); process.exit(1); }
  run(python, ['-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', apiPort], { PYTHONPATH: resolve(root, 'apps/api') });
  let ready = false;
  for (let attempt = 0; attempt < 40 && !stopping; attempt++) {
    if (await apiReady()) { ready = true; break; }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (!ready) { console.error('API did not become ready. Check the startup error above.'); stop(1); }
}
if (!stopping) run(process.execPath, [resolve(root, 'node_modules/next/dist/bin/next'), 'dev', resolve(root, 'apps/web'), '--hostname', '0.0.0.0', '--port', process.env.PORT || '3000'], { API_ORIGIN: apiOrigin });
