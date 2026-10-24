# Модель роста маны в GemCraft CS по циклам (Иван, 24.10.2026).
# Цикл ~6 волн: мана копится -> вся идёт в новый мана-камень -> он даёт ману в следующем цикле.
#
#   M[n+1] = K * N * C(M[n])^g * BB          (C - цена камня, ~= M[n])
#   g    = growth спека: сила ∝ цена^g (таблица Ивана: 0.625..0.647, растёт с ценой)
#   BB   = чёрный (bloodbound): base * hitMult * k^grade, k = 1.09..1.2 (форум Steam 617335934128518613);
#          grade ~ log2(цены) => k^grade = цена^log2(k): чёрный добавляет к g ещё 0.12..0.26
#   hitMult растёт с уровнем попаданий (~квадрат hit level, hit level ~ лог числа попаданий) — медленно, не учтён
#   N    = монстров на поле ∝ 1 / (доля времени в движении) = 1 / (1 - заморозка/заряд)
#
# В логарифмах: m[n+1] = log(K*N*BB) + g_eff*m[n]. Множитель N за n циклов даёт N^(1+g+...+g^(n-1)),
# в пределе N^(1/(1-g_eff)). При g_eff >= 1 рост без предела: N^n.
import math, sys


def monsters_gain(freeze, charge, bonus):
    """Во сколько раз больше монстров на поле при бонусе к длительности заморозки."""
    move = lambda d: 1 - min(d, charge) / charge
    return move(freeze) / move(freeze * (1 + bonus))


def total_gain(n_gain, g_eff, cycles):
    """Итоговый множитель маны после cycles циклов."""
    return n_gain ** sum(g_eff ** k for k in range(cycles))


if __name__ == "__main__":
    freeze, charge, bonus = 12.3, 28.0, 0.07  # секунды и +7% из фрагмента i40
    if len(sys.argv) == 4:
        freeze, charge, bonus = map(float, sys.argv[1:])
    n = monsters_gain(freeze, charge, bonus)
    print(f"монстров на поле: x{n:.4f}")
    print("g_eff  " + "  ".join(f"{c:>4} цикл" for c in (10, 50, 100)) + "   предел")
    for g in (0.645, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0):
        lim = n ** (1 / (1 - g)) if g < 1 else math.inf
        print(f"{g:<6} " + "  ".join(f"x{total_gain(n, g, c):>7.2f}" for c in (10, 50, 100)) + f"   x{lim:.2f}")
    assert abs(monsters_gain(12.3, 28, 0.07) - 1.0581) < 1e-3
    assert abs(total_gain(2, 0.5, 60) - 4) < 1e-9
