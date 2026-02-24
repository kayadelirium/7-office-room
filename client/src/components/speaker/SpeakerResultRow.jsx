import { Stack, Box, Typography, Button } from '@mui/material'

export default function SpeakerResultRow({ device, onConnect }) {
  return (
    <Stack direction="row" alignItems="center" gap={1.5} py={0.8}>
      <Box flex={1} minWidth={0}>
        <Typography fontSize={13} fontWeight={500} noWrap>
          {device.name || '(без имени)'}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap>
          {device.address}
        </Typography>
      </Box>
      <Button size="small" onClick={onConnect}>Подключить</Button>
    </Stack>
  )
}
