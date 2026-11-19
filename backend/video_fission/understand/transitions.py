"""转场检测与模型时间点的帧级精修。缺可选依赖时自动退化。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .normalize import get_lens_data, validate_segments

logger = logging.getLogger(__name__)

_BASE_DEPENDENCIES = {
    "cv2": "opencv-python",
    "scenedetect": "scenedetect",
}


@dataclass
class TransitionInfo:
    fps: float = 0.0
    duration: float = 0.0
    total_frames: int = 0
    frame_scores: list[float] = field(default_factory=list)
    cnn_scores: list[float] = field(default_factory=list)
    ok: bool = False
    warning: str = ""


def missing_detection_dependencies() -> list[str]:
    missing: list[str] = []
    for module, package in _BASE_DEPENDENCIES.items():
        try:
            __import__(module)
        except Exception:  # noqa: BLE001
            missing.append(package)
    return missing


def detection_available() -> bool:
    # TransNetV2 is optional. PySceneDetect remains useful without torch.
    return not missing_detection_dependencies()


class TransitionDetector:
    def __init__(self) -> None:
        self.shot_detector = None
        try:
            from .models.shot_detector import ShotDetector

            self.shot_detector = ShotDetector()
        except Exception as exc:  # noqa: BLE001
            logger.info("跳过 PySceneDetect 转场检测: %s", exc)
        self._transnet = None

    @property
    def transnet(self):
        if self._transnet is None:
            from .models.transnet_v2 import TransNetV2Model

            self._transnet = TransNetV2Model()
        return self._transnet

    def analyze(self, video_path: str | Path) -> TransitionInfo:
        if self.shot_detector is None:
            logger.warning("transition.detector_unavailable file=%s", Path(video_path).name)
            return TransitionInfo(ok=False, warning="转场检测依赖不可用，已退化为 ffmpeg 场景切分")
        started = time.perf_counter()
        logger.info("transition.analysis_started file=%s", Path(video_path).name)
        try:
            total_frames, duration, fps, frame_scores = self.shot_detector.get_frame_scores(str(video_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("transition.pyscenedetect_failed", exc_info=True)
            return TransitionInfo(ok=False, warning=f"转场检测失败，已退化为 ffmpeg 场景切分：{exc.__class__.__name__}")
        logger.info(
            "transition.pyscenedetect_completed total_frames=%s duration_s=%.3f fps=%.3f scores=%s",
            total_frames,
            duration,
            fps,
            len(frame_scores or []),
        )

        cnn_scores: list[float] = []
        cnn_warning = ""
        try:
            _, cnn_scores = self.transnet.predict_video(str(video_path), total_frames)
        except Exception as exc:  # noqa: BLE001
            cnn_warning = f"TransNetV2 不可用，仅使用 PySceneDetect：{exc.__class__.__name__}"
            logger.warning("transition.transnet_failed fallback=pyscenedetect", exc_info=True)
        logger.info(
            "transition.analysis_completed total_frames=%s frame_scores=%s cnn_scores=%s elapsed_ms=%.1f warning=%s",
            total_frames,
            len(frame_scores or []),
            len(cnn_scores or []),
            (time.perf_counter() - started) * 1000,
            cnn_warning or "-",
        )

        return TransitionInfo(
            fps=fps,
            duration=duration,
            total_frames=total_frames,
            frame_scores=list(frame_scores or []),
            cnn_scores=[float(value) for value in (cnn_scores or [])],
            ok=bool(frame_scores or cnn_scores),
            warning=cnn_warning,
        )

    def refine_segments(self, info: TransitionInfo, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not segments:
            return []
        fps = info.fps or 30.0
        duration = info.duration or max((get_lens_data(segment)[3] for segment in segments), default=0.0)
        if duration <= 0:
            return segments
        total = info.total_frames or int(duration * fps)
        frame_scores = self._pad_scores(info.frame_scores, total)
        cnn_scores = self._pad_scores(info.cnn_scores, total)
        if not any(frame_scores) and not any(cnn_scores):
            return self._continuity_only(segments, fps, duration)

        refined: list[dict[str, Any]] = []
        prev_end = -1.0
        for index, segment in enumerate(segments):
            _, _, seg_start, seg_end = get_lens_data(segment)
            start = 0.0 if index == 0 else prev_end + 1.0 / fps
            if index == len(segments) - 1:
                end = duration
            else:
                end = seg_end
                precise = self._find_precise(frame_scores, cnn_scores, total, fps, duration, seg_start, seg_end, start)
                if precise is not None:
                    end, need_cut = precise
                    if need_cut:
                        end -= 10.0 / fps
            end = min(end, duration)
            if end <= start:
                end = min(start + 1.0 / fps, duration)
            if end <= start:
                start = max(0.0, end - 1.0 / fps)
            updated = dict(segment)
            updated["start_time"] = round(start, 3)
            updated["end_time"] = round(end, 3)
            refined.append(updated)
            prev_end = end
        return validate_segments(refined, duration, fps)

    def cuts_ms(self, info: TransitionInfo) -> list[int]:
        if not info.ok:
            return []
        fps = info.fps or 30.0
        total = info.total_frames or len(info.frame_scores) or len(info.cnn_scores)
        frame_scores = self._pad_scores(info.frame_scores, total)
        cnn_scores = self._pad_scores(info.cnn_scores, total)
        peaks: list[int] = []
        for index in range(total):
            traditional = frame_scores[index] if index < len(frame_scores) else 0.0
            cnn = cnn_scores[index] if index < len(cnn_scores) else 0.0
            if traditional >= 35 or cnn > 0.3:
                ms = int(round(index / max(fps, 1e-6) * 1000))
                if not peaks or ms - peaks[-1] >= 200:
                    peaks.append(ms)
        logger.info("transition.cuts_completed cuts=%s preview=%s", len(peaks), peaks[:20])
        return peaks

    @staticmethod
    def _pad_scores(scores: list[float], total: int) -> list[float]:
        if total <= 0:
            return []
        if len(scores) >= total:
            return scores[:total]
        return list(scores) + [0.0] * (total - len(scores))

    @staticmethod
    def _find_precise(
        frame_scores: list[float],
        cnn_scores: list[float],
        total: int,
        fps: float,
        duration: float,
        seg_start: float,
        seg_end: float,
        floor: float,
    ) -> tuple[float, bool] | None:
        diff = 1.0 if seg_end - seg_start <= 2 else 2.0
        begin_frame = int(max(seg_end - diff, 0.0) * fps)
        end_frame = int(min(seg_end + diff, duration) * fps)
        begin_frame = max(0, min(begin_frame, total - 1))
        end_frame = max(begin_frame + 1, min(end_frame, total))
        traditional = frame_scores[begin_frame:end_frame]
        if traditional and max(traditional) >= 35:
            index = traditional.index(max(traditional)) + begin_frame
            precise = round(index / fps, 3)
            return (max(precise, floor + 1.0 / fps), False)
        cnn = cnn_scores[begin_frame:end_frame]
        if not cnn:
            return None
        best_value = max(cnn)
        index = cnn.index(best_value) + begin_frame
        precise = round(index / fps, 3)
        return (max(precise, floor + 1.0 / fps), best_value > 0.3)

    @staticmethod
    def _continuity_only(segments: list[dict[str, Any]], fps: float, duration: float) -> list[dict[str, Any]]:
        refined: list[dict[str, Any]] = []
        prev_end = -1.0
        for index, segment in enumerate(segments):
            _, _, _seg_start, seg_end = get_lens_data(segment)
            start = 0.0 if index == 0 else prev_end + 1.0 / fps
            end = duration if index == len(segments) - 1 else seg_end
            end = min(max(end, start + 1.0 / fps), duration)
            updated = dict(segment)
            updated["start_time"] = round(start, 3)
            updated["end_time"] = round(end, 3)
            refined.append(updated)
            prev_end = end
        return refined
