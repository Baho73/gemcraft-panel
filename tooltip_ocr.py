# Подсказка игры (чёрная непрозрачная рамка при наведении) -> текст. OCR встроенный в Windows (winrt), без новых пакетов.
import asyncio
import re

import cv2, numpy as np

import numread
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

ENGINE = OcrEngine.try_create_from_language(Language("en-US"))


def tooltip_box(img):
    """Самый большой почти чёрный прямоугольник кадра: (x, y, w, h) или None."""
    # фон подсказки — ровный 0; тёмные места поля 10-40, порог 12 склеивал рамку со всем кадром
    dark = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) <= 3).astype(np.uint8) * 255
    # чёрные места поля касались подсказки и склеивались с ней в рамку на весь кадр (волна 47, 19.10):
    # размыкание рвёт тонкие перемычки, а подсказка — почти сплошной прямоугольник (заполнен 0.98, поле 0.3-0.45)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    cs, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [(cv2.boundingRect(c), cv2.contourArea(c)) for c in cs]
    boxes = [b for b, a in boxes if b[2] > 150 and b[3] > 60 and a > 0.9 * b[2] * b[3]]
    return max(boxes, key=lambda b: b[2] * b[3], default=None)


def ocr(img, scale=3, net=True):
    """Строки текста с картинки BGR. net — слова-числа дочитывает сеть numnet (numread.py): Windows путает
    в числах ε, точку и цифры. Сеть не уверена — вместо числа «?», разбор такое число не примет."""
    big = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    bgra = cv2.cvtColor(big, cv2.COLOR_BGR2BGRA)
    w = DataWriter()
    w.write_bytes(bgra.tobytes())
    bmp = SoftwareBitmap(BitmapPixelFormat.BGRA8, big.shape[1], big.shape[0])
    bmp.copy_from_buffer(w.detach_buffer())
    async def run():
        return await ENGINE.recognize_async(bmp)
    res = asyncio.run(run())
    if not net or not numread.MODEL.exists():
        return [l.text for l in res.lines]
    lines = [[(wd.text, *(v / scale for v in (wd.bounding_rect.x, wd.bounding_rect.y, wd.bounding_rect.width,
                                               wd.bounding_rect.height))) for wd in l.words] for l in res.lines]
    nums = [(li, n, numread.band(img, *n[3:])) for li, ws in enumerate(lines) for n in numread.numbers(ws)]
    reads = iter(numread.read_many([g for *_, g in nums if g is not None]))  # все числа подсказки — одной пачкой
    reads = [next(reads) if g is not None else ("", 0.0) for *_, g in nums]
    words = [[t for t, *_ in ws] for ws in lines]
    for (li, (i0, i1, t, *_), g), (s, cf) in reversed(list(zip(nums, reads))):
        ok = cf >= numread.MIN_CONF and numread.VALID.fullmatch(s) and s.startswith("ε") == bool(re.match(r"\D", t))  # степень — с ε
        words[li][i0:i1] = [s.replace("ε", "e") if ok else "?"]
    out = []
    for ws in words:
        if "?" in ws:  # одно число не прочитано — строка без чисел: «? e12» дала бы здоровье 12 (23.10)
            ws = [t for t in ws if not re.fullmatch(r"[\d.,e]+|\?", t)] + ["?"]
        out.append(" ".join(ws))
    return out


def read_tooltip(img):
    b = tooltip_box(img)
    if not b:
        return None
    x, y, w, h = b
    return ocr(img[y:y + h, x:x + w])


if __name__ == "__main__":
    import sys
    print("\n".join(read_tooltip(cv2.imread(sys.argv[1])) or ["рамки нет"]))
