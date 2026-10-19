"""Злость волн бомбами. Пока шаг 1: найти светлые (неразозлённые) блоки волн и прочитать их подсказки.
Запуск (bash): cd "D:/Python/gems" && PYTHONUTF8=1 python enrage.py"""
import json
import re
import time
from pathlib import Path

import ctypes

import cv2

import win32gui

import capture
from detectors import wave_blocks
from tooltip_ocr import ocr, tooltip_box
from tooltip_table import parse

HERE = Path(__file__).parent
EMPTY = (560, 480)  # точка без подсказки — увести курсор
PAUSE = 1.0  # с между щелчками-бросками
BOMBS = 1500  # цель: бомб на волну (6 бросков по 250)
YELLOW = 0.4  # доля жёлтого на кнопке бомбы: с Shift 0.80, без 0.00 (19.10)
PER = 250  # бомб за щелчок — стоит на кнопке Throw Gem Bomb


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
    lines = ocr(full[by:by + bh, bx:bx + bw])
    t, k, row = parse(lines)
    if t != "wave":
        return None, None
    dim = ocr(cv2.convertScaleAbs(full[by:by + bh, bx:bx + bw], alpha=1.5))  # число бомб тусклое — без осветления теряется
    m = re.search(r"([\d,]+) gem bombs? dropped", " ".join(dim), re.I)
    row["Бомб"] = int(m.group(1).replace(",", "")) if m else 0
    return k, row


def bomb_on(full):
    """Режим бомбы включён: кнопка Throw Gem Bomb подсвечена (ярких пикселей 0.66 против 0.08 без режима, 19.10)."""
    return (cv2.cvtColor(full[97:130, 976:1010], cv2.COLOR_BGR2HSV)[:, :, 2] > 200).mean() > 0.3


def bomb_yellow(full):
    """Доля жёлтых пикселей кнопки бомбы: с зажатым Shift она жёлтая (бросок копиями), без Shift — белая."""
    hsv = cv2.cvtColor(full[97:130, 976:1010], cv2.COLOR_BGR2HSV)
    return float(((hsv[:, :, 0] >= 18) & (hsv[:, :, 0] <= 40) & (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 150)).mean())


def set_bomb(h, want):
    """B настоящей клавишей, пока режим бомбы не станет want (игра активна). B переключает режим туда-обратно."""
    for _ in range(3):
        if bomb_on(capture.grab(h, color=True)) == want:
            return True
        ctypes.windll.user32.keybd_event(0x42, 0x30, 0, 0)
        ctypes.windll.user32.keybd_event(0x42, 0x30, 2, 0)
        time.sleep(0.3)
    return bomb_on(capture.grab(h, color=True)) == want


def throw(h, points):
    """B (режим бомбы), отпустить; затем с зажатым Shift щёлкнуть по каждой волне — летят копии камня,
    сколько их — стоит на кнопке Throw Gem Bomb. Щелчок сообщением игра не принимает (19.10) — Shift и щелчок
    настоящие, игра должна быть видна."""
    u = ctypes.windll.user32
    kb = u.keybd_event
    was_pos, was_fg = win32gui.GetCursorPos(), win32gui.GetForegroundWindow()
    capture.real_hover(h, *EMPTY)
    try:
        win32gui.SetForegroundWindow(h)
    except Exception:  # Windows иногда не даёт фокус — пробуем щелчком по пустому месту
        capture.real_click(h, *EMPTY)
        u.SetCursorPos(*win32gui.ClientToScreen(h, EMPTY))
    time.sleep(0.2)
    if not set_bomb(h, True):
        raise RuntimeError("режим бомбы не включился")
    kb(0x10, 0x2A, 0, 0)  # Shift вниз
    try:
        for _ in range(10):  # Иван: бросать, только когда кнопка жёлтая
            time.sleep(0.1)
            yel = bomb_yellow(capture.grab(h, color=True))
            if yel > YELLOW:
                break
        else:
            raise RuntimeError(f"кнопка бомбы не пожелтела (жёлтого {yel:.2f})")
        for x, y in points:  # Shift не отпускаем между щелчками — Иван: так можно несколько подряд
            u.SetCursorPos(*win32gui.ClientToScreen(h, (x, y)))
            time.sleep(0.1)
            u.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN в текущей точке
            u.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            time.sleep(PAUSE)
    finally:
        kb(0x10, 0x2A, 2, 0)  # Shift вверх — даже при ошибке, иначе залипнет
        time.sleep(0.2)
        set_bomb(h, False)  # режим остаётся после бросков — выключить, иначе следующее B его выключит
        u.SetCursorPos(*was_pos)
        if was_fg and was_fg != h:
            try:
                win32gui.SetForegroundWindow(was_fg)
            except Exception:
                pass


def enrage_all():
    """Длинным волнам на экране — добросить до BOMBS бомб; брошенное читается из подсказки («N gem bombs dropped»).
    Все щелчки — одним зажатием Shift (Иван: шифтом бросаем во все волны)."""
    h = capture.find_window(json.load(open(HERE / "digit_grab.json", encoding="utf-8"))["title"])
    plan = []
    for top, bot, (x, y), kind, light in wave_blocks(capture.grab(h, color=True)):
        if kind != "длинный":
            continue
        k, row = wave_info(h, x, y)
        if not row:
            print(y, "подсказка не прочиталась — пропуск")
            continue
        n = -(-(BOMBS - row["Бомб"]) // PER)  # бросков осталось, вверх
        if n > 0:
            plan.append((k, x, y, n))
    capture.hover(h, *EMPTY)
    if not plan:
        return
    throw(h, [(x, y) for k, x, y, n in plan for _ in range(n)])
    time.sleep(0.5)
    for k, x, y, n in plan:
        _, row = wave_info(h, x, y)
        print(f"волна {k} +{n} бросков, бомб:", row and row["Бомб"], flush=True)
    capture.hover(h, *EMPTY)


def main():
    h = capture.find_window(json.load(open(HERE / "digit_grab.json", encoding="utf-8"))["title"])
    for top, bot, (x, y), kind, light in wave_blocks(capture.grab(h, color=True)):
        if light:
            k, row = wave_info(h, x, y)
            print(y, kind, k, row and {c: row.get(c) for c in ("Количество", "Здоровье", "Броня", "Мана за убийство")})
    capture.hover(h, *EMPTY)


if __name__ == "__main__":
    main()
