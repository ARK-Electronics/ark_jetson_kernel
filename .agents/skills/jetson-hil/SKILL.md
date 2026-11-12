---
name: jetson-hil
description: Drive a Jetson carrier (PAB, JAJ, PAB_V3) on the bench — power it from the Rigol DP712, capture the debug UART, flash over USB, reach it over SSH, and run boot or regression loops. Use when a task needs a real Jetson in the loop.
---

# Jetson hardware in the loop

> **TODO: partly tested.** `ports`, `psu`, `wait-ssh` and the DP712, USB and SSH facts below were checked on a JAJ on 2026-11-10, and the flash steps on 2026-11-11. `console` and the cold-boot loop have not been run against hardware. The first session that uses those must check them, fix what is wrong, and include those fixes and the removal of this notice in any PR produced with the skill's help.

`jetson_hil.py` (next to this file, needs pyserial) does the mechanics: `ports`, `psu`, `console` (with `--cycle-psu` for a timed cold boot), `wait-ssh`. Run it with `--help`.

## 1. Ask before touching anything

The bench changes between sessions and is shared. Ask the user, in one message:

1. Carrier and module (PAB / JAJ / PAB_V3; Orin NX 16/8 GB, Nano 8/4 GB) and the image it runs.
2. Supply: which (DP712?), the setpoint voltage and current limit, and **what else is on that output**. A cycle reboots everything it feeds.
3. Debug UART: is it wired, and which adapter?
4. USB-C device port to this host: wired? It carries flashing and SSH at `192.168.55.1`.
5. Ethernet: wired, and the Jetson's IP.
6. Whether someone can press Force Recovery. It is only needed when UEFI does not start (section 3).
7. Is anyone else using the bench now?

Do not power-cycle, flash or change a setpoint until the answers are in. Then run `jetson_hil.py ports` and check it matches what the user said. If there are several FTDI cables, `ports --identify` sends `*IDN?` to each one. The DP712 answers; a debug console gets the string typed into it, so ask before running it when a shell may be logged in.

## 2. Facts

| Item | Value |
|---|---|
| DP712 | RS232 through an FT232 cable, 9600 baud, SCPI. `:OUTP CH1,ON`/`OFF`, `:OUTP? CH1` → `ON`/`OFF`, `:SOUR:VOLT`/`:SOUR:CURR`, `:MEAS:VOLT? CH1`. The first query after opening the port is dropped, and back-to-back commands get garbled; the script retries and spaces them. |
| Supply setpoints | Carrier-specific: ask. Benches have used 16 V (JAJ boot timing), 20 V (JAJ power), 25.2 V (PAB power modules). |
| Debug UART | 115200 8N1, `ttyTCU0` on the device. Login `jetson` / `jetson`. With the fast-boot firmware, nothing prints before UEFI. |
| USB IDs | `0955:7020` = booted with the L4T gadget. `0955:7323/7423/7523/7623` = recovery mode (NX 16 GB / NX 8 GB / Nano 8 GB / Nano 4 GB). |
| USB network | The gadget shows up as two host NICs (rndis + cdc_ncm) and only one answers. Use whichever holds `192.168.55.100`, or bring the profile up by hand with `nmcli con up ark-jetson-usb ifname <nic>`. A Jetson reboot can leave the NIC without an address until the profile is brought up again. Never pin a profile to a MAC; it changes per board and per flash. |
| SSH | `jetson@192.168.55.1` over USB, or the Ethernet IP; `jetson.local` via mDNS. A stock image has no key installed: log in with `sshpass -p jetson`. `sudo` password `jetson`. |

## 3. Loops

- **Cold boot with a timed console:** `jetson_hil.py console <uart> --cycle-psu <psu> --seconds 120 --until 'login:' --log boot.log`. Timestamps are seconds since `OUTP ON`, and that includes the carrier's ~2 s power-on reset. Then `wait-ssh`.
- **Enter recovery without the button.** From a running Linux: `sudo reboot --force forced-recovery`. From the UEFI shell on the debug UART: `mm 0x0c392000 0x2 -w 4 -mmio`, then `reset -w`. That is the bit the kernel sets, `PMC_SCRATCH0_MODE_RCM` on T234. Both leave the BootROM download-ready, and the module re-enumerates with a recovery ID within three seconds. UEFI drops to its shell by itself when no boot option works, after about five minutes of PXE and HTTP timeouts on stock firmware. Only the button works when UEFI itself does not start: on the PAB, `FORCE_RECOVERYn` reaches SW1 and nothing else.
- **Flash:** check that `ports` shows a recovery ID, then `echo y | ./flash.sh <TARGET>`. That runs unattended where sudo needs no password, which is every test station. Elsewhere `flash.sh` and `build.sh` stop at a sudo prompt, so give the user the exact command to run themselves. Throw away the first boot after a flash (key generation, rootfs resize, first-boot provisioning) before timing anything.
- **A failed flash** can leave the module in NVIDIA's readinfo applet, which enumerates with the same recovery ID but takes no download. Re-authorizing USB does not reset the BootROM. Power-cycle and re-enter recovery, by either route above or the button. Never start an RCM flash right after the flasher's own applet `reboot recovery`, because it stalls at `Sending mb1`.

## 4. Gotchas

- Three chip states all enumerate as the same recovery ID: BootROM, the MB2 applet, and factory-QSPI recovery. A board powered on "in recovery" without a fresh button press may be in the wrong one.
- A factory-fresh module can report flash success and still not boot, then succeed on the second pass.
- On older images, an offline boot hangs in `starting` behind `systemd-time-wait-sync`. `systemctl list-jobs` shows it. Fix per device with `sudo systemctl disable systemd-time-wait-sync`.
- A flash that dies with "cannot mount the NFS server" means NetworkManager grabbed the flasher's USB NIC. `flash.sh` guards against this; a hand-rolled `l4t_initrd_flash.sh` does not.
- One process per serial port. A leftover reader steals lines; check with `fuser <tty>`.
- Measured supply current is a module plus carrier plus fan number. Read the module rail from the on-module INA3221 (`in1_input`).
- `systemctl isolate multi-user.target` takes the USB network down, and only a power cycle brings it back. To run without the desktop, use `systemctl stop gdm`.
- `flash.sh` returns once the first boot answers SSH. Bring the host's USB profile up again before the next SSH.
- A board can wait in recovery while a build finishes. One sat there for twelve minutes before a good flash.
- A software power-off does not keep a JAJ down: after `shutdown -h now` the module boots again within seconds. To power down cleanly, run `sync`, remount read-only with `echo u > /proc/sysrq-trigger`, then `psu off`.
- Leave the supply in the state you found it and say so. If you changed a setpoint, restore it.
