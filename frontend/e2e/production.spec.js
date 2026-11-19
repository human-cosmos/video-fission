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
  await page.getByTestId("sidebar-user-menu").click()
  const settings = page.getByTestId("mode-settings")
  await expect(settings).toBeVisible()
  await expect(settings).toHaveText("系统设置")
  await page.locator("main.workspace").click({ position: { x: 24, y: 220 } })
  await expect(settings).toBeHidden()
  await page.getByTestId("sidebar-user-menu").click()
  await expect(settings).toBeVisible()
  await settings.click()
  await expect(page.getByRole("heading", { name: "系统设置" })).toBeVisible()
  await expect(page.getByTestId("model-settings-page")).toBeVisible()
  await expect(page.getByTestId("sidebar-account-group")).toBeVisible()
  await expect(page.getByRole("button", { name: "更多操作" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "打开能力台" })).toHaveCount(0)
  await expect(page.getByText("多模态理解", { exact: true })).toHaveCount(0)
  await expect(page.getByText("视频生成", { exact: true })).toHaveCount(0)
  await expect(page.getByText("配音 TTS", { exact: true })).toHaveCount(0)
  await expect(page.getByTestId("settings-understand")).toHaveCount(0)
  await expect(page.getByTestId("settings-video-generator")).toHaveCount(0)
  await expect(page.getByTestId("settings-understand-observe")).toHaveCount(0)
  await expect(page.getByTestId("save-settings")).toHaveCount(0)
  await expect(page.getByRole("heading", { name: "Skill 与 Tool" })).toBeVisible()
  await page.getByTestId("sidebar-user-menu").click()
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible()
  await page.getByTestId("new-conversation").click()
  await expect(page.getByTestId("message-input")).toBeVisible()
})

test("composer requires a brief after a reference video is attached", async ({ page }) => {
  await page.goto("/")
  await page.getByTestId("new-conversation").click()
  await expect(page.getByTestId("message-input")).toBeVisible()
  await page.getByTestId("composer-video-upload").setInputFiles(fixture)
  await expect(page.getByTestId("composer-video-preview")).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId("composer-policy")).toHaveCount(0)
  await expect(page.getByTestId("composer-hint-need-brief")).toBeVisible()
  await expect(page.getByTestId("send-message")).toBeDisabled()
  await page.getByTestId("message-input").fill("把第二段和结尾重新生成")
  await expect(page.getByTestId("send-message")).toBeEnabled()
})

test("confirms scripts before generating candidates and keeps detail cards collapsed", async ({
  page,
  request,
}) => {
  const configured = await request.put("/api/settings", {
    data: {
      video_generator: {
        provider: "generic_http",
        base_url: "http://127.0.0.1:8002",
        api_key: "e2e-video-key",
        model: "e2e-video",
        resolution: "480p",
        timeout_seconds: 120,
      },
    },
  })
  expect(configured.ok()).toBeTruthy()
  await page.goto("/")
  await page.getByTestId("new-conversation").click()
  await expect(page.getByTestId("message-input")).toBeVisible()
  await page.getByTestId("composer-video-upload").setInputFiles(fixture)
  await expect(page.getByTestId("composer-video-preview")).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId("composer-policy")).toHaveCount(0)
  await page.getByTestId("message-input").fill("把第二段和结尾重新生成")
  await page.getByTestId("send-message").click()

  await expect(page.getByTestId("user-message-video")).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId("video-progress-card")).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId("video-production-steps")).toBeVisible()
  await expect(page.getByTestId("video-production-step-decompose")).toContainText("拆解")
  await expect(page.getByTestId("video-understanding-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-plan-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-script-approval")).toBeVisible({ timeout: 180_000 })
  expect(
    await page.getByTestId("video-understanding-disclosure").evaluate((element) => element.open),
  ).toBe(false)
  expect(
    await page.getByTestId("video-plan-disclosure").evaluate((element) => element.open),
  ).toBe(false)
  expect(
    await page.getByTestId("video-segments-disclosure").evaluate((element) => element.open),
  ).toBe(false)
  await page.getByTestId("video-script-approve").click()
  const chooseVideo = page.getByTestId("video-candidate-select").first()
  const segmentsCard = page.getByTestId("video-segments-card")
  await expect(chooseVideo).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-candidate-grid")).toBeVisible()
  await expect(page.getByTestId("video-candidate-select")).toHaveCount(3)
  await chooseVideo.click()
  await expect(segmentsCard).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-output-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-player")).toBeVisible()
  await expect(page.getByTestId("video-download-mp4")).toBeVisible()
  await expect(page.getByTestId("video-download-srt")).toHaveCount(0)
  await expect(page.getByTestId("video-production-step-decompose")).toContainText("切片结果")
  await expect(page.getByTestId("video-production-step-generate")).toContainText("片段结果")
  await expect(page.getByTestId("video-production-step-compose")).toContainText("成片文件")

  const productionId = (await page.getByTestId("video-production-id").innerText()).trim()
  await expect
    .poll(async () => {
      const current = await request.get(`/api/productions/${productionId}`)
      return (await current.json()).status
    })
    .toBe("completed")
  const detail = await request.get(`/api/productions/${productionId}`)
  expect(detail.ok()).toBeTruthy()
  const production = await detail.json()
  await expect(page.locator('[data-testid^="video-segment-regen-"]')).toHaveCount(0)
  await expect(page.getByTestId("video-rerun-selected")).toHaveCount(0)
  await expect(page.getByTestId("mode-studio")).toHaveCount(0)
})
