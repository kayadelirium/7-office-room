import { Stack, Typography, Slider } from '@mui/material'

export default function CctSlider({ brightness, onCctCommit }) {
  return (
    <Stack direction="row" alignItems="center" gap={2}>
      <Typography variant="caption" color="text.secondary" width={60}>
        CCT
      </Typography>
      <Slider
        defaultValue={50}
        min={0} max={100} size="small"
        onChangeCommitted={(_, v) => onCctCommit(brightness, v)}
        sx={{
          flex: 1, maxWidth: 200,
          '& .MuiSlider-track': {
            background: 'linear-gradient(to right, #ff9500, #fff)',
            border: 'none',
          },
        }}
      />
      <Typography variant="caption" color="text.secondary" width={28} noWrap>
        тёпл↔хол
      </Typography>
    </Stack>
  )
}
