import sys
from pathlib import Path

BACKEND_DIR_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR_FOR_IMPORTS))

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
