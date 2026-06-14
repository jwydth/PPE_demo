from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import ppe_detector as ppe


def _fake_torch(cuda_available: bool, device_count: int = 1):
    cuda = SimpleNamespace(
        is_available=lambda: cuda_available,
        device_count=lambda: device_count,
        get_device_name=lambda index: f"Fake CUDA GPU {index}",
    )
    return SimpleNamespace(cuda=cuda)


def test_auto_device_uses_cuda_when_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True))

    assert ppe._select_inference_device("auto") == "cuda:0"


def test_auto_device_falls_back_to_cpu_without_cuda(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=False))

    assert ppe._select_inference_device("auto") == "cpu"


def test_out_of_range_cuda_device_falls_back_to_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True, device_count=1))

    assert ppe._select_inference_device("cuda:3") == "cpu"
