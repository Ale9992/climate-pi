import { useState, useEffect } from 'react'
import { api } from '../api.js'
import Sparkline from './Sparkline.jsx'

const RANGES = [
  { h: 6, label: '6h' },
  { h: 24, label: '24h' },
  { h: 72, label: '3g' },
  { h: 168, label: '7g' },
]
const ACCENT = '#5b8def'

// Riquadro storico di una singola stanza (grafico + statistiche).
function RoomHistory({ room, hours }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    let alive = true
    setData(null)
    api.getHistory(room.name, hours)
      .then((h) => { if (alive) setData(h || []) })
      .catch(() => { if (alive) setData([]) })
    return () => { alive = false }
  }, [room.name, hours])

  const valid = (data || []).filter((p) => p.temperature != null)
  const lastT = valid.length ? valid[valid.length - 1].temperature : null
  const lastH = valid.length ? valid[valid.length - 1].humidity : null

  return (
    <section className="hist-card">
      <div className="hist-card-head">
        <h3>{room.name}</h3>
        {lastT != null && (
          <span className="hist-now">
            {lastT.toFixed(1)}°{lastH != null && <span className="muted"> · {Math.round(lastH)}%</span>}
          </span>
        )}
      </div>
      {data == null
        ? <div className="spark-empty">Caricamento…</div>
        : <Sparkline points={data} accent={ACCENT} />}
    </section>
  )
}

export default function History({ rooms }) {
  const [hours, setHours] = useState(24)
  const withSensor = (rooms || []).filter((r) => r.has_sensor)

  return (
    <div className="history">
      <div className="hist-head">
        <h2>Storico ambienti</h2>
        <div className="range-pick">
          {RANGES.map((r) => (
            <button key={r.h} className={hours === r.h ? 'active' : ''} onClick={() => setHours(r.h)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {withSensor.length === 0
        ? <p className="muted center">Nessuna stanza con sensore: storico non disponibile.</p>
        : withSensor.map((r) => <RoomHistory key={r.name} room={r} hours={hours} />)}

      <p className="hist-note muted small">
        Linea piena: temperatura · tratteggiata: umidità · solo stanze con sensore IKEA.
      </p>
    </div>
  )
}
