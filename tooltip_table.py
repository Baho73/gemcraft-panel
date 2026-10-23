# Распознавание: картинки из tooltips/ -> D:\Python\gems\tooltips.xlsx, листы «Камни» и «Волны», внизу суммы.
# По каждой клетке (камень) и каждому номеру волны берётся последний снимок.
import difflib, re, sys
from math import isclose
from pathlib import Path

import cv2
from openpyxl import Workbook

from tooltip_ocr import ocr

HERE = Path(__file__).parent
NUM = r"(\d[\d,]*(?:\.\d+)?)(?:\s*[€eEF]\s*(\d+))?"  # 1,234.5 или 4.642 €33 (так OCR видит «ε33»; и «F28», 22.10)

GEM = {"damage": "Урон от", "range": "Дальность", "mana value": "Стоимость маны", "upgrade cost": "Цена улучшения",
       "hits": "Попадания"}
WAVE = {"hit points": "Здоровье", "armor level": "Броня", "speed": "Скорость", "mana gain per kill": "Мана за убийство",
        "banishment mana cost": "Мана за изгнание", "xp gain per kill": "Опыт за убийство"}
# заголовок волны: «Wave 302», «W CVE 405», «Wave 77 of 262» — OCR портит «Wave», в новой игре нет «of N» (23.10)
WAVE_NO = r"^\W*W\S*(?:\s+(?!of\b)\S{1,4})?\s+(\d+)(?: of \d+)?\s*$"
SUM = {"Урон от", "Урон до", "Стоимость маны", "Цена улучшения", "Высасывает маны за выстрел", "Попадания", "Убийства",
       "Выстрелов в секунду", "Количество"}


def nums(s):
    s = re.sub(r"(?<![A-Za-z])O(?![A-Za-z])", "0", s)  # «Hits: O» — ноль, прочитанный буквой
    # «202 €29» — точка потеряна: игра пишет d.ddd eN, вернуть точку после первой цифры (волна 266, 22.10)
    return [float(a[0] + "." + a[1:] if e and len(a) > 1 and not set(a) & set(".,") else a.replace(",", ""))
            * 10 ** int(e or 0) for a, e in re.findall(NUM, s)]


def label(line, known):
    """Подпись -> известное поле (OCR путает буквы: «Raugc» = range). Сначала текст до двоеточия, потом до первого
    числа: двоеточие OCR ставит и внутрь подписи («mc:nca vatuæ 1.042 E18» = mana value)."""
    heads = [line.split(":")[0]] if ":" in line else []
    heads.append(re.split(r"\d", line, 1)[0].replace(":", ""))
    for h in heads:
        if (m := difflib.get_close_matches(h.strip().lower(), known, n=1, cutoff=0.5)):
            return m[0]
        # «Hit 4.916 €40» — от подписи осталось первое слово (волна 266, 23.10)
        if len(w := h.strip().lower()) >= 3 and len(m := [k for k in known if k.split()[0] == w]) == 1:
            return m[0]
    return None


def value(line):
    """Числа после подписи: после двоеточия, а без него — с первой цифры («gai2{ kill» — не число)."""
    return nums(line.split(":", 1)[1] if ":" in line else line[re.search(r"\d", line).start():] if re.search(r"\d", line) else "")


def glue(lines):
    """OCR переносит число на свою строку («Armor IEVEI:» / «9.991 €73») — приклеить к подписи."""
    out = []
    for l in lines:
        if out and re.fullmatch(r"[\d,.\s€eEF+]+", l) and out[-1].rstrip().endswith(":"):
            out[-1] += " " + l
        else:
            out.append(l)
    return out


def parse(lines):
    lines = glue(lines)
    for i in (1, 2):  # слово из нижней строки встало первым: «Drops», «Wave 415» (23.10)
        if len(lines) > i and not re.search(WAVE_NO, lines[0]) and re.search(WAVE_NO, lines[i]):
            lines = lines[i:]
            break
    head = lines[0] if lines else ""
    if (m := re.search(WAVE_NO, head)):
        mob = re.match(r"\s*(\d[\d,]*)\s+(.*)", lines[1]) if len(lines) > 1 else None  # «999 Reavers»
        row = {"Волна": int(m[1]), "Количество": int(mob[1].replace(",", "")) if mob else None,
               "Монстры": mob[2] if mob else (lines[1] if len(lines) > 1 else "")}
        for l in lines[2:]:
            k, v = label(l, WAVE), value(l)
            if k == "xp gain per kill" and "xp" not in l.lower():  # OCR потерял «Mana»: «gaiN per kill: 326»
                k = "mana gain per kill"
            if k and v and WAVE[k] not in row:
                row[WAVE[k]] = v[0]
        return "wave", row["Волна"], row
    if re.search(r"grade\s+\d+", head, re.I):
        row = {"Уровень": nums(head)[0] if nums(head) else None, "Тип": lines[1] if len(lines) > 1 else ""}
        for l in lines[2:]:
            low, v = l.lower(), value(l)
            k = label(l, GEM)
            if k == "damage" and len(v) >= 2:
                row["Урон от"], row["Урон до"] = v[0], v[1]
            elif k == "hits" and len(v) >= 2:
                row["Попадания"], row["Убийства"] = v[0], v[1]
            elif k and v:
                row[GEM[k]] = v[0]
            elif "per sec" in low or "shots" in low:
                row["Выстрелов в секунду"] = v[0] if v else None
            elif "per hit" in low and v:
                row["Высасывает маны за выстрел"] = v[0]
            elif "amplifier" in low:
                row["Где"] = "усилитель"
            elif "trap" in low:
                row["Где"] = "ловушка"
        row.setdefault("Где", "башня/прочее")
        return "gem", None, row
    return None, None, None


ORDER = ["Клетка", "Уровень", "Тип", "Где", "Урон от", "Урон до", "Дальность", "Выстрелов в секунду",
         "Высасывает маны за выстрел", "Попадания", "Убийства", "Стоимость маны", "Цена улучшения",
         "Волна", "Количество", "Монстры", *WAVE.values(), "Файл"]


def write(ws, rows, first):
    have = {k for r in rows for k in r}
    cols = [c for c in ORDER if c in have]
    ws.append(cols)
    for r in rows:
        ws.append([r.get(c) for c in cols])
    n = len(rows) + 1
    ws.append(["Сумма"] + [f"=SUM({ws.cell(2, i).column_letter}2:{ws.cell(2, i).column_letter}{n})" if c in SUM else None
                           for i, c in enumerate(cols[1:], 2)])
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    for c in ws.columns:
        ws.column_dimensions[c[0].column_letter].width = 16


def main(folder=HERE / "tooltips", out=HERE / "tooltips.xlsx"):
    gems, waves = {}, {}
    for p in sorted(Path(folder).glob("*.png")):  # по имени = по времени: поздний снимок перезаписывает ранний
        kind, key, row = parse(ocr(cv2.imread(str(p))))
        cx, cy = p.stem.split("_")[1:3]
        if kind == "gem":
            gems[(cx, cy)] = {"Клетка": f"{cx},{cy}", **row, "Файл": p.name}
        elif kind == "wave":
            waves[key] = {**row, "Файл": p.name}
        else:
            print("не понял:", p.name)
    wb = Workbook()
    write(wb.active, list(gems.values()), "Клетка")
    wb.active.title = "Камни"
    write(wb.create_sheet("Волны"), [waves[k] for k in sorted(waves)], "Волна")
    wb.save(out)
    print(f"камней {len(gems)}, волн {len(waves)} -> {out}")


def self_check():
    t, _, r = parse(["Grade 36 GEM", "Pure oraxge", "IN amplifier: ccmmot shoot", "Damage: 17,991,198,616-153,321,326,029",
                     "Raugc: 48", "48 shots per sccomd", "LEEChES 321,443,956.05 maxa per hit", "Hits: O, Kilts: O",
                     "Irlcnta value: 1.988 €14", "Upgrade cost: 1.988 E14"])
    assert t == "gem" and r["Урон до"] == 153321326029 and r["Дальность"] == 48 and r["Попадания"] == 0
    assert r["Цена улучшения"] == 1.988e14 and r["Высасывает маны за выстрел"] == 321443956.05 and r["Где"] == "усилитель"
    t, k, r = parse(["Wave 11 of 67", "999 Giamts", "Hit poiNts: 1.751 €35", "Armor IEVEI: 1.772 €74", "SPEEd: 0.33",
                     "Mama gaix pa kill: 1,032", "BamishMENt maxa cost: 588", "XP gai2{ kill: 4,396.6"])
    assert t == "wave" and k == 11 and isclose(r["Здоровье"], 1.751e35) and r["Мана за убийство"] == 1032
    _, _, r10 = parse(["Wave 10 of 67", "999 Reavers", "gaiN per kill: 326", "XP gaiN per kill: 3,077.6"])
    assert r10["Мана за убийство"] == 326 and r10["Опыт за убийство"] == 3077.6
    assert r["Мана за изгнание"] == 588 and r["Опыт за убийство"] == 4396.6 and r["Скорость"] == 0.33
    assert r["Количество"] == 999 and r["Монстры"] == "Giamts"
    _, _, r = parse(["Wave 26 of 67", "999 Marked RECVErs", "Hit poixts: 1.2E36", "Armor IEVEI:", "9.991 €73"])
    assert r["Здоровье"] == 1.2e36 and isclose(r["Броня"], 9.991e73)  # число на своей строке
    _, _, g = parse(["Grade 49 Gem", "x", "mc:nca vatuæ 1.042 E18"])
    assert g["Стоимость маны"] == 1.042e18  # двоеточие внутри подписи
    _, _, r = parse(["Wave 266", "999 PossessEd Giarts", "Hit 4.916 €40", "Armor IEVEI: 367,619"])
    assert isclose(r["Здоровье"], 4.916e40) and r["Броня"] == 367619
    _, k, r = parse(["W CVE 405", "78 PossessEd Swarmliitgs", "Hit poiNts:", "1.263 e41", "Armor IEVEI: 81,434"])
    assert k == 405 and isclose(r["Здоровье"], 1.263e41) and r["Броня"] == 81434  # 23.10
    assert isclose(nums("2.319 F28")[0], 2.319e28) and isclose(nums("202 €29")[0], 2.02e29)  # 22.10


if __name__ == "__main__":
    self_check()
    main(*sys.argv[1:])
