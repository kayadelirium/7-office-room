import { Stack, Button } from '@mui/material'
import PowerIcon    from '@mui/icons-material/PowerSettingsNew'
import PowerOffIcon from '@mui/icons-material/PowerOff'

export default function PowerControls({ onCmd }) {
  return (
    <Stack direction="row" gap={1} mb={2}>
      <Button startIcon={<PowerIcon />}    color="success" onClick={() => onCmd('on')}>Вкл</Button>
      <Button startIcon={<PowerOffIcon />} color="inherit" onClick={() => onCmd('off')}>Выкл</Button>
    </Stack>
  )
}
