import threading
import time

from app.services.inference_coordination import PriorityInferenceLock
from app.services.model_cadence import CadenceGate, ModelCadence


def test_model_cadences_for_24_fps_source():
    ppe = ModelCadence(24, 8)
    sign = ModelCadence(24, 1, phase=13)

    assert [index for index in range(24) if ppe.is_due(index)] == list(range(0, 24, 3))
    assert [index for index in range(48) if sign.is_due(index)] == [13, 37]
    assert not ppe.is_due(13)


def test_cadence_gate_uses_first_frame_after_a_missed_deadline():
    gate = CadenceGate(ModelCadence(24, 8))

    accepted = [index for index in (100, 101, 102, 105, 106, 108, 109) if gate.accept(index)]

    assert accepted == [100, 105, 108]


def test_priority_lock_admits_behavior_before_waiting_sign():
    lock = PriorityInferenceLock()
    order: list[str] = []
    ready = threading.Barrier(3)

    lock.acquire()

    def contender(name: str, priority: int):
        ready.wait()
        with lock.priority(priority):
            order.append(name)

    sign = threading.Thread(target=contender, args=("sign", 2))
    behavior = threading.Thread(target=contender, args=("behavior", 0))
    sign.start()
    behavior.start()
    ready.wait()
    time.sleep(0.01)
    lock.release()
    sign.join()
    behavior.join()

    assert order == ["behavior", "sign"]


def test_priority_lock_deadline_prevents_ppe_starvation(monkeypatch):
    lock = PriorityInferenceLock()
    lock._MAX_WAIT_SECONDS = {1: 0.01, 2: 1.0}
    order: list[str] = []
    lock.acquire()

    def contender(name: str, priority: int):
        with lock.priority(priority):
            order.append(name)

    ppe = threading.Thread(target=contender, args=("ppe", 1))
    ppe.start()
    time.sleep(0.02)
    behavior = threading.Thread(target=contender, args=("behavior", 0))
    behavior.start()
    time.sleep(0.01)
    lock.release()
    ppe.join()
    behavior.join()

    assert order == ["ppe", "behavior"]
