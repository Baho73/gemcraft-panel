# Экран талисмана: навести (сообщением, мышь Ивана не трогаем) на каждое гнездо и фрагмент в запасе,
# вырезать подсказку и распознать. Результат: amp/<имя>.png и amp/scan.json.
import json, sys, time
from pathlib import Path

import cv2

import capture
from tooltip_ocr import ocr, tooltip_box

HERE = Path(__file__).parent
OUT = HERE / "amp"
# ponytail: центры сняты с окна 1088×612 (17.10); другой размер окна — переснять
SLOTS = {f"t{i:02}": p for i, p in enumerate([
    (328, 65), (230, 135), (428, 135), (328, 165), (130, 205), (525, 205), (230, 237), (428, 237),
    (165, 322), (490, 322), (268, 352), (388, 352), (205, 435), (330, 435), (447, 435)])}
SLOTS |= {f"i{r}{c}": (713 + 51 * c, 157 + 51 * r) for r in range(6) for c in range(6)}
SLOTS["sum"] = (305, 545)
EMPTY = (560, 480)


def main():
    OUT.mkdir(exist_ok=True)
    h = capture.find_window(json.load(open(HERE / "digit_grab.json", encoding="utf-8"))["title"])
    res = {}
    for name, (x, y) in SLOTS.items():
        capture.hover(h, *EMPTY)
        time.sleep(0.25)
        capture.hover(h, x, y)
        time.sleep(0.6)
        full = capture.grab(h, color=True)
        b = tooltip_box(full)
        if not b:
            continue
        bx, by, bw, bh = b
        cut = full[by:by + bh, bx:bx + bw]
        cv2.imwrite(str(OUT / f"{name}.png"), cut)
        res[name] = ocr(cut)
        print(name, res[name][:1])
    capture.hover(h, *EMPTY)
    (OUT / "scan.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
