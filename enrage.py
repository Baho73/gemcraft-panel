"""Злость волн бомбами. Пока шаг 1: найти светлые (неразозлённые) блоки волн и прочитать их подсказки.
Запуск (bash): cd "D:/Python/gems" && PYTHONUTF8=1 python enrage.py"""
import json
import time
from pathlib import Path

import ctypes

import win32gui

import capture
from detectors import wave_blocks
from tooltip_ocr import ocr, tooltip_box
from tooltip_table import parse

HERE = Path(__file__).parent
EMPTY = (560, 480)  # точка без подсказки — увести курсор


def wave_info(h, x, y):
    """Навести на блок волны и разобрать подсказку: (номер волны, {Здоровье, Мана за убийство, ...}) или (None, None)."""
    capture.hover(h, *EMPTY)
    time.sleep(0.25)
    capture.hover(h, x, y)
    time.sleep(0.6)
    full = capture.grab(h, color=True)
    b = tooltip_box(full)
    if not b:
        return None, None
    bx, by, bw, bh = b
    t, k, row = parse(ocr(full[by:by + bh, bx:bx + bw]))
    return (k, row) if t == "wave" else (None, None)


def throw(h, points):
    """B (режим бомбы), отпустить; затем с зажатым Shift щёлкнуть по каждой волне — летят копии камня,
    сколько их — стоит на кнопке Throw Gem Bomb. Щелчок сообщением игра не принимает (19.10) — Shift и щелчок
    настоящие, игра должна быть видна."""
    kb = ctypes.windll.user32.keybd_event
    capture.real_hover(h, *EMPTY)
    win32gui.SetForegroundWindow(h)
    time.sleep(0.2)
    kb(0x42, 0x30, 0, 0)  # B
    kb(0x42, 0x30, 2, 0)
    time.sleep(0.3)
    kb(0x10, 0x2A, 0, 0)  # Shift вниз
    try:
        for x, y in points:
            capture.real_click(h, x, y)
            time.sleep(0.3)
    finally:
        kb(0x10, 0x2A, 2, 0)  # Shift вверх — даже при ошибке, иначе залипнет


def main():
    h = capture.find_window(json.load(open(HERE / "digit_grab.json", encoding="utf-8"))["title"])
    for top, bot, (x, y), kind, light in wave_blocks(capture.grab(h, color=True)):
        if light:
            k, row = wave_info(h, x, y)
            print(y, kind, k, row and {c: row.get(c) for c in ("Количество", "Здоровье", "Броня", "Мана за убийство")})
    capture.hover(h, *EMPTY)


if __name__ == "__main__":
    main()
