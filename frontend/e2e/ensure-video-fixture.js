import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, statSync } from 'node:fs'
import { dirname } from 'node:path'

export function ensureE2eVideoFixture(fixture) {
  if (existsSync(fixture) && statSync(fixture).size > 0) return

  mkdirSync(dirname(fixture), { recursive: true })
  execFileSync(
    'ffmpeg',
    [
      '-y',
      '-loglevel',
      'error',
      '-f',
      'lavfi',
      '-i',
      'testsrc2=size=480x854:rate=24:duration=8',
      '-f',
      'lavfi',
      '-i',
      'sine=frequency=440:duration=8',
      '-c:v',
      'libx264',
      '-preset',
      'veryfast',
      '-pix_fmt',
      'yuv420p',
      '-c:a',
      'aac',
      '-shortest',
      fixture,
    ],
    { stdio: 'inherit' },
  )
}
