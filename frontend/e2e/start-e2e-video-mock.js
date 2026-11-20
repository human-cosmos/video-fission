import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ensureE2eVideoFixture } from './ensure-video-fixture.js'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const fixture = resolve(frontendRoot, '../.e2e-fixtures/sample-reference.mp4')
let video = null
let fixtureError = ''
try {
  ensureE2eVideoFixture(fixture)
  video = await readFile(fixture)
} catch (error) {
  fixtureError = error instanceof Error ? error.message : String(error)
  console.warn(`[e2e-video-mock] fixture unavailable: ${fixtureError}`)
}

const server = createServer(async (request, response) => {
  if (request.method === 'GET' && request.url === '/health') {
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ ok: true }))
    return
  }
  if (request.method === 'GET' && request.url === '/video.mp4') {
    if (!video) {
      response.writeHead(503, { 'Content-Type': 'application/json' })
      response.end(
        JSON.stringify({
          detail: 'E2E video fixture is missing',
          fixture,
        }),
      )
      return
    }
    response.writeHead(200, {
      'Content-Type': 'video/mp4',
      'Content-Length': String(video.length),
    })
    response.end(video)
    return
  }
  if (request.method === 'POST') {
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ url: 'http://127.0.0.1:8002/video.mp4' }))
    return
  }
  response.writeHead(404)
  response.end()
})

server.listen(8002, '127.0.0.1')

function stop() {
  server.close(() => process.exit(0))
}

process.on('SIGINT', stop)
process.on('SIGTERM', stop)
