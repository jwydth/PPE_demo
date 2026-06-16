"""
Quick visual test for the sign detection model.

Usage (run from the backend/ directory):
    python scripts/test_sign_detection.py path/to/image.jpg
    python scripts/test_sign_detection.py path/to/image.jpg --conf 0.3
    python scripts/test_sign_detection.py path/to/image.jpg --out result.jpg

The script loads sign_model.pt, runs inference on the supplied image, draws
bounding boxes + labels for every detected sign, and saves the annotated image
next to the source file (or at --out if provided).
"""

import argparse
import sys
from pathlib import Path

# ── resolve project paths ────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import cv2
from ultralytics import YOLO

# Class IDs the sign model recognises (must match config.py)
CLASS_NAMES: dict[int, str] = {
    0: "M001_MustWearHardHat",
    1: "M002_MustWearSafetyVest",
    2: "P004_NoThoroughfare",
    3: "W011_Slippery",
}

# Distinct colours per class (BGR)
CLASS_COLORS: dict[int, tuple[int, int, int]] = {
    0: (0, 215, 255), # gold  – must wear hard hat
    1: (0, 255, 180), # green – must wear safety vest
    2: (0, 0, 220),   # red   – no-thoroughfare
    3: (0, 165, 255), # orange – slippery
}
DEFAULT_COLOR = (0, 255, 0)


def draw_detections(image_path: Path, model_path: Path, conf: float, out_path: Path) -> None:
    print(f"Model  : {model_path}")
    print(f"Image  : {image_path}")
    print(f"Conf   : {conf}")

    model = YOLO(str(model_path))

    img = cv2.imread(str(image_path))
    if img is None:
        sys.exit(f"ERROR: could not read image at {image_path}")

    results = model.predict(
        img,
        conf=conf,
        classes=list(CLASS_NAMES.keys()),
        verbose=False,
    )

    result = results[0]
    boxes = result.boxes

    if boxes is None or len(boxes) == 0:
        print("No signs detected — try lowering --conf")
    else:
        print(f"Detections: {len(boxes)}")

    for box in boxes:
        cls_id = int(box.cls[0])
        confidence = float(box.conf[0])
        x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])

        label_name = CLASS_NAMES.get(cls_id, f"class_{cls_id}")
        color = CLASS_COLORS.get(cls_id, DEFAULT_COLOR)
        label = f"{label_name} {confidence:.2f}"

        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)

        (text_w, text_h), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        tag_y1 = max(y1 - text_h - baseline - 4, 0)
        cv2.rectangle(img, (x1, tag_y1), (x1 + text_w + 4, y1), color, -1)
        cv2.putText(img, label, (x1 + 2, y1 - baseline - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

        print(f"  [{cls_id}] {label_name:30s} conf={confidence:.3f}  box=({x1},{y1},{x2},{y2})")

    cv2.imwrite(str(out_path), img)
    print(f"\nSaved  : {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test sign detection model on a single image")
    parser.add_argument("image", type=Path, help="Path to the input image")
    parser.add_argument(
        "--model", type=Path,
        default=BACKEND_DIR / "weights" / "sign_model.pt",
        help="Path to sign_model.pt (default: backend/weights/sign_model.pt)",
    )
    parser.add_argument(
        "--conf", type=float, default=0.35,
        help="Confidence threshold (default: 0.35)",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output image path (default: <input>_sign_result.jpg)",
    )
    args = parser.parse_args()

    if not args.image.exists():
        sys.exit(f"ERROR: image not found: {args.image}")
    if not args.model.exists():
        sys.exit(f"ERROR: model not found: {args.model}")

    out_path = args.out or args.image.parent / (args.image.stem + "_sign_result.jpg")
    draw_detections(args.image, args.model, args.conf, out_path)


if __name__ == "__main__":
    main()
