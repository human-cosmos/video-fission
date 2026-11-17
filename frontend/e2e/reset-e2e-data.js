import { rm } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const e2eData = resolve(dirname(fileURLToPath(import.meta.url)), '../../.e2e-data')
await rm(e2eData, { recursive: true, force: true })

