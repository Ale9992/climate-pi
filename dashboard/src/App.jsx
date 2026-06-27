import { useEffect, useState, useCallback } from 'react'
import Icon from '@mdi/react'
import {
  mdiHomeVariant, mdiChartBox, mdiTuneVerticalVariant, mdiBellOutline, mdiCog,
  mdiWeatherSunny, mdiWeatherNight, mdiPlus,
  mdiSnowflake, mdiFire, mdiLeaf, mdiThermometer, mdiWaterPercent,
  mdiLightningBolt, mdiHomeAccount, mdiHomeExportOutline, mdiLightbulbVariant,
  mdiBrightness5, mdiGauge, mdiWifi, mdiAccessPoint, mdiCloudCheckOutline,
  mdiAlertCircleOutline, mdiClockOutline, mdiWeatherPartlyCloudy,
  mdiHomeThermometerOutline, mdiPowerPlugOutline, mdiMenu, mdiClose,
  mdiWaterBoiler, mdiRadiator,
} from '@mdi/js'
import { api } from './api.js'
import Thermostat from './components/Thermostat.jsx'
import RoomLights from './components/RoomLights.jsx'
import History from './components/History.jsx'
import ConfigPanel from './components/ConfigPanel.jsx'
import EventLog from './components/EventLog.jsx'

const POLL_MS = 30000
const MESI = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']
// Coordinate generiche (centro Italia) usate SOLO per stimare alba/tramonto e
// commutare il tema chiaro/scuro: precisione al minuto irrilevante.
const SUN_LAT = 41.9
const SUN_LON = 12.5
const SUN_ZENITH = 90.833
const calendarSeasonMeta = (date = new Date()) => {
  const m = date.getMonth() + 1
  const d = date.getDate()
  if ((m === 12 && d >= 21) || m <= 2 || (m === 3 && d < 20)) {
    return { icon: mdiSnowflake, label: 'Inverno', tint: '#7aa7ff' }
  }
  if ((m === 3 && d >= 20) || m === 4 || m === 5 || (m === 6 && d < 21)) {
    return { icon: mdiLeaf, label: 'Primavera', tint: '#43c2b4' }
  }
  if ((m === 6 && d >= 21) || m === 7 || m === 8 || (m === 9 && d < 23)) {
    return { icon: mdiWeatherSunny, label: 'Estate', tint: '#f0a868' }
  }
  return { icon: mdiLeaf, label: 'Autunno', tint: '#d38b24' }
}

const degToRad = (v) => (v * Math.PI) / 180
const radToDeg = (v) => (v * 180) / Math.PI
const normDeg = (v) => ((v % 360) + 360) % 360
const normHour = (v) => ((v % 24) + 24) % 24

const dayOfYear = (date) => {
  const start = new Date(date.getFullYear(), 0, 0)
  return Math.floor((date - start) / 86400000)
}

const sunTime = (date, sunrise) => {
  const n = dayOfYear(date)
  const lngHour = SUN_LON / 15
  const t = n + ((sunrise ? 6 : 18) - lngHour) / 24
  const m = 0.9856 * t - 3.289
  const l = normDeg(m + 1.916 * Math.sin(degToRad(m)) + 0.020 * Math.sin(degToRad(2 * m)) + 282.634)
  let ra = normDeg(radToDeg(Math.atan(0.91764 * Math.tan(degToRad(l)))))
  ra += Math.floor(l / 90) * 90 - Math.floor(ra / 90) * 90
  ra /= 15

  const sinDec = 0.39782 * Math.sin(degToRad(l))
  const cosDec = Math.cos(Math.asin(sinDec))
  const cosH = (Math.cos(degToRad(SUN_ZENITH)) - sinDec * Math.sin(degToRad(SUN_LAT))) /
    (cosDec * Math.cos(degToRad(SUN_LAT)))
  if (cosH > 1 || cosH < -1) return null

  const h = (sunrise ? 360 - radToDeg(Math.acos(cosH)) : radToDeg(Math.acos(cosH))) / 15
  const utcHour = normHour(h + ra - 0.06571 * t - 6.622 - lngHour)
  const localHour = normHour(utcHour - date.getTimezoneOffset() / 60)
  const result = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  result.setHours(Math.floor(localHour), Math.round((localHour % 1) * 60), 0, 0)
  return result
}

const autoThemeFor = (date = new Date()) => {
  const sunrise = sunTime(date, true)
  const sunset = sunTime(date, false)
  if (!sunrise || !sunset) return 'light'
  return date >= sunrise && date < sunset ? 'light' : 'dark'
}

const formatTime = (date) => date
  ? `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  : '--:--'

// "iPhone Mario" -> "Mario": mostra solo il nome della persona, non il device.
const personName = (device) =>
  (device || '').replace(/^(iPhone|iPad|iPod|Galaxy|Pixel|Telefono|Smartphone)\s+/i, '').trim()

const relTime = (value, now = new Date()) => {
  if (!value) return 'nessun dato'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return 'dato non valido'
  const sec = Math.max(0, Math.round((now - d) / 1000))
  if (sec < 60) return `${sec}s fa`
  const min = Math.round(sec / 60)
  if (min < 60) return `${min} min fa`
  const h = Math.round(min / 60)
  return `${h}h fa`
}

const sensorFresh = (value, now = new Date()) => {
  if (!value) return false
  const d = new Date(value)
  return !Number.isNaN(d.getTime()) && (now - d) < 10 * 60 * 1000
}

const readingAgeMinutes = (value, now = new Date()) => {
  if (!value) return null
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return null
  return Math.max(0, Math.round((now - d) / 60000))
}

const humidityMeta = (v) => {
  if (v == null) return { label: 'Non disponibile', tone: 'muted' }
  if (v < 40) return { label: 'Secca', tone: 'warn' }
  if (v <= 60) return { label: 'Comfort', tone: 'ok' }
  return { label: 'Alta', tone: 'warn' }
}

const luxMeta = (v) => {
  if (v == null) return { label: 'Non disponibile', tone: 'muted' }
  if (v < 10) return { label: 'Buio', tone: 'muted' }
  if (v < 80) return { label: 'Penombra', tone: 'ok' }
  return { label: 'Luminosa', tone: 'ok' }
}

const pressureMeta = (v) => {
  if (v == null) return { label: 'Non disponibile', tone: 'muted' }
  if (v < 1005) return { label: 'Bassa', tone: 'warn' }
  if (v > 1025) return { label: 'Alta', tone: 'ok' }
  return { label: 'Stabile', tone: 'ok' }
}

function EnvironmentCard({ room, now }) {
  const h = humidityMeta(room?.humidity)
  const l = luxMeta(room?.lux)
  const p = pressureMeta(room?.pressure)
  const fresh = sensorFresh(room?.last_reading, now)

  return (
    <div className="card environment-card">
      <div className="env-card-head">
        <div>
          <h3>Ambiente stanza</h3>
          <p>{fresh ? 'Sensore aggiornato' : 'Sensore da controllare'} · {relTime(room?.last_reading, now)}</p>
        </div>
        <span className={`status-dot ${fresh ? 'ok' : 'warn'}`} />
      </div>
      <div className="env-metrics">
        <div className="env-metric primary">
          <Icon path={mdiThermometer} size={0.9} />
          <strong>{room?.temperature != null ? room.temperature.toFixed(1) : '—'}°</strong>
          <span>temperatura</span>
        </div>
        <div className={`env-metric ${h.tone}`}>
          <Icon path={mdiWaterPercent} size={0.8} />
          <strong>{room?.humidity != null ? `${Math.round(room.humidity)}%` : '—'}</strong>
          <span>{h.label}</span>
        </div>
        <div className={`env-metric ${l.tone}`}>
          <Icon path={mdiBrightness5} size={0.8} />
          <strong>{room?.lux != null ? `${Math.round(room.lux)} lx` : '—'}</strong>
          <span>{l.label}</span>
        </div>
        <div className={`env-metric ${p.tone}`}>
          <Icon path={mdiGauge} size={0.8} />
          <strong>{room?.pressure != null ? room.pressure.toFixed(1) : '—'}</strong>
          <span>{room?.pressure != null ? `${p.label} hPa` : p.label}</span>
        </div>
      </div>
    </div>
  )
}

function SystemCard({ status, lastRefresh, activeRoom, now }) {
  const sensorOk = sensorFresh(activeRoom?.last_reading, now)
  const items = [
    { label: 'Home Engine', value: 'online', ok: true, icon: mdiCloudCheckOutline },
    { label: 'Panasonic', value: status.panasonic ? 'ok' : 'errore', ok: !!status.panasonic, icon: mdiAccessPoint },
    { label: 'Dirigera', value: status.dirigera ? 'ok' : 'errore', ok: !!status.dirigera, icon: mdiLightbulbVariant },
    { label: 'Sensore', value: sensorOk ? relTime(activeRoom?.last_reading, now) : 'stale', ok: sensorOk, icon: mdiWifi },
  ]
  return (
    <div className="card system-card">
      <div className="system-head">
        <div>
          <h3>Sistema</h3>
          <p><Icon path={mdiClockOutline} size={0.62} /> refresh {lastRefresh ? relTime(lastRefresh, now) : 'in corso'}</p>
        </div>
        <Icon path={items.every((i) => i.ok) ? mdiCloudCheckOutline : mdiAlertCircleOutline} size={1.1} />
      </div>
      <div className="system-list">
        {items.map((item) => (
          <div key={item.label} className={item.ok ? 'ok' : 'warn'}>
            <Icon path={item.icon} size={0.68} />
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>
    </div>
  )
}

function WeatherCard({ weather, status }) {
  const forecast = weather?.forecast || []
  const temps = forecast.map((p) => p.temperature).filter((v) => v != null)
  const min = temps.length ? Math.min(...temps) : null
  const max = temps.length ? Math.max(...temps) : null
  const span = min != null && max != null ? Math.max(1, max - min) : 1
  const next = forecast.slice(0, 8)

  return (
    <div className="card weather-card">
      <div className="weather-main">
        <span className="weather-icon"><Icon path={mdiWeatherPartlyCloudy} size={1.4} /></span>
        <div>
          <h3>Meteo esterno</h3>
          <strong>{weather?.temperature != null ? `${weather.temperature.toFixed(1)}°` : '—'}</strong>
          <p>{weather?.humidity != null ? `Umidità ${Math.round(weather.humidity)}%` : 'Umidità non disponibile'}</p>
        </div>
      </div>
      <div className="weather-meta">
        <span>Media 24h casa</span>
        <strong>{status.outdoor_avg_temperature != null ? `${status.outdoor_avg_temperature}°` : '—'}</strong>
      </div>
      <div className="weather-forecast">
        {next.map((p) => {
          const h = (p.time || '').slice(11, 13)
          const pct = p.temperature != null ? ((p.temperature - min) / span) * 100 : 0
          return (
            <div key={p.time} className="wf-point">
              <span>{h || '—'}</span>
              <i style={{ height: `${28 + pct * 0.38}px` }} />
              <strong>{p.temperature != null ? Math.round(p.temperature) : '—'}°</strong>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function HudStrip({ rooms, lights, status, lastRefresh, now }) {
  const activeAcs = rooms.filter((r) => r.ac?.reachable && r.ac.power === 'On').length
  const sensorRooms = rooms.filter((r) => r.has_sensor).length
  const sensorsOk = rooms.filter((r) => r.has_sensor && sensorFresh(r.last_reading, now)).length
  const allLights = Object.values(lights || {}).flat()
  const lightsOn = allLights.filter((l) => l.is_on).length
  const nominal = status.panasonic && status.dirigera && sensorsOk === sensorRooms
  const items = [
    { label: 'Presenza', value: status.presence_home ? 'In casa' : 'Fuori', icon: status.presence_home ? mdiHomeAccount : mdiHomeExportOutline, tone: status.presence_home ? 'ok' : 'muted' },
    { label: 'Clima', value: `${activeAcs}/${rooms.length} AC`, icon: mdiThermometer, tone: activeAcs ? 'ok' : 'muted' },
    { label: 'Luci', value: `${lightsOn}/${allLights.length}`, icon: mdiLightbulbVariant, tone: lightsOn ? 'warn' : 'muted' },
    { label: 'Sensori', value: `${sensorsOk}/${sensorRooms}`, icon: mdiHomeThermometerOutline, tone: sensorsOk === sensorRooms ? 'ok' : 'warn' },
    { label: 'Home Engine', value: nominal ? 'Nominale' : 'Attenzione', icon: nominal ? mdiCloudCheckOutline : mdiAlertCircleOutline, tone: nominal ? 'ok' : 'warn' },
    { label: 'Refresh', value: lastRefresh ? relTime(lastRefresh, now) : 'in corso', icon: mdiClockOutline, tone: 'muted' },
  ]

  return (
    <div className="hud-strip">
      {items.map((item) => (
        <div key={item.label} className={`hud-badge ${item.tone}`}>
          <Icon path={item.icon} size={0.68} />
          <span>{item.label}</span>
          <strong>{item.value}</strong>
        </div>
      ))}
    </div>
  )
}

function AlertPanel({ rooms, status, now }) {
  const alerts = []
  if (!status.panasonic) alerts.push({ tone: 'warn', title: 'Panasonic non risponde', text: 'Controllo clima degradato', icon: mdiAccessPoint })
  if (!status.dirigera) alerts.push({ tone: 'warn', title: 'Dirigera non risponde', text: 'Controllo luci degradato', icon: mdiLightbulbVariant })
  rooms.filter((r) => r.has_sensor).forEach((r) => {
    const age = readingAgeMinutes(r.last_reading, now)
    if (!sensorFresh(r.last_reading, now)) {
      alerts.push({
        tone: 'warn',
        title: `${r.name}: sensore stale`,
        text: age == null ? 'nessun dato recente' : `ultimo dato ${age} min fa`,
        icon: mdiHomeThermometerOutline,
      })
    }
    if (r.humidity != null && r.humidity > 65) {
      alerts.push({ tone: 'warn', title: `${r.name}: umidità alta`, text: `${Math.round(r.humidity)}%`, icon: mdiWaterPercent })
    }
  })
  const unreachable = rooms.filter((r) => r.ac && !r.ac.reachable)
  unreachable.forEach((r) => alerts.push({ tone: 'warn', title: `${r.name}: AC non raggiungibile`, text: r.ac?.error || 'verifica cloud Panasonic', icon: mdiAlertCircleOutline }))

  return (
    <div className={`card alerts-card ${alerts.length ? '' : 'clear'}`}>
      <div className="home-rooms-head">
        <h3>Alert</h3>
        <span>{alerts.length ? `${alerts.length} da verificare` : 'tutto regolare'}</span>
      </div>
      {alerts.length === 0 ? (
        <div className="all-clear">
          <Icon path={mdiCloudCheckOutline} size={1.35} />
          <strong>Sistemi nominali</strong>
          <span>Nessuna anomalia attiva</span>
        </div>
      ) : (
        <div className="alert-list">
          {alerts.slice(0, 4).map((a) => (
            <div key={`${a.title}-${a.text}`} className={a.tone}>
              <Icon path={a.icon} size={0.75} />
              <span><strong>{a.title}</strong><em>{a.text}</em></span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function EnergyOpsCard({ rooms, config }) {
  const acRooms = rooms.filter((r) => r.ac?.reachable)
  const total = acRooms.reduce((s, r) => s + (r.ac?.energy_today_kwh || 0), 0)
  const cooling = acRooms.reduce((s, r) => s + (r.ac?.energy_cooling_kwh || 0), 0)
  const heating = acRooms.reduce((s, r) => s + (r.ac?.energy_heating_kwh || 0), 0)
  const tariff = config?.tariff
  const price = tariff ? tariff.variable_eur_kwh * (1 + tariff.vat_rate) : null
  const cost = price != null ? total * price : null
  const sorted = [...acRooms].sort((a, b) => (b.ac?.energy_today_kwh || 0) - (a.ac?.energy_today_kwh || 0))

  return (
    <div className="card energy-ops-card">
      <div className="home-rooms-head">
        <h3>Reactor Load</h3>
        <span>{cost != null ? `~${cost.toFixed(2)} EUR oggi` : 'costo non disponibile'}</span>
      </div>
      <div className="energy-bars">
        {sorted.map((r) => {
          const kwh = r.ac?.energy_today_kwh || 0
          const pct = total > 0 ? Math.max(4, (kwh / total) * 100) : 0
          return (
            <div key={r.name} className="energy-row">
              <span>{r.name}</span>
              <i><b style={{ width: `${pct}%` }} /></i>
              <strong>{kwh.toFixed(2)} kWh</strong>
            </div>
          )
        })}
      </div>
      <div className="energy-splitline">
        <span>Cool {cooling.toFixed(2)} kWh</span>
        <span>Heat {heating.toFixed(2)} kWh</span>
        <strong>{total.toFixed(2)} kWh</strong>
      </div>
    </div>
  )
}

function TimelineCard({ logs }) {
  const items = (logs || []).slice(0, 5)

  return (
    <div className="card timeline-card">
      <div className="home-rooms-head">
        <h3>Telemetry</h3>
        <span>ultimi eventi</span>
      </div>
      {items.length === 0 ? (
        <div className="timeline-empty">Nessun evento recente</div>
      ) : (
        <div className="timeline-list">
          {items.map((l) => (
            <div key={l.id}>
              <i />
              <span>
                <strong>{l.room_name}</strong>
                <em>{l.action_taken || l.rule_matched || 'evento'}</em>
              </span>
              <b>{(l.timestamp || '').slice(11, 16)}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function BoilerCard({ boiler, onToggle }) {
  const [busy, setBusy] = useState(false)
  const on = boiler?.on
  const click = async () => {
    setBusy(true)
    try { await onToggle(!on) } finally { setBusy(false) }
  }
  return (
    <div className="boiler-card">
      <div className={`boiler-orb ${on ? 'on' : ''}`}>
        <Icon path={mdiWaterBoiler} size={2.3} />
      </div>
      <div className="boiler-state">{on == null ? '—' : on ? 'Accesa' : 'Spenta'}</div>
      <div className="boiler-sub">
        relè in rete locale{boiler?.age_seconds != null ? ` · letto ${boiler.age_seconds}s fa` : ''}
      </div>
      <button className={`boiler-toggle ${on ? 'on' : ''}`} onClick={click} disabled={busy || on == null}>
        {busy ? '…' : on ? 'Spegni' : 'Accendi'}
      </button>
      <div className="boiler-note">Comandata anche dal cronotermostato (ingresso S1/S2)</div>
    </div>
  )
}

function HomeOverview({ rooms, lights, status, weather, lastRefresh, now, onSelectRoom, logs, config }) {
  const activeAcs = rooms.filter((r) => r.ac?.reachable && r.ac.power === 'On').length
  const totalEnergy = rooms.reduce((s, r) => s + (r.ac?.reachable ? (r.ac.energy_today_kwh || 0) : 0), 0)
  const sensorsOk = rooms.filter((r) => r.has_sensor && sensorFresh(r.last_reading, now)).length
  const sensorRooms = rooms.filter((r) => r.has_sensor).length
  const allLights = Object.values(lights || {}).flat()
  const lightsOn = allLights.filter((l) => l.is_on).length
  const tempRooms = rooms.filter((r) => r.temperature != null)
  const humidityRooms = rooms.filter((r) => r.humidity != null)
  const avgTemp = tempRooms.length ? tempRooms.reduce((s, r) => s + r.temperature, 0) / tempRooms.length : null
  const avgHumidity = humidityRooms.length ? humidityRooms.reduce((s, r) => s + r.humidity, 0) / humidityRooms.length : null
  const peopleHome = Array.isArray(status.presence_people) ? status.presence_people : []
  const sm = calendarSeasonMeta(now)
  const heroFacts = [
    { label: 'Media interna', value: avgTemp != null ? `${avgTemp.toFixed(1)}°` : '—', icon: mdiThermometer },
    { label: 'Umidità', value: avgHumidity != null ? `${Math.round(avgHumidity)}%` : '—', icon: mdiWaterPercent },
    { label: 'AC attivi', value: `${activeAcs}/${rooms.length}`, icon: mdiPowerPlugOutline },
    { label: 'Luci accese', value: `${lightsOn}/${allLights.length}`, icon: mdiLightbulbVariant },
  ]

  return (
    <div className="home-grid">
      <HudStrip rooms={rooms} lights={lights} status={status} lastRefresh={lastRefresh} now={now} />

      <div className="card home-hero">
        <div className="home-hero-main">
          <p className="home-kicker">Casa</p>
          <h2>{status.presence_home ? 'Casa abitata' : 'Casa vuota'}</h2>
          <span className="home-hero-summary">Refresh {lastRefresh ? relTime(lastRefresh, now) : 'in corso'} · {rooms.length} stanze</span>
        </div>
        <div className="home-hero-insights">
          <div className="hero-facts">
            {heroFacts.map((fact) => (
              <div key={fact.label} className="hero-insight">
                <Icon path={fact.icon} size={0.72} />
                <span>{fact.label}</span>
                <strong>{fact.value}</strong>
              </div>
            ))}
          </div>
          <div className={`presence-badges ${peopleHome.length ? '' : 'empty'}`}>
            {peopleHome.length ? peopleHome.map((name) => (
              <span key={name}>{personName(name)}</span>
            )) : <span>Nessuno presente</span>}
          </div>
        </div>
        <Icon className="home-hero-icon" path={status.presence_home ? mdiHomeAccount : mdiHomeExportOutline} size={2.2} />
      </div>

      <WeatherCard weather={weather} status={status} />
      <AlertPanel rooms={rooms} status={status} now={now} />

      <div className="card home-status-card">
        <h3>Clima e consumi</h3>
        <div className="home-stat-list">
          <div><Icon path={sm.icon} size={0.8} /><span>Stagione</span><strong>{sm.label}</strong></div>
          <div><Icon path={mdiPowerPlugOutline} size={0.8} /><span>AC attivi</span><strong>{activeAcs}/{rooms.length}</strong></div>
          <div><Icon path={mdiLightningBolt} size={0.8} /><span>Energia oggi</span><strong>{totalEnergy.toFixed(2)} kWh</strong></div>
          <div><Icon path={mdiLightbulbVariant} size={0.8} /><span>Luci accese</span><strong>{lightsOn}/{allLights.length}</strong></div>
        </div>
      </div>

      <div className="card home-system-card">
        <h3>Stato impianto</h3>
        <div className="system-list">
          <div className="ok"><Icon path={mdiCloudCheckOutline} size={0.68} /><span>Home Engine</span><strong>online</strong></div>
          <div className={status.panasonic ? 'ok' : 'warn'}><Icon path={mdiAccessPoint} size={0.68} /><span>Panasonic</span><strong>{status.panasonic ? 'ok' : 'errore'}</strong></div>
          <div className={status.dirigera ? 'ok' : 'warn'}><Icon path={mdiLightbulbVariant} size={0.68} /><span>Dirigera</span><strong>{status.dirigera ? 'ok' : 'errore'}</strong></div>
          <div className={sensorsOk === sensorRooms ? 'ok' : 'warn'}><Icon path={mdiHomeThermometerOutline} size={0.68} /><span>Sensori</span><strong>{sensorsOk}/{sensorRooms}</strong></div>
        </div>
      </div>

      <EnergyOpsCard rooms={rooms} config={config} />
      <TimelineCard logs={logs} />

      <div className="card home-rooms-card">
        <div className="home-rooms-head">
          <h3>Stanze</h3>
          <span>tocca per controllare</span>
        </div>
        <div className="home-room-list">
          {rooms.map((r) => (
            <button key={r.name} onClick={() => onSelectRoom(r.name)}>
              <span>
                <strong>{r.name}</strong>
                <em>{r.last_reading ? relTime(r.last_reading, now) : 'nessun sensore'}</em>
              </span>
              <b>{r.temperature != null ? `${r.temperature.toFixed(1)}°` : '—'}</b>
              <i className={r.ac?.power === 'On' ? 'on' : ''}>{r.ac?.power === 'On' ? 'AC on' : 'AC off'}</i>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function App() {
  const [rooms, setRooms] = useState([])        // stanze con AC (dal climate)
  const [lights, setLights] = useState({})      // {stanza: [luci]}
  const [status, setStatus] = useState({})
  const [config, setConfig] = useState(null)
  const [logs, setLogs] = useState([])
  const [section, setSection] = useState('overview')  // overview | room | history | config | logs
  const [activeRoom, setActiveRoom] = useState(null)
  const [weather, setWeather] = useState({})
  const [boiler, setBoiler] = useState({})
  const [now, setNow] = useState(new Date())
  const [themeMode, setThemeMode] = useState('auto')
  const [autoTheme, setAutoTheme] = useState(() => autoThemeFor(new Date()))
  const [error, setError] = useState(null)
  const [lastRefresh, setLastRefresh] = useState(null)
  const [navOpen, setNavOpen] = useState(false)   // drawer mobile (hamburger)
  // Tema come singolo bottone che cicla auto -> light -> dark -> auto.
  const cycleTheme = () => setThemeMode((m) => (m === 'auto' ? 'light' : m === 'light' ? 'dark' : 'auto'))
  const params = new URLSearchParams(window.location.search)
  const isKiosk = params.get('kiosk') === '1'
  const isForcedTouch = params.get('touch') === '1' || params.get('mode') === 'touch'
  const isTabletTouch = window.matchMedia?.('(pointer: coarse) and (min-width: 700px) and (max-width: 1180px)').matches
  const isTouch = isForcedTouch || isTabletTouch
  const theme = themeMode === 'auto' ? autoTheme : themeMode

  const refresh = useCallback(async () => {
    try {
      const [r, s, l, w, b] = await Promise.all([
        api.getRooms(), api.getStatus(), api.getLights(),
        api.getWeather().catch(() => ({})), api.getBoiler().catch(() => ({})),
      ])
      setRooms(r); setStatus(s); setLights(l); setWeather(w); setBoiler(b)
      setLastRefresh(new Date()); setError(null)
    } catch (e) { setError(e.message) }
  }, [])
  const toggleBoiler = useCallback(async (on) => {
    try { await api.setBoiler(on); await refresh() } catch (e) { setError(e.message) }
  }, [refresh])
  const refreshLogs = useCallback(async () => { try { setLogs(await api.getLogs(100)) } catch { /**/ } }, [])
  const refreshConfig = useCallback(async () => { try { setConfig(await api.getConfig()) } catch { /**/ } }, [])

  useEffect(() => {
    refresh(); refreshConfig(); refreshLogs()
    const id = setInterval(() => { refresh(); if (section === 'logs') refreshLogs() }, POLL_MS)
    const clock = setInterval(() => setNow(new Date()), 30000)
    return () => { clearInterval(id); clearInterval(clock) }
  }, [refresh, refreshConfig, refreshLogs, section])

  useEffect(() => { document.documentElement.dataset.theme = theme }, [theme])
  useEffect(() => {
    const update = () => setAutoTheme(autoThemeFor(new Date()))
    update()
    const id = setInterval(update, 60000)
    return () => clearInterval(id)
  }, [])

  // Elenco unificato di TUTTE le stanze (con AC e/o luci).
  // Normalizzo i nomi (trim) per evitare doppioni tipo "Camera da letto " (spazio).
  const norm = (s) => (s || '').trim()
  const acRoomNames = rooms.map((r) => norm(r.name))
  const lightRoomNames = Object.keys(lights).map(norm)
  const boilerRoom = boiler?.enabled ? norm(boiler.room) : null
  const allRooms = [...new Set([...acRoomNames, ...lightRoomNames,
    ...(boilerRoom ? [boilerRoom] : [])])]
  // stanza attiva di default: la prima con AC, o la prima in assoluto
  const curRoom = activeRoom || acRoomNames[0] || allRooms[0]
  const acOfRoom = rooms.find((r) => norm(r.name) === curRoom)
  // le chiavi luci possono avere spazi finali: cerca con trim
  const lightsKey = Object.keys(lights).find((k) => norm(k) === curRoom)
  const lightsOfRoom = lightsKey ? lights[lightsKey] : []

  const sm = calendarSeasonMeta(now)
  const hh = String(now.getHours()).padStart(2, '0')
  const mm = String(now.getMinutes()).padStart(2, '0')
  const dateStr = `${now.getDate()} ${MESI[now.getMonth()]} ${now.getFullYear()}`
  const totalEnergy = rooms.reduce((s, r) => s + (r.ac?.reachable ? (r.ac.energy_today_kwh || 0) : 0), 0)
  const sunrise = sunTime(now, true)
  const sunset = sunTime(now, false)

  return (
    <div className={`layout ${isKiosk ? 'kiosk' : ''} ${isTouch ? 'touch' : ''} ${navOpen ? 'nav-open' : ''}`}>
      {/* Backdrop del drawer mobile (chiude al tap fuori). */}
      <div className="nav-backdrop" onClick={() => setNavOpen(false)} />
      {/* ===== SIDEBAR = STANZE (navigazione primaria) ===== */}
      <aside className="sidebar">
        <button className="nav-close" onClick={() => setNavOpen(false)} title="Chiudi">
          <Icon path={mdiClose} size={0.9} />
        </button>
        <button className={`side-logo ${section === 'overview' ? 'on' : ''}`}
          onClick={() => { setSection('overview'); setNavOpen(false) }} title="Home">
          <Icon path={mdiHomeVariant} size={1} />
          <span className="side-logo-label">Home</span>
        </button>

        <nav className="side-rooms">
          {allRooms.map((r) => {
            const hasAc = acRoomNames.includes(r)
            const isBoiler = r === boilerRoom
            const lk = Object.keys(lights).find((k) => norm(k) === r)
            const nLights = lk ? lights[lk].length : 0
            const active = section === 'room' && r === curRoom
            const icon = hasAc ? mdiThermometer : isBoiler ? mdiRadiator : mdiLightbulbVariant
            return (
              <button key={r} className={active ? 'on' : ''}
                onClick={() => { setSection('room'); setActiveRoom(r); setNavOpen(false) }} title={r}>
                <Icon path={icon} size={0.85} />
                <span className="sr-name">{r}</span>
                <span className="sr-meta">{hasAc ? 'AC' : ''}{isBoiler ? 'Caldaia' : ''}{(hasAc || isBoiler) && nLights ? '·' : ''}{nLights ? `${nLights}💡` : ''}</span>
              </button>
            )
          })}
          <button className="sr-add" onClick={() => { setSection('config'); setNavOpen(false) }} title="Aggiungi/Configura">
            <Icon path={mdiPlus} size={0.85} /><span className="sr-name">Stanza</span>
          </button>
        </nav>

        {/* sezioni di sistema in fondo */}
        <div className="side-sys">
          <button className={section === 'history' ? 'on' : ''} onClick={() => { setSection('history'); setNavOpen(false) }} title="Consumi/Storico"><Icon path={mdiChartBox} size={0.9} /></button>
          <button className={section === 'config' ? 'on' : ''} onClick={() => { setSection('config'); setNavOpen(false) }} title="Regole"><Icon path={mdiTuneVerticalVariant} size={0.9} /></button>
          <button className={section === 'logs' ? 'on' : ''} onClick={() => { setSection('logs'); refreshLogs(); setNavOpen(false) }} title="Attività"><Icon path={mdiBellOutline} size={0.9} /></button>
        </div>
      </aside>

      {/* ===== MAIN ===== */}
      <div className="main">
        {/* HEADER */}
        <header className="topbar">
          <button className="nav-burger" onClick={() => setNavOpen(true)} title="Menu">
            <Icon path={mdiMenu} size={1} />
          </button>
          <div className="welcome">
            <div className="avatar"><Icon path={mdiHomeAccount} size={1} /></div>
            <div>
              <div className="welcome-hi">Casa</div>
              <div className="welcome-sub">{dateStr} · {hh}:{mm}</div>
            </div>
          </div>
          {/* Tema: bottone singolo che cicla (mobile). Le 3 voci restano per desktop. */}
          <button className="theme-cycle" onClick={cycleTheme} title={`Tema: ${themeMode}`}>
            <Icon path={theme === 'dark' ? mdiWeatherNight : mdiWeatherSunny} size={0.72} />
            <span>{themeMode === 'auto' ? 'Auto' : themeMode === 'light' ? 'Light' : 'Dark'}</span>
          </button>
          <div className="theme-toggle">
            <button className={themeMode === 'auto' ? 'on' : ''} onClick={() => setThemeMode('auto')} title={`Alba ${formatTime(sunrise)} · Tramonto ${formatTime(sunset)}`}>
              <Icon path={theme === 'dark' ? mdiWeatherNight : mdiWeatherSunny} size={0.7} /> Auto
            </button>
            <button className={themeMode === 'light' ? 'on' : ''} onClick={() => setThemeMode('light')}><Icon path={mdiWeatherSunny} size={0.7} /> Light</button>
            <button className={themeMode === 'dark' ? 'on' : ''} onClick={() => setThemeMode('dark')}><Icon path={mdiWeatherNight} size={0.7} /> Dark</button>
          </div>
          <button className="bell"><Icon path={mdiBellOutline} size={0.9} /></button>
        </header>

        {error && <div className="error-banner">Errore: {error}</div>}

        {section === 'overview' && (
          <HomeOverview
            rooms={rooms}
            lights={lights}
            status={status}
            weather={weather}
            lastRefresh={lastRefresh}
            now={now}
            logs={logs}
            config={config}
            onSelectRoom={(room) => { setActiveRoom(room); setSection('room') }}
          />
        )}

        {section === 'room' && (
          <>
            <div className="room-grid">
              {/* Riepilogo casa (sempre visibile in alto) */}
              <div className="card mini-clock">
                <div className="mc-date">{dateStr}</div>
                <div className="mc-time">{hh}:{mm}</div>
              </div>
              <div className="card mini-stat">
                <span className="card-ic" style={{ color: sm.tint }}><Icon path={sm.icon} size={0.8} /></span>
                <div><div className="ms-val">{sm.label}</div><div className="ms-lbl">stagione {status.outdoor_avg_temperature != null ? `· ${status.outdoor_avg_temperature}°` : ''}</div></div>
              </div>
              <div className="card mini-stat">
                <span className="card-ic" style={{ color: '#e8b53f' }}><Icon path={mdiLightningBolt} size={0.8} /></span>
                <div><div className="ms-val">{totalEnergy.toFixed(2)} kWh</div><div className="ms-lbl">consumo oggi</div></div>
              </div>
              <div className="card mini-stat">
                <span className="card-ic" style={{ color: status.presence_home ? '#2f9e8f' : '#c4631e' }}>
                  <Icon path={status.presence_home ? mdiHomeAccount : mdiHomeExportOutline} size={0.8} /></span>
                <div><div className="ms-val">{status.presence_home ? 'In casa' : 'Fuori'}</div><div className="ms-lbl">presenza</div></div>
              </div>

              {/* DISPOSITIVI DELLA STANZA SELEZIONATA */}
              {acOfRoom && (
                <div className="card span-thermo">
                  <div className="card-head"><h3>Climatizzatore · {curRoom}</h3></div>
                  <Thermostat room={acOfRoom} onAction={refresh} />
                </div>
              )}
              {curRoom === boilerRoom && (
                <div className="card span-thermo">
                  <div className="card-head"><h3>Caldaia · {curRoom}</h3></div>
                  <BoilerCard boiler={boiler} onToggle={toggleBoiler} />
                </div>
              )}
              <RoomLights room={curRoom} lights={lightsOfRoom} onChange={refresh} />
              {acOfRoom && <EnvironmentCard room={acOfRoom} now={now} />}
              <SystemCard status={status} lastRefresh={lastRefresh} activeRoom={acOfRoom} now={now} />
            </div>

          </>
        )}

        {section === 'history' && <main className="content"><History rooms={rooms} /></main>}
        {section === 'config' && <main className="content"><ConfigPanel config={config} onSaved={refreshConfig} /></main>}
        {section === 'logs' && <main className="content"><EventLog logs={logs} onRefresh={refreshLogs} /></main>}
      </div>
    </div>
  )
}
