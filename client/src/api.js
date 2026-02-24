async function req(method, path, body) {
  const r = await fetch('/api/' + path, {
    method,
    headers: body != null ? { 'Content-Type': 'application/json' } : {},
    body:    body != null ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error('HTTP ' + r.status)
  return r.json()
}

export const getState          = ()               => req('GET',  'state')
export const scanBLE           = (timeout = 8)    => req('POST', `scan?timeout=${timeout}`)
export const connectDevice     = (address, preset) => req('POST', 'connect',            { address, preset })
export const disconnectDevice  = (address)         => req('POST', 'disconnect',          { address })
export const sendCommand       = (target, cmd)     => req('POST', 'command',             { target, cmd })
export const speakerScan       = ()                => req('POST', 'speaker/scan')
export const speakerConnect    = (name = '')       => req('POST', 'speaker/connect',     { name })
export const speakerDisconnect = ()                => req('POST', 'speaker/disconnect')
