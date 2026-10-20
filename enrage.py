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
SLOT = (1052, 467)  # D кладёт копию камня в правую нижнюю ячейку инвентаря (Иван, 20.10)


def _tip(h, x, y):
    capture.hover(h, *EMPTY)
    time.sleep(0.25)
    capture.hover(h, x, y)
    time.sleep(0.6)
    full = capture.grab(h, color=True)
    return full, tooltip_box(full)


def dot():
    """Клавиша «.» — в игре включает/выключает подсказки (Иван, 19.10). Игра должна быть активной."""
    ctypes.windll.user32.keybd_event(0xBE, 0x34, 0, 0)
    ctypes.windll.user32.keybd_event(0xBE, 0x34, 2, 0)
    time.sleep(0.3)


def wave_info(h, x, y):
    """Навести на блок волны и разобрать подсказку: (номер волны, {Здоровье, Бомб, ...}) или (None, None).
    Подсказки нет — нажать «.» (подсказки могли выключить) и проверить снова; не помогло — «.» назад."""
    full, b = _tip(h, x, y)
    if not b:
        dot()
        full, b = _tip(h, x, y)
        if not b:
            dot()
            return None, None
    bx, by, bw, bh = b
    lines = ocr(full[by:by + bh, bx:bx + bw])
    t, k, row = parse(lines)
    if any("Spark" in s for s in lines[:3]):  # маленький блок — искра: «Drop gem bomb to heat spark» (20.10)
        k, row = lines[0], {"spark": True}
    elif t != "wave":
        m = re.search(r"(\d+) of \d+", lines[0] if lines else "")  # «Wave» бывает прочитано криво: «W CVE 77 of 262»
        for sc in (4, 2):  # номер бывает потерян целиком: «Wave of 262» на масштабе 3, на 4 и 2 читается (волна 130, 20.10)
            if m:
                break
            alt = ocr(full[by:by + bh, bx:bx + bw], scale=sc)
            m = re.search(r"(\d+) of \d+", alt[0] if alt else "")
        if not m:
            return None, None
        k, row = int(m.group(1)), {}
    dim =ocr(cv2.convertScaleAbs(full[by:by + bh, bx:bx + bw], alpha=1.5))  # число бомб тусклое — без осветления теряется
    m = re.search(r"([\d,]+) gem bombs? dropped", " ".join(dim), re.I)
    # чистая волна — короткая подсказка (12 строк, высота 257), злая — с «Rage modifiers» (26 строк, 417)
    if not m and (bh > 320 or any("modifiers" in s.lower() or "dropped" in s.lower() for s in lines + dim)):
        return None, None  # волна уже злая, а число не прочитано: 0 по ошибке — лишние броски в 143 (20.10)
    row["Бомб"] = int(m.group(1).replace(",", "")) if m else 0
    return k, row


def focus(h):
    """Сделать игру активной: подсказки по наведению сообщением игра показывает только активной (19.10).
    Windows не всегда отдаёт фокус фоновому процессу — нажатие Alt это разрешает."""
    try:
        win32gui.SetForegroundWindow(h)
    except Exception:
        ctypes.windll.user32.keybd_event(0x12, 0x38, 0, 0)  # Alt
        ctypes.windll.user32.keybd_event(0x12, 0x38, 2, 0)
        win32gui.SetForegroundWindow(h)
    time.sleep(0.2)


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
    focus(h)
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


INV = (980, 142, 1070, 484)  # сетка инвентаря: x0, y0, x1, y1


def click(h, p):
    """Настоящий щелчок по точке окна игры."""
    u = ctypes.windll.user32
    u.SetCursorPos(*win32gui.ClientToScreen(h, tuple(p)))
    time.sleep(0.15)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.3)


def inv(h):
    x0, y0, x1, y1 = INV
    return capture.grab(h, color=True)[y0:y1, x0:x1]


def take(h, src):
    """Камень с поля в инвентарь: навести и Tab (Иван, 20.10). Возвращает точку, куда он лёг, — по разнице снимков."""
    capture.real_hover(h, *src)
    time.sleep(0.3)
    before = inv(h)
    ctypes.windll.user32.keybd_event(0x09, 0x0F, 0, 0)  # Tab
    ctypes.windll.user32.keybd_event(0x09, 0x0F, 2, 0)
    time.sleep(0.4)
    diff = cv2.absdiff(before, inv(h)).max(axis=2) > 40
    ys, xs = diff.nonzero()
    if len(xs) < 50:
        raise RuntimeError("камень с поля в инвентарь не лёг (Tab)")
    return (INV[0] + int(xs.mean()), INV[1] + int(ys.mean())), before


def put_back(h, slot, src, empty):
    """Камень из инвентаря назад: взять щелчком и щёлкнуть по пустому месту, где он лежал (Иван, 20.10).
    Проверка: ячейка снова как до Tab (empty — снимок инвентаря тогда); нет — ещё раз, потом ошибка."""
    x, y = slot[0] - INV[0], slot[1] - INV[1]
    for _ in range(2):
        click(h, slot)
        click(h, src)
        capture.real_hover(h, *EMPTY)
        time.sleep(0.3)
        if cv2.absdiff(empty[y - 8:y + 8, x - 8:x + 8], inv(h)[y - 8:y + 8, x - 8:x + 8]).mean() < 10:
            return
    raise RuntimeError(f"камень не вернулся на поле — лежит в инвентаре {slot}")


def drag(h, a, b):
    """Настоящее перетаскивание мышью из точки a в точку b окна игры."""
    u = ctypes.windll.user32
    (ax, ay), (bx, by) = (win32gui.ClientToScreen(h, p) for p in (a, b))
    u.SetCursorPos(ax, ay)
    time.sleep(0.15)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.15)
    for i in range(1, 9):
        u.SetCursorPos(ax + (bx - ax) * i // 8, ay + (by - ay) * i // 8)
        time.sleep(0.04)
    time.sleep(0.15)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.3)


def throw_copy(h, src, points):
    """Маленьким волнам — по одному камню (Иван, 20.10): навести на камень src, D — копия ложится в SLOT,
    B, перетащить копию на волну. Оригинал остаётся на месте."""
    was_pos, was_fg = win32gui.GetCursorPos(), win32gui.GetForegroundWindow()
    focus(h)
    try:
        for x, y in points:
            set_bomb(h, False)
            capture.real_hover(h, *src)
            time.sleep(0.3)
            sx, sy = SLOT
            before = capture.grab(h, color=True)[sy - 10:sy + 10, sx - 10:sx + 10]
            ctypes.windll.user32.keybd_event(0x44, 0x20, 0, 0)  # D
            ctypes.windll.user32.keybd_event(0x44, 0x20, 2, 0)
            time.sleep(0.4)
            after = capture.grab(h, color=True)[sy - 10:sy + 10, sx - 10:sx + 10]
            if cv2.absdiff(before, after).mean() < 10:
                raise RuntimeError("копия камня не появилась в правой нижней ячейке")
            if not set_bomb(h, True):
                raise RuntimeError("режим бомбы не включился")
            drag(h, SLOT, (x, y))
    finally:
        set_bomb(h, False)
        ctypes.windll.user32.SetCursorPos(*was_pos)
        if was_fg and was_fg != h:
            try:
                win32gui.SetForegroundWindow(was_fg)
            except Exception:
                pass


def enrage_all():
    """Длинным волнам на экране — добросить до BOMBS бомб; брошенное читается из подсказки («N gem bombs dropped»).
    Все щелчки — одним зажатием Shift (Иван: шифтом бросаем во все волны)."""
    cfg = json.load(open(HERE / "digit_grab.json", encoding="utf-8"))
    h = capture.find_window(cfg["title"])
    was_fg = win32gui.GetForegroundWindow()
    try:
        focus(h)
        _enrage(h, cfg.get("gems", {}))
    finally:
        if was_fg and was_fg != h:
            try:
                win32gui.SetForegroundWindow(was_fg)
            except Exception:
                pass


MAXN = 6  # бросков в одну волну за всё время (Иван, 20.10: в 143 накидано сверх меры)
THROWN_FILE = HERE / "waves" / "thrown.json"
try:
    THROWN = json.load(open(THROWN_FILE, encoding="utf-8"))
except Exception:
    THROWN = {}


def sure(h, x, y, tries=5):
    """Прочитать подсказку блока, пока два чтения не совпадут по номеру волны и числу бомб.
    Одно чтение врёт: 1476 бывало прочитано меньшим числом — лишний бросок в 128; пустое — пропуск 130 (20.10).
    Не совпали за tries чтений — (None, None): волну не трогать."""
    reads = []
    for _ in range(tries):
        k, row = wave_info(h, x, y)
        if row:
            key = (k, row["Бомб"])
            if key in reads:
                return k, row
            reads.append(key)
    print(y, "чтения не совпали:", reads, flush=True)
    return None, None


def _enrage(h, gems):
    plan, small, seen = [], [], set()
    for top, bot, (x, y), kind, light in wave_blocks(capture.grab(h, color=True)):
        k, row = sure(h, x, y)  # вид решает подсказка: у края полосы искра тоже «обрез» (Иван, 20.10)
        if not row:
            print(y, "подсказка не прочиталась — пропуск")
            continue
        if k in seen:
            continue
        seen.add(k)
        if row.get("spark"):
            if row["Бомб"] == 0:
                small.append((k, x, y, 1))
            continue
        b = row["Бомб"]
        n = min(round((BOMBS - b) / PER), MAXN - THROWN.get(str(k), 0))  # больше MAXN за всё время — никогда
        if n > 0:
            plan.append((k, x, y, n))
    capture.hover(h, *EMPTY)
    if plan:
        slot = None
        if "1" in gems:  # камень 1 лежит на поле — на время бросков в инвентарь
            focus(h)
            slot, empty = take(h, gems["1"])
        try:
            for k, x, y, n in plan:  # учёт до броска: сорвётся посреди — лишнего всё равно не будет
                THROWN[str(k)] = THROWN.get(str(k), 0) + n
            json.dump(THROWN, open(THROWN_FILE, "w", encoding="utf-8"))
            throw(h, [(x, y) for k, x, y, n in plan for _ in range(n)])
        finally:
            if slot:
                focus(h)
                put_back(h, slot, gems["1"], empty)
    if small and "61" not in gems:
        print("маленькие волны", [k for k, *_ in small], "— не задано место камня 61, пропуск")
        small = []
    if small:
        throw_copy(h, gems["61"], [(x, y) for k, x, y, n in small])
    time.sleep(0.5)
    for k, x, y, n in plan + small:
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
