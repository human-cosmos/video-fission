import { spawn } from 'node:child_process'
import { rm } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const projectRoot = resolve(frontendRoot, '..')
const backendRoot = resolve(projectRoot, 'backend')
const dataDir = resolve(projectRoot, '.e2e-data')
const python = process.platform === 'win32'
  ? resolve(projectRoot, '.venv', 'Scripts', 'python.exe')
  : resolve(projectRoot, '.venv', 'bin', 'python')

await rm(dataDir, { recursive: true, force: true })

const child = spawn(
  python,
  [
    '-m',
    'uvicorn',
    'video_fission.main:app',
    '--host',
    '127.0.0.1',
    '--port',
    '8001',
  ],
  {
    cwd: backendRoot,
    env: {
      ...process.env,
      VF_AUTH_REQUIRED: 'false',
      VF_DATA_DIR: dataDir,
      VF_LOG_FILE: '0',
      VF_GEMINI_API_URL: '',
      VF_GEMINI_UPLOAD_URL: '',
      VF_GEMINI_TOKEN_URL: '',
      VF_GEMINI_MODEL: '',
      VF_GEMINI_SOURCE: '',
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
