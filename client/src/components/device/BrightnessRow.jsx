import { Stack, Typography, Slider, Box, Tooltip } from '@mui/material'

export default function BrightnessRow({ brightness, isLamp, onBrightnessChange, onBrightnessCommit, onColor }) {
  return (
    <Stack direction="row" alignItems="center" gap={2} mb={isLamp ? 2 : 0}>
      <Typography variant="caption" color="text.secondary" width={60}>Яркость</Typography>
      <Slider
        value={brightness}
        min={0} max={100} size="small"
        onChange={(_, v) => onBrightnessChange(v)}
        onChangeCommitted={(_, v) => onBrightnessCommit(v)}
        sx={{ flex: 1, maxWidth: 200 }}
      />
      <Typography variant="caption" color="text.secondary" width={28}>{brightness}%</Typography>
      <Tooltip title="Выбрать цвет">
        <Box
          component="input" type="color" defaultValue="#ff8000"
          onChange={onColor}
          sx={{
            width: 34, height: 28, border: '1px solid',
            borderColor: 'divider', borderRadius: 1,
            cursor: 'pointer', p: '2px', bgcolor: 'transparent',
          }}
        />
      </Tooltip>
    </Stack>
  )
}
