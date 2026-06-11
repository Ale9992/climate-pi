import { useState } from 'react'
import { api } from '../api.js'

const MODES = ['Cool', 'Heat', 'Dry', 'Fan', 'Auto']
const FANS = ['Auto', 'Low', 'LowMid', 'Mid', 'HighMid', 'High']

function fmtRemaining(sec) {
  if (sec <= 0) return ''
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}m ${s.toString().padStart(2, '0')}s`
}

export default function RoomCard({ room, onAction }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({
    power: true, mode: 'Cool', temperature: 24, fan_speed: 'Auto', minutes: 60,
  })

  const ac = room.ac
  const acOn = ac && ac.power === 'On'

  const submitOverride = async () => {
    setBusy(true)
    try {
      await api.setOverride(room.name, {
        power: form.power,
        mode: form.mode,
        temperature: form.power ? Number(form.temperature) : null,
        fan_speed: form.fan_speed,
        minutes: Number(form.minutes),
      })
      setOpen(false)
      onAction()
    } catch (e) { alert('Errore override: ' + e.message) }
    finally { setBusy(false) }
  }

  const clearOverride = async () => {
    setBusy(true)
    try { await api.clearOverride(room.name); onAction() }
    catch (e) { alert('Errore: ' + e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className={`card ${acOn ? 'on' : 'off'}`}>
      <div className="card-head">
        <h2>{room.name}</h2>
        {room.override_active && (
          <span className="badge override">
            👤 Override {fmtRemaining(room.override_remaining_seconds)}
          </span>
        )}
      </div>

      <div className="readings">
        <div className="reading">
          <span className={`val ${room.temperature == null ? 'empty' : ''}`}>
            {room.temperature != null ? room.temperature.toFixed(1) : '—'}
          </span>
          {room.temperature != null && <span className="unit">°</span>}
          <span className="lbl">Temperatura</span>
        </div>
        <div className="reading secondary">
          <span className={`val ${room.humidity == null ? 'empty' : ''}`}>
            {room.humidity != null ? Math.round(room.humidity) : '—'}
          </span>
          {room.humidity != null && <span className="unit">%</span>}
          <span className="lbl">Umidità</span>
        </div>
      </div>

      {!room.has_sensor && (
        <p className="note-nosensor">Nessun sensore in questa stanza · automazione non attiva</p>
      )}

      <div className="ac-status">
        {ac && ac.reachable ? (
          <>
            <span className={`pill ${acOn ? 'on' : 'off'}`}>{acOn ? '● Acceso' : '○ Spento'}</span>
            {acOn && <span className="ac-detail">{ac.mode} · {ac.target_temperature}° · {ac.fan_speed}</span>}
          </>
        ) : (
          <span className="pill ko">AC non raggiungibile</span>
        )}
      </div>

      {ac && ac.reachable && ac.energy_today_kwh != null && (
        <div className="energy">
          <span className="energy-val">{ac.energy_today_kwh.toFixed(2)}</span>
          <span className="energy-unit">kWh oggi</span>
          {(ac.energy_cooling_kwh > 0 || ac.energy_heating_kwh > 0) && (
            <span className="energy-split muted small">
              {ac.energy_cooling_kwh > 0 && `❄️ ${ac.energy_cooling_kwh.toFixed(2)} `}
              {ac.energy_heating_kwh > 0 && `🔥 ${ac.energy_heating_kwh.toFixed(2)}`}
            </span>
          )}
        </div>
      )}

      <div className="actions">
        {room.override_active ? (
          <button className="btn ghost" disabled={busy} onClick={clearOverride}>Torna automatico</button>
        ) : (
          <button className="btn" disabled={busy} onClick={() => setOpen(!open)}>
            {open ? 'Annulla' : 'Override manuale'}
          </button>
        )}
      </div>

      {open && (
        <div className="override-form">
          <label className="row">
            <span>Accensione</span>
            <input type="checkbox" checked={form.power}
              onChange={(e) => setForm({ ...form, power: e.target.checked })} />
          </label>
          {form.power && <>
            <label className="row">
              <span>Modalità</span>
              <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
                {MODES.map((m) => <option key={m}>{m}</option>)}
              </select>
            </label>
            <label className="row">
              <span>Temperatura</span>
              <input type="number" min="16" max="30" value={form.temperature}
                onChange={(e) => setForm({ ...form, temperature: e.target.value })} />
            </label>
            <label className="row">
              <span>Ventola</span>
              <select value={form.fan_speed} onChange={(e) => setForm({ ...form, fan_speed: e.target.value })}>
                {FANS.map((f) => <option key={f}>{f}</option>)}
              </select>
            </label>
          </>}
          <label className="row">
            <span>Durata (min)</span>
            <input type="number" min="1" max="1440" value={form.minutes}
              onChange={(e) => setForm({ ...form, minutes: e.target.value })} />
          </label>
          <button className="btn primary" disabled={busy} onClick={submitOverride}>
            {busy ? 'Invio…' : 'Applica'}
          </button>
        </div>
      )}
    </div>
  )
}
