# Analisi dati storici per l'algoritmo stagionale (estate/inverno)

_Analisi del 2026-05-29, propedeutica al perfezionamento del rule engine._

## 1. Disponibilità dei dati storici — verdetto

| Fonte | Storico disponibile? | Cosa contiene |
|-------|----------------------|---------------|
| **IKEA Dirigera / VINDSTYRKA** | ❌ **NO** | L'API locale espone solo lo **stato corrente**. Nessun endpoint di history/statistiche. Né il sensore né l'hub conservano lo storico annuale accessibile in locale. |
| **Il nostro SQLite** | ⏳ Da zero | `sensor_readings` accumula da ora in avanti. Oggi quasi vuoto. |
| **Panasonic Comfort Cloud** | ✅ **SÌ, ricco** | `history()` restituisce dati **orari** (mode Day) e **giornalieri** (mode Month) da **luglio 2025**: temp interna, **temp esterna**, setpoint, raffrescamento/riscaldamento attivo, kWh e costo. |

**Conclusione**: la premessa "il sensore deve contenere lo storico annuale" non è
realizzabile col sensore IKEA — ma **non serve**. I condizionatori Panasonic
hanno **già ~11 mesi di storico** (un intero ciclo stagionale), perfino migliore
perché include la **temperatura esterna** e il comportamento reale cool/heat.

## 2. Dati estratti — Camerina (Stanza da letto, l'unica stanza automatizzabile)

| Mese | T. esterna | T. interna | cool (gg) | heat (gg) | kWh |
|------|-----------|-----------|-----------|-----------|-----|
| 2025-07 | 24.7°C | 23.0 | 8 | 0 | 36.0 |
| 2025-08 | 26.1°C | 24.2 | 31 | 0 | 137.2 |
| 2025-09 | 22.2°C | 25.0 | 7 | 0 | 25.6 |
| 2025-10 | 17.7°C | 22.2 | 0 | 3 | 8.5 |
| 2025-11 | 13.4°C | 22.6 | 0 | 25 | 143.7 |
| 2025-12 | 10.8°C | 21.7 | 0 | 21 | 94.8 |
| 2026-01 | 9.5°C | 21.8 | 0 | 23 | 104.4 |
| 2026-02 | 12.1°C | 21.2 | 0 | 16 | 59.5 |
| 2026-03 | 13.4°C | 22.9 | 0 | 21 | 94.9 |
| 2026-04 | 16.7°C | 22.2 | 1 | 5 | 29.3 |
| 2026-05 | 19.8°C | 22.0 | 6 | 0 | 22.4 |

(Camera principale e Salotto seguono lo stesso pattern stagionale.)

## 3. Pattern emersi

1. **La temperatura ESTERNA è il discriminante di stagione**, non quella interna.
   La T. interna resta stabile ~21–25°C tutto l'anno (proprio perché l'AC la
   mantiene), quindi da sola **non dice** se serve caldo o freddo. La T. esterna
   invece separa nettamente le stagioni.

2. **Crossover netto cool↔heat sulla temperatura esterna**:
   - T. esterna **≥ ~20°C** → **raffrescamento** (mag, lug, ago, set)
   - T. esterna **≤ ~18°C** → **riscaldamento** (ott→apr)
   - **18–20°C** → fascia morta: uso minimo (ottobre = 8.5 kWh, AC quasi sempre spento)

3. **Stagioni reali** (questa casa, clima Nord Italia):
   - **Raffrescamento**: maggio→settembre
   - **Riscaldamento**: novembre→marzo
   - **Mezza stagione** (ottobre, aprile): uso quasi nullo

4. **Rischio dell'attuale algoritmo statico**: le regole odierne reagiscono solo
   alla T. interna. In una sera di novembre con interni a 26°C il rule engine
   accenderebbe il **Cool** (sbagliato: è stagione di riscaldamento). Serve un
   blocco di modalità basato sulla stagione.

5. **Umidità**: presente solo dal VINDSTYRKA (Stanza da letto); Panasonic non la
   storicizza. La logica `Dry` resta legata al sensore IKEA per quella stanza.

## 4. Algoritmo stagionale proposto

Idea chiave: **separare la decisione di MODALITÀ (caldo/freddo/off) — guidata
dalla stagione/temperatura esterna — dalla decisione di SETPOINT/on-off — guidata
dalle condizioni interne.**

```
1. STAGIONE da temperatura esterna (media mobile 24-48h, da Panasonic live):
     T_est_media > soglia_cool (21°C)  -> stagione RAFFRESCAMENTO
     T_est_media < soglia_heat (16°C)  -> stagione RISCALDAMENTO
     in mezzo                          -> MEZZA STAGIONE (deadband: AC off)
   (con isteresi sulle soglie per non oscillare giorno per giorno)

2. Filtro di modalità sulle regole:
     - in RAFFRESCAMENTO: ammesse solo azioni Cool / Dry (mai Heat)
     - in RISCALDAMENTO : ammesse solo azioni Heat        (mai Cool)
     - in MEZZA STAGIONE: AC spento salvo limiti di sicurezza (T int <16 o >29)

3. Dentro la stagione, le regole interne (T/umidità) decidono on/off e setpoint,
   come oggi, ma non possono più scegliere la modalità sbagliata.

4. Restano: cooldown, isteresi, spegnimento forzato 03:00, override manuale.
   In più: override manuale di stagione (forza estate/inverno/auto).
```

### Soglie iniziali consigliate (dai dati)

| Parametro | Valore | Motivazione |
|-----------|--------|-------------|
| `season.cooling_outdoor_threshold` | 21°C | sopra i 20°C c'è sempre stato solo cooling |
| `season.heating_outdoor_threshold` | 16°C | sotto i ~18°C c'è sempre stato solo heating |
| `season.outdoor_avg_window_hours` | 24 | media mobile per smorzare il rumore giornaliero |
| limiti di sicurezza mezza stagione | T<16 → heat, T>29 → cool | comfort estremo anche fuori stagione |

La temperatura esterna live è già disponibile (`outside_temperature` dal
device Panasonic); la media mobile si può calcolare dallo storico `history(Day)`
oppure accumulando le letture nel nostro SQLite.

## 5. Possibili estensioni (con i dati a disposizione)

- **Ottimizzazione costi/energia**: i dati `consumption`/`cost` permettono di
  stimare il risparmio e, in futuro, preferire `Dry`/setpoint più alti nelle ore
  costose.
- **Setpoint appresi**: i setpoint medi reali (`averageSettingTemp`) mostrano le
  preferenze della famiglia (~24°C estate) e possono diventare i default.
- **Backfill storico**: importare lo storico Panasonic nel nostro SQLite per
  avere grafici annuali in dashboard fin da subito.

---

# 6. Analisi dei CONSUMI e modello comfort-band (2026-05-29)

Estratti i dati ORARI (history mode Day) di Camerina su settimane campione.

## Scoperte sui consumi

1. **Lo standby è gratis, il costo è il compressore.** Ora idle = 0.009 kWh/h in
   tutte le stagioni; ora con compressore attivo = 0.28–0.54 kWh/h (30–60×).
   → Lo spegnimento automatico NON fa risparmiare di per sé: serve per comfort
   (non raffreddare oltre il necessario). Il risparmio vero è il SETPOINT.

2. **Il setpoint è il driver principale.** Estate maggio '26 (ore attive):
   setpoint 20°C = 0.296 kWh/h, setpoint 24–25°C = 0.08–0.11 kWh/h.
   → Raffreddare a 20°C invece di 24°C consuma ~3×.

3. **Il consumo cresce col divario |T.esterna − setpoint|.** Estate ago '25:
   0.28 kWh/h (gap 1°C) → 0.64 kWh/h (gap 5–6°C). Stessa fisica d'inverno.

4. Le vecchie `rules` spingevano setpoint bassi (22°C) = i più costosi.

## Decisione: modello COMFORT-BAND (non a fasce di gravità)

I consumi indicano di **raffreddare/riscaldare il minimo per stare in comfort,
poi spegnere** (isteresi), con setpoint efficiente. Implementato in
`core/rule_engine.py` (`comfort_decision`) + config `comfort` per stanza.

**Valori scelti dall'utente (Stanza da letto):**

| | Estate | Inverno |
|--|--------|---------|
| Target comfort | 24.5°C | 21.5°C |
| Accende | T > 25.5°C | T < 20.5°C |
| Spegne (auto) | T < 23.5°C | T > 22.5°C |
| Setpoint efficiente | 24°C | 21°C |
| Boost | T≥29°C → 22°C | T≤17°C → 23°C |
| Dry | umidità > 70% | — |

Stima risparmio: ~12 €/mese per unità solo dalla scelta del setpoint (a comfort
accettabile), a ~0.32 €/kWh.

## Nota
Le `rules` legacy restano in config ma sono **ignorate** quando `comfort` è
presente. La dashboard al momento edita ancora le `rules`: da aggiornare per
editare il comfort-band (follow-up).
