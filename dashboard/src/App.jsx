import { useEffect, useState, useCallback } from 'react'
import Icon from '@mdi/react'
import {
  mdiHomeVariant, mdiChartBox, mdiTuneVerticalVariant, mdiBellOutline, mdiCog,
  mdiMagnify, mdiWeatherSunny, mdiWeatherNight, mdiPlus,
  mdiSnowflake, mdiFire, mdiLeaf, mdiThermometer, mdiWaterPercent,
  mdiLightningBolt, mdiHomeAccount, mdiHomeExportOutline, mdiLightbulbVariant,
} from '@mdi/js'
import { api } from './api.js'
import Thermostat from './components/Thermostat.jsx'
import RoomLights from './components/RoomLights.jsx'
import History from './components/History.jsx'
import ConfigPanel from './components/ConfigPanel.jsx'
import EventLog from './components/EventLog.jsx'

const POLL_MS = 30000
const MESI = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']
const seasonMeta = (s) => ({
  raffrescamento: { icon: mdiSnowflake, label: 'Raffrescamento', tint: '#4ab4d8' },
  riscaldamento: { icon: mdiFire, label: 'Riscaldamento', tint: '#e8893f' },
  mezza_stagione: { icon: mdiLeaf, label: 'Mezza stagione', tint: '#43c2b4' },
}[s] || { icon: mdiThermometer, label: s || '—', tint: '#71809a' })

export default function App() {
  const [rooms, setRooms] = useState([])        // stanze con AC (dal climate)
  const [lights, setLights] = useState({})      // {stanza: [luci]}
  const [status, setStatus] = useState({})
  const [config, setConfig] = useState(null)
  const [logs, setLogs] = useState([])
  const [section, setSection] = useState('home')  // sidebar
  const [activeRoom, setActiveRoom] = useState(null)
  const [now, setNow] = useState(new Date())
  const [theme, setTheme] = useState('light')
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const [r, s, l] = await Promise.all([api.getRooms(), api.getStatus(), api.getLights()])
      setRooms(r); setStatus(s); setLights(l); setError(null)
    } catch (e) { setError(e.message) }
  }, [])
  const refreshLogs = useCallback(async () => { try { setLogs(await api.getLogs(100)) } catch { /**/ } }, [])
  const refreshConfig = useCallback(async () => { try { setConfig(await api.getConfig()) } catch { /**/ } }, [])

  useEffect(() => {
    refresh(); refreshConfig(); refreshLogs()
    const id = setInterval(() => { refresh(); if (section === 'logs') refreshLogs() }, POLL_MS)
    const clock = setInterval(() => setNow(new Date()), 30000)
    return () => { clearInterval(id); clearInterval(clock) }
  }, [refresh, refreshConfig, refreshLogs, section])

  useEffect(() => { document.documentElement.dataset.theme = theme }, [theme])

  // Elenco unificato di TUTTE le stanze (con AC e/o luci).
  // Normalizzo i nomi (trim) per evitare doppioni tipo "Camera da letto " (spazio).
  const norm = (s) => (s || '').trim()
  const acRoomNames = rooms.map((r) => norm(r.name))
  const lightRoomNames = Object.keys(lights).map(norm)
  const allRooms = [...new Set([...acRoomNames, ...lightRoomNames])]
  // stanza attiva di default: la prima con AC, o la prima in assoluto
  const curRoom = activeRoom || acRoomNames[0] || allRooms[0]
  const acOfRoom = rooms.find((r) => norm(r.name) === curRoom)
  // le chiavi luci possono avere spazi finali: cerca con trim
  const lightsKey = Object.keys(lights).find((k) => norm(k) === curRoom)
  const lightsOfRoom = lightsKey ? lights[lightsKey] : []

  const sm = seasonMeta(status.season)
  const hh = String(now.getHours()).padStart(2, '0')
  const mm = String(now.getMinutes()).padStart(2, '0')
  const dateStr = `${now.getDate()} ${MESI[now.getMonth()]} ${now.getFullYear()}`
  const totalEnergy = rooms.reduce((s, r) => s + (r.ac?.reachable ? (r.ac.energy_today_kwh || 0) : 0), 0)

  return (
    <div className="layout">
      {/* ===== SIDEBAR = STANZE (navigazione primaria) ===== */}
      <aside className="sidebar">
        <div className="side-logo"><Icon path={mdiHomeVariant} size={1} /></div>

        <nav className="side-rooms">
          {allRooms.map((r) => {
            const hasAc = acRoomNames.includes(r)
            const lk = Object.keys(lights).find((k) => norm(k) === r)
            const nLights = lk ? lights[lk].length : 0
            const active = section === 'home' && r === curRoom
            return (
              <button key={r} className={active ? 'on' : ''}
                onClick={() => { setSection('home'); setActiveRoom(r) }} title={r}>
                <Icon path={hasAc ? mdiThermometer : mdiLightbulbVariant} size={0.85} />
                <span className="sr-name">{r}</span>
                <span className="sr-meta">{hasAc ? 'AC' : ''}{hasAc && nLights ? '·' : ''}{nLights ? `${nLights}💡` : ''}</span>
              </button>
            )
          })}
          <button className="sr-add" onClick={() => setSection('config')} title="Aggiungi/Configura">
            <Icon path={mdiPlus} size={0.85} /><span className="sr-name">Stanza</span>
          </button>
        </nav>

        {/* sezioni di sistema in fondo */}
        <div className="side-sys">
          <button className={section === 'history' ? 'on' : ''} onClick={() => setSection('history')} title="Consumi/Storico"><Icon path={mdiChartBox} size={0.9} /></button>
          <button className={section === 'config' ? 'on' : ''} onClick={() => setSection('config')} title="Regole"><Icon path={mdiTuneVerticalVariant} size={0.9} /></button>
          <button className={section === 'logs' ? 'on' : ''} onClick={() => { setSection('logs'); refreshLogs() }} title="Attività"><Icon path={mdiBellOutline} size={0.9} /></button>
        </div>
      </aside>

      {/* ===== MAIN ===== */}
      <div className="main">
        {/* HEADER */}
        <header className="topbar">
          <div className="welcome">
            <div className="avatar"><Icon path={mdiHomeAccount} size={1} /></div>
            <div>
              <div className="welcome-hi">Casa</div>
              <div className="welcome-sub">{dateStr} · {hh}:{mm}</div>
            </div>
          </div>
          <div className="searchbar">
            <Icon path={mdiMagnify} size={0.8} />
            <input placeholder="Cerca un dispositivo…" />
          </div>
          <div className="theme-toggle">
            <button className={theme === 'light' ? 'on' : ''} onClick={() => setTheme('light')}><Icon path={mdiWeatherSunny} size={0.7} /> Light</button>
            <button className={theme === 'dark' ? 'on' : ''} onClick={() => setTheme('dark')}><Icon path={mdiWeatherNight} size={0.7} /> Dark</button>
          </div>
          <button className="bell"><Icon path={mdiBellOutline} size={0.9} /></button>
        </header>

        {error && <div className="error-banner">Errore: {error}</div>}

        {section === 'home' && (
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
              <RoomLights room={curRoom} lights={lightsOfRoom} onChange={refresh} />

              {acOfRoom && acOfRoom.humidity != null && (
                <div className="card mini-stat">
                  <span className="card-ic" style={{ color: '#4ea1ff' }}><Icon path={mdiWaterPercent} size={0.8} /></span>
                  <div><div className="ms-val">{acOfRoom.humidity.toFixed(0)}%</div><div className="ms-lbl">umidità {curRoom}</div></div>
                </div>
              )}
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
