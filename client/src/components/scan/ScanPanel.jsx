import {
  Box, Typography, Stack, Button,
  TextField, Paper, Divider,
  CircularProgress,
} from '@mui/material'
import BluetoothSearchingIcon from '@mui/icons-material/BluetoothSearching'
import { useScan } from '../../hooks/useScan'
import ScanResultRow from './ScanResultRow'

export default function ScanPanel({ connectedAddrs, onConnect }) {
  const { timeout, setTimeout_, scanning, results, presets, setPresets, scan, connect, connected } =
    useScan(connectedAddrs, onConnect)

  return (
    <Box mb={4}>
      <Typography variant="overline" color="text.secondary" fontWeight={700}>
        Поиск BLE-устройств
      </Typography>

      <Stack direction="row" gap={1.5} alignItems="center" mt={1} mb={2}>
        <Button
          startIcon={scanning ? <CircularProgress size={14} color="inherit" /> : <BluetoothSearchingIcon />}
          onClick={scan}
          disabled={scanning}
        >
          {scanning ? 'Сканирование…' : 'Сканировать'}
        </Button>
        <TextField
          label="Таймаут" type="number" size="small"
          value={timeout} onChange={e => setTimeout_(+e.target.value)}
          inputProps={{ min: 2, max: 30 }}
          sx={{ width: 110 }}
          InputProps={{ endAdornment: <Typography variant="caption" color="text.secondary">с</Typography> }}
        />
      </Stack>

      {results.length > 0 && (
        <Paper variant="outlined" sx={{ borderRadius: 2 }}>
          {results.map((d, i) => (
            <Box key={d.address}>
              <ScanResultRow
                device={d}
                preset={presets[i]}
                onPresetChange={val => setPresets(p => ({ ...p, [i]: val }))}
                isConnected={connected.has(d.address)}
                onConnect={() => connect(i)}
              />
              {i < results.length - 1 && <Divider />}
            </Box>
          ))}
        </Paper>
      )}
    </Box>
  )
}
