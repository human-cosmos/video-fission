import { spawn } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const vite = resolve(frontendRoot, 'node_modules', 'vite', 'bin', 'vite.js')

const child = spawn(
  process.execPath,
  [vite, '--host', '127.0.0.1', '--port', '5175'],
  {
    cwd: frontendRoot,
    env: {
      ...process.env,
      VITE_API_TARGET: 'http://127.0.0.1:8001',
    },
    stdio: 'inherit',
  },
)

function stop() {
  child.kill()
}

process.on('SIGINT', stop)
process.on('SIGTERM', stop)
child.on('exit', (code) => process.exit(code ?? 0))
