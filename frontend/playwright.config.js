import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 120_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:5175',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 950 } },
    },
  ],
  webServer: [
    {
      command:
        'node ../frontend/e2e/reset-e2e-data.js && VF_DATA_DIR=../.e2e-data ../.venv/bin/python -m uvicorn video_fission.main:app --host 127.0.0.1 --port 8001',
      cwd: '../backend',
      url: 'http://127.0.0.1:8001/api/health',
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: 'VITE_API_TARGET=http://127.0.0.1:8001 npx vite --host 127.0.0.1 --port 5175',
      cwd: '.',
      url: 'http://127.0.0.1:5175',
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
})

