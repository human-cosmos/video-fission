import { expect, test } from '@playwright/test'

const cleanup = {
  conversationId: '',
  skillSlug: '',
}

test.afterEach(async ({ request }) => {
  const conversations = await request.get('/api/conversations?include_archived=true')
  if (conversations.ok()) {
    const payload = await conversations.json()
    for (const conversation of payload.items || []) {
      if (conversation.title.startsWith('这是端到端测试 VF_E2E_')) {
        await request.delete(`/api/conversations/${conversation.id}`)
      }
    }
  }
  if (cleanup.skillSlug) {
    await request.delete(`/api/skills/${cleanup.skillSlug}`)
  }
  cleanup.conversationId = ''
  cleanup.skillSlug = ''
})

test('creates a persistent Codex conversation, invokes a tool, and creates a skill', async ({
  page,
}, testInfo) => {
  const marker = `VF_E2E_${Date.now()}`
  cleanup.skillSlug = `e2e-hook-${marker.slice(-6).toLowerCase()}`
  await page.goto('/')
  await expect(page.getByTestId('runtime-status')).toContainText('Codex 已连接')

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
  await expect(page.getByText(/data\/artifacts\/.*\.md/).last()).toBeVisible({
    timeout: 120_000,
  })

  await page.getByTestId('create-skill').click()
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
    .locator('.skill-card')
    .filter({ hasText: `E2E 钩子检查 ${marker.slice(-6)}` })
  await createdSkill.getByRole('checkbox').uncheck()
  await expect(createdSkill).toHaveClass(/disabled/)
  await createdSkill.getByRole('checkbox').check()
  await expect(createdSkill).not.toHaveClass(/disabled/)

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

test('keeps the conversation and capability controls reachable on mobile', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Video Fission' })).toBeVisible()

  await page.getByRole('button', { name: '打开会话列表' }).click()
  await expect(page.getByTestId('new-conversation')).toBeInViewport()
  await page.getByRole('button', { name: '关闭会话列表' }).click()

  await page.getByRole('button', { name: '打开能力台' }).click()
  await expect(page.getByRole('heading', { name: '能力台' })).toBeInViewport()
  await page.waitForTimeout(300)
  await page.screenshot({
    path: testInfo.outputPath('video-fission-mobile.png'),
    fullPage: false,
  })
})

