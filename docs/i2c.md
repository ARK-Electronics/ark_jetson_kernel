# I2C on ARK Jetson Carriers

Orin Nano / NX, all ARK carriers (JAJ / PAB / PAB_V3).

## Scan a bus

```bash
i2cdetect -l              # list buses
sudo i2cdetect -y -r 7    # scan bus 7
```

Always pass **`-r`**. Without it, `i2cdetect` skips most addresses on Tegra — including `0x40–0x4F`, where power monitors live — so the scan looks emptier than it is.

## Which bus is which

Linux bus numbers come from device-tree aliases and **don't** match the connector signal names — use this table, don't guess:

| Connector signal | `/dev`  | Logic | Devices |
|------------------|---------|-------|---------|
| I2C2             | `i2c-0` | 1.8 V | system / module ID EEPROM |
| I2C0             | `i2c-1` | 3.3 V | on-module INA3221 `0x40`, USB-PD `0x25` |
| Camera CSI       | `i2c-2` | 3.3 V | camera sensors |
| I2C1             | `i2c-7` | 3.3 V | **INA238 `0x45`**, EEPROMs `0x50` / `0x58` |

Adding an **ARK Servo Expander** for PWM? Its PCA9685 also defaults to `0x40` and collides with the INA3221 on `i2c-1` — see [servo_expander.md](servo_expander.md).
