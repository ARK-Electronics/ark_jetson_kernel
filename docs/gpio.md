# GPIO on ARK Jetson Carriers

Using GPIO pins on ARK JAJ / PAB / PAB_V3 carriers running JetPack 7 (L4T r39.x).

## TL;DR

- The I2S / 40-pin connector pins come up as GPIO at boot, programmed by MB1 BCT, and the SoC drives **none** of them at idle: the four outputs (MCLK / SCLK / LRCLK / DOUT) sit **hi-z, held HIGH by the carrier's 604Ω pull-ups**, and **DIN** is a floating hi-z input (no pull-up). They hold the pull-ups' level from power-on through Linux, so a pin wired to a relay / actuator **never transitions at boot**.
- The idle level is therefore **HIGH** — "safe-off" for **active-low** loads. An active-high load sees HIGH at power-on until your app drives it; the pull-ups make a power-on LOW impossible in software, so add an external pull-down if a line must be low at boot.
- At idle the SoC drives nothing, so an accidental short to GND just pulls ~5.5 mA through the 604Ω resistor — it can't damage the pad. (Once your app drives a line, normal output rules apply, including short exposure.)
- Hi-z idle also means **no** standing current — the ~73 mW the old driven-low default burned into the pull-ups is gone; the ~5.5 mA/pin only flows while your app actively drives a line low.
- Drive / read the lines from userspace with `libgpiod` (`gpioset` / `gpioget`) or `Jetson.GPIO`; requesting a line as output enables the driver, requesting it as input reads the pulled level.
- While your app holds the line, the kernel guarantees its value. **On release (clean exit, crash, kill), the pin retains its last-written value** until the next reboot, when MB1 BCT re-asserts the hi-z input (pulled high). NVIDIA's pinctrl-tegra SFSEL fix has been upstream since JP6.2.2 (L4T r36.5) and is present in JetPack 7, so the pad keeps its last-written value on release.

## Pin map

I2S0 connector pins on **PAB** and **JAJ**, exposed as GPIO by default (configured in MB1 BCT, no overlay):

| HDR40 | Signal     | libgpiod name | SoC name             | Chip        | Carrier pullup | Idle at boot |
|------:|------------|---------------|----------------------|-------------|----------------|--------------|
| 12    | I2S0_SCLK  | `PH.07`       | `soc_gpio41_ph7`     | `gpiochip0` | 604Ω → 3.3V    | HIGH (pulled) |
| 40    | I2S0_DOUT  | `PI.00`       | `soc_gpio42_pi0`     | `gpiochip0` | 604Ω → 3.3V    | HIGH (pulled) |
| 38    | I2S0_DIN   | `PI.01`       | `soc_gpio43_pi1`     | `gpiochip0` | none           | hi-z (input) |
| 35    | I2S0_FS    | `PI.02`       | `soc_gpio44_pi2`     | `gpiochip0` | 604Ω → 3.3V    | HIGH (pulled) |
| 7     | AUD_MCLK   | `PAC.06`      | `soc_gpio59_pac6`    | `gpiochip1` (AON) | 604Ω → 3.3V | HIGH (pulled) |

`PAC.06` is on the AON GPIO controller, which retains state through SC7 suspend; main GPIO does not.

On **PAB_V3**, `PAC.06` is reserved for the KSZ8795 ethernet switch reset and is driven HIGH by BCT — do not use as general GPIO.

## Verify the lines

No overlay or jetson-io step is needed — MB1 BCT brings these pins up as GPIO at boot. Confirm they're exposed:

```bash
sudo gpioinfo | grep -E '"P(H\.07|I\.0[0-2]|AC\.06)"'
```

All five show as `unused` **inputs** at idle — the four outputs held high by their pull-ups, DIN floating. Your app claims a line and sets its direction when it needs to drive or read.

> **PAB** and **JAJ** wire this connector and ship it GPIO-by-default. **PAB_V3** does not route the I2S0 signals to a connector, so it has no connector GPIO here (and `PAC.06`/MCLK is its KSZ8795 ethernet reset — see above). The `ark_i2s_gpio` jetson-io overlay has been removed from all three.

## Drive and read

The libgpiod 1.x tools that ship with JP6 don't accept line names directly when the chip is specified — pass `$(gpiofind <name>)` to expand to `<chip> <offset>`:

```bash
# Drive HDR40 pin 40 (DOUT) high
gpioset $(gpiofind PI.00)=1

# In another terminal, read HDR40 pin 38 (DIN). With a jumper from
# pin 40 to pin 38, this is a loopback test.
gpioget $(gpiofind PI.01)
```

Without `--mode=signal`, `gpioset` exits immediately after writing. The value still persists in the OUTPUT_VAL register thanks to the upstream SFSEL fix, but the line is no longer "owned" — the next consumer to request it wins.

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

All five pins idle as inputs with their buffers enabled, so `gpioget $(gpiofind <name>)` reads any of them directly:

- **MCLK / SCLK / LRCLK / DOUT** read `1` — the carrier's 604Ω pull-up holds the undriven pad high.
- **DIN** has no external pull, so it floats — a CMOS input on a 3.3V system typically reads `1` from leakage and parasitic capacitance. That's normal, not a stuck pin; pull it to GND through a 1.5kΩ resistor and the read flips to `0`.

When your app claims one of the four outputs and drives it, the GPIO controller enables the driver; release it and the pad returns to the hi-z input (pulled high), keeping its last-written value until reboot per the SFSEL note above.

## Safe state when no app owns the pin

At boot no pin is driven — the four outputs idle high on their pull-ups, DIN floats. Once an app drives a pin it retains that last-written value on release, until reboot when MB1 BCT re-asserts the hi-z input. If your application drives a relay-control pin and crashes, the pin stays at its last value until reboot. Pick the pattern that fits your failure tolerance:

- **External pull resistor** — the only mechanism robust to all software failures. Required where idle-HIGH is the wrong rest state for your actuator (e.g. an active-high load that must stay off at boot needs an external pull-down).
- **`gpio-hog` in the kernel DTS** — kernel holds the pin for the system's lifetime; userspace `gpioset` returns `EBUSY`. Right answer for "userspace should never touch this pin" (peripheral resets, fixed enables).
- **Systemd service with `Restart=always`** + an `ExecStop=` script that drives the pin to its safe value on stop. Pin briefly floats during respawn (~1 s).
- **`gpioset --mode=signal`** as a daemon — closes the "clean exit left pin in unsafe state" window. SIGKILL still releases.

## Customizing boot defaults

The connector pins default to hi-z inputs (idle high on their pull-ups). If you instead need a pin **driven** to a fixed level at boot — to release a peripheral's reset, or to force a level the pull-up can't provide — edit two BCT DTSIs in the carrier directory and reflash:

- `products/<TARGET>/device_tree/bootloader/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi` — pinmux (function, pull, tristate, input-enable).
- `products/<TARGET>/device_tree/bootloader/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi` — direction + initial value.

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

## References

- [NVIDIA Jetson Linux — Pinmux and GPIO Configuration](https://docs.nvidia.com/jetson/archives/r39.2/DeveloperGuide/SD/Bootloader/PinmuxGpioConfig.html)
- [Linux kernel GPIO chardev API (v2)](https://docs.kernel.org/userspace-api/gpio/chardev.html)
- [libgpiod docs](https://libgpiod.readthedocs.io/en/stable/)
- NVIDIA forum thread on the SFSEL/PADCTL regression: <https://forums.developer.nvidia.com/t/40hdr-spi1-gpio-padctl-register-bit-10-effect-by-gpiod-tools-in-jp6/301171>
- Root-cause investigation: [#54](https://github.com/ARK-Electronics/ark_jetson_kernel/issues/54)
