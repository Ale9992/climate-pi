import { useState, useEffect, useRef, useCallback } from 'react'
import Icon from '@mdi/react'
import {
  mdiSnowflake, mdiFire, mdiWaterPercent, mdiFan, mdiWeatherNight, mdiLightningBolt,
  mdiPower, mdiThermometer, mdiLeaf, mdiMoleculeCo2, mdiAirFilter, mdiBrightness5,
  mdiChevronLeft, mdiCloudCheckOutline, mdiAccessPoint, mdiLightbulbVariant, mdiWifi,
  mdiHomeThermometerOutline, mdiBrain, mdiServerNetwork, mdiChip, mdiRadiatorDisabled,
  mdiArrowOscillating, mdiLockOutline, mdiCalendarClock, mdiAirConditioner, mdiHubOutline,
  mdiClockOutline, mdiAccountOutline, mdiCurrencyEur, mdiRefresh, mdiAccountGroup,
} from '@mdi/js'
import { api } from '../api.js'

const TMIN = 16, TMAX = 30
const MODES = [
  { key: 'Cool', label: 'Cool', icon: mdiSnowflake },
  { key: 'Heat', label: 'Heat', icon: mdiFire },
  { key: 'Dry', label: 'Dry', icon: mdiWaterPercent },
  { key: 'Fan', label: 'Fan', icon: mdiWeatherNight },
  { key: 'Auto', label: 'Auto', icon: mdiFan },
]
const FAN_LABEL = { Auto: 'Auto', Low: 'Bassa', LowMid: 'Medio-bassa', Mid: 'Media', HighMid: 'Medio-alta', High: 'Alta' }
const relTime = (v, now) => {
  if (!v) return '—'
  const d = new Date(v); if (isNaN(d)) return '—'
  const s = Math.max(0, Math.round((now - d) / 1000))
  if (s < 60) return `${s} s fa`
  const m = Math.round(s / 60); if (m < 60) return `${m} min fa`
  return `${Math.round(m / 60)} h fa`
}
const fmtRuntime = (min) => {
  if (min == null) return '—'
  const h = Math.floor(min / 60), m = min % 60
  return h ? `${h}h ${m}min` : `${m} min`
}

// ---- Climatizzatore: gauge temp reale + setpoint + slider + modi + Quiet/Powerful
function ClimateControl({ room, onAction }) {
  const ac = room.ac
  const reachable = ac && ac.reachable
  const [draft, setDraft] = useState(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef(null)
  useEffect(() => {
    if (!reachable || (draft && busy)) return
    setDraft({
      power: ac.power === 'On', mode: ac.mode || 'Cool',
      temperature: ac.target_temperature ?? 24, fan_speed: ac.fan_speed || 'Auto',
      eco_mode: ac.eco_mode || 'Auto', swing_vertical: ac.swing_vertical,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ac?.power, ac?.mode, ac?.target_temperature, ac?.fan_speed, ac?.eco_mode, reachable])

  if (!reachable) return (
    <div className="card rv-clima">
      <div className="rv-card-head"><span className="rv-h-ic blue"><Icon path={mdiSnowflake} size={0.8} /></span><h3>Climatizzatore</h3></div>
      <p className="rv-muted" style={{ padding: '40px 0', textAlign: 'center' }}>Condizionatore non raggiungibile</p>
    </div>
  )
  if (!draft) return <div className="card rv-clima"><p className="rv-muted">Caricamento…</p></div>

  const send = (patch, immediate = false) => {
    const next = { ...draft, ...patch }; setDraft(next); setBusy(true)
    clearTimeout(timer.current)
    const modeTemp = next.mode !== 'Fan'
    const fire = async () => {
      try {
        await api.control(room.name, {
          power: next.power, mode: next.mode, temperature: modeTemp ? next.temperature : null,
          fan_speed: next.fan_speed, eco_mode: next.eco_mode || null, swing_vertical: next.swing_vertical || null,
        })
      } finally { setBusy(false); onAction && onAction() }
    }
    timer.current = setTimeout(fire, immediate ? 0 : 600)
  }
  const realTemp = room.temperature != null ? room.temperature : (ac.inside_temperature ?? null)
  const gaugePct = realTemp != null ? Math.min(1, Math.max(0, (realTemp - TMIN) / (TMAX - TMIN))) : 0
  const R = 86, C = Math.PI * R * 1.5 // arco 270°
  const dim = !draft.power

  return (
    <div className="card rv-clima">
      <div className="rv-card-head">
        <span className="rv-h-ic blue"><Icon path={mdiSnowflake} size={0.8} /></span>
        <h3>Climatizzatore</h3>
        <button className={`rv-acpwr ${draft.power ? 'on' : ''}`} onClick={() => send({ power: !draft.power }, true)}>
          <span className="rv-acpwr-dot" />{draft.power ? 'AC ON' : 'AC OFF'}
        </button>
      </div>
      <div className={`rv-clima-top ${dim ? 'dim' : ''}`}>
        <div className="rv-set">
          <span className="rv-set-lbl">SETPOINT</span>
          <strong>{draft.temperature.toFixed(1)}°</strong>
          <span className="rv-set-sub">Temperatura impostata</span>
        </div>
        <div className="rv-gauge">
          <svg viewBox="0 0 220 220">
            <circle className="rv-g-track" cx="110" cy="110" r={R} strokeDasharray={`${C} 999`} transform="rotate(135 110 110)" />
            <circle className="rv-g-fill" cx="110" cy="110" r={R} strokeDasharray={`${C * gaugePct} 999`} transform="rotate(135 110 110)" />
          </svg>
          <div className="rv-g-center">
            <Icon path={mdiThermometer} size={0.7} />
            <strong>{realTemp != null ? realTemp.toFixed(1) : '—'}°</strong>
            <span>Temperatura reale</span>
          </div>
        </div>
        <div className="rv-modeinfo">
          <div><span className="rv-mi-lbl">MODALITÀ</span><span className="rv-mi-val"><Icon path={MODES.find(m => m.key === draft.mode)?.icon || mdiSnowflake} size={0.7} />{draft.mode}</span></div>
          <div><span className="rv-mi-lbl">VENTILAZIONE</span><span className="rv-mi-val"><Icon path={mdiFan} size={0.7} />{FAN_LABEL[draft.fan_speed] || draft.fan_speed}</span></div>
        </div>
      </div>
      <div className="rv-slider">
        <button onClick={() => send({ temperature: Math.max(TMIN, draft.temperature - 0.5) })} disabled={dim}>−</button>
        <input type="range" min={TMIN} max={TMAX} step="0.5" value={draft.temperature} disabled={dim}
          onChange={(e) => send({ temperature: Number(e.target.value) })}
          style={{ '--fill': `${((draft.temperature - TMIN) / (TMAX - TMIN)) * 100}%` }} />
        <button onClick={() => send({ temperature: Math.min(TMAX, draft.temperature + 0.5) })} disabled={dim}>+</button>
      </div>
      <div className="rv-ticks"><span>16°</span><span>18°</span><span>20°</span><span>22°</span><span>24°</span><span>26°</span><span>28°</span><span>30°</span></div>
      <div className="rv-modes">
        {MODES.map((m) => (
          <button key={m.key} className={`rv-mode ${draft.mode === m.key ? 'active' : ''}`} onClick={() => send({ mode: m.key }, true)}>
            <Icon path={m.icon} size={0.72} />{m.label}
          </button>
        ))}
      </div>
      <div className="rv-eco">
        <button className={`rv-ecobtn ${draft.eco_mode === 'Quiet' ? 'active' : ''}`} disabled={dim}
          onClick={() => send({ eco_mode: draft.eco_mode === 'Quiet' ? 'Auto' : 'Quiet' }, true)}>
          <Icon path={mdiWeatherNight} size={0.72} />Quiet
        </button>
        <button className={`rv-ecobtn ${draft.eco_mode === 'Powerful' ? 'active' : ''}`} disabled={dim}
          onClick={() => send({ eco_mode: draft.eco_mode === 'Powerful' ? 'Auto' : 'Powerful' }, true)}>
          <Icon path={mdiLightningBolt} size={0.72} />Powerful
        </button>
      </div>
    </div>
  )
}

// ---- Andamento 24h: temp (blu) + umidità (verde)
function RoomChart({ data }) {
  const W = 680, H = 220, padL = 8, padR = 8, padT = 16, padB = 26
  const pts = (data || []).filter((d) => d.temperature != null)
  const innerW = W - padL - padR, innerH = H - padT - padB
  const temps = pts.map((p) => p.temperature)
  const hums = pts.map((p) => p.humidity).filter((v) => v != null)
  const tMin = temps.length ? Math.min(...temps) - 1 : 16, tMax = temps.length ? Math.max(...temps) + 1 : 30
  const line = (vals, lo, hi) => {
    if (vals.length < 2) return ''
    const span = Math.max(0.5, hi - lo)
    return vals.map((v, i) => {
      const x = padL + (i / (vals.length - 1)) * innerW
      const y = v == null ? null : padT + innerH - ((v - lo) / span) * innerH
      return v == null ? '' : `${i && vals[i - 1] != null ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`
    }).join(' ')
  }
  const labels = pts.filter((_, i) => i % Math.ceil(pts.length / 6 || 1) === 0).map((p) => (p.timestamp || '').slice(11, 16))
  return (
    <div className="card rv-chart">
      <div className="rv-card-head"><h3>Andamento 24h</h3>
        <div className="rv-legend"><span className="t">Temperatura</span><span className="h">Umidità</span></div>
      </div>
      {pts.length < 2 ? <p className="rv-muted" style={{ padding: '40px 0', textAlign: 'center' }}>Dati insufficienti</p> : (
        <>
          <svg className="rv-chart-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
            <path className="rv-l-hum" d={line(pts.map((p) => p.humidity ?? null), 20, 90)} />
            <path className="rv-l-temp" d={line(temps, tMin, tMax)} />
          </svg>
          <div className="rv-chart-x">{labels.map((l, i) => <span key={i}>{l}</span>)}</div>
        </>
      )}
    </div>
  )
}

// ---- Ambiente stanza (6 tile)
function AmbientCard({ room, comfort, now }) {
  const tiles = [
    { icon: mdiThermometer, val: room.temperature != null ? `${room.temperature.toFixed(1)}°` : 'n/d', lbl: 'Temperatura' },
    { icon: mdiWaterPercent, val: room.humidity != null ? `${Math.round(room.humidity)}%` : 'n/d', lbl: 'Umidità' },
    { icon: mdiLeaf, val: comfort != null ? `${comfort}%` : 'n/d', lbl: 'Comfort' },
    { icon: mdiMoleculeCo2, val: 'n/d', lbl: 'CO₂', off: true },
    { icon: mdiAirFilter, val: 'n/d', lbl: 'Qualità aria', off: true },
    { icon: mdiBrightness5, val: room.lux != null ? `${Math.round(room.lux)} lx` : 'n/d', lbl: 'Luce' },
  ]
  return (
    <div className="card rv-amb">
      <div className="rv-card-head"><h3>Ambiente stanza</h3><span className="rv-dot ok" /></div>
      <p className="rv-sub">Aggiornato {relTime(room.last_reading, now)}</p>
      <div className="rv-amb-grid">
        {tiles.map((t) => (
          <div key={t.lbl} className={`rv-amb-tile ${t.off ? 'off' : ''}`}>
            <Icon path={t.icon} size={0.78} />
            <strong>{t.val}</strong>
            <span>{t.lbl}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---- Stato sistema (impianti + nodi stanza)
function SystemPanel({ overview, room, now, onRefresh }) {
  const sysMap = Object.fromEntries((overview?.systems || []).map((s) => [s.key, s]))
  const wifi = overview?.wifi || {}
  const sensorFresh = room.last_reading && (now - new Date(room.last_reading)) < 30 * 60 * 1000
  const rows = [
    { icon: mdiBrain, name: 'Home Engine', on: sysMap.home_engine?.online ?? true, detail: sysMap.home_engine?.detail || 'attivo' },
    { icon: mdiAccessPoint, name: 'Panasonic', on: sysMap.panasonic?.online, detail: sysMap.panasonic?.detail },
    { icon: mdiLightbulbVariant, name: 'Dirigera', on: sysMap.dirigera?.online, detail: sysMap.dirigera?.detail },
    { icon: mdiHomeThermometerOutline, name: 'Sensore stanza', on: sensorFresh, detail: relTime(room.last_reading, now) },
    { icon: mdiChip, name: 'ESP32 Stanza', on: null, detail: 'n/d' },
    { icon: mdiWifi, name: 'Rete Wi-Fi', on: wifi.dbm != null, detail: wifi.dbm != null ? `${Math.round(wifi.dbm)} dBm` : 'n/d' },
    { icon: mdiServerNetwork, name: 'MQTT Broker', on: null, detail: 'n/d' },
  ]
  return (
    <div className="card rv-sys">
      <div className="rv-card-head"><h3>Stato sistema</h3>
        <button className="rv-link" onClick={onRefresh}><Icon path={mdiRefresh} size={0.6} /> Aggiorna</button>
      </div>
      <div className="rv-sys-list">
        {rows.map((r) => (
          <div key={r.name} className={r.on == null ? 'na' : r.on ? 'ok' : 'warn'}>
            <Icon path={r.icon} size={0.68} />
            <span className="rv-sys-name">{r.name}</span>
            <span className="rv-sys-st">{r.on == null ? 'n/d' : r.on ? 'Online' : 'Offline'}</span>
            <em className="rv-sys-det">{r.detail}</em>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---- Azioni rapide
function QuickActions({ room, onAction }) {
  const ac = room.ac
  const act = async (patch) => { try { await api.control(room.name, patch) } finally { onAction && onAction() } }
  const swingOn = ac?.swing_vertical === 'Swing'
  const items = [
    { icon: mdiPower, label: 'Spegni AC', tone: 'red', onClick: () => act({ power: false }) },
    { icon: mdiWeatherNight, label: 'Modalità Quiet', tone: 'blue', onClick: () => act({ eco_mode: 'Quiet' }) },
    { icon: mdiLightningBolt, label: 'Modalità Powerful', tone: 'amber', onClick: () => act({ eco_mode: 'Powerful' }) },
    { icon: mdiArrowOscillating, label: 'Oscillazione', tone: 'blue', onClick: () => act({ swing_vertical: swingOn ? 'Mid' : 'Swing' }) },
    { icon: mdiLockOutline, label: 'Blocco remoto', tone: 'grey', disabled: true },
    { icon: mdiCalendarClock, label: 'Programmazione', tone: 'grey', disabled: true },
  ]
  return (
    <div className="card rv-qa">
      <div className="rv-card-head"><h3>Azioni rapide</h3></div>
      <div className="rv-qa-grid">
        {items.map((it) => (
          <button key={it.label} className={`rv-qa-btn ${it.disabled ? 'off' : ''}`} onClick={it.onClick} disabled={it.disabled}>
            <span className={`rv-qa-ic ${it.tone}`}><Icon path={it.icon} size={0.72} /></span>{it.label}
          </button>
        ))}
      </div>
    </div>
  )
}

// ---- Dispositivi stanza
function DeviceList({ room, overview, now }) {
  const ac = room.ac
  const sysMap = Object.fromEntries((overview?.systems || []).map((s) => [s.key, s]))
  const sensorAge = relTime(room.last_reading, now)
  const devs = [
    { icon: mdiAirConditioner, name: 'Climatizzatore', sub: 'Panasonic TZ25', on: ac?.reachable, badge: ac?.power === 'On' ? 'ON' : 'OFF', detail: ac?.reachable ? 'attivo' : '—' },
    { icon: mdiHomeThermometerOutline, name: 'Sensore ambiente', sub: 'BME280', on: !!room.last_reading, badge: room.temperature != null ? `${room.temperature.toFixed(1)}° ${room.humidity != null ? Math.round(room.humidity) + '%' : ''}` : '—', detail: sensorAge },
    { icon: mdiChip, name: 'ESP32 Stanza', sub: 'mmWave (non installato)', on: null, badge: 'n/d', detail: 'n/d' },
    { icon: mdiHubOutline, name: 'Dirigera Hub', sub: 'IKEA', on: sysMap.dirigera?.online, badge: sysMap.dirigera?.online ? 'Online' : 'Offline', detail: sysMap.dirigera?.detail || '—' },
  ]
  return (
    <div className="card rv-dev">
      <div className="rv-card-head"><h3>Dispositivi stanza</h3></div>
      <div className="rv-dev-grid">
        {devs.map((d) => (
          <div key={d.name} className={`rv-dev-card ${d.on == null ? 'na' : ''}`}>
            <span className="rv-dev-ic"><Icon path={d.icon} size={0.8} /></span>
            <div className="rv-dev-txt"><strong>{d.name}</strong><span>{d.sub}</span></div>
            <div className="rv-dev-meta"><b className={d.on == null ? 'na' : d.on ? 'ok' : 'warn'}>{d.badge}</b><em>{d.detail}</em></div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function RoomView({ room, status, overview, energy, now, dateStr, hh, mm, season, onBack, onAction }) {
  const [detail, setDetail] = useState({})
  const [history, setHistory] = useState([])
  const load = useCallback(async () => {
    if (!room?.name) return
    try {
      const [d, h] = await Promise.all([
        api.getRoomDetail(room.name).catch(() => ({})),
        api.getHistory(room.name, 24).catch(() => []),
      ])
      setDetail(d); setHistory(h)
    } catch { /**/ }
  }, [room?.name])
  useEffect(() => { load() }, [load])
  if (!room) return null
  const comfort = detail.comfort ?? overview?.rooms_comfort?.[room.name]
  const ac = room.ac
  const topChips = [
    { date: true },
    { icon: season?.icon, tint: season?.tint, val: season?.label, lbl: `Esterno ${status?.outdoor_avg_temperature != null ? status.outdoor_avg_temperature + '°' : '—'}` },
    { icon: mdiLightningBolt, tint: '#e8b53f', val: `${(energy?.today_kwh ?? 0).toFixed(2)} kWh`, lbl: 'Consumo oggi' },
    { icon: mdiAccountGroup, tint: '#2f9e8f', val: status?.presence_home ? 'In casa' : 'Fuori', lbl: detail.people != null ? `${detail.people} persone` : 'presenza' },
  ]
  const bottom = [
    { icon: mdiClockOutline, lbl: 'Prossima azione programmata', val: detail.next_action ? `${detail.next_action.label.replace(' programmato', '')} alle ${detail.next_action.time}` : '—' },
    { icon: mdiAccountOutline, lbl: 'Presenza rilevata', val: status?.presence_home ? `${detail.people ?? '—'} in casa` : 'Nessuno' },
    { icon: mdiClockOutline, lbl: 'Tempo AC oggi', val: fmtRuntime(detail.ac_runtime_today_min) },
    { icon: mdiLightningBolt, lbl: 'Consumo AC oggi', val: detail.ac_energy_today_kwh != null ? `${detail.ac_energy_today_kwh.toFixed(2)} kWh` : '—' },
    { icon: mdiCurrencyEur, lbl: 'Costo AC oggi', val: detail.ac_cost_today != null ? `${detail.ac_cost_today.toFixed(2)} €` : '—' },
  ]
  return (
    <div className="rv">
      <div className="rv-top">
        {topChips.map((c, i) => c.date ? (
          <div key={i} className="rv-chip rv-chip-date"><span className="rv-cd-date">{dateStr}</span><span className="rv-cd-time">{hh}:{mm}</span></div>
        ) : (
          <div key={i} className="rv-chip">
            <span className="rv-chip-ic" style={{ color: c.tint }}><Icon path={c.icon} size={0.8} /></span>
            <div><div className="rv-chip-val">{c.val}</div><div className="rv-chip-lbl">{c.lbl}</div></div>
          </div>
        ))}
      </div>

      <div className="rv-top-grid">
        {ac ? <div className="rv-a-clima"><ClimateControl room={room} onAction={() => { onAction && onAction(); load() }} /></div> : <div className="rv-a-clima" />}
        <div className="rv-a-amb"><AmbientCard room={room} comfort={comfort} now={now} /></div>
        <div className="rv-a-sys"><SystemPanel overview={overview} room={room} now={now} onRefresh={() => { onAction && onAction(); load() }} /></div>
        <div className="rv-a-azioni"><QuickActions room={room} onAction={() => { onAction && onAction(); load() }} /></div>
      </div>
      <div className="rv-bot-grid">
        <RoomChart data={history} />
        <DeviceList room={room} overview={overview} now={now} />
      </div>

      <div className="rv-bottom">
        {bottom.map((b) => (
          <div key={b.lbl} className="rv-bchip">
            <span className="rv-bchip-ic"><Icon path={b.icon} size={0.78} /></span>
            <div><div className="rv-bchip-lbl">{b.lbl}</div><div className="rv-bchip-val">{b.val}</div></div>
          </div>
        ))}
      </div>
    </div>
  )
}
