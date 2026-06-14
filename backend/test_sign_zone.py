"""
Visual test for sign-based auto-zone generation.

Usage:
    python test_sign_zone.py <image_path>
    python test_sign_zone.py <image_path> --out zone_debug_output.jpg

Output: zone_debug_output.jpg showing:
    Red box    = raw sign detection bbox
    Yellow     = expanded floor-level zone (direction-aware)
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image, ImageDraw

from app.services.detection.sign_detector import (
    detect_signs,
    load_sign_model,
    signs_to_zone_records,
)
from app.services.zone_service import COORD_SCALE


def main() -> None:
    parser = argparse.ArgumentParser(description="Visual test for sign zone generation")
    parser.add_argument("image", help="Path to input image (PNG, JPG, etc.)")
    parser.add_argument("--out", default="zone_debug_output.jpg", help="Output image path")
    args = parser.parse_args()

    print("Loading sign model...")
    load_sign_model()

    img = Image.open(args.image).convert("RGB")
    frame_width, frame_height = img.size
    print(f"Image: {args.image}  ({frame_width}x{frame_height})")

    detections = detect_signs(img)
    if not detections:
        print("\nNo signs detected — check that SIGN_MODEL_PATH points to valid weights.")
        return

    zones = signs_to_zone_records(detections, frame_width, frame_height)

    print(f"\nDetected {len(detections)} sign(s), generated {len(zones)} zone(s):\n")

    draw = ImageDraw.Draw(img, "RGBA")

    for det, zone in zip(detections, zones):
        b = det.bbox

        # Print detection info
        print(f"  Sign   : {det.label}  (class {det.class_id})  conf={det.confidence:.3f}")
        print(f"  Bbox   : x1={b['x1']:.1f}  y1={b['y1']:.1f}  x2={b['x2']:.1f}  y2={b['y2']:.1f}")

        # Convert zone poly from 0-1000 scale back to pixel coords
        px_poly = [
            (int(p[0] / COORD_SCALE * frame_width), int(p[1] / COORD_SCALE * frame_height))
            for p in zone.poly
        ]
        print(f"  Zone   : {zone.zone_name}  type={zone.zone_type}")
        print(f"  Poly px: {px_poly}\n")

        # Draw zone polygon — yellow semi-transparent fill
        draw.polygon(px_poly, fill=(253, 224, 71, 70), outline=(253, 224, 71, 230))
        draw.text(
            (px_poly[0][0] + 4, px_poly[0][1] + 4),
            f"AUTO ZONE ({zone.zone_type})",
            fill=(253, 224, 71),
        )

        # Draw sign bbox — red outline
        draw.rectangle(
            [b["x1"], b["y1"], b["x2"], b["y2"]],
            outline=(220, 38, 38),
            width=3,
        )
        draw.rectangle(
            [b["x1"], max(0, b["y1"] - 22), b["x2"], b["y1"]],
            fill=(220, 38, 38, 200),
        )
        draw.text(
            (b["x1"] + 4, max(0, b["y1"] - 20)),
            f"{det.label}  {det.confidence:.2f}",
            fill=(255, 255, 255),
        )

    img.save(args.out)
    print(f"Saved → {args.out}")
    print("  Red    = sign detection bbox")
    print("  Yellow = auto zone (wide left/right, large downward, small upward)")


if __name__ == "__main__":
    main()
