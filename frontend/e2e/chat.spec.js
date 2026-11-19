import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

const cleanup = {
  conversationId: '',
  skillSlug: '',
  uploadId: '',
}

test.afterEach(async ({ request }) => {
  const conversations = await request.get('/api/conversations?include_archived=true')
  if (conversations.ok()) {
    const payload = await conversations.json()
    for (const conversation of payload.items || []) {
      if (
        conversation.title.startsWith('这是端到端测试 VF_E2E_') ||
        conversation.title.startsWith('VF_E2E_LIST_')
      ) {
        await request.delete(`/api/conversations/${conversation.id}`)
      }
    }
  }
  if (cleanup.skillSlug) {
    await request.delete(`/api/skills/${cleanup.skillSlug}`)
  }
  if (cleanup.uploadId) {
    await request.delete(`/api/uploads/${cleanup.uploadId}`)
  }
  cleanup.conversationId = ''
  cleanup.skillSlug = ''
  cleanup.uploadId = ''
})

test('creates a persistent Codex conversation, invokes a tool, and creates a skill', async ({
  page,
}, testInfo) => {
  const marker = `VF_E2E_${Date.now()}`
  cleanup.skillSlug = `e2e-hook-${marker.slice(-6).toLowerCase()}`
  await page.goto('/')
  await expect(page.locator('.app-shell')).toBeVisible()

  await page.getByTestId('new-conversation').click()
  await expect(page.getByTestId('message-input')).toBeVisible()
  cleanup.conversationId = await page.evaluate(() =>
    window.localStorage.getItem('video-fission.activeConversation'),
  )

  await page
    .getByTestId('message-input')
    .fill(`这是端到端测试 ${marker}。请只回复 ${marker}_OK，不要解释。`)
  await page.getByTestId('send-message').click()
  await expect(page.getByText(`${marker}_OK`, { exact: true })).toBeVisible({
    timeout: 120_000,
  })

  await page
    .getByTestId('message-input')
    .fill(
      '请调用 create_video_brief 工具，生成并保存一个 15 秒的“雨夜霓虹开场”视频简报，受众是短视频创作者。完成后只回复文件路径。',
    )
  await page.getByTestId('send-message').click()
  await expect(page.getByText('create_video_brief', { exact: true }).first()).toBeVisible({
    timeout: 120_000,
  })
  await expect(page.getByText(/artifacts[\\/].*\.md/).last()).toBeVisible({
    timeout: 120_000,
  })

  await page.getByTestId('sidebar-user-menu').click()
  await page.getByTestId('mode-settings').click()
  await page.getByTestId('settings-create-skill').click()
  const editor = page.getByTestId('skill-editor')
  await expect(editor).toBeVisible()
  await editor.getByLabel('显示名称').fill(`E2E 钩子检查 ${marker.slice(-6)}`)
  await editor.getByLabel('标识（可选）').fill(cleanup.skillSlug)
  await editor
    .getByLabel('一句话描述')
    .fill('端到端测试创建的 Skill，用于检查创建、启停与持久化。')
  await editor
    .getByLabel('工作说明')
    .fill('## 检查\n\n1. 确认前三秒钩子。\n2. 输出一个可执行镜头。')
  await editor.getByRole('button', { name: '保存 Skill' }).click()
  await expect(page.getByText(`E2E 钩子检查 ${marker.slice(-6)}`, { exact: true })).toBeVisible()

  const createdSkill = page
    .locator('.global-skill')
    .filter({ hasText: `E2E 钩子检查 ${marker.slice(-6)}` })
  await createdSkill.getByRole('checkbox').uncheck()
  await expect(createdSkill).toHaveClass(/disabled/)
  await createdSkill.getByRole('checkbox').check()
  await expect(createdSkill).not.toHaveClass(/disabled/)
  await page.locator('.conversation-row').first().click()

  await page.reload()
  await expect(page.getByText(`${marker}_OK`, { exact: true })).toBeVisible({
    timeout: 120_000,
  })
  await expect(page.getByText('create_video_brief', { exact: true }).first()).toBeVisible()
  await expect(page.getByText(`E2E 钩子检查 ${marker.slice(-6)}`, { exact: true })).toBeVisible()

  await page
    .locator('.message-scroller')
    .evaluate((element) => element.scrollTo({ top: element.scrollHeight, behavior: 'instant' }))
  await page.waitForTimeout(500)
  await page.screenshot({
    path: testInfo.outputPath('video-fission-e2e.png'),
    fullPage: false,
  })
})

test('keeps the conversation and account controls reachable on mobile', async ({
  page,
  request,
}, testInfo) => {
  await request.post('/api/conversations', {
    data: { title: '这是端到端测试 VF_E2E_MOBILE' },
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.locator('.app-shell')).toBeVisible()

  await page.getByRole('button', { name: '打开会话列表' }).click()
  await expect(page.getByTestId('new-conversation')).toBeInViewport()
  await page.getByTestId('sidebar-user-menu').click()
  await expect(page.getByTestId('mode-settings')).toBeInViewport()
  await expect(page.getByRole('button', { name: '退出登录' })).toBeInViewport()
  await expect(page.getByTestId('sidebar-account-group')).toBeInViewport()
  await expect(page.getByRole('button', { name: '打开能力台' })).toHaveCount(0)
  await page.locator('.sidebar-close').click({ force: true })
  await expect(page.getByRole('button', { name: '模型' })).toBeVisible()
  await expect(page.getByRole('button', { name: '推理强度' })).toBeVisible()
  await page.waitForTimeout(300)
  await page.screenshot({
    path: testInfo.outputPath('video-fission-mobile.png'),
    fullPage: false,
  })
})

test('preserves an attachment uploaded before the first conversation exists', async ({
  page,
}, testInfo) => {
  const marker = `VF_E2E_ATTACHMENT_${Date.now()}`
  const attachmentPath = testInfo.outputPath('attachment-note.txt')
  await writeFile(attachmentPath, 'attachment survives conversation creation', 'utf8')

  await page.goto('/')
  await expect(page.locator('.app-shell')).toBeVisible()
  const uploadResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/uploads') && response.request().method() === 'POST',
  )
  await page.getByTitle('添加文件').locator('input[type=file]').setInputFiles(attachmentPath)
  cleanup.uploadId = (await (await uploadResponse).json()).id
  await expect(page.getByText('attachment-note.txt', { exact: true })).toBeVisible()
  await page
    .getByTestId('message-input')
    .fill(`这是端到端测试 ${marker}。请只回复 ATTACHMENT_OK。`)
  await page.getByTestId('send-message').click()
  await expect(page.getByText('ATTACHMENT_OK', { exact: true })).toBeVisible({
    timeout: 120_000,
  })
  await expect(page.getByText('attachment-note.txt', { exact: true })).toBeVisible()
})

test('creates the conversation only when the first message is sent', async ({ page, request }) => {
  await page.goto('/')
  await expect(page.locator('.app-shell')).toBeVisible()
  const before = await request.get('/api/conversations?archived=false&limit=100')
  const beforeCount = (await before.json()).items.length

  await page.getByTestId('new-conversation').click()
  await expect(page.getByTestId('message-input')).toBeVisible()
  await page.waitForTimeout(500)

  const after = await request.get('/api/conversations?archived=false&limit=100')
  expect((await after.json()).items.length).toBe(beforeCount)
})

test('searches conversations and keeps archived items out of the sidebar', async ({ page, request }) => {
  const marker = `VF_E2E_LIST_${Date.now()}`
  const active = await request.post('/api/conversations', {
    data: { title: `${marker}_ACTIVE` },
  })
  const archived = await request.post('/api/conversations', {
    data: { title: `${marker}_ARCHIVED` },
  })
  const activeId = (await active.json()).id
  const archivedId = (await archived.json()).id
  await request.post(`/api/conversations/${archivedId}/archive`)

  await page.goto('/')
  const search = page.getByRole('searchbox', { name: '搜索对话' })
  await search.fill(`${marker}_ACTIVE`)
  await expect(page.locator('.conversation-row').filter({ hasText: `${marker}_ACTIVE` })).toBeVisible()
  await expect(page.locator('.conversation-row').filter({ hasText: `${marker}_ARCHIVED` })).toHaveCount(0)

  await search.fill('')
  await expect(page.locator('.conversation-row').filter({ hasText: `${marker}_ACTIVE` })).toBeVisible()
  await expect(page.locator('.conversation-row').filter({ hasText: `${marker}_ARCHIVED` })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '查看归档' })).toHaveCount(0)
  await request.delete(`/api/conversations/${activeId}`)
  await request.delete(`/api/conversations/${archivedId}`)
})

