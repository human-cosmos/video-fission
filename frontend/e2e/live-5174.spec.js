import { expect, test } from '@playwright/test'

test.skip(process.env.RUN_LIVE_5174 !== '1', 'set RUN_LIVE_5174=1 to test the live service')

test('live 5174 frontend reaches the authenticated workspace', async ({ page, request }) => {
  const health = await request.get('/api/health')
  expect(health.ok()).toBeTruthy()
  const payload = await health.json()
  expect(payload.ok).toBe(true)

  await page.goto('/')
  const username = page.getByPlaceholder('输入用户名')
  if (await username.isVisible().catch(() => false)) {
    const loginUser = process.env.VF_LIVE_USERNAME || ''
    const loginPassword = process.env.VF_LIVE_PASSWORD || ''
    test.skip(!loginUser || !loginPassword, 'VF_LIVE_USERNAME and VF_LIVE_PASSWORD are required')
    await username.fill(loginUser)
    await page.getByPlaceholder('输入密码').fill(loginPassword)
    await page.getByRole('button', { name: '进入工作台' }).click()
  }

  await expect(page.getByTestId('new-conversation')).toBeVisible()
  await page.getByTestId('new-conversation').click()
  await expect(page.getByTestId('message-input')).toBeVisible()
})
