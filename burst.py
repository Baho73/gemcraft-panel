# Серия снимков окна игры: python burst.py [секунд] [папка] — 10 кадров/с, пишет только изменившиеся кадры.
# Иван 24.10: экран загрузки поля с камнями компаса мелькает слишком быстро, чтобы прочитать глазами.
import json, sys, time
from pathlib import Path

import cv2, numpy as np

import capture

HERE = Path(__file__).parent
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
out = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "burst"
out.mkdir(exist_ok=True)
h = capture.find_window(json.load(open(HERE / "digit_grab.json", encoding="utf-8"))["title"])
prev, end, n = None, time.time() + secs, 0
while time.time() < end:
    img = capture.grab(h, color=True)
    if prev is None or img.shape != prev.shape or np.abs(img.astype(int) - prev).mean() > 2:
        cv2.imwrite(str(out / f"{time.strftime('%H%M%S')}_{n:04}.png"), img)
        n += 1
    prev = img
    time.sleep(0.1)
print("кадров:", n)
