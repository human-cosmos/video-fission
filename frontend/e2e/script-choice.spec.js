import { expect, test } from "@playwright/test"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const fixture = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../backend/tests/fixtures/sample-reference.mp4",
)

test("generates three candidate videos, then finalizes the selected video", async ({
  page,
  request,
}) => {
  test.setTimeout(360_000)
  await page.goto("/")
  await page.getByTestId("new-conversation").click()
  await expect(page.getByTestId("message-input")).toBeVisible()
  await page.getByTestId("composer-video-upload").setInputFiles(fixture)
  await expect(page.getByTestId("composer-video-preview")).toBeVisible({ timeout: 30_000 })
  await page.getByTestId("message-input").fill("按原片风格做一条短视频")
  await page.getByTestId("send-message").click()

  await expect(page.getByTestId("user-message-video")).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId("video-progress-card")).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId("video-plan-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-script-approval")).toBeVisible({ timeout: 180_000 })
  await page.getByTestId("video-script-approve").click()
  await expect(page.getByTestId("video-progress-awaiting-video")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-script-choose")).toHaveCount(0)
  await expect(page.getByTestId("video-candidate-grid")).toBeVisible()
  await expect(page.getByTestId("video-candidate-script_a")).toBeVisible()
  await expect(page.getByTestId("video-candidate-script_b")).toBeVisible()
  await expect(page.getByTestId("video-candidate-script_c")).toBeVisible()
  await expect(page.getByTestId("video-candidate-select")).toHaveCount(3)
  await expect(page.getByTestId("video-output-card")).toBeVisible()
  // Segments card may already show the decompose/replace plan; it is not
  // evidence that generate has started. Gate on status, clips, and output.

  const productionId = (await page.getByTestId("video-production-id").innerText()).trim()
  const paused = await request.get(`/api/productions/${productionId}`)
  expect(paused.ok()).toBeTruthy()
  const pausedBody = await paused.json()
  expect(pausedBody.status).toBe("awaiting_video_choice")
  expect((pausedBody.script_candidates || []).map((item) => item.id)).toEqual([
    "script_a",
    "script_b",
    "script_c",
  ])
  expect(pausedBody.selected_script_id == null || pausedBody.selected_script_id === "").toBeTruthy()
  expect((pausedBody.candidate_videos || []).map((item) => item.id)).toEqual([
    "script_a",
    "script_b",
    "script_c",
  ])
  expect(pausedBody.clips || []).toHaveLength(0)
  expect(pausedBody.output == null || !pausedBody.output?.path).toBeTruthy()

  await page.getByTestId("video-candidate-script_b").getByTestId("video-candidate-select").click()
  await expect(page.getByTestId("video-segments-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-output-card")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-player")).toBeVisible()
  await expect(page.getByTestId("video-candidate-script_b")).toContainText("已选用")
  await expect
    .poll(async () => {
      const current = await request.get(`/api/productions/${productionId}`)
      return (await current.json()).status
    })
    .toBe("completed")

  const finished = await request.get(`/api/productions/${productionId}`)
  expect(finished.ok()).toBeTruthy()
  const done = await finished.json()
  expect(done.status).toBe("completed")
  expect(done.selected_script_id).toBe("script_b")
})
