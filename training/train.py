"""Train the PPE model:   python training/train.py
Result: runs/detect/train*/weights/best.pt  (point MODEL_PATH at it in .env)

Tip: incidents marked "False alarm" in the dashboard show where the model is wrong -
add those screenshots (correctly labelled) to your dataset and retrain."""
from ultralytics import YOLO

model = YOLO("yolo11n.pt")
model.train(data="training/data.yaml", epochs=100, imgsz=640, batch=16)
