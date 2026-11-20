import { expect, test } from "@playwright/test"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const fixture = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../.e2e-fixtures/sample-reference.mp4",
)

test("generates three full-width candidate videos with synchronized comparison", async ({
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
  await expect(page.getByTestId("video-script-choose")).toHaveCount(0)
  await expect(page.getByTestId("video-candidate-grid")).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId("video-candidate-script_a")).toBeVisible()
  await expect(page.getByTestId("video-candidate-script_b")).toBeVisible()
  await expect(page.getByTestId("video-candidate-script_c")).toBeVisible()
  await expect(page.getByTestId("video-candidate-select")).toHaveCount(0)
  await expect(page.getByTestId("video-output-card")).toBeVisible()
  await expect(page.getByTestId("video-player")).toHaveCount(0)
  await expect(page.getByText("最终成片", { exact: true })).toHaveCount(0)
  await expect(page.getByTestId("video-candidate-download-script_a")).toBeVisible()
  await expect(page.getByTestId("video-candidate-compare-script_a")).toBeVisible()
  await expect(page.getByTestId("video-candidate-download-script_b")).toBeVisible()
  await expect(page.getByTestId("video-candidate-compare-script_b")).toBeVisible()
  await expect(page.getByTestId("video-candidate-download-script_c")).toBeVisible()
  await expect(page.getByTestId("video-candidate-compare-script_c")).toBeVisible()

  const gridBox = await page.getByTestId("video-candidate-grid").boundingBox()
  for (const candidateId of ["script_a", "script_b", "script_c"]) {
    const rowBox = await page.getByTestId(`video-candidate-${candidateId}`).boundingBox()
    expect(rowBox.width).toBeCloseTo(gridBox.width, 0)
  }
  const activeCandidateVideos = page.locator(".candidate-video-row video")
  await expect(activeCandidateVideos).toHaveCount(0)
  await page.getByTestId("video-candidate-player-script_a").click()
  await expect(activeCandidateVideos).toHaveCount(1)
  await page.getByTestId("video-candidate-player-script_b").click()
  await expect(activeCandidateVideos).toHaveCount(1)

  const productionId = (await page.getByTestId("video-production-id").innerText()).trim()
  const finished = await request.get(`/api/productions/${productionId}`)
  expect(finished.ok()).toBeTruthy()
  const finishedBody = await finished.json()
  expect(finishedBody.status).toBe("completed")
  expect((finishedBody.script_candidates || []).map((item) => item.id)).toEqual([
    "script_a",
    "script_b",
    "script_c",
  ])
  expect(
    finishedBody.selected_script_id == null || finishedBody.selected_script_id === "",
  ).toBeTruthy()
  expect((finishedBody.candidate_videos || []).map((item) => item.id)).toEqual([
    "script_a",
    "script_b",
    "script_c",
  ])
  for (const candidate of finishedBody.candidate_videos || []) {
    expect(candidate.video_url).toContain("final-burned-")
  }
  expect(finishedBody.clips || []).toHaveLength(0)
  expect(finishedBody.output == null || !finishedBody.output?.path).toBeTruthy()

  await page.getByTestId("video-candidate-compare-script_b").click()
  await expect(page.getByTestId("video-compare-modal")).toBeVisible()
  await expect(page.getByTestId("video-compare-source")).toBeVisible()
  await expect(page.getByTestId("video-compare-candidate")).toBeVisible()
  await expect(page.getByTestId("video-compare-toggle")).toBeEnabled({ timeout: 30_000 })
  await expect(page.getByTestId("video-compare-close")).toBeFocused()
  await page.keyboard.press("Shift+Tab")
  await expect(page.getByTestId("video-compare-audio")).toBeFocused()
  await page.keyboard.press("Tab")
  await expect(page.getByTestId("video-compare-close")).toBeFocused()

  await page.getByTestId("video-compare-toggle").click()
  await expect
    .poll(() =>
      page.evaluate(() => {
        const source = document.querySelector('[data-testid="video-compare-source"]')
        const candidate = document.querySelector('[data-testid="video-compare-candidate"]')
        return Boolean(
          source &&
            candidate &&
            !source.paused &&
            !candidate.paused &&
            source.playbackRate === 2 &&
            candidate.playbackRate === 2,
        )
      }),
    )
    .toBe(true)

  await page.getByTestId("video-compare-toggle").click()
  await expect
    .poll(() =>
      page.evaluate(() => {
        const source = document.querySelector('[data-testid="video-compare-source"]')
        const candidate = document.querySelector('[data-testid="video-compare-candidate"]')
        return Boolean(source?.paused && candidate?.paused)
      }),
    )
    .toBe(true)

  await page.getByTestId("video-compare-close").click()
  await expect(page.getByTestId("video-compare-modal")).toHaveCount(0)
  await expect(page.getByTestId("video-candidate-compare-script_b")).toBeFocused()
})
