"""PySceneDetect 帧级内容变化分数。"""

import cv2
from scenedetect import SceneManager, StatsManager, open_video
from scenedetect.detectors import ContentDetector

METRIC_KEY = "content_val"


class ShotDetector:
    """基于 PySceneDetect ContentDetector 的帧分数提取。"""

    def __init__(self, threshold: float = 27.0, min_scene_len: int = 1) -> None:
        self.threshold = threshold
        self.min_scene_len = min_scene_len

    def get_frame_scores(self, video_path: str) -> tuple[int, float, float, list[float]]:
        """返回 (总帧数, 时长, fps, 每帧内容变化分数)。"""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频: {video_path}")
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        finally:
            cap.release()

        if fps <= 0 or total_frames <= 0:
            raise RuntimeError(f"视频信息异常: fps={fps}, frames={total_frames}")
        duration = total_frames / fps

        stats = StatsManager()
        manager = SceneManager(stats_manager=stats)
        manager.add_detector(ContentDetector(threshold=self.threshold, min_scene_len=self.min_scene_len))
        video = open_video(video_path, backend="opencv")
        manager.detect_scenes(video, show_progress=False)

        scores: list[float] = []
        for frame_num in range(total_frames):
            try:
                values = stats.get_metrics(frame_num, [METRIC_KEY])
                scores.append(float(values[0]) if values else 0.0)
            except Exception:  # noqa: BLE001 - 未记录的帧按 0 处理
                scores.append(0.0)

        return total_frames, duration, fps, scores
