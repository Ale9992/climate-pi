"""
core/humidity_model.py — Modello dell'umidita' della stanza (grey-box), ACCOPPIATO
al modello termico (NON indipendente).

Si modella l'umidita' ASSOLUTA W (g/m3, l'acqua nell'aria). Bilancio di massa:

    dW/dt = ACH*(W_est - W_in)            # infiltrazione (aria esterna)
          + (g_persona * n_persone)/V     # le persone aggiungono acqua (sudore!)
          - rimozione_AC / V              # l'AC condensa (Cool poco, Dry molto)

La RH si ricava da W + la TEMPERATURA del modello termico (psychro.rh_from_abs):
e' qui che i due modelli si fondono in un unico stato (temperatura, umidita').

Parametri provvisori (grey-box), da affinare su switchbot_history (che ha
abs_humidity reale). Servono a far partire il modello con numeri plausibili.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import psychro


@dataclass
class HumidityParams:
    volume_m3: float = 45.0          # volume stanza (per i guadagni specifici)
    ach: float = 0.7                 # ricambi aria/ora (= infiltrazione termica)
    moisture_person_g_h: float = 60.0    # acqua aggiunta da una persona a riposo (g/h)
    moisture_person_active_g_h: float = 120.0  # se suda (attivita'/caldo)
    ac_removal_cool_g_h: float = 250.0   # condensa in Cool (g/h)
    ac_removal_dry_g_h: float = 600.0    # condensa in Dry (g/h, molto piu' aggressivo)

    @classmethod
    def load(cls, path: str) -> "HumidityParams":
        """Carica i parametri umidita' dal blocco 'humidity' del file termico
        della stanza (volume dalla geometria se assente). Default se manca."""
        import json
        import os
        p = cls()
        if not os.path.exists(path):
            return p
        try:
            data = json.load(open(path))
            for k, v in (data.get("humidity") or {}).items():
                if hasattr(p, k):
                    setattr(p, k, float(v))
            if "humidity" not in data or "volume_m3" not in data["humidity"]:
                vol = (data.get("geometry") or {}).get("volume_m3")
                if vol:
                    p.volume_m3 = float(vol)
        except Exception:  # noqa: BLE001
            pass
        return p


class HumidityModel:
    """Simulatore dell'umidita' assoluta, accoppiato alla temperatura."""

    def __init__(self, params: HumidityParams | None = None) -> None:
        self.p = params or HumidityParams()

    def step(self, w_in: float, w_out: float, mode: str | None, on: bool,
             n_person: float, dt_s: float, sweating: bool = False) -> float:
        """W interna (g/m3) dopo dt_s, dati: W esterna, modalita' AC, presenza."""
        p = self.p
        # infiltrazione (per secondo)
        dw = (p.ach / 3600.0) * (w_out - w_in)
        # guadagno persone
        g = p.moisture_person_active_g_h if sweating else p.moisture_person_g_h
        dw += (g * n_person) / p.volume_m3 / 3600.0
        # rimozione AC
        if on and mode:
            m = mode.lower()
            if m == "dry":
                dw -= p.ac_removal_dry_g_h / p.volume_m3 / 3600.0
            elif m == "cool":
                dw -= p.ac_removal_cool_g_h / p.volume_m3 / 3600.0
        return max(0.0, w_in + dw * dt_s)

    def simulate(self, rh0: float, temp_series: list[float],
                 w_out_series: list[float], mode: str | None, on_series: list[bool],
                 n_person: float, dt_s: float = 900.0,
                 sweating: bool = False) -> list[float]:
        """Simula la RH (%) sull'orizzonte. temp_series viene dal modello termico
        (e' l'accoppiamento): la RH a ogni passo si ricava da W e dalla T."""
        w = psychro.abs_humidity(temp_series[0], rh0)
        out_rh = [rh0]
        for i, t in enumerate(temp_series[1:] if len(temp_series) > 1 else temp_series):
            w_out = w_out_series[i] if i < len(w_out_series) else w_out_series[-1]
            on = on_series[i] if i < len(on_series) else False
            w = self.step(w, w_out, mode, on, n_person, dt_s, sweating)
            out_rh.append(psychro.rh_from_abs(t, w))
        return out_rh
