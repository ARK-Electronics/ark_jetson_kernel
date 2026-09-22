---
name: jetson-hil
description: Drive a Jetson carrier (PAB, JAJ, PAB_V3) on the bench — power it from the Rigol DP712, capture the debug UART, flash over USB, reach it over SSH, and run boot or regression loops. Use when a task needs a real Jetson in the loop.
---

# Jetson hardware in the loop

> **TODO: untested.** Neither this skill nor `jetson_hil.py` has been run against a real Jetson or DP712. The first session that uses it must check each step and command it relies on against the hardware, fix what is wrong, and include those fixes and the removal of this notice in any PR produced with the skill's help.

`jetson_hil.py` (next to this file, needs pyserial) does the mechanics: `ports`, `psu`, `console` (with `--cycle-psu` for a timed cold boot), `wait-ssh`. Run it with `--help`.

## 1. Ask before touching anything

The bench changes between sessions and is shared. Ask the user, in one message:

1. Carrier and module (PAB / JAJ / PAB_V3; Orin NX 16/8 GB, Nano 8/4 GB) and the image it runs.
2. Supply: which (DP712?), the setpoint voltage and current limit, and **what else is on that output**. A cycle reboots everything it feeds.
3. Debug UART: is it wired, and which adapter?
4. USB-C device port to this host: wired? It carries flashing and SSH at `192.168.55.1`.
5. Ethernet: wired, and the Jetson's IP.
6. Who presses Force Recovery. Flashing needs a human.
7. Is anyone else using the bench now?

Do not power-cycle, flash or change a setpoint until the answers are in. Then run `jetson_hil.py ports` and check it matches what the user said. If there are several FTDI cables, `ports --identify` sends `*IDN?` to each one. The DP712 answers; a debug console gets the string typed into it, so ask before running it when a shell may be logged in.

## 2. Facts

| Item | Value |
|---|---|
| DP712 | RS232 through an FT232 cable, 9600 baud, SCPI. `:OUTP CH1,ON`/`OFF`, `:OUTP? CH1` → `ON`/`OFF`, `:SOUR:VOLT`/`:SOUR:CURR`, `:MEAS:VOLT? CH1`. The first query after opening the port is dropped, and back-to-back commands get garbled; the script retries and spaces them. |
| Supply setpoints | Carrier-specific: ask. Benches have used 16 V (JAJ boot timing), 20 V (JAJ power), 25.2 V (PAB power modules). |
| Debug UART | 115200 8N1, `ttyTCU0` on the device. Login `jetson` / `jetson`. With the fast-boot firmware, nothing prints before UEFI. |
| USB IDs | `0955:7020` = booted with the L4T gadget. `0955:7323/7423/7523/7623` = recovery mode (NX 16 GB / NX 8 GB / Nano 8 GB / Nano 4 GB). |
| USB network | The gadget shows up as two host NICs (rndis + cdc_ncm) and only one answers. Use whichever holds `192.168.55.100`, or bring the profile up by hand with `nmcli con up ark-jetson-usb ifname <nic>`. Never pin a profile to a MAC; it changes per board and per flash. |
| SSH | `jetson@192.168.55.1` over USB, or the Ethernet IP; `jetson.local` via mDNS. `sudo` password `jetson`. |

## 3. Loops

- **Cold boot with a timed console:** `jetson_hil.py console <uart> --cycle-psu <psu> --seconds 120 --until 'login:' --log boot.log`. Timestamps are seconds since `OUTP ON`, and that includes the carrier's ~2 s power-on reset. Then `wait-ssh`.
- **Flash:** the user holds Force Recovery while you run `psu off` then `psu on`. Check that `ports` shows a recovery ID. `flash.sh` and `build.sh` prompt for sudo, so give the user the exact command (`./flash.sh <TARGET>`) to run themselves. Throw away the first boot after a flash (key generation, rootfs resize, first-boot provisioning) before timing anything.
- **A failed flash** needs a physical re-entry: power off, then on with Force Recovery held. Re-authorizing USB does not reset the BootROM. Never start an RCM flash right after a software `reboot recovery`, because it stalls at `Sending mb1`.

## 4. Gotchas

- Three chip states all enumerate as the same recovery ID: BootROM, the MB2 applet, and factory-QSPI recovery. A board powered on "in recovery" without a fresh button press may be in the wrong one.
- A factory-fresh module can report flash success and still not boot, then succeed on the second pass.
- On older images, an offline boot hangs in `starting` behind `systemd-time-wait-sync`. `systemctl list-jobs` shows it. Fix per device with `sudo systemctl disable systemd-time-wait-sync`.
- A flash that dies with "cannot mount the NFS server" means NetworkManager grabbed the flasher's USB NIC. `flash.sh` guards against this; a hand-rolled `l4t_initrd_flash.sh` does not.
- One process per serial port. A leftover reader steals lines; check with `fuser <tty>`.
- Measured supply current is a module plus carrier plus fan number. Read the module rail from the on-module INA3221 (`in1_input`).
- Leave the supply in the state you found it and say so. If you changed a setpoint, restore it.
