# GPIO on ARK Jetson Carriers

Using GPIO pins on ARK JAJ / PAB / PAB_V3 carriers running JetPack 6 (L4T r36.x).

## TL;DR

- The I2S / 40-pin connector pins come up **driven output-low** at boot, programmed by MB1 BCT. This gives active-high relays / actuators a deterministic safe-off state from the moment the SoC powers on, before Linux or the user's app start.
- The carrier has 604Ω pullups to 3.3V on **MCLK / SCLK / LRCLK / DOUT**. The GPIO pads sink ~5.5 mA each while held low — ~22 mA / 73 mW total, which is the cost of the active-low default. **DIN** has no external pullup.
- Drive the lines from userspace with `libgpiod` (`gpioset` / `gpioget`) or `Jetson.GPIO`.
- While your app holds the line, the kernel guarantees its value.
- **On release (clean exit, crash, kill), the pin retains its last-written value** until the next reboot, when MB1 BCT re-asserts output-low. This BSP applies NVIDIA's pinctrl-tegra SFSEL fix; without it (stock JP6.0–6.2.1) the pad would be flipped to SFIO mode on release, effectively floating the pin. The fix is upstream in JP6.2.2.
- For active-low actuators, edit MB1 BCT to drive output-high at boot — see ["Customizing boot defaults"](#customizing-boot-defaults).

## Pin map

I2S connector pins exposed as GPIO after applying the `ark_i2s_gpio` overlay:

| HDR40 | Signal     | libgpiod name | SoC name             | Chip        | Carrier pullup | Idle at boot |
|------:|------------|---------------|----------------------|-------------|----------------|--------------|
| 12    | I2S0_SCLK  | `PH.07`       | `soc_gpio41_ph7`     | `gpiochip0` | 604Ω → 3.3V    | LOW (driven) |
| 40    | I2S0_DOUT  | `PI.00`       | `soc_gpio42_pi0`     | `gpiochip0` | 604Ω → 3.3V    | LOW (driven) |
| 38    | I2S0_DIN   | `PI.01`       | `soc_gpio43_pi1`     | `gpiochip0` | none           | LOW (driven) |
| 35    | I2S0_FS    | `PI.02`       | `soc_gpio44_pi2`     | `gpiochip0` | 604Ω → 3.3V    | LOW (driven) |
| 7     | AUD_MCLK   | `PAC.06`      | `soc_gpio59_pac6`    | `gpiochip1` (AON) | 604Ω → 3.3V | LOW (driven) |

`PAC.06` is on the AON GPIO controller, which retains state through SC7 suspend; main GPIO does not.

On **PAB_V3**, `PAC.06` is reserved for the KSZ8795 ethernet switch reset and is driven HIGH by BCT — do not use as general GPIO.

## Apply the overlay

```bash
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n "ARK I2S to GPIO"
sudo reboot
```

Verify the lines are exposed:

```bash
sudo gpioinfo | grep -E '"P(H\.07|I\.0[0-2]|AC\.06)"'
```

You should see the lines marked `unused`. If they're missing, the overlay didn't load — on **PAB_V3** the default `extlinux.conf` has no `OVERLAYS=` line, so add `OVERLAYS /boot/ark_i2s_gpio.dtbo` manually (or build the overlay into the base DTB).

## Drive and read

The libgpiod 1.x tools that ship with JP6 don't accept line names directly when the chip is specified — pass `$(gpiofind <name>)` to expand to `<chip> <offset>`:

```bash
# Drive HDR40 pin 40 (DOUT) high; hold the line until SIGINT/SIGTERM.
gpioset --mode=signal $(gpiofind PI.00)=1

# In another terminal, read HDR40 pin 38 (DIN). With a jumper from
# pin 40 to pin 38, this is a loopback test.
gpioget $(gpiofind PI.01)
```

Without `--mode=signal`, `gpioset` exits immediately after writing. The value still persists in the OUTPUT_VAL register thanks to the SFSEL patch, but the line is no longer "owned" — the next consumer to request it wins.

Python — install `Jetson.GPIO` 2.1.12 or newer first; the apt-shipped version doesn't recognize the Orin Nano Super (`p3768-0000+p3767-0005-super`) and fails with `Could not determine Jetson model`:

```bash
sudo pip3 install 'Jetson.GPIO>=2.1.12'
```

The example below jumpers HDR40 pin 40 (DOUT) to pin 38 (DIN) and toggles HIGH/LOW/HIGH/LOW as a loopback test:

```python
import time
import Jetson.GPIO as GPIO

GPIO.setmode(GPIO.BOARD)
GPIO.setup(40, GPIO.OUT, initial=GPIO.LOW)  # I2S0_DOUT
GPIO.setup(38, GPIO.IN)                     # I2S0_DIN

try:
    for level in (GPIO.HIGH, GPIO.LOW, GPIO.HIGH, GPIO.LOW):
        GPIO.output(40, level)
        time.sleep(0.2)
        got = GPIO.input(38)
        label = "HIGH" if level else "LOW"
        result = "PASS" if got == level else f"FAIL (read {got})"
        print(f"DOUT=40 {label}  DIN=38 {got}  {result}")
finally:
    GPIO.cleanup()
```

Expect two cosmetic warnings on first run — both are harmless and the script still works:

```
WARNING: Carrier board is not from a Jetson Developer Kit.
UserWarning: Could not open /dev/mem for pinmux check.
```

The first is `Jetson.GPIO` noticing this isn't NVIDIA's branded p3768 devkit baseboard — we don't impersonate the devkit board IDs in the device tree, so the library prints once at module load. The second is the library trying to validate the requested pin direction against the PADCTL register via `/dev/mem`, which non-root users can't open. Run with `sudo` to silence the second warning and actually get the pinmux check.

### Reading inputs

The BCT pinmux for these pins is `nvidia,pull = TEGRA_PIN_PULL_NONE`, so once `gpioget` reconfigures a pin from output to input, the SoC's internal pull is off and the pad's idle level is determined entirely by what's pulling it externally:

- **MCLK / SCLK / LRCLK / DOUT** read `1` because the carrier's 3.3V pullup wins the unloaded pad.
- **DIN** has no external pull, so the pad floats — a CMOS input on a 3.3V system typically reads `1` from leakage and parasitic capacitance. This is normal, not a stuck pin.

To verify input is working, pull the line to GND through a 1.5kΩ resistor and confirm the read flips to `0`.

The "output-low at boot" guarantee from BCT only applies while the pin is configured as an **output**. The moment any consumer requests it as an input, the output driver detaches and BCT's drive value no longer reaches the pad.

## Safe state when no app owns the pin

Pins retain their last-written value on release (until reboot, when MB1 BCT re-asserts output-low). If your application drives a relay-control pin high and crashes, the pin stays high until reboot. Pick the pattern that fits your failure tolerance:

- **External pull resistor** — the only mechanism that's robust to all software failures. Required for safety-critical outputs where the BCT default is the wrong polarity for your actuator (e.g. active-low relays driven from these pins).
- **`gpio-hog` in the kernel DTS** — kernel holds the pin for the system's lifetime; userspace `gpioset` returns `EBUSY`. Right answer for "userspace should never touch this pin" (peripheral resets, fixed enables).
- **Systemd service with `Restart=always`** + an `ExecStop=` script that drives the pin to its safe value on stop. Pin briefly floats during respawn (~1 s).
- **`gpioset --mode=signal`** as a daemon — closes the "clean exit left pin in unsafe state" window. SIGKILL still releases.

## Customizing boot defaults

If your actuator is active-low, or you need a pin to come up high to release a peripheral's reset, edit two BCT DTSIs in the carrier directory and reflash:

- `products/<TARGET>/device_tree/Linux_for_Tegra/bootloader/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi` — pinmux (function, pull, tristate, input-enable).
- `products/<TARGET>/device_tree/Linux_for_Tegra/bootloader/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi` — direction + initial value.

For a generic output GPIO, the pinmux entry should be:

```
nvidia,function = "rsvd2";
nvidia,pull = <TEGRA_PIN_PULL_NONE>;
nvidia,tristate = <TEGRA_PIN_DISABLE>;
nvidia,enable-input = <TEGRA_PIN_DISABLE>;
```

…and place the `TEGRA234_MAIN_GPIO(...)` token under `gpio-output-low` or `gpio-output-high` (or `gpio-input` for inputs). Look up the right token in the Pinmux Spreadsheet (in the product directory).

Build and flash:

```bash
./build.sh PAB && ./flash.sh PAB
```

For iterative BCT-only work (much faster than a full flash):

```bash
cd staging/PAB/Linux_for_Tegra/
sudo ./flash.sh -k A_MB1_BCT -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit-super internal
sudo ./flash.sh -k B_MB1_BCT -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit-super internal
```

Reboot to apply.

For a kernel-owned hog instead, add to the carrier's `nv-common.dtsi`:

```dts
gpio@2200000 {
    my-peripheral-en {
        gpio-hog;
        output-high;
        gpios = <TEGRA234_MAIN_GPIO(H, 7) 0>;
        line-name = "my-peripheral-en";
    };
};
```

Note: BCT is the only layer that controls pad state from the moment of power-on. Kernel pinctrl and DT overlays don't apply until several seconds into Linux boot, so any pin whose state must be deterministic at power-on (relay safety, peripheral reset release, etc.) has to be configured in BCT — overlays alone are not sufficient.

## Caveats

- **Suspend/resume (SC7)**: main GPIO loses state through suspend. Only AON pins (`PAA`–`PEE`) retain. Route through-suspend signals to AON pins.
- **NVIDIA Pinmux Spreadsheet** "Int PD" / "Int PU" only applies to **inputs**. For outputs use `Drive 0` / `Drive 1`. (Trips up many customers — see NVIDIA forum [280082](https://forums.developer.nvidia.com/t/how-do-i-set-the-default-gpio-level-status-for-jetson-agx-orin/280082).)
- Don't remove `patches/pinctrl-tegra-sfsel.patch` until rebasing onto JP6.2.2 (r36.5), where NVIDIA's fix is already upstream.

## References

- [NVIDIA Jetson Linux — Pinmux and GPIO Configuration](https://docs.nvidia.com/jetson/archives/r36.4.3/DeveloperGuide/SD/Bootloader/PinmuxGpioConfig.html)
- [Linux kernel GPIO chardev API (v2)](https://docs.kernel.org/userspace-api/gpio/chardev.html)
- [libgpiod docs](https://libgpiod.readthedocs.io/en/stable/)
- NVIDIA forum thread on the SFSEL/PADCTL regression: <https://forums.developer.nvidia.com/t/40hdr-spi1-gpio-padctl-register-bit-10-effect-by-gpiod-tools-in-jp6/301171>
- Root-cause investigation: [#54](https://github.com/ARK-Electronics/ark_jetson_kernel/issues/54)
