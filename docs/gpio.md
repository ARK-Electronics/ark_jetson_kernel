# GPIO on ARK Jetson Carriers

This page shows how to use the GPIO pins on ARK JAJ, PAB and PAB_V3 carriers running JetPack 6 (L4T r36.x).

## TL;DR

- The I2S / 40-pin connector pins come up as GPIO at boot, set up by the MB1 BCT, and the SoC drives **none** of them at idle. The four outputs, MCLK, SCLK, LRCLK and DOUT, sit **hi-z and are held HIGH by the carrier's 604Ω pull-ups**. **DIN** is a floating hi-z input with no pull-up. The pins hold the pull-ups' level from power-on through Linux, so a pin wired to a relay or an actuator **never changes level at boot**.
- The idle level is therefore **HIGH**, which is "safe-off" for **active-low** loads. An active-high load sees HIGH at power-on until your app drives the pin. The pull-ups make a LOW at power-on impossible in software, so add an external pull-down if a line must be low at boot.
- At idle the SoC drives nothing, so an accidental short to GND only pulls ~5.5 mA through the 604Ω resistor and can't damage the pad. Once your app drives a line, the normal rules for an output apply, including the exposure to a short.
- A hi-z idle also means **no** standing current. The ~73 mW that the old driven-low default burned in the pull-ups is gone, and the ~5.5 mA per pin flows only while your app drives a line low.
- Drive and read the lines from userspace with the `libgpiod` tools `gpioset` and `gpioget`, or with `Jetson.GPIO`. Requesting a line as an output enables the driver, and requesting it as an input reads the pulled level.
- While your app holds a line, the kernel guarantees its value. **When the app lets go, whether by a clean exit, a crash or a kill, the pin keeps the last value written** until the next reboot. At reboot the MB1 BCT makes it a hi-z input again, pulled high. JP6.2.2 (L4T r36.5) includes NVIDIA's upstream pinctrl-tegra SFSEL fix, which is why the pad keeps its value on release. On stock JP6.0–6.2.1 the pad flipped to SFIO mode on release, which in effect floated the pin.

## Pin map

These are the I2S0 connector pins on **PAB** and **JAJ**. They are GPIO by default, set up in the MB1 BCT with no overlay:

| HDR40 | Signal     | libgpiod name | SoC name             | Chip        | Carrier pullup | Idle at boot |
|------:|------------|---------------|----------------------|-------------|----------------|--------------|
| 12    | I2S0_SCLK  | `PH.07`       | `soc_gpio41_ph7`     | `gpiochip0` | 604Ω → 3.3V    | HIGH (pulled) |
| 40    | I2S0_DOUT  | `PI.00`       | `soc_gpio42_pi0`     | `gpiochip0` | 604Ω → 3.3V    | HIGH (pulled) |
| 38    | I2S0_DIN   | `PI.01`       | `soc_gpio43_pi1`     | `gpiochip0` | none           | hi-z (input) |
| 35    | I2S0_FS    | `PI.02`       | `soc_gpio44_pi2`     | `gpiochip0` | 604Ω → 3.3V    | HIGH (pulled) |
| 7     | AUD_MCLK   | `PAC.06`      | `soc_gpio59_pac6`    | `gpiochip1` (AON) | 604Ω → 3.3V | HIGH (pulled) |

`PAC.06` is on the AON GPIO controller, which keeps its state through SC7 suspend. The main GPIO controller does not.

On **PAB_V3**, `PAC.06` is reserved for the reset of the KSZ8795 ethernet switch, and the BCT drives it HIGH. Do not use it as general GPIO.

## Verify the lines

The MB1 BCT brings these pins up as GPIO at boot, without an overlay or a jetson-io step. Check that they are there:

```bash
sudo gpioinfo | grep -E '"P(H\.07|I\.0[0-2]|AC\.06)"'
```

At idle all five show as `unused` **inputs**. The four outputs are held high by their pull-ups, and DIN floats. Your app claims a line and sets its direction when it needs to drive or read it.

> **PAB** and **JAJ** wire this connector and ship it as GPIO by default. **PAB_V3** does not route the I2S0 signals to a connector, so it has no connector GPIO here, and its `PAC.06`/MCLK is the KSZ8795 ethernet reset described above. The `ark_i2s_gpio` jetson-io overlay has been removed from all three.

## Drive and read

The libgpiod 1.x tools that ship with JP6 don't accept a line name directly when you specify the chip. Pass `$(gpiofind <name>)` instead, which expands to `<chip> <offset>`:

```bash
# Drive HDR40 pin 40 (DOUT) high
gpioset $(gpiofind PI.00)=1

# In another terminal, read HDR40 pin 38 (DIN). With a jumper from
# pin 40 to pin 38, this is a loopback test.
gpioget $(gpiofind PI.01)
```

Without `--mode=signal`, `gpioset` exits as soon as it has written the value. The value stays in the OUTPUT_VAL register, thanks to the upstream SFSEL fix, but nothing owns the line any more, and the next consumer to request it wins.

For Python, install `Jetson.GPIO` 2.1.12 or newer first. The version that apt installs doesn't recognize the Orin Nano Super (`p3768-0000+p3767-0005-super`) and fails with `Could not determine Jetson model`.

```bash
sudo pip3 install 'Jetson.GPIO>=2.1.12'
```

The example below is a loopback test. Put a jumper from HDR40 pin 40 (DOUT) to pin 38 (DIN), and the script toggles the output HIGH/LOW/HIGH/LOW and reads it back:

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

The first run prints two warnings. Both are harmless, and the script still works:

```
WARNING: Carrier board is not from a Jetson Developer Kit.
UserWarning: Could not open /dev/mem for pinmux check.
```

The first is `Jetson.GPIO` noticing that this isn't NVIDIA's own p3768 devkit baseboard. We don't copy the devkit's board IDs into the device tree, so the library prints the warning once when the module loads. The second is the library trying to check the pin direction you asked for against the PADCTL register through `/dev/mem`, which only root can open. Run the script with `sudo` to get rid of the second warning and to get the pinmux check.

### Reading inputs

All five pins idle as inputs with their buffers enabled, so `gpioget $(gpiofind <name>)` reads any of them:

- **MCLK, SCLK, LRCLK and DOUT** read `1`, because the carrier's 604Ω pull-up holds the undriven pad high.
- **DIN** has no external pull, so it floats. A CMOS input on a 3.3V system typically reads `1` from leakage and parasitic capacitance. That is normal and does not mean the pin is stuck. Pull it to GND through a 1.5kΩ resistor and it reads `0`.

When your app claims one of the four outputs and drives it, the GPIO controller enables the driver. When the app releases the line, the pad keeps the last value written until the next reboot, as the SFSEL note above describes. The reboot returns it to a hi-z input, pulled high.

## Safe state when no app owns the pin

At boot no pin is driven. The four outputs idle high on their pull-ups, and DIN floats. Once an app has driven a pin, the pin keeps the last value written when the app lets go, until a reboot, when the MB1 BCT makes it a hi-z input again. So if your application drives a relay-control pin and then crashes, the pin stays at its last value until the reboot. Pick the pattern that fits how much failure you can tolerate:

- **An external pull resistor** is the only mechanism that holds up against every software failure. You need one where idle HIGH is the wrong rest state for your actuator. An active-high load that must stay off at boot needs an external pull-down, for example.
- **A `gpio-hog` in the kernel DTS** makes the kernel hold the pin for as long as the system runs, and `gpioset` from userspace returns `EBUSY`. Use it when userspace should never touch the pin, as with peripheral resets and fixed enables.
- **A systemd service with `Restart=always`**, plus an `ExecStop=` script that drives the pin to its safe value on stop. The pin floats briefly while the service respawns, for about 1 s.
- **`gpioset --mode=signal` run as a daemon** closes the window where a clean exit leaves the pin in an unsafe state. A SIGKILL still releases the line.

## Customizing boot defaults

The connector pins default to hi-z inputs, which idle high on their pull-ups. You may instead need a pin **driven** to a fixed level at boot, to release a peripheral's reset or to force a level the pull-up can't provide. In that case, edit two BCT DTSIs in the carrier directory and flash again:

- `products/<TARGET>/device_tree/bootloader/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi` sets the pinmux: function, pull, tristate and input-enable.
- `products/<TARGET>/device_tree/bootloader/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi` sets the direction and the initial value.

For a generic output GPIO, the pinmux entry should be:

```
nvidia,function = "rsvd2";
nvidia,pull = <TEGRA_PIN_PULL_NONE>;
nvidia,tristate = <TEGRA_PIN_DISABLE>;
nvidia,enable-input = <TEGRA_PIN_DISABLE>;
```

Then place the `TEGRA234_MAIN_GPIO(...)` token under `gpio-output-low` or `gpio-output-high`, or under `gpio-input` for an input. Look up the right token in the Pinmux Spreadsheet in the product directory.

Build and flash the image:

```bash
./build.sh PAB && ./flash.sh PAB
```

When you are only changing the BCT, flash just those partitions. It is much faster than a full flash:

```bash
cd staging/PAB/Linux_for_Tegra/
sudo ./flash.sh -k A_MB1_BCT -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit-super internal
sudo ./flash.sh -k B_MB1_BCT -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit-super internal
```

Reboot for the change to take effect.

For a hog owned by the kernel instead, add this to the carrier's `ark-<target>-overrides.dtsi`. [device-tree.md](device-tree.md) shows where that file lives.

```dts
&{/bus@0/gpio@2200000} {
	my-peripheral-en {
		gpio-hog;
		output-high;
		gpios = <TEGRA234_MAIN_GPIO(H, 7) 0>;
		line-name = "my-peripheral-en";
	};
};
```

Note: the BCT is the only layer that controls pad state from the moment of power-on. Kernel pinctrl and DT overlays don't apply until several seconds into the Linux boot. Any pin whose state must be certain at power-on, such as a relay's safe state or a peripheral's reset release, has to be configured in the BCT. An overlay alone is not enough.

## Caveats

- **Suspend and resume (SC7):** main GPIO loses its state through suspend. Only the AON pins, `PAA`–`PEE`, keep theirs. Route a signal that must survive suspend to an AON pin.
- **NVIDIA Pinmux Spreadsheet:** "Int PD" and "Int PU" apply only to **inputs**. For an output, use `Drive 0` or `Drive 1`. This trips up many people, as NVIDIA forum thread [280082](https://forums.developer.nvidia.com/t/how-do-i-set-the-default-gpio-level-status-for-jetson-agx-orin/280082) shows.

## References

- [NVIDIA Jetson Linux — Pinmux and GPIO Configuration](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/Bootloader/PinmuxGpioConfig.html)
- [Linux kernel GPIO chardev API (v2)](https://docs.kernel.org/userspace-api/gpio/chardev.html)
- [libgpiod docs](https://libgpiod.readthedocs.io/en/stable/)
- NVIDIA forum thread on the SFSEL/PADCTL regression: <https://forums.developer.nvidia.com/t/40hdr-spi1-gpio-padctl-register-bit-10-effect-by-gpiod-tools-in-jp6/301171>
- Root-cause investigation: [#54](https://github.com/ARK-Electronics/ark_jetson_kernel/issues/54)
