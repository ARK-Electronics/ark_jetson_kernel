# GPIO on ARK Jetson Carriers

This guide covers how GPIO pin state is managed on ARK JAJ / PAB / PAB_V3 carriers running JetPack 6 (L4T r36.x), what this BSP does for you, and how to reconfigure pins for your own application.

## TL;DR

- Pins on the I2S / 40-pin connector default to **output-low** at boot. This means relays and other actuators come up de-energized, not floating.
- You control these pins from userspace with `libgpiod` (`gpioset`, `gpioget`, the `libgpiod-dev` C/C++ API, or `Jetson.GPIO` in Python).
- **While your application holds the line, the pin state is whatever your application sets.** That's how GPIO chardev works on Linux.
- **Once your application closes the line (normal exit, crash, or kill), the kernel releases the pin.** After release the pad reverts to the BSP default (output-low). See ["Pin state after application exit"](#pin-state-after-application-exit) for the full story.

## The three layers

Pin state on a Jetson Orin carrier is determined by three stacked layers. Understanding which layer owns the pin at each moment is the key to reasoning about GPIO behavior.

### Layer 1 — MB1 BCT (bootloader)

MB1 is the first bootloader stage after BootROM. It runs on BPMP very early in the boot process, before the Linux kernel. MB1 programs the Tegra234 pinmux pad-control registers and the GPIO controller direction/value registers from two Device Tree Source Include (DTSI) files:

- `device_tree/<board>/Linux_for_Tegra/bootloader/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi` — pinmux (function, pull, tristate, input-enable).
- `device_tree/<board>/Linux_for_Tegra/bootloader/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi` — GPIO direction + initial value.

These files are compiled into the MB1 BCT binary by NVIDIA's `pinmux-dts2cfg.py` tool and flashed to the `A_MB1_BCT` / `B_MB1_BCT` QSPI partitions. A change to either file takes effect only after a flash (full flash via `./flash.sh`, or a partial MB1-BCT-only flash — see ["Partial BCT flash"](#partial-bct-flash)).

**This is where pin state at power-on, after reset, and after system hang comes from.**

### Layer 2 — Linux kernel pinctrl and GPIO drivers

Once Linux boots, two things happen:

- The Tegra pinctrl driver takes over the pinmux registers. If a kernel driver claims pins via a `pinctrl-0` phandle in the kernel Device Tree, the kernel reprograms those pins at driver probe. Pins not claimed by any driver keep whatever MB1 BCT configured.
- The Tegra GPIO driver takes over the GPIO controller registers. Any `gpio-hog` nodes in the kernel Device Tree are applied at chip probe — the kernel permanently holds those pins at the hog state, and userspace cannot take them over.

For pins that are **not** claimed by a driver and **not** hogged (the common case for user-facing relay / signal pins), the kernel leaves the MB1 BCT state intact and waits for a userspace client to request the line.

### Layer 3 — Userspace (libgpiod chardev)

Your application opens `/dev/gpiochip0` or `/dev/gpiochip1`, requests a line, and configures direction/value. While the file descriptor is open, the line's state is guaranteed by the kernel. Once the fd closes — by `close(2)`, by `exit(3)`, or by kernel-forced fd teardown on a crashing process — the kernel releases the line.

This is the upstream Linux chardev contract:

> "The state of a line, including the value of output lines, is guaranteed to remain as requested until the returned file descriptor is closed. Once the file descriptor is closed, the state of the line becomes uncontrolled from the userspace perspective, and may revert to its default state."
> — <https://docs.kernel.org/userspace-api/gpio/gpio-v2-get-line-ioctl.html>

## What this BSP does for you

### The I2S connector / 40-pin header defaults

As shipped, the following pins are configured as **driven outputs at 0V** by MB1 BCT on all three carriers (JAJ / PAB / PAB_V3):

| HDR40 pin | Signal | Tegra pin | SoC name |
|---|---|---|---|
| 12 | I2S0_SCLK | `H,7` | `soc_gpio41_ph7` |
| 40 | I2S0_DOUT | `I,0` | `soc_gpio42_pi0` |
| 38 | I2S0_DIN | `I,1` | `soc_gpio43_pi1` |
| 35 | I2S0_FS | `I,2` | `soc_gpio44_pi2` |
| 7 (JAJ / PAB only) | AUD_MCLK | `AC,6` | `soc_gpio59_pac6` |

On PAB_V3, `AC,6` is reserved for the KSZ8795 ethernet switch reset and is held high by BCT — do not use it as a general GPIO on that carrier.

This means the pad is actively sourcing 0V from the moment MB1 runs, through UEFI, and all the way into the Linux kernel, until a userspace app claims the line.

### The pinctrl SFSEL patch

JetPack 6.0–6.2.1 shipped with a pinctrl-tegra regression: when a userspace GPIO consumer releases a line, the driver would flip the pad's SFIO bit back to 1, disconnecting the GPIO controller from the pad and handing it to an unused alternate function — leaving the pin effectively floating. NVIDIA fixed this in JetPack 6.2.2 (L4T r36.5). Until we rebase onto 6.2.2, this BSP applies NVIDIA's official fix via `patches/pinctrl-tegra-sfsel.patch`. The patch captures the original SFIO-bit state on GPIO request and restores it on release, so pins stay in GPIO mode — and the BCT-programmed output level stays connected to the pad — across userspace open/close cycles.

Relevant forum thread: <https://forums.developer.nvidia.com/t/40hdr-spi1-gpio-padctl-register-bit-10-effect-by-gpiod-tools-in-jp6/301171>

## Pin state after application exit

With this BSP:

1. Your app opens the line and drives it high. Pin reads 3.3V.
2. Your app exits or crashes. Kernel tears down the fd, releases the line.
3. The kernel re-routes the pad back through the GPIO controller (thanks to the SFSEL patch). The GPIO controller's `OUTPUT_VALUE` register still holds whatever your app last wrote.
4. **Important**: The kernel does **not** automatically restore the BCT-programmed output-low value on release. The last-written value persists in the GPIO register until the next reboot — at which point MB1 BCT re-asserts output-low.

So if your application drives a pin high and then crashes, **the pin stays high until you reboot or until some other consumer writes to the line**. This is standard upstream Linux behavior and is not unique to Jetson.

### Recommended patterns for safe release semantics

Pick the one that matches your failure tolerance:

- **Systemd with `Restart=always`** — package your GPIO control as a systemd service with `Restart=always` and a small `ExecStop=` script that drives pins to the safe value. systemd will respawn on crash within ~1 second. The pin briefly floats during restart, so add an external pull resistor if that matters.
- **`gpioset --mode=signal`** — a simple daemon that sets a pin and holds the fd open forever. Kills on SIGTERM/SIGINT cleanly; on SIGKILL the line still releases, but this closes the "app crashed and left the pin high" window for normal process exits.
- **`gpio-hog` in the kernel Device Tree** — for pins that should *never* be driven by userspace (e.g. a peripheral reset or a fixed enable). The kernel holds the pin for the system's lifetime. Userspace `gpioset` will fail with `EBUSY`. See ["Claiming a pin with gpio-hog"](#claiming-a-pin-with-gpio-hog).
- **External pull resistor** — the only mechanism that guarantees a known state when no software owns the pin. Recommended for safety-critical outputs (relays controlling high-current loads, etc.) regardless of what you do in software.

## Using these pins from userspace

The I2S connector pins are exposed as GPIO after applying the `ark_i2s_gpio` overlay via `jetson-io`:

```bash
sudo /opt/nvidia/jetson-io/config-by-hardware.py -n "ARK I2S to GPIO"
sudo reboot
```

After reboot, use `gpioinfo` to confirm the lines are available, then drive them with `gpioset`:

```bash
# Drive HDR40 pin 40 (I2S0_DOUT, soc_gpio42_pi0) high and hold forever.
gpioset --mode=signal gpiochip0 PI.00=1

# In another terminal, read it back.
gpioget gpiochip0 PI.00
```

From Python:

```python
import Jetson.GPIO as GPIO
GPIO.setmode(GPIO.BOARD)
GPIO.setup(40, GPIO.OUT, initial=GPIO.LOW)
GPIO.output(40, GPIO.HIGH)
```

## Reconfiguring pins for a custom application

If you need a pin to come up in a different state — driven high at boot, or mapped to a different peripheral — you have three options, in order of preference.

### Option 1: Edit MB1 BCT (best for custom boot-time state)

For custom boot-time state (e.g. a pin that must come up HIGH to release a reset on an external chip), edit both BCT files for your carrier and reflash.

1. **Pinmux** (`device_tree/<board>/Linux_for_Tegra/bootloader/generic/BCT/tegra234-mb1-bct-pinmux-p3767-dp-a03.dtsi`): set the pin's `nvidia,function`, `nvidia,pull`, `nvidia,tristate`, and `nvidia,enable-input`. For an output GPIO use:
   ```
   nvidia,function = "rsvd2";
   nvidia,pull = <TEGRA_PIN_PULL_NONE>;
   nvidia,tristate = <TEGRA_PIN_DISABLE>;
   nvidia,enable-input = <TEGRA_PIN_DISABLE>;
   ```
2. **GPIO** (`device_tree/<board>/Linux_for_Tegra/bootloader/tegra234-mb1-bct-gpio-p3767-dp-a03.dtsi`): move the pin's `TEGRA234_MAIN_GPIO(...)` entry into `gpio-output-low` or `gpio-output-high` (or `gpio-input` for input-only pins).
3. Rebuild and flash: `./build_kernel.sh && ./flash.sh`.

To find the right `TEGRA234_MAIN_GPIO(...)` for a given 40-pin header pin, consult the Pinmux Spreadsheet in the repo root or the table in [`device_tree/ark_jaj/.../overlay/ark_i2s_gpio.dts`](../device_tree/ark_jaj/Linux_for_Tegra/source/hardware/nvidia/t23x/nv-public/overlay/ark_i2s_gpio.dts).

### Option 2: Claim a pin with `gpio-hog` (best for "kernel-owned, never-userspace" pins)

For pins the kernel should hold at a fixed state for the lifetime of the system and userspace should never touch, add a `gpio-hog` node under the appropriate GPIO controller in your carrier DTS. Example:

```dts
gpio@2200000 {
    my-peripheral-enable {
        gpio-hog;
        output-high;
        gpios = <TEGRA234_MAIN_GPIO(H, 7) 0>;
        line-name = "my-peripheral-en";
    };
};
```

Place this under your carrier's `nv-common.dtsi` and rebuild the kernel DTB. Userspace `gpioset` will return `-EBUSY` on a hogged line, by design.

### Option 3: Handle it in userspace (best for pins customers drive from their own application)

For pins your application legitimately needs to toggle, the MB1 BCT default is the "safe" state at power-on. Runtime state is your application's responsibility. Use one of the [recommended patterns above](#recommended-patterns-for-safe-release-semantics).

## Partial BCT flash

If you've changed only BCT files and you're testing iteratively, you can flash just the MB1 BCT partitions (much faster than a full flash):

```bash
cd prebuilt/Linux_for_Tegra/
sudo ./flash.sh -k A_MB1_BCT -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit-super internal
sudo ./flash.sh -k B_MB1_BCT -c bootloader/generic/cfg/flash_t234_qspi.xml jetson-orin-nano-devkit-super internal
```

Reboot the target after flashing — MB1 BCT only applies at boot.

## Caveats

- **Suspend/resume (SC7)**: Main GPIO loses state through suspend because MB1/MB2 reload on resume. Only AON GPIO pins (`PAA`–`PEE` range) retain state through SC7. If your application needs a pin to hold through suspend, route it to an AON pin.
- **JetPack 6.0 / 6.1 / 6.2.0 / 6.2.1** all ship the pinctrl-tegra SFSEL regression. This BSP applies NVIDIA's official fix at build time. Do not remove `patches/pinctrl-tegra-sfsel.patch` unless you're rebasing onto r36.5 / JP 6.2.2, where the fix is already upstream.
- **`jetson-io` overlays don't auto-load on PAB_V3** in the default configuration — the `extlinux.conf` has no `OVERLAYS=` line. If you need the `ark_i2s_gpio` overlay on PAB_V3, add the line manually or build the overlay into the base DTB.
- **The `Int PD` / `Int PU` setting in the NVIDIA Pinmux Spreadsheet only applies to input pins.** For outputs, use `Drive 0` (low) or `Drive 1` (high). This trips up a lot of customers — see NVIDIA forum [280082](https://forums.developer.nvidia.com/t/how-do-i-set-the-default-gpio-level-status-for-jetson-agx-orin/280082).

## Further reading

- [NVIDIA Jetson Linux Developer Guide — Pinmux and GPIO Configuration](https://docs.nvidia.com/jetson/archives/r36.4.3/DeveloperGuide/SD/Bootloader/PinmuxGpioConfig.html)
- [NVIDIA Jetson Linux — Orin Nano / NX adaptation and bring-up](https://docs.nvidia.com/jetson/archives/r36.4.3/DeveloperGuide/HR/JetsonModuleAdaptationAndBringUp/JetsonOrinNxNanoSeries.html)
- [Linux kernel GPIO chardev API (v2)](https://docs.kernel.org/userspace-api/gpio/chardev.html)
- [libgpiod documentation](https://libgpiod.readthedocs.io/en/stable/)
- Root-cause investigation for the PAB/JAJ/PAB_V3 GPIO drift issue: [#54](https://github.com/ARK-Electronics/ark_jetson_kernel/issues/54)
