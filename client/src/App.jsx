import { useState, useEffect, useCallback } from 'react'
import {
  AppBar, Toolbar, Typography, IconButton,
  Container, Box,
} from '@mui/material'
import RefreshIcon   from '@mui/icons-material/Refresh'
import LightbulbIcon from '@mui/icons-material/Lightbulb'
import { getState } from './api'
import { showToast } from './toast'
import DeviceCard   from './components/device/DeviceCard'
import ScanPanel    from './components/scan/ScanPanel'
import SpeakerPanel from './components/speaker/SpeakerPanel'

export default function App() {
  const [lamps, setLamps] = useState([])

  const refresh = useCallback(async () => {
    try {
      const s = await getState()
      setLamps(s.lamps || [])
    } catch {
      showToast('Сервер недоступен', false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 5000)
    return () => clearInterval(id)
  }, [refresh])

  return (
    <>
      <AppBar position="static" color="transparent" elevation={0}
        sx={{ borderBottom: '1px solid', borderColor: 'divider', mb: 3 }}>
        <Toolbar>
          <LightbulbIcon sx={{ mr: 1, color: 'warning.main' }} />
          <Typography variant="h6" fontWeight={700} sx={{ flexGrow: 1 }}>
            Room Control
          </Typography>
          <IconButton onClick={refresh} size="small" title="Обновить">
            <RefreshIcon />
          </IconButton>
        </Toolbar>
      </AppBar>

      <Container maxWidth="md">
        <Box mb={4}>
          <Typography variant="overline" color="text.secondary" fontWeight={700}>
            Подключённые устройства
          </Typography>
          <Box mt={1}>
            {lamps.length === 0
              ? <Typography color="text.secondary" fontSize={13}>Нет подключённых устройств</Typography>
              : lamps.map(lamp => (
                  <DeviceCard
                    key={lamp.address}
                    lamp={lamp}
                    onRefresh={refresh}
                  />
                ))
            }
          </Box>
        </Box>

        <ScanPanel
          connectedAddrs={lamps.map(l => l.address)}
          onConnect={refresh}
        />

        <SpeakerPanel />
      </Container>
    </>
  )
}
