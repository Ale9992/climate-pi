import { useState, useEffect, useRef } from 'react'
import Icon from '@mdi/react'
import {
  mdiSnowflake, mdiFire, mdiWaterPercent, mdiFan, mdiFanAuto,
  mdiArrowOscillating, mdiHomeThermometer, mdiThermometer,
  mdiLightningBolt, mdiWeatherNight, mdiPower,
  mdiChevronUp, mdiChevronDown, mdiChevronLeft, mdiChevronRight,
} from '@mdi/js'
import { api } from '../api.js'

// Modalità AC con icona e se usano il setpoint di temperatura.
const MODES = [
  { key: 'Cool', label: 'Raffresca', temp: true },
  { key: 'Heat', label: 'Riscalda', temp: true },
  { key: 'Dry', label: 'Deumidifica', temp: true },
  { key: 'Fan', label: 'Ventola', temp: false },
  { key: 'Auto', label: 'Auto', temp: true },
]
const FANS = ['Auto', 'Low', 'LowMid', 'Mid', 'HighMid', 'High']
const FAN_LABEL = { Auto: 'Auto', Low: 'Bassa', LowMid: 'Medio-bassa', Mid: 'Media', HighMid: 'Medio-alta', High: 'Alta' }
// Livelli manuali (esclusa Auto), dal più basso al più alto: 5 tacche.
const FAN_LEVELS = ['Low', 'LowMid', 'Mid', 'HighMid', 'High']
// Posizioni alette swing verticale, dall'alto al basso (escluse Auto/Swing).
const SWING_POS = ['Up', 'UpMid', 'Mid', 'DownMid', 'Down']
const TEMP_MIN = 16
const TEMP_MAX = 30

// Colore tematico per modalità (allineato all'app Panasonic).
const MODE_ACCENT = {
  Auto: '#8ed24a', Heat: '#e8893f', Cool: '#4ab4d8', Dry: '#7fd6cf', Fan: '#8ed24a',
}

// ---- Icone: Material Design Icons (@mdi/js, Apache-2.0, offline) ----
// Wrapper che accetta width/height come gli SVG precedenti (compat. col resto).
const mk = (path) => ({ width = 24, height = 24, className } = {}) =>
  <Icon path={path} size={`${(Number(width) || 24) / 16}rem`} className={className} />
const Ic = {
  inside: mk(mdiHomeThermometer),
  outside: mk(mdiThermometer),
  cool: mk(mdiSnowflake),
  heat: mk(mdiFire),
  dry: mk(mdiWaterPercent),
  fan: mk(mdiFan),
  auto: mk(mdiFanAuto),
  swing: mk(mdiArrowOscillating),
  power: mk(mdiPower),
  powerful: mk(mdiLightningBolt),
  quiet: mk(mdiWeatherNight),
  chevUp: mk(mdiChevronUp),
  chevDown: mk(mdiChevronDown),
  chevLeft: mk(mdiChevronLeft),
  chevRight: mk(mdiChevronRight),
}
const modeIcon = { Cool: Ic.cool, Heat: Ic.heat, Dry: Ic.dry, Fan: Ic.fan, Auto: Ic.auto }

export default function Thermostat({ room, onAction }) {
  const ac = room.ac
  const reachable = ac && ac.reachable

  // Stato locale "ottimistico": riflette i tocchi subito, poi il server conferma.
  const [draft, setDraft] = useState(null)
  const [panel, setPanel] = useState(null) // 'mode' | 'fan' | null
  const [busy, setBusy] = useState(false)
  const sendTimer = useRef(null)

  // Sincronizza il draft con lo stato reale quando arriva (e non stiamo editando).
  useEffect(() => {
    if (!reachable) return
    if (draft && busy) return
    setDraft({
      power: ac.power === 'On',
      mode: ac.mode || 'Cool',
      temperature: ac.target_temperature ?? 24,
      fan_speed: ac.fan_speed || 'Auto',
      nanoe: ac.nanoe,
      swing_vertical: ac.swing_vertical,
      eco_mode: ac.eco_mode || 'Auto',
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ac?.power, ac?.mode, ac?.target_temperature, ac?.fan_speed, ac?.nanoe, ac?.swing_vertical, ac?.eco_mode, reachable])

  if (!reachable) {
    return <div className="thermo thermo-off">
      <p className="thermo-unreach">Condizionatore non raggiungibile</p>
      <p className="muted small">Ricontrollo automatico in corso…</p>
    </div>
  }
  if (!draft) return <div className="thermo"><p className="muted">Caricamento…</p></div>

  const accent = draft.power ? (MODE_ACCENT[draft.mode] || '#7d97b8') : '#aab4bf'
  const modeMeta = MODES.find((m) => m.key === draft.mode) || MODES[0]

  // Invia il comando (con debounce per +/- ripetuti).
  const send = (patch, immediate = false) => {
    const next = { ...draft, ...patch }
    setDraft(next)
    setBusy(true)
    clearTimeout(sendTimer.current)
    const fire = async () => {
      try {
        await api.control(room.name, {
          power: next.power, mode: next.mode,
          temperature: modeMeta.temp ? next.temperature : null,
          fan_speed: next.fan_speed,
          nanoe: next.nanoe === null ? null : next.nanoe,
          swing_vertical: next.swing_vertical || null,
          eco_mode: next.eco_mode || null,
        })
        onAction && onAction()
      } catch (e) {
        // ripristina dallo stato reale al prossimo refresh
        onAction && onAction()
      } finally { setBusy(false) }
    }
    sendTimer.current = setTimeout(fire, immediate ? 0 : 600)
  }

  const stepTemp = (d) => {
    const t = Math.min(TEMP_MAX, Math.max(TEMP_MIN, (draft.temperature ?? 24) + d))
    send({ temperature: t })
  }
  const onSlider = (e) => send({ temperature: Number(e.target.value) })
  const togglePower = () => send({ power: !draft.power }, true)
  const pickMode = (m) => { send({ mode: m }, true); setPanel(null) }
  // La modale ventola resta aperta: frecce e toggle Auto cambiano solo il valore.
  // Debounce (non immediate): scorrendo veloce coalesce, invia solo il livello finale.
  const setFan = (f) => send({ fan_speed: f })
  // Swing: la modale resta aperta. Le frecce muovono la posizione (debounced),
  // i toggle Auto/Swing applicano subito.
  const setSwing = (v, immediate = false) => send({ swing_vertical: v }, immediate)
  const toggleNanoe = () => { if (draft.nanoe !== null) send({ nanoe: !draft.nanoe }, true) }
  // Eco mode esclusivo: tocco su Powerful/Quiet attivo -> torna ad Auto.
  const pickEco = (e) => send({ eco_mode: draft.eco_mode === e ? 'Auto' : e }, true)

  const dim = !draft.power
  const ModeIcon = modeIcon[draft.mode] || Ic.cool

  return (
    <div className="thermo" style={{ '--accent': accent }}>
      {/* Letture ambiente: due "pillole" in vetro + nanoe.
          La temperatura "interna" viene dal sensore IKEA (affidabile, in stanza).
          Se manca, fallback alla sonda dello split AC — ma ETICHETTATA come tale:
          quella sonda sta sull'evaporatore e durante il funzionamento legge l'aria
          fredda/calda dello scambiatore, non la stanza. */}
      <div className="thermo-top">
        {(() => {
          const hasIkea = room.temperature != null
          const t = hasIkea ? room.temperature
            : (ac.inside_temperature != null ? ac.inside_temperature : null)
          return (
            <div className={`env-chip ${hasIkea ? '' : 'env-probe'}`}
                 title={hasIkea ? 'Sensore ambiente IKEA' : 'Sonda interna del condizionatore (indicativa)'}>
              <Ic.inside width="20" height="20" />
              <span className="env-val">{t != null ? Math.round(t) : '—'}°</span>
              <span className="env-lbl">{hasIkea ? 'interna' : 'sonda AC'}</span>
            </div>
          )
        })()}
        <div className="env-chip">
          <Ic.outside width="20" height="20" />
          <span className="env-val">{ac.outside_temperature != null ? Math.round(ac.outside_temperature) : '—'}°</span>
          <span className="env-lbl">esterna</span>
        </div>
        {draft.nanoe !== null && (
          <button className={`nanoe ${draft.nanoe ? 'on' : ''}`} onClick={toggleNanoe} title="nanoe X">
            <span className="nanoe-dot" /> nanoe<b>X</b>
          </button>
        )}
      </div>

      {/* Setpoint grande */}
      <div className={`thermo-set ${dim ? 'dim' : ''}`}>
        <div className="set-mode"><ModeIcon width="16" height="16" /> {modeMeta.label}</div>
        {modeMeta.temp ? (
          <div className="set-big">
            {Math.trunc(draft.temperature)}
            <span className="set-dec">.{(draft.temperature % 1) ? '5' : '0'}</span>
            <span className="set-u">°C</span>
          </div>
        ) : (
          <div className="set-big set-nofan">—</div>
        )}
      </div>

      {/* Slider + / - */}
      {modeMeta.temp && (
        <div className="thermo-slider">
          <button className="round" onClick={() => stepTemp(-0.5)} disabled={dim} aria-label="Diminuisci">−</button>
          <input type="range" min={TEMP_MIN} max={TEMP_MAX} step="0.5"
            value={draft.temperature} onChange={onSlider} disabled={dim}
            style={{ '--fill': `${((draft.temperature - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)) * 100}%` }} />
          <button className="round" onClick={() => stepTemp(0.5)} disabled={dim} aria-label="Aumenta">+</button>
        </div>
      )}

      {/* Foglio modale: selezione MODALITÀ (stile app Panasonic) */}
      {panel === 'mode' && (
        <div className="sheet-overlay" onClick={() => setPanel(null)}>
          <div className="sheet" onClick={(e) => e.stopPropagation()}>
            <button className="sheet-close" onClick={() => setPanel(null)} aria-label="Chiudi">✕</button>
            <div className="mode-grid">
              {MODES.filter((m) => m.temp).map((m) => {
                const I = modeIcon[m.key]
                return (
                  <button key={m.key} className={`mode-card ${draft.mode === m.key ? 'active' : ''}`}
                    style={{ '--mc': MODE_ACCENT[m.key] }} onClick={() => pickMode(m.key)}>
                    <I width="30" height="30" />
                    <span className="mode-name">{m.key === 'Cool' ? 'Cool' : m.key === 'Heat' ? 'Heat' : m.key === 'Dry' ? 'Dry' : 'Auto'}</span>
                    <span className="mode-underline" />
                  </button>
                )
              })}
            </div>
            <button className={`mode-row ${draft.mode === 'Fan' ? 'active' : ''}`}
              style={{ '--mc': MODE_ACCENT.Fan }} onClick={() => pickMode('Fan')}>
              <Ic.fan width="26" height="26" />
              <span className="mode-name">Fan</span>
              <span className="mode-underline" />
            </button>
          </div>
        </div>
      )}

      {/* Foglio modale: VENTOLA — barre di livello + frecce + toggle Auto */}
      {panel === 'fan' && (() => {
        const isAuto = draft.fan_speed === 'Auto'
        // livello corrente (0..4); se Auto, mostra il massimo come riferimento.
        const lvl = isAuto ? FAN_LEVELS.length - 1 : Math.max(0, FAN_LEVELS.indexOf(draft.fan_speed))
        const stepFan = (d) => {
          const ni = Math.min(FAN_LEVELS.length - 1, Math.max(0, lvl + d))
          setFan(FAN_LEVELS[ni])
        }
        return (
          <div className="sheet-overlay" onClick={() => setPanel(null)}>
            <div className="sheet" onClick={(e) => e.stopPropagation()}>
              <button className="sheet-close" onClick={() => setPanel(null)} aria-label="Chiudi">✕</button>

              {/* Icona ventola + barre crescenti, le attive scure */}
              <div className="fan-vis">
                <Ic.fan width="48" height="48" />
                <div className="fan-bars">
                  {FAN_LEVELS.map((_, i) => (
                    <span key={i}
                      className={`fan-bar ${!isAuto && i <= lvl ? 'on' : ''}`}
                      style={{ height: `${28 + i * 16}px` }} />
                  ))}
                </div>
              </div>

              {/* Frecce avanti / indietro (disattive in Auto) */}
              <div className="fan-arrows">
                <button onClick={() => stepFan(-1)} disabled={isAuto || lvl === 0} aria-label="Più bassa">
                  <Ic.chevLeft width="34" height="34" />
                </button>
                <button onClick={() => stepFan(1)} disabled={isAuto || lvl === FAN_LEVELS.length - 1} aria-label="Più alta">
                  <Ic.chevRight width="34" height="34" />
                </button>
              </div>

              {/* Etichetta + toggle Auto */}
              <div className="fan-auto">
                <div className="fan-auto-label">{isAuto ? 'Auto' : FAN_LABEL[draft.fan_speed]}</div>
                <button className={`switch ${isAuto ? 'on' : ''}`} role="switch" aria-checked={isAuto}
                  onClick={() => setFan(isAuto ? 'Mid' : 'Auto')}>
                  <span className="switch-knob" />
                </button>
              </div>
            </div>
          </div>
        )
      })()}

      {/* Foglio modale: SWING alette su/giù — frecce posizione + toggle Auto/Swing */}
      {panel === 'swing' && (() => {
        const sv = draft.swing_vertical
        const isAuto = sv === 'Auto'
        const isSwing = sv === 'Swing'
        // posizione corrente (0=Up .. 4=Down); default centro se Auto/Swing.
        const pos = SWING_POS.indexOf(sv)
        const cur = pos >= 0 ? pos : 2
        const stepPos = (d) => {
          const ni = Math.min(SWING_POS.length - 1, Math.max(0, cur + d))
          setSwing(SWING_POS[ni])
        }
        return (
          <div className="sheet-overlay" onClick={() => setPanel(null)}>
            <div className="sheet" onClick={(e) => e.stopPropagation()}>
              <button className="sheet-close" onClick={() => setPanel(null)} aria-label="Chiudi">✕</button>

              {/* Layout come app Panasonic: icona+titolo a sinistra, toggle a destra */}
              <div className="swing-layout">
                <div className="swing-main">
                  <div className="swing-title">Air swing (Up-Down)</div>
                  <div className={`swing-icon ${isSwing ? 'is-swing' : ''} ${(isAuto || isSwing) ? '' : 'is-manual'}`}
                       style={{ '--tilt': `${-40 + cur * 20}deg` }}>
                    <Ic.swing width="68" height="68" />
                  </div>
                  <div className="fan-arrows">
                    <button onClick={() => stepPos(1)} disabled={isAuto || isSwing || cur === SWING_POS.length - 1} aria-label="Più in basso">
                      <Ic.chevDown width="34" height="34" />
                    </button>
                    <button onClick={() => stepPos(-1)} disabled={isAuto || isSwing || cur === 0} aria-label="Più in alto">
                      <Ic.chevUp width="34" height="34" />
                    </button>
                  </div>
                </div>
                <div className="swing-side">
                  <div className="swing-tg">
                    <div className="fan-auto-label">Auto</div>
                    <button className={`switch ${isAuto ? 'on' : ''}`} role="switch" aria-checked={isAuto}
                      onClick={() => setSwing(isAuto ? 'Mid' : 'Auto', true)}>
                      <span className="switch-knob" />
                    </button>
                  </div>
                  <div className="swing-tg">
                    <div className="fan-auto-label">Swing</div>
                    <button className={`switch ${isSwing ? 'on' : ''}`} role="switch" aria-checked={isSwing}
                      onClick={() => setSwing(isSwing ? 'Mid' : 'Swing', true)}>
                      <span className="switch-knob" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )
      })()}

      {/* Barra azioni */}
      <div className="thermo-bar">
        <button className={`bar-btn ${panel === 'mode' ? 'active' : ''}`} onClick={() => setPanel(panel === 'mode' ? null : 'mode')}>
          <ModeIcon width="24" height="24" />
        </button>
        <button className={`bar-btn ${panel === 'fan' ? 'active' : ''}`} onClick={() => setPanel(panel === 'fan' ? null : 'fan')}>
          <Ic.fan width="24" height="24" />
          <span className="bar-sub">{FAN_LABEL[draft.fan_speed] || draft.fan_speed}</span>
        </button>
        <button className={`bar-btn ${panel === 'swing' ? 'active' : ''}`} onClick={() => setPanel(panel === 'swing' ? null : 'swing')}>
          <Ic.swing width="24" height="24" />
          <span className="bar-sub">{draft.swing_vertical === 'Swing' ? 'Oscilla' : draft.swing_vertical === 'Auto' ? 'Auto' : 'Alette'}</span>
        </button>
        <button className={`bar-btn power ${draft.power ? 'on' : ''}`} onClick={togglePower}>
          <Ic.power width="24" height="24" />
        </button>
      </div>

      {/* Intensità (sotto i controlli): Powerful / Quiet, esclusivi.
          Richiedono l'AC acceso. */}
      <div className="eco-row">
        <button className={`eco-btn powerful ${draft.eco_mode === 'Powerful' ? 'active' : ''}`}
          onClick={() => pickEco('Powerful')} disabled={dim}>
          <Ic.powerful width="20" height="20" />
          <span>Powerful</span>
        </button>
        <button className={`eco-btn quiet ${draft.eco_mode === 'Quiet' ? 'active' : ''}`}
          onClick={() => pickEco('Quiet')} disabled={dim}>
          <Ic.quiet width="20" height="20" />
          <span>Quiet</span>
        </button>
      </div>

      {room.override_active && (
        <button className="thermo-auto" onClick={async () => { await api.clearOverride(room.name); onAction && onAction() }}>
          Torna in automatico
        </button>
      )}
    </div>
  )
}
