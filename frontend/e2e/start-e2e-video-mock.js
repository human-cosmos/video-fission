import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const fixture = resolve(frontendRoot, '../backend/tests/fixtures/sample-reference.mp4')
const video = await readFile(fixture)

const server = createServer(async (request, response) => {
  if (request.method === 'GET' && request.url === '/health') {
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ ok: true }))
    return
  }
  if (request.method === 'GET' && request.url === '/video.mp4') {
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
