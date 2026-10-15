# Захват подсказок (встроен в пульт): Иван водит своей мышью по камням и волнам, на каждом кадре пульта
# проверяется, есть ли подсказка под настоящим курсором; новая — сохраняется картинкой в tooltips/.
# Только захват; распознавание и таблица — tooltip_table.py.
import time
from pathlib import Path

import cv2, win32gui

from tooltip_ocr import tooltip_box

OUT = Path(__file__).parent / "tooltips"
CELL, X0, Y0 = 34, 5, 2  # ponytail: сетка поля при окне 1088x612 (центры клеток 243,277… / 138,172…); другой размер окна — пересчитать


def cell(x, y):
    return (x - X0 + CELL // 2) // CELL, (y - Y0 + CELL // 2) // CELL


class TooltipGrab:
    def __init__(self):
        self.last = None  # клетка последнего сохранённого снимка
        self.seen = None  # клетка с прошлого кадра: сохраняем, когда подсказка простояла 2 кадра (дорисовалась)

    def feed(self, hwnd, full):
        """Кадр пульта -> имя сохранённого файла или None."""
        cx, cy = win32gui.ScreenToClient(hwnd, win32gui.GetCursorPos())
        b = tooltip_box(full)
        # подсказка стоит справа от курсора на 28 px; иначе она чужая (наведение цепочки сообщением) или её нет
        if not b or abs(b[0] - cx - 28) > 6 or not (0 <= cy < full.shape[0]):
            self.last = self.seen = None
            return None
        c = cell(cx, cy)
        if c == self.last or c != self.seen:
            self.seen = c
            return None
        OUT.mkdir(exist_ok=True)
        x, y, w, h = b
        name = f"{time.strftime('%Y%m%d-%H%M%S')}_{c[0]}_{c[1]}.png"
        cv2.imwrite(str(OUT / name), full[y:y + h, x:x + w])
        self.last = c
        return name
