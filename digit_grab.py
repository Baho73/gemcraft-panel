# START_MODULE_CONTRACT
#   PURPOSE: Точка запуска: окно-пульт (M-PANEL) и служебные команды — выбор области маны, самопроверки, пробный пробел.
#   SCOPE: Только разбор командной строки; логика — в модулях. Пробел — через M-PAUSE-POLICY.
#   DEPENDS: M-PANEL, M-PAUSE-POLICY, M-CHAIN, M-MANA-OCR, M-DETECTORS, M-CAPTURE
#   LINKS: V-M-CLI
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   select - выбрать окно и обвести область маны (digit_grab.json: title, roi)
#   test - самопроверки без окна: политика паузы, цепочка, распознавание, фильтры, детекторы
#   check - сверка распознавания с кадрами, прочитанными глазами (digit_truth.json)
# END_MODULE_MAP
"""Окно-пульт GemCraft: паузы по событиям и цепочка заморозки.

  python digit_grab.py [интервал_сек]    окно-пульт (по умолчанию 10 кадров в секунду)
  python digit_grab.py select [заголовок] выбрать окно (по умолчанию GemCraft Chasing Shadows) и обвести область маны
  python digit_grab.py test              самопроверка без окна
  python digit_grab.py check             сверка с кадрами, прочитанными глазами (digit_truth.json)
  python digit_grab.py space             через 3 с пробел в окно игры
"""
import json, sys, time

import cv2, numpy as np

# Иван 24.10: при открытом пульте тормозит терминал чата — не занимать все ядра и уступать другим окнам
cv2.setNumThreads(1)
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL

from capture import find_window, grab
from detectors import filled, wave_blocks
from mana_ocr import flat, load, load_grad, mana_text, number, read, steady, surge
from panel import CFG, HERE, loop
from pause_policy import PausePolicy


def select(part):
    hwnd = find_window(part)
    img = grab(hwnd)
    x, y, w, h = cv2.selectROI("1/2 rough area, Enter", img, showCrosshair=False)
    cv2.destroyAllWindows()
    k = 4  # шаг 2: грубый кусок крупно, чтобы точно обвести мелкий шрифт
    big = cv2.resize(img[y:y + h, x:x + w], None, fx=k, fy=k, interpolation=cv2.INTER_NEAREST)
    x2, y2, w2, h2 = cv2.selectROI("2/2 exact area x4, Enter", big, showCrosshair=False)
    cv2.destroyAllWindows()
    roi = [x + x2 // k, y + y2 // k, -(-w2 // k), -(-h2 // k)]
    cfg = json.loads(CFG.read_text(encoding="utf-8")) if CFG.exists() else {}
    cfg.update(title=part, roi=roi)  # остальное (индикаторы) не трогаем
    CFG.write_text(json.dumps(cfg), encoding="utf-8")
    print("Сохранено:", CFG)


def test():
    import pause_policy
    pause_policy.self_check()  # сценарии политики паузы (M-PAUSE-POLICY)
    import chain
    chain.self_check()  # прерванная цепочка ничего не жмёт (M-CHAIN)
    def render(s):
        img = np.zeros((40, 300), np.uint8)
        cv2.putText(img, s, (5, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, 255, 2)
        return img
    tpls = []
    for ch in "0123456789.E+-,":
        f = flat(render(ch))
        xs = np.flatnonzero(f[5:38].max(axis=0) > 60)
        tpls.append((ch, f[5:38, xs[0]:xs[-1] + 1]))
    for s in ["907", "2.5E-7", "1.5E+12", "4E9", "3,14"]:
        assert read(render(s), tpls) == s, (s, read(render(s), tpls))
    assert read(255 - render("3,14"), tpls) == "3,14"  # тёмный текст на светлом
    glued = render("29")
    glued[18:21, 23:28] = 255  # перемычка между 2 и 9 — маскам не мешает
    assert read(glued, tpls).replace("?", "") == "29", read(glued, tpls)  # сама перемычка может дать «?»
    assert read(np.zeros((40, 300), np.uint8) + 30, tpls) == ""
    edge = np.zeros((612, 1088, 3), np.uint8)
    edge[44:46, 6:32] = 255
    edge[608:612, 6:32] = 255  # разделители у самых краёв полосы — 6.10 это роняло весь опрос
    assert wave_blocks(edge) == []
    assert number("6.202e231") is None and number("6.202e23") == 6.202e23
    assert mana_text(number("1959e38")) == "1.959e38" and mana_text(1798646114.0, False) == "1798646114"
    push = steady()
    seq = [6.21e23, 6.20e23, 6.20e25, 6.19e23, 51065, 49931, 46937, 46900]
    got = [push(v) for v in seq + [46900]][1:]  # вердикт приходит на кадр позже
    assert got == [6.21e23, 6.20e23, None, 6.19e23, 51065, 49931, 46937, 46900], got
    f = filled()  # рамка мигнула на кадр — не считается; держится 2 кадра — одно событие; мигание внутри — без повтора
    assert [f(v) for v in [0, 1, 0, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0, 0, 1, 1]] == [0] * 4 + [1] + [0] * 10 + [1]
    j = surge()
    calm = [4e30] * 25  # 2.5 с покоя
    evs = [j(t / 10, n) for t, n in enumerate([5e30, 4.5e30] + calm + [2e33, 2e33, 2e33, 1e30])]
    assert [e for e in evs if e] == [(4e30, 2e33)], evs  # пауза, потом рост x500 — одно событие
    j = surge()  # 7.144 стояла, чтение сбилось на 1.144 и вернулось — паузы нет; настоящий рост — есть
    seq = [7.144e35] * 30 + [1.144e35] * 30 + [7.144e35] * 3 + [7.144e35] * 30 + [9.5e35] * 3
    evs = [e for e in (j(t / 10, n) for t, n in enumerate(seq)) if e]
    assert evs == [(7.144e35, 9.5e35)], evs
    j = surge()  # 17:24 — чтение скачет 7.744/3.744/2.744 каждые полсекунды, потом настоящий рост до 9.968: пауза есть
    seq = [7.744e38] * 30 + [3.744e38, 3.744e38, 7.744e38, 7.744e38, 2.744e38, 2.744e38] * 10 + [9.968e38] * 3
    evs = [e for e in (j(t / 10, n) for t, n in enumerate(seq)) if e]
    assert len(evs) == 1 and evs[0][1] == 9.968e38, evs
    j = surge()
    spend =[6e23 - i * 1e21 for i in range(20)]  # трата без паузы, потом ошибка e23 -> e25
    assert not any(j(t / 10, n) for t, n in enumerate(spend + [5.8e25, 5.8e25, 5.8e25]))
    assert number("6?833e12") == 6.833e12 and number("8864e12") == 8.864e12 and number("2?.092e19") == 2.092e19
    assert number("8,871e12") == 8.871e12 and number("8.86?4e12") is None and number("8.8?1e12") is None
    assert number("15.732e26") == 5.732e26 and number("9.5501e12") == 9.55e12 and number("9.5?6e12") is None and number("9.5?61e12") is None
    assert number("92,447") == 92447 and number("21.365") == 21365 and number("9.550e12") == 9.55e12 and number("1.2?") is None
    # заморозка: доля поля цвета заморозки, порог FREEZE_SAT (17.10: жёлтое поле прятало её от старой меры)
    from detectors import FREEZE_SAT, freeze_level
    assert freeze_level(cv2.imread(str(HERE / "assets" / "freeze_no.png"))) < FREEZE_SAT
    assert freeze_level(cv2.imread(str(HERE / "assets" / "freeze_yes.png"))) > FREEZE_SAT
    print("ok")


def check():
    """Прогон по кадрам, прочитанным глазами (digit_truth.json): запускать после каждой правки масок и правил."""
    truth = json.loads((HERE / "digit_truth.json").read_text(encoding="utf-8"))
    tpls, grads = [t for t in load() if t[0] != "?"], load_grad()
    wrong = [(k, v, read(cv2.imread(str(HERE / k), cv2.IMREAD_GRAYSCALE), tpls, None, grads)) for k, v in truth.items()]
    wrong = [w for w in wrong if number(w[2]) != number(w[1])]
    for w in wrong:
        print("ошибка:", *w)
    print(f"{len(truth) - len(wrong)} из {len(truth)} верно")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "select":
        select(" ".join(a[1:]) or "GemCraft Chasing Shadows")
    elif a and a[0] == "test":
        test()
    elif a and a[0] == "check":
        check()
    elif a and a[0] == "space":  # проверка: через 3 с пробел в окно игры (успейте переключиться или нет)
        time.sleep(3)
        PausePolicy.for_window(find_window(json.loads(CFG.read_text(encoding="utf-8"))["title"])).press("pause")
    else:
        loop(float(a[0]) if a else 0.1)  # 10 раз в секунду: круг ~40 мс (снимок 31, распознавание 5)
