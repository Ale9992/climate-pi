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
  mdiWaterBoiler, mdiRadiator, mdiAirConditioner, mdiAccountGroup, mdiShieldCheck,
  mdiBrain, mdiCheckCircle, mdiWeatherWindy, mdiWhiteBalanceSunny, mdiUmbrellaOutline,
  mdiChevronRight, mdiWaterOutline, mdiWeatherPouring,
} from '@mdi/js'
import { api } from './api.js'
import Thermostat from './components/Thermostat.jsx'
import RoomLights from './components/RoomLights.jsx'
import History from './components/History.jsx'
import ConfigPanel from './components/ConfigPanel.jsx'
import EventLog from './components/EventLog.jsx'
import RoomView from './components/RoomView.jsx'

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

function ComfortGauge({ value }) {
  const v = value == null ? 0 : value
  const r = 52
  const c = 2 * Math.PI * r
  const off = c * (1 - v / 100)
  const tone = v >= 80 ? 'good' : v >= 60 ? 'mid' : 'bad'
  const label = value == null ? 'Nessun dato' : v >= 80 ? 'Comfort ottimo' : v >= 60 ? 'Comfort buono' : 'Comfort basso'
  return (
    <div className={`comfort-gauge ${tone}`}>
      <svg viewBox="0 0 120 120">
        <circle className="cg-track" cx="60" cy="60" r={r} />
        <circle className="cg-fill" cx="60" cy="60" r={r}
          strokeDasharray={c} strokeDashoffset={off} transform="rotate(-90 60 60)" />
      </svg>
      <div className="cg-center">
        <strong>{value == null ? '—' : `${v}%`}</strong>
        <span>{label}</span>
      </div>
    </div>
  )
}

function Sparkline({ points }) {
  const W = 240, H = 48
  const vals = points.filter((v) => v != null)
  if (vals.length < 2) return null
  const min = Math.min(...vals), max = Math.max(...vals), span = Math.max(0.5, max - min)
  const step = W / (points.length - 1)
  const coords = points.map((v, i) => [i * step, v == null ? H / 2 : H - 6 - ((v - min) / span) * (H - 16)])
  const d = coords.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      <path className="spark-line" d={d} />
      {coords.map((p, i) => <circle key={i} className="spark-dot" cx={p[0]} cy={p[1]} r="2.4" />)}
    </svg>
  )
}

function WeatherCard({ weather }) {
  const forecast = (weather?.forecast || []).slice(0, 6)
  const uvLabel = (u) => u == null ? '—' : u < 3 ? 'Basso' : u < 6 ? 'Moderato' : u < 8 ? 'Alto' : u < 11 ? 'Molto alto' : 'Estremo'
  const windLabel = (w) => w == null ? '—' : w < 12 ? 'Leggero' : w < 20 ? 'Moderato' : w < 39 ? 'Forte' : 'Burrasca'
  const humLabel = (h) => h == null ? '—' : h < 40 ? 'Bassa' : h <= 65 ? 'Buona' : 'Alta'
  const stats = [
    { icon: mdiWhiteBalanceSunny, label: 'UV', value: weather?.uv_index != null ? `${weather.uv_index}` : '—', sub: uvLabel(weather?.uv_index) },
    { icon: mdiWeatherPouring, label: 'Pioggia', value: weather?.precipitation_probability != null ? `${weather.precipitation_probability}%` : '—', sub: 'Probabilità' },
    { icon: mdiWeatherWindy, label: 'Vento', value: weather?.wind_speed != null ? `${weather.wind_speed} km/h` : '—', sub: windLabel(weather?.wind_speed) },
    { icon: mdiWaterOutline, label: 'Umidità est.', value: weather?.humidity != null ? `${Math.round(weather.humidity)}%` : '—', sub: humLabel(weather?.humidity) },
  ]
  return (
    <div className="card wx-card">
      <div className="wc-head">
        <h3>Meteo esterno</h3>
        {weather?.location && <span className="wc-loc">{weather.location}</span>}
      </div>
      <div className="wc-now">
        <div className="wc-left">
          <div className="wc-now-top">
            <span className="wc-ic"><Icon path={mdiWeatherPartlyCloudy} size={2.3} /></span>
            <div className={`wc-temp ${weather?.temperature == null ? 'empty' : ''}`}>
              <strong>{weather?.temperature != null ? `${weather.temperature.toFixed(1)}°` : '—'}</strong>
              <span>{weather?.description || '—'}</span>
            </div>
          </div>
          {weather?.apparent_temperature != null && <em className="wc-feels">Percepita {Math.round(weather.apparent_temperature)}°</em>}
        </div>
        <div className="wc-chart">
          <div className="wc-hours">
            {forecast.map((p) => <span key={p.time}>{(p.time || '').slice(11, 16)}</span>)}
          </div>
          <Sparkline points={forecast.map((p) => p.temperature)} />
          <div className="wc-temps">
            {forecast.map((p) => <span key={p.time}>{p.temperature != null ? `${Math.round(p.temperature)}°` : '—'}</span>)}
          </div>
        </div>
      </div>
      <div className="wc-stats">
        {stats.map((s) => (
          <div key={s.label} className="wc-stat">
            <span className="wc-st-ic"><Icon path={s.icon} size={0.9} /></span>
            <div className="wc-st-txt">
              <span>{s.label}</span>
              <strong>{s.value}</strong>
              <em>{s.sub}</em>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function StatRow({ rooms, status, now, energy }) {
  const activeAcs = rooms.filter((r) => r.ac?.reachable && r.ac.power === 'On').length
  const sensorRooms = rooms.filter((r) => r.has_sensor).length
  const sensorsOk = rooms.filter((r) => r.has_sensor && sensorFresh(r.last_reading, now)).length
  const nominal = status.panasonic && status.dirigera && sensorsOk === sensorRooms
  const people = Array.isArray(status.presence_people) ? status.presence_people.length : 0
  const tempRooms = rooms.filter((r) => r.temperature != null)
  const avgTemp = tempRooms.length ? tempRooms.reduce((s, r) => s + r.temperature, 0) / tempRooms.length : null
  const cards = [
    { tint: 'blue', icon: mdiAccountGroup, label: 'Presenza', value: `${people} ${people === 1 ? 'persona' : 'persone'}`, sub: status.presence_home ? 'In casa' : 'Fuori' },
    { tint: 'blue', icon: mdiSnowflake, label: 'Clima', value: `${activeAcs}/${rooms.length} attivi`, sub: avgTemp != null ? `${avgTemp.toFixed(1)}° media` : '—' },
    { tint: 'amber', icon: mdiLightningBolt, label: 'Energia clima oggi', value: `${(energy?.today_kwh || 0).toFixed(2)} kWh`, sub: energy?.today_cost != null ? `Costo stimato: ${energy.today_cost.toFixed(2)} €` : '' },
    { tint: nominal ? 'green' : 'amber', icon: nominal ? mdiShieldCheck : mdiAlertCircleOutline, label: 'Stato sistema', value: nominal ? 'Tutto OK' : 'Attenzione', sub: nominal ? 'Nessun allarme' : 'Verifica impianti' },
  ]
  return (
    <div className="stat-row">
      {cards.map((c) => (
        <div key={c.label} className="card stat-card">
          <span className={`sc-ic ${c.tint}`}><Icon path={c.icon} size={1} /></span>
          <div className="sc-body">
            <span className="sc-label">{c.label}</span>
            <strong className="sc-value">{c.value}</strong>
            <span className="sc-sub">{c.sub}</span>
          </div>
        </div>
      ))}
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

function AlertPanel({ rooms, status, now, logs }) {
  const last = (logs || [])[0]
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
        <h3>Alert e notifiche</h3>
        <span>{alerts.length ? `${alerts.length} da verificare` : 'tutto regolare'}</span>
      </div>
      {alerts.length === 0 ? (
        <div className="all-clear">
          <Icon path={mdiCheckCircle} size={1.35} />
          <strong>Nessuna anomalia</strong>
        </div>
      ) : (
        <div className="alert-list">
          {alerts.slice(0, 3).map((a) => (
            <div key={`${a.title}-${a.text}`} className={a.tone}>
              <Icon path={a.icon} size={0.75} />
              <span><strong>{a.title}</strong><em>{a.text}</em></span>
            </div>
          ))}
        </div>
      )}
      {last && (
        <div className="alert-last">
          <span className="al-label">Ultima automazione</span>
          <div className="al-row">
            <span><strong>{last.action_taken || last.rule_matched || 'evento'}</strong><em>{last.room_name}</em></span>
            <b>{(last.timestamp || '').slice(11, 16)}</b>
          </div>
        </div>
      )}
    </div>
  )
}

const MESI_COMPACT = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic']

function EnergyOpsCard({ energy }) {
  // Consumo Panasonic = UNICO d'impianto (non per-AC). Grafico giornaliero del mese.
  const days = energy?.days || []
  const max = days.reduce((m, d) => Math.max(m, d.kwh || 0), 0) || 1
  const monthName = days.length ? MESI_COMPACT[parseInt(days[0].day.slice(4, 6), 10) - 1] : ''
  const n = new Date()
  const todayStr = `${n.getFullYear()}${String(n.getMonth() + 1).padStart(2, '0')}${String(n.getDate()).padStart(2, '0')}`

  return (
    <div className="card energy-ops-card">
      <div className="home-rooms-head">
        <h3>Consumo impianto</h3>
        <span>{monthName ? `${monthName} ${n.getFullYear()}` : ''}</span>
      </div>
      <div className="energy-totals">
        <div><strong>{(energy?.today_kwh || 0).toFixed(2)}</strong><em>kWh oggi</em></div>
        <div><strong>{(energy?.month_kwh || 0).toFixed(1)}</strong><em>kWh nel mese</em></div>
        <div><strong>{energy?.month_cost != null ? `${energy.month_cost.toFixed(2)}€` : '—'}</strong><em>costo mese</em></div>
      </div>
      <div className="energy-chart">
        {days.length === 0 && <div className="energy-empty">Nessun dato</div>}
        {days.map((d) => {
          const h = Math.max(4, ((d.kwh || 0) / max) * 100)
          const dd = d.day.slice(6)
          return (
            <div key={d.day} className={`ec-bar ${d.day === todayStr ? 'today' : ''}`}
              title={`${dd}/${d.day.slice(4, 6)}: ${d.kwh} kWh${d.cost != null ? ` · ${d.cost.toFixed(2)}€` : ''}`}>
              <i style={{ height: `${h}%` }} />
              <span>{dd}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function TimelineCard({ logs }) {
  const items = (logs || []).slice(0, 5)
  const meta = (l) => {
    const t = `${l.action_taken || ''} ${l.rule_matched || ''}`.toLowerCase()
    if (t.includes('cool') || t.includes('heat') || t.includes('dry') || t.includes('ac') || t.includes('powerful') || t.includes('quiet')) return { icon: mdiSnowflake, tone: 'blue', cat: 'Automazione' }
    if (t.includes('luce') || t.includes('light')) return { icon: mdiLightbulbVariant, tone: 'amber', cat: 'Luci' }
    if (t.includes('sensor')) return { icon: mdiAccessPoint, tone: 'blue', cat: 'Sensore' }
    if (t.includes('caldaia') || t.includes('boiler')) return { icon: mdiRadiator, tone: 'red', cat: 'Caldaia' }
    return { icon: mdiBrain, tone: 'green', cat: 'Sistema' }
  }
  return (
    <div className="card timeline-card">
      <div className="home-rooms-head">
        <h3>Eventi recenti</h3>
        <span className="card-link">Vedi tutti</span>
      </div>
      {items.length === 0 ? (
        <div className="timeline-empty">Nessun evento recente</div>
      ) : (
        <div className="ev-list">
          {items.map((l) => {
            const m = meta(l)
            return (
              <div key={l.id} className="ev-row">
                <span className={`ev-ic ${m.tone}`}><Icon path={m.icon} size={0.62} /></span>
                <div className="ev-txt">
                  <strong>{l.action_taken || l.rule_matched || 'Evento'}</strong>
                  <em>{l.room_name} · {m.cat}</em>
                </div>
                <b>{(l.timestamp || '').slice(11, 16)}</b>
              </div>
            )
          })}
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

function HomeStateCard({ rooms, lights, status, overview }) {
  const activeAcs = rooms.filter((r) => r.ac?.reachable && r.ac.power === 'On').length
  const tempRooms = rooms.filter((r) => r.temperature != null)
  const avgTemp = tempRooms.length ? tempRooms.reduce((s, r) => s + r.temperature, 0) / tempRooms.length : null
  const humRooms = rooms.filter((r) => r.humidity != null)
  const avgHum = humRooms.length ? humRooms.reduce((s, r) => s + r.humidity, 0) / humRooms.length : null
  const allLights = Object.values(lights || {}).flat()
  const lightsOn = allLights.filter((l) => l.is_on).length
  const people = Array.isArray(status.presence_people) ? status.presence_people.length : 0
  const list = [
    { icon: mdiThermometer, label: 'Temperatura media', value: avgTemp != null ? `${avgTemp.toFixed(1)}°` : '—' },
    { icon: mdiWaterPercent, label: 'Umidità media', value: avgHum != null ? `${Math.round(avgHum)}%` : '—' },
    { icon: mdiAccountGroup, label: 'Persone presenti', value: people },
    { icon: mdiSnowflake, label: 'Climatizzatori attivi', value: `${activeAcs}/${rooms.length}` },
    { icon: mdiLightbulbVariant, label: 'Luci accese', value: `${lightsOn}/${allLights.length}` },
  ]
  return (
    <div className="card hs-card">
      <h3>Stato della casa</h3>
      <div className="hs-body">
        <ComfortGauge value={overview?.comfort_home} />
        <div className="hs-list">
          {list.map((r) => (
            <div key={r.label}>
              <span className="hs-ic"><Icon path={r.icon} size={0.72} /></span>
              <div><strong>{r.value}</strong><span>{r.label}</span></div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function HomeEngineCard({ overview }) {
  const he = overview?.home_engine || {}
  const stable = he.stable
  return (
    <div className="card he-card">
      <div className="he-head">
        <span className="he-ic"><Icon path={mdiBrain} size={0.95} /></span>
        <h3>Home Engine</h3>
      </div>
      <div className="he-status">
        <span className={`he-badge ${stable ? 'good' : 'warn'}`}>
          <Icon path={stable ? mdiCheckCircle : mdiAlertCircleOutline} size={0.95} />
          {stable ? 'Casa stabile' : 'Da verificare'}
        </span>
        {he.comfort != null && <span className="he-comfort">Comfort {he.comfort}%</span>}
      </div>
      <div className="he-grid">
        <div><span>Consumo previsto oggi</span><strong>{he.projected_kwh_today != null ? `${he.projected_kwh_today} kWh` : '—'}</strong></div>
        <div><span>Prossima decisione</span><strong>{he.next_decision || '—'}</strong></div>
      </div>
      <div className="he-suggest">
        <span className="he-sg-label">Suggerimento</span>
        <p>{he.suggestion || '—'}</p>
        <em className="he-action">{(!he.next_decision || he.next_decision === 'Nessuna') ? 'Nessuna azione richiesta.' : he.next_decision}</em>
      </div>
      <span className="card-link he-more">Vedi dettagli →</span>
      <svg className="he-illu" viewBox="0 0 130 100" aria-hidden="true">
        <rect x="30" y="54" width="5" height="26" rx="2" fill="#b9a3e6" />
        <circle cx="32" cy="46" r="16" fill="#cdbcf0" />
        <circle cx="23" cy="52" r="9" fill="#d7caf4" />
        <path d="M61 52 L88 29 L115 52 Z" fill="#a98fd9" />
        <rect x="67" y="52" width="42" height="30" rx="2" fill="#c6b4ec" />
        <rect x="84" y="62" width="12" height="20" rx="1.5" fill="#a98fd9" />
        <rect x="71" y="58" width="9" height="9" rx="1.5" fill="#ece5fb" />
        <rect x="16" y="80" width="102" height="4" rx="2" fill="#ddd2f5" />
      </svg>
    </div>
  )
}

function EnergyClimateCard({ energy }) {
  const today = energy?.today_kwh ?? 0
  const delta = energy?.delta_pct
  const days = (energy?.days || []).slice(-14)
  const max = days.reduce((m, d) => Math.max(m, d.kwh || 0), 0) || 1
  const n = new Date()
  const todayStr = `${n.getFullYear()}${String(n.getMonth() + 1).padStart(2, '0')}${String(n.getDate()).padStart(2, '0')}`
  const tiles = [
    { label: 'Costo stimato', value: energy?.today_cost != null ? `${energy.today_cost.toFixed(2)}€` : '—' },
    { label: 'Previsione giornata', value: energy?.projected_today_kwh != null ? `${energy.projected_today_kwh} kWh` : '—' },
    { label: 'Media 7 giorni', value: energy?.avg7_kwh != null ? `${energy.avg7_kwh} kWh` : '—' },
  ]
  return (
    <div className="card ecl-card">
      <h3>Energia climatizzazione</h3>
      <div className="ecl-big">
        <strong>{today.toFixed(2)} kWh</strong>
        {delta != null && <span className={`ecl-delta ${delta >= 0 ? 'up' : 'down'}`}>{delta >= 0 ? '+' : ''}{delta}% rispetto a ieri</span>}
      </div>
      <div className="ecl-spark">
        {days.map((d) => {
          const h = Math.max(6, ((d.kwh || 0) / max) * 100)
          return <i key={d.day} className={d.day === todayStr ? 'today' : ''} style={{ height: `${h}%` }} title={`${d.day.slice(6)}: ${d.kwh} kWh`} />
        })}
      </div>
      <div className="ecl-tiles">
        {tiles.map((t) => <div key={t.label}><strong>{t.value}</strong><span>{t.label}</span></div>)}
      </div>
    </div>
  )
}

function SystemsHealthCard({ overview, boiler }) {
  const systems = [...(overview?.systems || [])]
  const iconFor = {
    home_engine: mdiBrain, panasonic: mdiAirConditioner, dirigera: mdiLightbulbVariant,
    sensori: mdiHomeThermometerOutline, wifi: mdiWifi, caldaia: mdiRadiator,
  }
  // La caldaia (relè Sonoff in LAN) come impianto, se configurata.
  if (boiler?.enabled) {
    systems.splice(3, 0, {
      key: 'caldaia', name: 'Caldaia', online: boiler.on != null,
      detail: boiler.on != null ? (boiler.on ? 'accesa' : 'spenta') : 'non vista',
    })
  }
  return (
    <div className="card sh-card">
      <h3>Stato impianti</h3>
      <div className="sh-list">
        {systems.length === 0 && <div className="sh-empty">Nessun dato</div>}
        {systems.map((s) => (
          <div key={s.key} className={s.online ? 'ok' : 'warn'}>
            <Icon path={iconFor[s.key] || mdiAccessPoint} size={0.72} />
            <span className="sh-name">{s.name}</span>
            <span className="sh-status">{s.online ? 'Online' : 'Offline'}</span>
            <em className="sh-detail">{s.detail}</em>
          </div>
        ))}
      </div>
    </div>
  )
}

function RoomsStrip({ rooms, lights, boiler, onSelectRoom, now }) {
  const norm = (s) => (s || '').trim()
  const acByName = Object.fromEntries(rooms.map((r) => [norm(r.name), r]))
  const boilerRoom = boiler?.enabled ? norm(boiler.room) : null
  const names = [...new Set([
    ...rooms.map((r) => norm(r.name)),
    ...Object.keys(lights || {}).map(norm),
    ...(boilerRoom ? [boilerRoom] : []),
  ])]
  return (
    <div className="card rooms-strip-card">
      <div className="home-rooms-head">
        <h3>Stanze</h3>
        <span>tocca per controllare</span>
      </div>
      <div className="rooms-strip">
        {names.map((name) => {
          const r = acByName[name] || {}
          const lk = Object.keys(lights || {}).find((k) => norm(k) === name)
          const roomLights = lk ? lights[lk] : []
          const lightsOn = roomLights.filter((l) => l.is_on).length
          const acOn = r.ac?.power === 'On'
          const isBoiler = name === boilerRoom
          return (
            <button key={name} className="room-chip" onClick={() => onSelectRoom(name)}>
              <div className="rc-head">
                <strong>{name}</strong>
                <Icon path={mdiChevronRight} size={0.7} />
              </div>
              <div className="rc-temp">{r.temperature != null ? `${r.temperature.toFixed(1)}°` : '—.-°'}</div>
              <div className="rc-meta">
                <span><Icon path={mdiWaterPercent} size={0.6} />{r.humidity != null ? `${Math.round(r.humidity)}%` : '—'}</span>
              </div>
              <div className="rc-badges">
                {r.ac && <span className={`rc-badge ${acOn ? 'on' : ''}`}><Icon path={mdiSnowflake} size={0.55} />{acOn ? 'AC ON' : 'AC OFF'}</span>}
                {roomLights.length > 0 && (
                  <span className={`rc-badge ${lightsOn ? 'on amber' : ''}`}><Icon path={mdiLightbulbVariant} size={0.55} />{lightsOn ? 'Luce ON' : 'Luce OFF'}</span>
                )}
                {isBoiler && <span className={`rc-badge ${boiler?.on ? 'on red' : ''}`}><Icon path={mdiRadiator} size={0.55} />{boiler?.on ? 'Caldaia ON' : 'Caldaia OFF'}</span>}
              </div>
              <em className="rc-age">{r.last_reading ? `Agg. ${relTime(r.last_reading, now)}` : (isBoiler ? 'relè LAN' : 'solo luci')}</em>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function HomeOverview({ rooms, lights, status, weather, now, onSelectRoom, logs, energy, boiler, overview }) {
  return (
    <div className="home2">
      {/* Riga 1 — stat card */}
      <StatRow rooms={rooms} status={status} now={now} energy={energy} />

      {/* Riga 2 — Stato della casa | Meteo | Home Engine */}
      <div className="home2-row r3">
        <HomeStateCard rooms={rooms} lights={lights} status={status} overview={overview} />
        <WeatherCard weather={weather} />
        <HomeEngineCard overview={overview} />
      </div>

      {/* Riga 4 — Energia | Impianti | Alert | Eventi */}
      <div className="home2-row r4">
        <EnergyClimateCard energy={energy} />
        <SystemsHealthCard overview={overview} boiler={boiler} />
        <AlertPanel rooms={rooms} status={status} now={now} logs={logs} />
        <TimelineCard logs={logs} />
      </div>

      {/* Riga 5 — Consumo impianto (totale reale) | Stanze */}
      <div className="home2-row r2">
        <EnergyOpsCard energy={energy} />
        <RoomsStrip rooms={rooms} lights={lights} boiler={boiler} onSelectRoom={onSelectRoom} now={now} />
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
  const [energy, setEnergy] = useState({})
  const [overview, setOverview] = useState({})
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
      const [r, s, l, w, b, e, o] = await Promise.all([
        api.getRooms(), api.getStatus(), api.getLights(),
        api.getWeather().catch(() => ({})), api.getBoiler().catch(() => ({})),
        api.getEnergyMonth().catch(() => ({})), api.getOverview().catch(() => ({})),
      ])
      setRooms(r); setStatus(s); setLights(l); setWeather(w); setBoiler(b); setEnergy(e); setOverview(o)
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
            energy={energy}
            boiler={boiler}
            overview={overview}
            onSelectRoom={(room) => { setActiveRoom(room); setSection('room') }}
          />
        )}

        {section === 'room' && (
          <RoomView
            room={acOfRoom || { name: curRoom, ac: null }}
            status={status}
            overview={overview}
            energy={energy}
            now={now}
            dateStr={dateStr}
            hh={hh}
            mm={mm}
            season={sm}
            onAction={refresh}
          />
        )}

        {section === 'history' && <main className="content"><History rooms={rooms} /></main>}
        {section === 'config' && <main className="content"><ConfigPanel config={config} onSaved={refreshConfig} /></main>}
        {section === 'logs' && <main className="content"><EventLog logs={logs} onRefresh={refreshLogs} /></main>}
      </div>
    </div>
  )
}
