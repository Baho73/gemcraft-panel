# Подсказка игры (чёрная непрозрачная рамка при наведении) -> текст. OCR встроенный в Windows (winrt), без новых пакетов.
import asyncio

import cv2, numpy as np
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

ENGINE = OcrEngine.try_create_from_language(Language("en-US"))


def tooltip_box(img):
    """Самый большой почти чёрный прямоугольник кадра: (x, y, w, h) или None."""
    # фон подсказки — ровный 0; тёмные места поля 10-40, порог 12 склеивал рамку со всем кадром
    dark = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) <= 3).astype(np.uint8) * 255
    cs, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(c) for c in cs]
    boxes = [b for b in boxes if b[2] > 150 and b[3] > 60]
    return max(boxes, key=lambda b: b[2] * b[3], default=None)


def ocr(img, scale=3):
    """Строки текста с картинки BGR."""
    big = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    bgra = cv2.cvtColor(big, cv2.COLOR_BGR2BGRA)
    w = DataWriter()
    w.write_bytes(bgra.tobytes())
    bmp = SoftwareBitmap(BitmapPixelFormat.BGRA8, big.shape[1], big.shape[0])
    bmp.copy_from_buffer(w.detach_buffer())
    async def run():
        return await ENGINE.recognize_async(bmp)
    res = asyncio.run(run())
    return [l.text for l in res.lines]


def read_tooltip(img):
    b = tooltip_box(img)
    if not b:
        return None
    x, y, w, h = b
    return ocr(img[y:y + h, x:x + w])


if __name__ == "__main__":
    import sys
    print("\n".join(read_tooltip(cv2.imread(sys.argv[1])) or ["рамки нет"]))
