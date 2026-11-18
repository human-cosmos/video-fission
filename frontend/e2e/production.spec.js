import { expect, test } from "@playwright/test"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const fixture = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../backend/tests/fixtures/sample-reference.mp4",
)

test("settings entry lives in the sidebar, not the top bar", async ({ page }) => {
  await page.goto("/")
  await expect(page.getByRole("navigation", { name: "工作台模式" })).toHaveCount(0)
  await expect(page.getByTestId("mode-chat")).toHaveCount(0)
  await expect(page.getByTestId("mode-studio")).toHaveCount(0)
  const settings = page.getByTestId("mode-settings")
  await expect(settings).toBeVisible()
  await expect(settings).toHaveText("系统设置")
  await settings.click()
  await expect(page.getByRole("heading", { name: "系统设置" })).toBeVisible()
  await expect(page.getByTestId("model-settings-page")).toBeVisible()
  await expect(page.getByTestId("settings-multimodal-provider")).toBeVisible()
  await page.getByTestId("settings-multimodal-provider").selectOption("gemini_gateway")
  await expect(page.getByTestId("settings-token-url")).toBeVisible()
  await expect(page.getByTestId("settings-understand")).toBeVisible()
  await expect(page.getByTestId("settings-understand-observe")).toBeVisible()
  await page.getByTestId("new-conversation").click()
  await expect(page.getByTestId("message-input")).toBeVisible()
})

test("composer requires a brief after a reference video is attached", async ({ page }) => {
  await page.goto("/")
  await page.getByTestId("new-conversation").click()
  await expect(page.getByTestId("message-input")).toBeVisible()
  await page.getByTestId("composer-video-upload").setInputFiles(fixture)
  await expect(page.getByTestId("composer-video-preview")).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId("composer-policy-brief")).toBeChecked()
  await expect(page.getByTestId("composer-hint-need-brief")).toBeVisible()
  await expect(page.getByTestId("send-message")).toBeDisabled()
  await page.getByTestId("message-input").fill("把第二段和结尾重新生成")
  await expect(page.getByTestId("send-message")).toBeEnabled()
})

test("runs a production inside a conversation, then regenerates a selected segment", async ({
  page,
  request,
}) => {
  await page.goto("/")
  await page.getByTestId("new-conversation").click()
  await expect(page.getByTestId("message-input")).toBeVisible()
  await page.getByTestId("composer-video-upload").setInputFiles(fixture)
  await expect(page.getByTestId("composer-video-preview")).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId("composer-policy-brief")).toBeChecked()
  await page.getByTestId("message-input").fill("把第二段和结尾重新生成")
  await page.getByTestId("send-message").click()

  await expect(page.getByTestId("user-message-video")).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId("video-progress-card")).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId("video-understanding-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-plan-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-segments-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-output-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-player")).toBeVisible()
  await expect(page.getByTestId("video-download-mp4")).toBeVisible()
  await expect(page.getByTestId("video-download-srt")).toBeVisible()

  const productionId = (await page.getByTestId("video-production-id").innerText()).trim()
  const detail = await request.get(`/api/productions/${productionId}`)
  expect(detail.ok()).toBeTruthy()
  const production = await detail.json()
  const valueSegment = (production.decomposition?.segments || []).find((segment) => segment.role === "value")
  expect(valueSegment?.id).toBeTruthy()

  const checkbox = page.getByTestId(`video-segment-regen-${valueSegment.id}`)
  await checkbox.check()
  await expect(page.getByTestId("video-rerun-selected")).toBeEnabled()
  await page.getByTestId("video-rerun-selected").click()
  await expect(page.getByTestId("video-progress-card")).toContainText("进行中", { timeout: 15_000 })
  await expect(page.getByTestId("video-output-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId(`video-segment-action-${valueSegment.id}`)).toHaveText("新生成")
  await expect(page.getByTestId("mode-studio")).toHaveCount(0)
})
