import { useState } from 'react'
import { sendCommand, disconnectDevice } from '../api'
import { showToast } from '../toast'

export function useLampCard(address, onRefresh) {
  const [brightness, setBrightness] = useState(70)

  async function cmd(c) {
    try {
      const r = await sendCommand(address, c)
      if (!r.ok) showToast(r.error, false)
      else showToast('✓ ' + c)
    } catch (e) { showToast(e.message, false) }
  }

  async function handleDisconnect() {
    try {
      const r = await disconnectDevice(address)
      if (r.ok) { showToast('Отключено'); onRefresh() }
      else showToast(r.error, false)
    } catch (e) { showToast(e.message, false) }
  }

  function onColor(e) {
    const hex = e.target.value
    const r = parseInt(hex.slice(1, 3), 16)
    const g = parseInt(hex.slice(3, 5), 16)
    const b = parseInt(hex.slice(5, 7), 16)
    cmd(`rgb ${r} ${g} ${b}`)
  }

  return { brightness, setBrightness, cmd, handleDisconnect, onColor }
}
