"""График светлой волны: уровень (строка, где сверху начинается светлый блок) и порог паузы во времени.
Запуск: python wave_plot.py [часов=3] -> waves/wave_plot.png. Данные: waves/top.tsv, события «wave» из digit_events.tsv.
В журналах только время суток: берём хвост после последнего перехода через полночь."""
import json, sys
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

HERE = Path(__file__).parent


def clock(stamp):
    return datetime.strptime(stamp, "%H%M%S_%f")


def tail(rows):
    """Строки после последнего скачка времени назад больше чем на час (полночь), с датой: сегодня/вчера."""
    start = 0
    for i in range(1, len(rows)):
        if clock(rows[i][0]) < clock(rows[i - 1][0]) - timedelta(hours=1):
            start = i
    return rows[start:]


def main(hours=3.0):
    default = json.loads((HERE / "digit_grab.json").read_text(encoding="utf-8")).get("wave_y")
    rows = [l.rstrip("\n").split("\t") for l in open(HERE / "waves" / "top.tsv", encoding="utf-8") if l.strip()]
    rows = tail(rows)
    t = [clock(r[0]) for r in rows]
    top = [None if r[1] == "None" else int(r[1]) for r in rows]
    wy = [int(r[2]) if len(r) > 2 and r[2] != "None" else default for r in rows]
    t0 = t[-1] - timedelta(hours=hours)
    keep = [i for i in range(len(t)) if t[i] >= t0]
    t, top, wy = [t[i] for i in keep], [top[i] for i in keep], [wy[i] for i in keep]
    ev = [l.rstrip("\n").split("\t") for l in open(HERE / "digit_events.tsv", encoding="utf-8") if "\twave\t" in l]
    ev = [(clock(e[0]), int(e[2]), e[-1]) for e in tail(ev) if t and t[0] <= clock(e[0]) <= t[-1]]

    fig, ax = plt.subplots(figsize=(16, 6))
    ax.step(t, [v if v is not None else float("nan") for v in top], where="post", lw=1, label="уровень белого (строка)")
    ax.step(t, wy, where="post", color="red", lw=1.5, ls="--", label="порог (пауза, когда уровень ≤ порога)")
    for x, y, how in ev:
        ax.plot(x, y, "o", color="green" if how == "space" else "orange", ms=7)
    ax.plot([], [], "o", color="green", label="пауза отправлена")
    ax.plot([], [], "o", color="orange", label="событие без пробела")
    ax.invert_yaxis()  # меньше строка — выше на экране: волна растёт вверх
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_ylabel("строка экрана (меньше = выше)")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower left")
    fig.tight_layout()
    out = HERE / "waves" / "wave_plot.png"
    fig.savefig(out, dpi=90)
    print(out, len(t), "точек,", len(ev), "событий")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 3.0)
