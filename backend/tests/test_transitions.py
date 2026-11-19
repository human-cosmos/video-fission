from __future__ import annotations

from video_fission.understand.transitions import (
    detection_available,
    missing_detection_dependencies,
)


def test_transition_detection_dependencies_are_installed_by_default():
    assert missing_detection_dependencies() == []
    assert detection_available() is True
