import { useState, useEffect } from 'react'
import { api } from '../api.js'

const MODES = ['Cool', 'Heat', 'Dry', 'Fan', 'Auto']
const FANS = ['Auto', 'Low', 'LowMid', 'Mid', 'HighMid', 'High']

// Editor regole per singola stanza. Le regole sono valutate in ordine:
// scatta la prima la cui condizione e' soddisfatta.
function RoomRules({ room, onSaved }) {
  const [rules, setRules] = useState(room.rules || [])
  const [busy, setBusy] = useState(false)

  const upd = (i, path, value) => {
    const next = structuredClone(rules)
    const [grp, key] = path.split('.')
    if (value === '' || value == null) delete next[i][grp][key]
    else next[i][grp][key] = grp === 'condition' || key === 'temperature' ? Number(value) : value
    setRules(next)
  }

  const addRule = () =>
    setRules([...rules, { condition: { temp_gt: 26 }, action: { power: true, mode: 'Cool', temperature: 24, fan_speed: 'Auto' } }])
  const delRule = (i) => setRules(rules.filter((_, idx) => idx !== i))

  const save = async () => {
    setBusy(true)
    try { await api.updateRules(room.name, rules); onSaved && onSaved(); alert('Regole salvate.') }
    catch (e) { alert('Errore: ' + e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="cfg-room">
      <h3>{room.name}</h3>
      {rules.map((rule, i) => (
        <div className="rule" key={i}>
          <div className="rule-head">
            <span className="rule-idx">#{i}</span>
            <button className="btn ghost tiny" onClick={() => delRule(i)}>✕</button>
          </div>
          <div className="rule-grid">
            <fieldset>
              <legend>SE (AND)</legend>
              <label>T &gt; <input type="number" value={rule.condition.temp_gt ?? ''} onChange={(e) => upd(i, 'condition.temp_gt', e.target.value)} /></label>
              <label>T &lt; <input type="number" value={rule.condition.temp_lt ?? ''} onChange={(e) => upd(i, 'condition.temp_lt', e.target.value)} /></label>
              <label>RH &gt; <input type="number" value={rule.condition.humidity_gt ?? ''} onChange={(e) => upd(i, 'condition.humidity_gt', e.target.value)} /></label>
              <label>RH &lt; <input type="number" value={rule.condition.humidity_lt ?? ''} onChange={(e) => upd(i, 'condition.humidity_lt', e.target.value)} /></label>
            </fieldset>
            <fieldset>
              <legend>ALLORA</legend>
              <label>Modalità
                <select value={rule.action.mode ?? 'Cool'} onChange={(e) => upd(i, 'action.mode', e.target.value)}>
                  {MODES.map((m) => <option key={m}>{m}</option>)}
                </select>
              </label>
              <label>Temp <input type="number" value={rule.action.temperature ?? ''} onChange={(e) => upd(i, 'action.temperature', e.target.value)} /></label>
              <label>Ventola
                <select value={rule.action.fan_speed ?? 'Auto'} onChange={(e) => upd(i, 'action.fan_speed', e.target.value)}>
                  {FANS.map((f) => <option key={f}>{f}</option>)}
                </select>
              </label>
            </fieldset>
          </div>
        </div>
      ))}
      <div className="cfg-actions">
        <button className="btn ghost" onClick={addRule}>+ Aggiungi regola</button>
        <button className="btn primary" disabled={busy} onClick={save}>{busy ? 'Salvataggio…' : 'Salva regole'}</button>
      </div>
    </div>
  )
}

export default function ConfigPanel({ config, onSaved }) {
  const [forceOff, setForceOff] = useState('03:00')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (config?.schedule?.force_off_time) setForceOff(config.schedule.force_off_time)
  }, [config])

  if (!config) return <p className="muted">Caricamento configurazione…</p>

  const saveSchedule = async () => {
    setBusy(true)
    try { await api.updateSchedule(forceOff); onSaved && onSaved(); alert('Orario aggiornato.') }
    catch (e) { alert('Errore: ' + e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="config-panel">
      <section className="cfg-section">
        <h2>Spegnimento forzato</h2>
        <div className="row">
          <span>Ogni giorno alle</span>
          <input type="time" value={forceOff} onChange={(e) => setForceOff(e.target.value)} />
          <button className="btn primary" disabled={busy} onClick={saveSchedule}>Salva</button>
        </div>
      </section>

      <section className="cfg-section">
        <h2>Regole per stanza</h2>
        {config.rooms.map((room) => (
          <RoomRules key={room.name} room={room} onSaved={onSaved} />
        ))}
      </section>
    </div>
  )
}
