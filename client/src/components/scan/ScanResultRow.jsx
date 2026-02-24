import {
  Stack, Box, Typography, Chip,
  FormControl, Select, MenuItem, Button,
} from '@mui/material'

const PRESETS = ['surplife', 'elk_bledom', 'magic_home', 'govee', 'triones', 'happylighting', 'nus']

function rssiColor(rssi) {
  if (rssi >= -60) return 'success'
  if (rssi >= -75) return 'warning'
  return 'error'
}

export default function ScanResultRow({ device, preset, onPresetChange, isConnected, onConnect }) {
  return (
    <Stack direction="row" alignItems="center" gap={1.5} px={2} py={1.2}>
      <Chip
        label={`${device.rssi} dBm`}
        size="small"
        color={rssiColor(device.rssi)}
        variant="outlined"
        sx={{ fontSize: 11, minWidth: 72 }}
      />

      <Box flex={1} minWidth={0}>
        <Typography fontSize={13} fontWeight={500} noWrap>
          {device.name || '<без имени>'}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap>
          {device.address}
        </Typography>
      </Box>

      <FormControl size="small" sx={{ minWidth: 130 }}>
        <Select
          value={preset || 'surplife'}
          onChange={e => onPresetChange(e.target.value)}
          sx={{ fontSize: 12 }}
        >
          {PRESETS.map(p => <MenuItem key={p} value={p} sx={{ fontSize: 12 }}>{p}</MenuItem>)}
        </Select>
      </FormControl>

      {isConnected
        ? <Chip label="подключено" color="success" size="small" />
        : <Button size="small" onClick={onConnect}>Подключить</Button>
      }
    </Stack>
  )
}
