"""
core/thermal_model.py — Modello fisico (grey-box) della stanza.

NON e' machine learning: e' l'equazione termodinamica di una stanza (modello RC),
con pochi parametri dal significato fisico, stimati da principi primi (dimensioni,
isolamento, potenza AC) e affinati sui dati reali (grey-box).

STRUTTURA (rivista 2026-06-17 sui dati reali): si e' SCOPERTO che queste stanze
NON rilassano verso l'ESTERNO ma verso l'INTERNO della casa. Prova: nelle derive
AC-off (dato SwitchBot 1-min, Camera da letto) l'asintoto della temperatura sta
in media a 1.1° dall'interno-casa contro 2.4° dall'esterno (18 segmenti su 23);
d'estate la stanza sale e si ferma a ~27° MENTRE fuori ci sono 30°+. Causa fisica:
ogni stanza ha 1 sola parete esterna e 5 superfici verso altre stanze climatizzate.

Bilancio di energia della stanza (1 nodo termico, 2 conduttanze):

    C * dT_int/dt = UA_house*(T_house - T_int)   # verso l'interno casa (DOMINANTE)
                  + UA*(T_est - T_int)           # verso l'esterno (1 parete/finestra)
                  + Q_ac + Q_interni + Q_solare

  C          [J/°C]  capacita' termica effettiva (aria + massa muraria attiva)
  UA_house   [W/°C]  conduttanza verso il resto della casa (~costante, climatizzata)
  UA         [W/°C]  conduttanza verso l'esterno (finestra+muro+infiltrazioni)
  T_house    [°C]    temperatura dell'interno casa (≈ setpoint/comfort della casa)
  Q_ac       [W]     potenza termica AC (NEGATIVA in raffreddamento)
  Q_interni  [W]     guadagni interni (elettronica + persone)

Grandezze derivate:
  tau = C/(UA_house+UA)  [s]  costante di tempo (quanto e' "lenta" la stanza)
  A regime senza AC:
     T_eq = (UA_house*T_house + UA*T_est + Q_interni) / (UA_house+UA)
  cioe' una media pesata casa/esterno (peso casa ~0.7) + spinta dei guadagni.

I default sono per la "Stanza da letto"; ogni stanza ha il suo file
config/thermal_params_<stanza>.json (vedi RoomThermalParams.load).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RoomThermalParams:
    """
    Parametri fisici di una stanza (appartamento, 2° piano, edificio in muratura).
    UA (esterno) e' da geometria; UA_house (verso casa) e C dalla risposta reale
    (tau misurato ~5h sul dato SwitchBot). C resta il piu' incerto (massa muraria).
    """
    # Capacita' termica effettiva. Con il modello a 2 conduttanze tau=C/(UA+UA_house),
    # quindi a parita' di tau misurato C e' piu' grande di prima (include la massa
    # muraria condivisa con la casa). [INCERTO: affinato sui dati]
    C: float = 1_830_000.0    # J/°C
    # Conduttanza verso l'ESTERNO: finestra(1.77*5.7=10.1) + muro(8.6) + infiltr(7.7)
    UA: float = 26.0          # W/°C  (verso esterno, 1 sola parete)
    # Conduttanza verso l'INTERNO casa (5 superfici verso stanze climatizzate).
    # Dominante: dai dati il peso casa nell'equilibrio e' ~0.7 -> UA_house ~ 2.3*UA.
    UA_house: float = 60.0    # W/°C  (verso il resto della casa)
    # Temperatura di riferimento dell'interno casa quando non se ne passa una reale.
    # E' ~il comfort della casa (estate ~25-26°, inverno ~21°). Andrebbe passata
    # dai sensori delle altre stanze; questo e' solo un fallback.
    T_house: float = 25.0     # °C
    # Elettronica sempre accesa: Mac mini 20 + stampante 8 + router 12 + PC 80.
    Q_internal: float = 120.0  # W  (fisso); +Q_person quando qualcuno e' presente
    Q_person: float = 100.0   # W  per persona presente
    # Guadagno solare dalla finestra (ovest/mare nella camerina; meno in altre stanze).
    Q_solar_peak: float = 700.0  # W  picco solare pomeridiano
    solar_start_h: int = 13
    solar_end_h: int = 20
    Q_cool_max: float = 2000.0  # W    potenza frigorifera max (CS-TZ20: ~2.0 kW)
    cop: float = 4.0          # W frigoriferi per W elettrico (EER di targa ~4.0)

    @property
    def tau_hours(self) -> float:
        """Costante di tempo termica in ore: C/(UA_house+UA)."""
        return self.C / (self.UA_house + self.UA) / 3600.0

    def t_equilibrium(self, t_out: float, t_house: float | None = None,
                      q_extra: float = 0.0) -> float:
        """Temperatura di equilibrio senza AC: media pesata casa/esterno + guadagni."""
        th = self.T_house if t_house is None else t_house
        q = self.Q_internal + q_extra
        return (self.UA_house * th + self.UA * t_out + q) / (self.UA_house + self.UA)

    def q_solar(self, hour: float) -> float:
        """Guadagno solare alla data ora (W). Profilo a campana sul pomeriggio."""
        if not (self.solar_start_h <= hour <= self.solar_end_h):
            return 0.0
        import math
        frac = (hour - self.solar_start_h) / (self.solar_end_h - self.solar_start_h)
        return self.Q_solar_peak * math.sin(math.pi * frac)

    # -- persistenza dei parametri calibrati -------------------------------
    @classmethod
    def load(cls, path: str = "config/thermal_params.json") -> "RoomThermalParams":
        """Carica i parametri calibrati se il file esiste, altrimenti i default."""
        import json
        import os
        p = cls()
        if os.path.exists(path):
            try:
                data = json.load(open(path))
                for k, v in data.items():
                    if hasattr(p, k):
                        setattr(p, k, v)
            except Exception:  # noqa: BLE001 - file corrotto: usa i default
                pass
        return p

    def save(self, path: str = "config/thermal_params.json",
             extra: dict | None = None) -> None:
        """Salva i parametri (piu' eventuali metadati di calibrazione). PRESERVA
        le chiavi extra gia' presenti nel file (es. humidity, geometry) che non
        fanno parte della dataclass, cosi' la calibrazione non le cancella."""
        import json
        import os
        from dataclasses import asdict
        data = {}
        if os.path.exists(path):
            try:
                data = json.load(open(path))
            except Exception:  # noqa: BLE001
                data = {}
        data.update(asdict(self))
        if extra:
            data.update(extra)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


class RoomThermalModel:
    """Simulatore della dinamica termica della stanza (passo di Eulero)."""

    def __init__(self, params: RoomThermalParams | None = None) -> None:
        self.p = params or RoomThermalParams()

    # -- un passo di integrazione ------------------------------------------
    def step(self, t_in: float, t_out: float, q_ac: float, dt_s: float,
             q_internal: float | None = None, t_house: float | None = None) -> float:
        """T interna dopo dt_s secondi: bilancio a 2 conduttanze (casa + esterno)."""
        p = self.p
        qi = p.Q_internal if q_internal is None else q_internal
        th = p.T_house if t_house is None else t_house
        flux = (p.UA_house * (th - t_in) + p.UA * (t_out - t_in) + q_ac + qi)
        return t_in + flux / p.C * dt_s

    # -- potenza AC da stato/setpoint --------------------------------------
    def q_ac_cooling(self, t_in: float, setpoint: float, on: bool) -> float:
        """Potenza frigorifera (W, negativa). Controllo proporzionale saturato."""
        if not on or t_in <= setpoint:
            return 0.0
        frac = min(1.0, (t_in - setpoint) / 2.0)
        return -self.p.Q_cool_max * frac

    # -- simulazione su un orizzonte ---------------------------------------
    def simulate(self, t_in0: float, t_out_series: list[float],
                 ac_on_series: list[bool], setpoint: float,
                 start_hour: float = 12.0, person_present: bool = True,
                 dt_s: float = 900.0,
                 t_house_series: list[float] | None = None) -> list[float]:
        """
        Simula la T interna su un orizzonte. t_out_series e ac_on_series hanno un
        valore per passo; t_house_series (opzionale) l'interno casa per passo
        (default: T_house dei parametri). Ritorna la T interna a ogni passo.
        """
        t = t_in0
        out = [t]
        p = self.p
        sub = 60.0  # sub-integrazione a 60s per stabilita'
        hour = start_hour
        for i, (t_out, on) in enumerate(zip(t_out_series, ac_on_series)):
            th = (t_house_series[i] if t_house_series is not None else p.T_house)
            elapsed = 0.0
            while elapsed < dt_s:
                step_dt = min(sub, dt_s - elapsed)
                qi = p.Q_internal + (p.Q_person if person_present else 0.0) \
                    + p.q_solar(hour % 24)
                q = self.q_ac_cooling(t, setpoint, on)
                t = self.step(t, t_out, q, step_dt, q_internal=qi, t_house=th)
                elapsed += step_dt
                hour += step_dt / 3600.0
            out.append(t)
        return out

    def electrical_kwh(self, q_ac_w: float, dt_s: float) -> float:
        """Energia elettrica consumata per erogare q_ac frigoriferi (kWh)."""
        return abs(q_ac_w) / self.p.cop * dt_s / 3.6e6


# ===========================================================================
# Demo
# ===========================================================================
def _demo() -> None:
    m = RoomThermalModel()
    p = m.p
    print("MODELLO (stanza<->interno casa + esterno) — Stanza da letto:")
    print(f"  C={p.C/1000:.0f} kJ/°C  UA_casa={p.UA_house}  UA_est={p.UA}  "
          f"tau={p.tau_hours:.1f} h")
    w = p.UA_house / (p.UA_house + p.UA)
    print(f"  peso casa nell'equilibrio: {w:.0%}  (esterno {1-w:.0%})")
    print(f"  estate, casa 26°, fuori 32°, AC spento -> equilibrio "
          f"{p.t_equilibrium(32.0, 26.0, p.Q_person):.1f}°C "
          f"(NON insegue i 32° esterni)")

    dt = 900.0
    steps = 16  # 4 ore
    print("\nDeriva AC-off (casa 26°, fuori 31°, parto da 24°):")
    traj = m.simulate(24.0, [31.0]*steps, [False]*steps, setpoint=25,
                      start_hour=2.0, t_house_series=[26.0]*steps)
    for i in range(0, steps + 1, 4):
        print(f"   +{i*15:>3} min: {traj[i]:.1f}°C")


if __name__ == "__main__":
    _demo()
