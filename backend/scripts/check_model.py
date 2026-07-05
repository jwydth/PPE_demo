import sys
import os
from pathlib import Path

# Add backend to path
sys.path.append(os.path.abspath('backend'))

from app.core.config import settings, BACKEND_DIR
from app.services.ppe_detector import PPEDetector
from PIL import Image
import numpy as np

def test_model():
    print(f"BACKEND_DIR: {BACKEND_DIR}")
    print(f"MODEL_PATH: {settings.MODEL_PATH}")
    
    detector = PPEDetector()
    if detector.model is None:
        print("FAILED: Model could not be loaded.")
        return
    
    print(f"Model loaded successfully on {detector.device}")
    
    # Create a dummy image
    img = Image.fromarray(np.zeros((640, 640, 3), dtype=np.uint8))
    results = detector.model.predict(img, conf=0.1)
    print(f"Predict on dummy image returned {len(results)} results")
    
    if hasattr(detector.model, 'names'):
        print(f"Model class names: {detector.model.names}")

if __name__ == "__main__":
    test_model()
