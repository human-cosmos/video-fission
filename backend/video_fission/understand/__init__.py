"""video_slot 理解与切片能力的适配包。不要运行时 import /workspace/video_slot。"""

from .normalize import (
    SLOT_TO_ROLE,
    coverage_ok_lenses,
    extract_video_tags,
    normalize_tags,
    role_from_slot,
    validate_segments,
)
from .service import run_decompose, understand_settings

__all__ = [
    "SLOT_TO_ROLE",
    "coverage_ok_lenses",
    "extract_video_tags",
    "normalize_tags",
    "role_from_slot",
    "run_decompose",
    "understand_settings",
    "validate_segments",
]
