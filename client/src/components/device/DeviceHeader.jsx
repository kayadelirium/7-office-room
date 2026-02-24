import { Stack, Typography, Chip } from '@mui/material'
import LightbulbIcon from '@mui/icons-material/Lightbulb'
import LeakAddIcon   from '@mui/icons-material/LeakAdd'

export default function DeviceHeader({ isLamp, name }) {
  return (
    <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1.5}>
      <Stack direction="row" alignItems="center" gap={1}>
        {isLamp
          ? <LightbulbIcon sx={{ color: 'purple.main', fontSize: 20 }} />
          : <LeakAddIcon   sx={{ color: 'warning.main', fontSize: 20 }} />
        }
        <Typography fontWeight={600}>{name}</Typography>
      </Stack>
      <Chip
        label={isLamp ? 'лампа' : 'лента'}
        sx={{
          bgcolor: isLamp ? '#2e1065' : '#451a03',
          color:   isLamp ? '#a855f7' : '#f59e0b',
          fontWeight: 700, fontSize: 11,
        }}
      />
    </Stack>
  )
}
