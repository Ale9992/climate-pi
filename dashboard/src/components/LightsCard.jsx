import { useState, useEffect, useCallback, useRef } from 'react'
import Icon from '@mdi/react'
import { mdiLightbulbVariant, mdiLightbulbVariantOutline, mdiLightbulbGroup } from '@mdi/js'
import { api } from '../api.js'

// Card "Smart Lighting" — luci IKEA reali, raggruppate per stanza.
export default function LightsCard() {
  const [rooms, setRooms] = useState({})
  const [open, setOpen] = useState(null)        // stanza espansa
  const sendTimer = useRef({})

  const load = useCallback(async () => {
    try { setRooms(await api.getLights()) } catch (e) { /* ignore */ }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [load])

  const roomNames = Object.keys(rooms)
  const allLights = roomNames.flatMap((r) => rooms[r])
  const onCount = allLights.filter((l) => l.is_on).length

  // toggle ottimistico di una luce
  const toggleLight = (lt) => {
    setRooms((prev) => {
      const next = structuredClone(prev)
      for (const r of Object.values(next))
        for (const l of r) if (l.id === lt.id) l.is_on = !l.is_on
      return next
    })
    api.setLight(lt.id, { on: !lt.is_on }).then(load).catch(load)
  }

  // dimmer (debounced)
  const dimLight = (lt, level) => {
    setRooms((prev) => {
      const next = structuredClone(prev)
      for (const r of Object.values(next))
        for (const l of r) if (l.id === lt.id) { l.level = level; l.is_on = true }
      return next
    })
    clearTimeout(sendTimer.current[lt.id])
    sendTimer.current[lt.id] = setTimeout(
      () => api.setLight(lt.id, { level }).then(load).catch(load), 400)
  }

  const toggleRoom = (room, on) => {
    setRooms((prev) => {
      const next = structuredClone(prev)
      next[room] = next[room].map((l) => ({ ...l, is_on: on }))
      return next
    })
    api.setRoomLights(room, { on }).then(load).catch(load)
  }

  return (
    <div className="card card-lights">
      <div className="card-head">
        <span className="card-ic light"><Icon path={mdiLightbulbGroup} size={0.85} /></span>
        <h3>Luci</h3>
        <span className="card-sub">{onCount}/{allLights.length} accese</span>
      </div>

      <div className="lights-rooms">
        {roomNames.length === 0 && <p className="muted small">Nessuna luce trovata.</p>}
        {roomNames.map((room) => {
          const lights = rooms[room]
          const anyOn = lights.some((l) => l.is_on)
          return (
            <div key={room} className="light-room">
              <button className="light-room-head" onClick={() => setOpen(open === room ? null : room)}>
                <Icon path={anyOn ? mdiLightbulbVariant : mdiLightbulbVariantOutline}
                  size={0.8} className={anyOn ? 'lr-on' : 'lr-off'} />
                <span className="lr-name">{room}</span>
                <span className="lr-count">{lights.filter((l) => l.is_on).length}/{lights.length}</span>
                <span className={`mini-switch ${anyOn ? 'on' : ''}`}
                  onClick={(e) => { e.stopPropagation(); toggleRoom(room, !anyOn) }}>
                  <span className="mini-knob" />
                </span>
              </button>
              {open === room && (
                <div className="light-list">
                  {lights.map((lt) => (
                    <div key={lt.id} className={`light-row ${lt.is_on ? 'on' : ''}`}>
                      <button className="light-name" onClick={() => toggleLight(lt)}>
                        <Icon path={lt.is_on ? mdiLightbulbVariant : mdiLightbulbVariantOutline} size={0.7} />
                        {lt.name}
                      </button>
                      {lt.supports_level && (
                        <input type="range" min="1" max="100" value={lt.level ?? 100}
                          disabled={!lt.is_on}
                          onChange={(e) => dimLight(lt, Number(e.target.value))}
                          className="light-dim" />
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
