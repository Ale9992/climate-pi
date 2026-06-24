"""
core/psychro.py — Psicrometria: il ponte fisico tra TEMPERATURA e UMIDITA'.

L'umidita' relativa (RH, il "60%") dipende sia dall'acqua nell'aria sia dalla
temperatura: raffreddando la stanza la RH SALE a parita' di acqua. Per questo i
modelli di temperatura e umidita' non sono indipendenti: si lavora sull'umidita'
ASSOLUTA (g/m3, l'acqua vera, che persone aggiungono e AC toglie) e si converte
in RH usando la temperatura del modello termico.

Formula di Magnus (verificata sui dati SwitchBot: 25.4°C/62% -> 14.6 g/m3).
"""

from __future__ import annotations

import math


def sat_vapor_pressure_pa(t_c: float) -> float:
    """Pressione di vapore saturo (Pa) alla temperatura t_c (Magnus)."""
    return 611.2 * math.exp(17.62 * t_c / (243.12 + t_c))


def abs_humidity(t_c: float, rh_pct: float) -> float:
    """Umidita' assoluta (g/m3) da temperatura e umidita' relativa (%)."""
    pv = (rh_pct / 100.0) * sat_vapor_pressure_pa(t_c)
    return 2.16679 * pv / (t_c + 273.15)


def rh_from_abs(t_c: float, ah_gm3: float) -> float:
    """Umidita' relativa (%) da temperatura e umidita' assoluta (g/m3)."""
    pv = ah_gm3 * (t_c + 273.15) / 2.16679
    rh = 100.0 * pv / sat_vapor_pressure_pa(t_c)
    return max(0.0, min(100.0, rh))


def dew_point(t_c: float, rh_pct: float) -> float:
    """Punto di rugiada (°C) — utile per il rischio condensa."""
    if rh_pct <= 0:
        return -100.0
    g = math.log(rh_pct / 100.0) + 17.62 * t_c / (243.12 + t_c)
    return 243.12 * g / (17.62 - g)
