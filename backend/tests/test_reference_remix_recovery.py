import threading
from unittest.mock import Mock

import pytest

from video_fission.remixes import REMIX_TASK_KIND, ReferenceRemixService


class FakeDatabase:
    def __init__(self, productions):
        self.productions = productions
        self.updates = []

    def list_productions_by_status(self, task_kind, statuses):
        assert task_kind == REMIX_TASK_KIND
        return [
            production
            for production in self.productions
            if production["status"] in statuses
        ]

    def update_production(self, production_id, payload):
        self.updates.append((production_id, payload))


def make_service(production):
    service = ReferenceRemixService.__new__(ReferenceRemixService)
    service.db = FakeDatabase([production])
    service._lock = threading.Lock()
    service._cancels = {}
    service._emit = Mock()
    service._start_render = Mock()
    service._start_analysis = Mock()
    return service


def test_recovery_continues_legacy_awaiting_order_task():
    production = make_plan_production()
    production["id"] = "rmx_legacy"
    production["status"] = "awaiting_order"
    production["remix_plan"]["phase"] = "awaiting_order"
    production["remix_plan"]["ordered_units"] = [{"id": "seg_02"}]
    production["remix_plan"]["middle_order"] = ["seg_02"]
    service = make_service(production)

    recovered = service.recover_orphaned_runs()

    assert recovered == ["rmx_legacy"]
    assert service._start_analysis.call_count == 0
    service._start_render.assert_called_once()
    _, kwargs = service._start_render.call_args
    assert kwargs == {"wait": False, "cancel": None}
    update = service.db.updates[0][1]
    assert update["status"] == "running"
    assert update["current_stage"] == "render"
    assert update["remix_plan"]["phase"] == "render_queued"
    assert update["remix_plan"]["adjusted_value_count"] == 2


def test_recovery_failure_does_not_escape_or_start_render():
    production = make_plan_production()
    production["id"] = "rmx_invalid_recovery"
    production["status"] = "awaiting_order"
    production["remix_plan"] = {
        **production["remix_plan"],
        "phase": "awaiting_order",
        "ordered_units": [{"id": "seg_02"}],
        "strategy": "reverse",
    }
    service = make_service(production)
    service._fail = Mock()

    recovered = service.recover_orphaned_runs()

    assert recovered == []
    service._start_render.assert_not_called()
    service._fail.assert_called_once()
    assert "最多调整 3 个 value 片段" in service._fail.call_args.args[1]


def make_plan_production():
    segments = [
        {"id": "seg_01", "role": "hook", "start_ms": 0, "end_ms": 1000},
        {"id": "seg_02", "role": "value", "start_ms": 1000, "end_ms": 2000},
        {"id": "seg_03", "role": "value", "start_ms": 2000, "end_ms": 3000},
        {"id": "seg_04", "role": "value", "start_ms": 3000, "end_ms": 4000},
        {"id": "seg_05", "role": "value", "start_ms": 4000, "end_ms": 5000},
        {"id": "seg_06", "role": "cta", "start_ms": 5000, "end_ms": 6000},
    ]
    return {
        "id": "rmx_plan",
        "source_video": {"duration_ms": 6000},
        "decomposition": {
            "segments": segments,
            "opening": {"start_ms": 0, "end_ms": 1000},
        },
        "remix_plan": {"strategy": "seeded_shuffle", "seed": "stable-seed"},
    }


def test_default_remix_only_swaps_one_adjacent_value_pair():
    service = ReferenceRemixService.__new__(ReferenceRemixService)

    plan = service._build_plan(make_plan_production())

    assert plan["adjusted_value_count"] == 2
    assert plan["max_auto_adjusted_units"] == 3
    assert [unit["id"] for unit in plan["ordered_units"][1:-1]] == plan["middle_order"]
    changed = set(plan["adjusted_value_ids"])
    original_values = ["seg_02", "seg_03", "seg_04", "seg_05"]
    ordered_values = [item for item in plan["middle_order"] if item in original_values]
    changed_indexes = [
        index
        for index, (before, after) in enumerate(zip(original_values, ordered_values))
        if before != after
    ]
    assert len(changed_indexes) == 2
    assert changed_indexes[1] == changed_indexes[0] + 1
    assert len(changed) == 2


def test_manual_order_is_not_limited_by_auto_reorder_cap():
    service = ReferenceRemixService.__new__(ReferenceRemixService)

    plan = service._build_plan(
        make_plan_production(),
        strategy="manual",
        middle_order=["seg_05", "seg_04", "seg_03", "seg_02"],
    )

    assert plan["adjusted_value_count"] == 4
    assert plan["middle_order"] == ["seg_05", "seg_04", "seg_03", "seg_02"]


def test_reverse_strategy_rejects_more_than_three_automatic_changes():
    service = ReferenceRemixService.__new__(ReferenceRemixService)

    with pytest.raises(ValueError, match="最多调整 3 个 value 片段"):
        service._build_plan(make_plan_production(), strategy="reverse")
