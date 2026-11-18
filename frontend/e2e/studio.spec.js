import { expect, test } from '@playwright/test'
import { access } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const fixture = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../backend/tests/fixtures/sample-reference.mp4',
)

async function fixtureExists() {
  try {
    await access(fixture)
    return true
  } catch {
    return false
  }
}

async function skipUnlessPipelineReady(request) {
  test.skip(!(await fixtureExists()), 'sample-reference.mp4 尚未落地，等后端 fixture 后重跑')
  const health = await request.get('/api/health')
  const ready = health.ok() ? await health.json() : {}
  test.skip(!health.ok() || ready.pipeline === undefined, 'productions 管线尚未就绪')
}

test('opens video studio and model settings from the workspace switcher', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.app-shell')).toBeVisible()
  await page.getByTestId('mode-studio').click()
  await expect(page.getByTestId('video-studio-page')).toBeVisible()
  await expect(page.getByTestId('studio-start')).toBeVisible()
  await expect(page.getByTestId('studio-policy-default')).toBeVisible()
  await expect(page.getByTestId('studio-policy-all')).toBeVisible()
  await expect(page.getByTestId('studio-policy-brief')).toBeVisible()
  await page.getByTestId('mode-settings').click()
  await expect(page.getByTestId('model-settings-page')).toBeVisible()
  await expect(page.getByTestId('save-settings')).toBeVisible()
  await page.getByTestId('mode-chat').click()
  await expect(page.getByTestId('message-input')).toBeVisible()
})

test('uploads a reference video, runs the local pipeline, and shows a download link', async ({
  page,
  request,
}) => {
  await skipUnlessPipelineReady(request)

  await page.goto('/')
  await expect(page.locator('.app-shell')).toBeVisible()
  await page.getByTestId('mode-studio').click()
  await page.getByTestId('studio-brief').fill('把这段参考视频改成更利落的开场，保留商品展示，结尾加强行动号召。')
  await page.getByTestId('studio-upload').setInputFiles(fixture)
  await expect(page.getByText('sample-reference.mp4', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('studio-start')).toBeEnabled()
  await page.getByTestId('studio-start').click()
  await expect(page.getByTestId('studio-download-mp4')).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId('studio-player')).toBeVisible()
})

test('reruns a checked non-default segment and keeps the completed output downloadable', async ({
  page,
  request,
}) => {
  test.setTimeout(360_000)
  await skipUnlessPipelineReady(request)

  await page.goto('/')
  await expect(page.locator('.app-shell')).toBeVisible()
  await page.getByTestId('mode-studio').click()
  await expect(page.getByTestId('studio-policy-default')).toBeChecked()
  await page.getByTestId('studio-brief').fill('把这段参考视频改成更利落的开场，保留商品展示，结尾加强行动号召。')
  await page.getByTestId('studio-upload').setInputFiles(fixture)
  await expect(page.getByText('sample-reference.mp4', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('studio-start')).toBeEnabled()
  await page.getByTestId('studio-start').click()
  await expect(page.getByTestId('studio-download-mp4')).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId('studio-player')).toBeVisible()
  await expect(page.getByTestId('studio-progress')).toContainText('已完成')

  const productionId = (await page.getByTestId('studio-production-id').innerText()).trim()
  expect(productionId).toMatch(/^prd_/)

  const first = await (await request.get(`/api/productions/${productionId}`)).json()
  const valueSegment = (first.decomposition?.segments || []).find((segment) => segment.role === 'value')
  expect(valueSegment?.id).toBeTruthy()
  expect(first.replace_plan?.resolved_segment_ids || []).not.toContain(valueSegment.id)

  const checkbox = page.getByTestId(`studio-segment-regen-${valueSegment.id}`)
  await expect(checkbox).toBeVisible()
  await checkbox.check()
  await expect(page.getByTestId('studio-rerun-selected')).toBeEnabled()
  await page.getByTestId('studio-rerun-selected').click()

  await expect(page.getByTestId('studio-progress')).toContainText('进行中', { timeout: 15_000 })
  await expect(page.getByTestId('studio-progress')).toContainText('已完成', { timeout: 180_000 })
  await expect(page.getByTestId('studio-download-mp4')).toBeVisible()
  await expect(page.getByTestId('studio-player')).toBeVisible()
  await expect(page.getByTestId(`studio-segment-action-${valueSegment.id}`)).toHaveText('新生成')
  await expect(page.getByTestId('studio-replace-plan')).toContainText(valueSegment.id)

  const after = await (await request.get(`/api/productions/${productionId}`)).json()
  expect(after.replace_policy?.mode).toBe('selected')
  expect(after.replace_plan?.resolved_segment_ids).toEqual([valueSegment.id])
  expect(after.status).toBe('completed')
})
