import {
  Box, Typography, Stack, Button,
  TextField, Paper, Divider,
  CircularProgress,
} from '@mui/material'
import BluetoothIcon          from '@mui/icons-material/Bluetooth'
import BluetoothDisabledIcon  from '@mui/icons-material/BluetoothDisabled'
import BluetoothSearchingIcon from '@mui/icons-material/BluetoothSearching'
import { useSpeaker } from '../../hooks/useSpeaker'
import SpeakerResultRow from './SpeakerResultRow'

export default function SpeakerPanel() {
  const { name, setName, scanning, results, connect, disconnect, scan, connectResult } = useSpeaker()

  return (
    <Box mb={4}>
      <Typography variant="overline" color="text.secondary" fontWeight={700}>
        Bluetooth-колонка
      </Typography>

      <Paper variant="outlined" sx={{ mt: 1, p: 2, borderRadius: 2 }}>
        <Stack direction="row" flexWrap="wrap" gap={1} alignItems="center">
          <TextField
            label="Имя колонки" size="small"
            placeholder="JBL, Sony…"
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && connect()}
            sx={{ width: 180 }}
          />
          <Button startIcon={<BluetoothIcon />} onClick={connect}>
            Подключить
          </Button>
          <Button
            startIcon={<BluetoothDisabledIcon />}
            color="inherit" variant="outlined"
            onClick={disconnect}
          >
            Отключить
          </Button>
          <Button
            startIcon={scanning ? <CircularProgress size={14} color="inherit" /> : <BluetoothSearchingIcon />}
            color="inherit" variant="outlined"
            onClick={scan}
            disabled={scanning}
          >
            {scanning ? 'Поиск…' : 'Сканировать BT'}
          </Button>
        </Stack>

        {results.length > 0 && (
          <Box mt={2}>
            <Divider sx={{ mb: 1 }} />
            {results.map((d, i) => (
              <SpeakerResultRow key={i} device={d} onConnect={() => connectResult(d)} />
            ))}
          </Box>
        )}
      </Paper>
    </Box>
  )
}
