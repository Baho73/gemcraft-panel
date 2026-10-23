"""Датасет для numnet: из сохранённых подсказок волн (tooltips/*_0_*.png) — вырезки слов-чисел с подписями Windows OCR
(crops.pkl; clean — число кусков по просветам совпало с длиной подписи) и образцы символов из чистых (samples.pkl).
Запуск (bash): cd "D:/Python/gems" && PYTHONUTF8=1 python numnet/harvest.py"""
import asyncio, collections, glob, pickle, re, sys
from pathlib import Path

import cv2, numpy as np
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.storage.streams import DataWriter

sys.path.insert(0, str(Path(__file__).parent.parent))
import numread, tooltip_ocr as T

OUT = Path(__file__).parent / "data"


def words(img, sc=3):
    big = cv2.resize(img, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
    bgra = cv2.cvtColor(big, cv2.COLOR_BGR2BGRA)
    w = DataWriter()
    w.write_bytes(bgra.tobytes())
    bmp = SoftwareBitmap(BitmapPixelFormat.BGRA8, big.shape[1], big.shape[0])
    bmp.copy_from_buffer(w.detach_buffer())

    async def run():
        return await T.ENGINE.recognize_async(bmp)
    return [[(wd.text, *(v / sc for v in (wd.bounding_rect.x, wd.bounding_rect.y, wd.bounding_rect.width,
                                           wd.bounding_rect.height))) for wd in l.words] for l in asyncio.run(run()).lines]


def segs(on):
    cols, out, x = on.sum(0), [], 0
    while x < len(cols):
        if cols[x]:
            a = x
            while x < len(cols) and cols[x]:
                x += 1
            out.append((a, x))
        x += 1
    return out


def main():
    crops, samples = [], collections.defaultdict(list)
    for f in sorted(glob.glob(str(Path(__file__).parent.parent / "tooltips" / "*_0_*.png"))):
        c = cv2.imread(f)
        for line in words(c):
            for i0, i1, text, x, y, w, h in numread.numbers(line):
                exp = re.fullmatch(r"\D\d{1,3}", text)
                if exp and not (i0 and re.fullmatch(r"[\d.,]*\d[\d.,]*", line[i0 - 1][0])):
                    continue  # степень — только сразу после числа
                lab = "ε" + text[1:] if exp else text
                g = numread.band(c, x, y, w, h)
                if g is None:
                    continue
                on = g >= 115
                sg = [s for s in segs(on) if on[:, s[0]:s[1]].sum() >= 2]
                clean = len(sg) == len(lab)
                crops.append((f, lab, clean, g))
                if clean:
                    for ch, (a, b) in zip(lab, sg):
                        samples[ch].append((g.shape[0], g[:, max(a - 1, 0):b + 1].copy()))
    OUT.mkdir(exist_ok=True)
    pickle.dump(crops, open(OUT / "crops.pkl", "wb"))
    pickle.dump(dict(samples), open(OUT / "samples.pkl", "wb"))
    print(len(crops), "вырезок, чистых", sum(c[2] for c in crops), {k: len(v) for k, v in sorted(samples.items())})


if __name__ == "__main__":
    main()
