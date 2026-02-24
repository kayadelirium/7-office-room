import { useState } from 'react'
import { scanBLE, connectDevice } from '../api'
import { showToast } from '../toast'

function guessPreset(name) {
  const n = (name || '').toLowerCase()
  if (n.includes('elk') || n.includes('bledom') || n.includes('lotus')) return 'elk_bledom'
  if (n.includes('magic') || n.includes('zengge'))   return 'magic_home'
  if (n.includes('govee') || n.includes('h6'))       return 'govee'
  if (n.includes('triones') || n.includes('ledble')) return 'triones'
  return 'surplife'
}

export function useScan(connectedAddrs, onConnect) {
  const [timeout, setTimeout_] = useState(8)
  const [scanning, setScanning] = useState(false)
  const [results, setResults]   = useState([])
  const [presets, setPresets]   = useState({})

  async function scan() {
    setScanning(true)
    setResults([])
    try {
      const data = await scanBLE(timeout)
      setResults(data)
      const p = {}
      data.forEach((d, i) => { p[i] = guessPreset(d.name) })
      setPresets(p)
      showToast(`Найдено: ${data.length}`)
    } catch (e) { showToast(e.message, false) }
    finally { setScanning(false) }
  }

  async function connect(i) {
    const d = results[i]
    if (!d) return
    try {
      const r = await connectDevice(d.address, presets[i] || 'surplife')
      if (r.ok) { showToast('Подключено: ' + r.lamp.name); onConnect() }
      else showToast(r.error, false)
    } catch (e) { showToast(e.message, false) }
  }

  const connected = new Set(connectedAddrs)

  return { timeout, setTimeout_, scanning, results, presets, setPresets, scan, connect, connected }
}
