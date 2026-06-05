import cv2
import numpy as np
import re
import ast
import sys
from pathlib import Path

def visualize_test(image_path, report_path="output.txt", output_path="test_visualization.jpg"):
    # 1. Load Image
    img = cv2.imread(image_path)
    if img is None:
        print(f"Error: Could not read image at {image_path}")
        return

    # 2. Read Report
    if not Path(report_path).exists():
        print(f"Error: Report file {report_path} not found.")
        return
    
    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 3. Parse Calibration Points
    # Format: Point 1: Camera(946.4, 361.7)
    calib_matches = re.finditer(r"Point \d+: Camera\(([\d\.-]+), ([\d\.-]+)\)", content)
    for match in calib_matches:
        cx, cy = float(match.group(1)), float(match.group(2))
        cv2.circle(img, (int(cx), int(cy)), 8, (255, 0, 0), -1) # Large Blue dot
        cv2.circle(img, (int(cx), int(cy)), 9, (255, 255, 255), 1)

    # 4. Parse Zones / Polygons
    # Format: Zone 1 (Zone):\n  Camera Coordinates: [(x, y), ...]
    zone_matches = re.finditer(r"Zone \d+ \((.*?)\):\s+Camera Coordinates: (\[.*?\])", content)
    for match in zone_matches:
        name = match.group(1)
        pts_str = match.group(2)
        try:
            pts = ast.literal_eval(pts_str)
            if not pts: continue
            
            pts_array = np.array(pts, dtype=np.int32).reshape((-1, 1, 2))
            
            # Draw polygon outline (Red)
            cv2.polylines(img, [pts_array], isClosed=True, color=(0, 0, 255), thickness=2)
            
            # Draw individual vertices (Small Blue circles)
            for pt in pts:
                cv2.circle(img, (int(pt[0]), int(pt[1])), 3, (255, 0, 0), -1)
            
            # Label the zone
            label_pos = pts[0]
            cv2.putText(img, name, (int(label_pos[0]), int(max(20, label_pos[1] - 10))), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        except Exception as e:
            print(f"Error parsing zone {name}: {e}")

    # 5. Parse Human Detections
    # Format:
    # Person 1 (Conf: 0.85):
    #   Bounding Box: [x1=329.4, y1=277.6, x2=419.7, y2=620.7]
    #   Foot Point: Camera(374.6, 620.7)
    person_blocks = re.split(r"Person \d+", content)[1:]
    for i, block in enumerate(person_blocks):
        pid = i + 1
        
        # Bounding Box
        bbox_match = re.search(r"Bounding Box: \[x1=([\d\.-]+), y1=([\d\.-]+), x2=([\d\.-]+), y2=([\d\.-]+)\]", block)
        if bbox_match:
            x1, y1, x2, y2 = map(float, bbox_match.groups())
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), (0, 165, 255), 2) # Orange
            cv2.putText(img, f"P{pid}", (int(x1), int(max(20, y1 - 5))), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)

        # Foot Point
        foot_match = re.search(r"Foot Point: Camera\(([\d\.-]+), ([\d\.-]+)\)", block)
        if foot_match:
            fx, fy = map(float, foot_match.groups())
            cv2.circle(img, (int(fx), int(fy)), 6, (0, 255, 0), -1) # Green dot
            cv2.putText(img, "Feet", (int(fx) + 10, int(fy)), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

    # 6. Save output
    cv2.imwrite(output_path, img)
    print(f"Visualization saved to {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python visualize_test.py <image_path> [report_path]")
        sys.exit(1)
    
    img_p = sys.argv[1]
    rpt_p = sys.argv[2] if len(sys.argv) > 2 else "output.txt"
    visualize_test(img_p, rpt_p)
