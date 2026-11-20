import threading
from unittest.mock import Mock

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
    production = {
        "id": "rmx_legacy",
        "status": "awaiting_order",
        "decomposition": {"segments": [{"id": "seg_01"}]},
        "remix_plan": {
            "phase": "awaiting_order",
            "ordered_units": [{"id": "seg_01"}],
            "middle_order": ["seg_01"],
        },
    }
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
