import { Card, CardContent, CardActions, Button } from '@mui/material'
import LinkOffIcon from '@mui/icons-material/LinkOff'
import { useLampCard } from '../../hooks/useLampCard'
import DeviceHeader  from './DeviceHeader'
import PowerControls from './PowerControls'
import BrightnessRow from './BrightnessRow'
import CctSlider     from './CctSlider'

export default function DeviceCard({ lamp, onRefresh }) {
  const isLamp = lamp.type === 'lamp'
  const { brightness, setBrightness, cmd, handleDisconnect, onColor } = useLampCard(lamp.address, onRefresh)

  return (
    <Card sx={{ mb: 1.5 }}>
      <CardContent sx={{ pb: 1 }}>
        <DeviceHeader isLamp={isLamp} name={lamp.name} />
        <PowerControls onCmd={cmd} />
        <BrightnessRow
          brightness={brightness}
          isLamp={isLamp}
          onBrightnessChange={setBrightness}
          onBrightnessCommit={v => cmd('brightness ' + v)}
          onColor={onColor}
        />
        {isLamp && (
          <CctSlider
            brightness={brightness}
            onCctCommit={(br, cct) => cmd(`white ${br} ${cct}`)}
          />
        )}
      </CardContent>

      <CardActions sx={{ pt: 0, justifyContent: 'flex-end' }}>
        <Button
          startIcon={<LinkOffIcon />}
          color="error"
          variant="outlined"
          size="small"
          onClick={handleDisconnect}
        >
          Отключить
        </Button>
      </CardActions>
    </Card>
  )
}
