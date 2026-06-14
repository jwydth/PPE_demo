"""Report the backend Python, CUDA, Ultralytics, and YOLO runtime state."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR_FOR_IMPORTS))

from app.core.config import BACKEND_DIR, settings  # noqa: E402
from app.services.ppe_detector import PPEDetector  # noqa: E402


def main() -> None:
    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version.split()[0]}")
    _print_torch_runtime()
    _print_ultralytics_runtime()
    print(f"MODEL_PATH: {settings.MODEL_PATH}")
    print(f"Resolved MODEL_PATH: {_resolved_model_path()}")
    print(f"INFERENCE_DEVICE: {settings.INFERENCE_DEVICE}")

    detector = PPEDetector()
    model_loaded = detector.model is not None
    print(f"Selected detector device: {detector.device}")
    print(f"YOLO model loaded: {model_loaded}")
    print(f"Backend using mock mode: {not model_loaded}")

    if detector.device == "cpu":
        print(
            "Status: CPU runtime selected. This is valid for CPU-only PCs, but "
            "GPU inference requires a CUDA PyTorch build and available GPU."
        )
    elif model_loaded:
        print("Status: GPU runtime is ready for real YOLO inference.")
    else:
        print(
            "Status: GPU is visible, but the YOLO model is not loaded. "
            "Check MODEL_PATH and model dependencies."
        )


def _print_torch_runtime() -> None:
    try:
        import torch
    except Exception as exc:
        print(f"torch import error: {exc!r}")
        print("torch version: not installed")
        print("torch CUDA build: None")
        print("CUDA available: False")
        print("CUDA device count: 0")
        print("GPU: None")
        return

    print(f"torch version: {getattr(torch, '__version__', 'unknown')}")
    print(f"torch CUDA build: {getattr(torch.version, 'cuda', None)}")

    try:
        cuda_available = bool(torch.cuda.is_available())
        device_count = int(torch.cuda.device_count())
    except Exception as exc:
        print(f"torch CUDA check error: {exc!r}")
        print("CUDA available: False")
        print("CUDA device count: 0")
        print("GPU: None")
        return

    print(f"CUDA available: {cuda_available}")
    print(f"CUDA device count: {device_count}")
    if cuda_available and device_count > 0:
        try:
            print(f"GPU: {torch.cuda.get_device_name(0)}")
        except Exception as exc:
            print(f"GPU name error: {exc!r}")
    else:
        print("GPU: None")


def _print_ultralytics_runtime() -> None:
    try:
        import ultralytics
    except Exception as exc:
        print(f"Ultralytics import error: {exc!r}")
        print("Ultralytics version: not installed")
        return

    print(f"Ultralytics version: {getattr(ultralytics, '__version__', 'unknown')}")


def _resolved_model_path() -> Path:
    model_path = Path(settings.MODEL_PATH).expanduser()
    if not model_path.is_absolute():
        model_path = BACKEND_DIR / model_path
    return model_path.resolve()


if __name__ == "__main__":
    main()
