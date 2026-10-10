# START_MODULE_CONTRACT
#   PURPOSE: Окно игры и ввод в него: найти окно, снять кадр (PrintWindow), нажать клавишу/пробел, навести курсор, щёлкнуть.
#   SCOPE: Только WinAPI-обёртки. Никаких решений «когда жать» — их принимает M-PAUSE-POLICY (пробел) и M-CHAIN (шаги цепочки).
#   DEPENDS: none
#   LINKS: M-PAUSE-POLICY, M-CHAIN, M-PANEL, V-M-CAPTURE
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   find_window - окно по точному заголовку (Excel «gemcraft (version 1).xlsb» не должен подходить)
#   grab - кадр окна в память (окно можно перекрыть; свёрнутое — нельзя), целиком или участок
#   press_space - пробел в игру: активна — как клавиша, нет — сообщением в окно
#   press_key - клавиша сообщением в окно (VK-код; у «.» свой код 0xBE)
#   hover / click - курсор и щелчок сообщением, без движения настоящей мыши
#   real_hover / real_click - настоящий курсор и щелчок (клики сообщением игра не принимает)
#   click - щелчок сообщением в точку окна
#   real_click - щелчок настоящим курсором
# END_MODULE_MAP
import ctypes, sys

import cv2, numpy as np, win32gui, win32ui

ctypes.windll.user32.SetProcessDPIAware()


def find_window(part):
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.IsWindowVisible(h)
                         and part.lower() in win32gui.GetWindowText(h).lower() else None, None)
    if not found:
        sys.exit(f"Окно с '{part}' в заголовке не найдено")
    # точный заголовок важнее: «gemcraft (version 1).xlsb» в Excel тоже содержит «GemCraft»
    exact = [h for h in found if win32gui.GetWindowText(h).lower() == part.lower()]
    if len(found) > 1 and not exact:
        sys.exit("Под '" + part + "' подходит несколько окон, укажите заголовок точно: "
                 + "; ".join(win32gui.GetWindowText(h) for h in found))
    return (exact or found)[0]


def grab(hwnd, roi=None, color=False, screen=False):
    # PrintWindow: окно рисует себя в память, поэтому его можно перекрыть другими окнами; свёрнутое — нельзя.
    # screen=True — копия с экрана (BitBlt): 54 мс против 5 с у PrintWindow на тяжёлой игре (11.10) и игру не грузит,
    # но окно игры ничто не должно перекрывать
    W, H = win32gui.GetClientRect(hwnd)[2:]
    hdc = win32gui.GetDC(0) if screen else win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, W, H)
    mem.SelectObject(bmp)
    try:
        if screen:
            mem.BitBlt((0, 0), (W, H), src, win32gui.ClientToScreen(hwnd, (0, 0)), 0x00CC0020)  # SRCCOPY
        else:
            ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 3)  # 1 = только клиентская часть, 2 = DirectX/AIR
        img = np.frombuffer(bmp.GetBitmapBits(True), np.uint8).reshape(H, W, 4)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(0 if screen else hwnd, hdc)
    x, y, w, h = roi or (0, 0, W, H)
    return cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGRA2BGR if color else cv2.COLOR_BGRA2GRAY)


def press_space(hwnd):
    """Пробел в окно игры: активно — как нажатие пользователя; нет — сообщением в окно (AIR может проигнорировать)."""
    if win32gui.GetForegroundWindow() == hwnd:
        ctypes.windll.user32.keybd_event(0x20, 0x39, 0, 0)
        ctypes.windll.user32.keybd_event(0x20, 0x39, 2, 0)  # 2 = KEYEVENTF_KEYUP
    else:
        win32gui.PostMessage(hwnd, 0x0100, 0x20, 0x00390001)  # WM_KEYDOWN, VK_SPACE
        win32gui.PostMessage(hwnd, 0x0101, 0x20, 0xC0390001)  # WM_KEYUP


def press_key(hwnd, vk):
    """Клавиша в окно игры сообщением (так же, как пробел, — работает и под другим окном)."""
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    win32gui.PostMessage(hwnd, 0x0100, vk, 1 | scan << 16)            # WM_KEYDOWN
    win32gui.PostMessage(hwnd, 0x0101, vk, 1 | scan << 16 | 0xC0 << 24)  # WM_KEYUP


def hover(hwnd, x, y):
    """Навести мышь на точку окна (координаты клиентской части) сообщением, без движения настоящей мыши."""
    win32gui.PostMessage(hwnd, 0x0200, 0, (y << 16) | x)  # WM_MOUSEMOVE


def real_hover(hwnd, x, y):
    """Настоящий курсор на точку окна игры (координаты клиентской части -> экран)."""
    ctypes.windll.user32.SetCursorPos(*win32gui.ClientToScreen(hwnd, (x, y)))


def real_click(hwnd, x, y):
    """Настоящий щелчок: курсор на точку и левая кнопка. Щёлкает то, что на экране сверху — игра должна быть видна."""
    real_hover(hwnd, x, y)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP


def click(hwnd, x, y):
    """Щелчок в точку окна сообщением (с наведением, чтобы игра знала, где курсор)."""
    pos = (y << 16) | x
    win32gui.PostMessage(hwnd, 0x0200, 0, pos)  # WM_MOUSEMOVE
    win32gui.PostMessage(hwnd, 0x0201, 1, pos)  # WM_LBUTTONDOWN
    win32gui.PostMessage(hwnd, 0x0202, 0, pos)  # WM_LBUTTONUP
