import { useState } from 'react'
import { speakerScan, speakerConnect, speakerDisconnect } from '../api'
import { showToast } from '../toast'

export function useSpeaker() {
  const [name, setName]         = useState('')
  const [scanning, setScanning] = useState(false)
  const [results, setResults]   = useState([])

  async function connect() {
    try {
      const r = await speakerConnect(name)
      showToast(r.message || (r.ok ? 'Подключено' : 'Ошибка'), r.ok)
    } catch (e) { showToast(e.message, false) }
  }

  async function disconnect() {
    try {
      const r = await speakerDisconnect()
      showToast(r.message || (r.ok ? 'Отключено' : 'Ошибка'), r.ok)
    } catch (e) { showToast(e.message, false) }
  }

  async function scan() {
    setScanning(true)
    try {
      const data = await speakerScan()
      setResults(data)
      showToast(`BT: найдено ${data.length}`)
    } catch (e) { showToast(e.message, false) }
    finally { setScanning(false) }
  }

  async function connectResult(d) {
    try {
      const r = await speakerConnect(d.name || d.address)
      showToast(r.message || (r.ok ? 'Подключено' : 'Ошибка'), r.ok)
    } catch (e) { showToast(e.message, false) }
  }

  return { name, setName, scanning, results, connect, disconnect, scan, connectResult }
}
