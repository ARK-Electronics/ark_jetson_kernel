# Release Notes

## 6.2.2.8 - 2026-10-30

- The UEFI boot order is re-sorted on every boot with the NVMe first: a boot without the SSD detected no longer moves it to the end of the order, where the unit stopped in the UEFI shell instead of booting
- A boot order changed in the UEFI menu now resets on the next boot; use the F11 boot menu for a one-time boot from another device

## 6.2.2.7 - 2026-10-19

- PCIe link on the FFC connector capped at Gen2 (5 GT/s), ending the correctable-error flood it ran at Gen3
- ARK-OS updated to v1.3.1: adds a Logs page and PointPerfect client fixes
- Boots the NVMe first: network boot (HTTP/PXE) no longer runs before the SSD, and NVMe outranks USB storage
- `apt upgrade` no longer replaces the ARK kernel and device tree with NVIDIA's stock ones, and `apt install nvidia-jetpack` now works
- A root filesystem on a UAS USB drive now mounts
- SSH no longer stays dead if power is cut while the first boot generates host keys
- The serial console prints errors only, so a burst of kernel warnings can no longer stall boot into a watchdog reset

## 6.2.2.6 - 2026-07-23

- ARK-OS updated to v1.2.0

## 6.2.2.5 - 2026-07-21

- The camera userspace (Argus, `nvarguscamerasrc` and the NVIDIA multimedia libraries) is pinned to the L4T 36.4.4 packages and held from `apt upgrade`, fixing nvargus-daemon crashes and dropped frames when a camera pipeline is restarted
- The kernel now includes WireGuard VPN support as the `wireguard` module

## 6.2.2.4 - 2026-07-09

- Fixed the onboard IMU on `/dev/spidev1.0` reading all zeros: its SPI pins are now routed to the SPI controller from boot
- Dual IMX219 cameras are enabled on first boot with no jetson-io step; select a different camera in jetson-io to replace them
- jetson-io now lists the CSI camera overlays, which were missing from its menu in 6.2.2.1
- Added IMX708 (Raspberry Pi / Arducam Camera Module 3) support with a dual-camera overlay
- Removed the single-camera IMX219-A and IMX219-C overlays
- `nvarguscamerasrc` and the other NVIDIA GStreamer camera plugins are now preinstalled
- The I2S0 connector pins now boot as GPIO inputs idling high on the carrier pull-ups instead of being driven low, so the ARK I2S to GPIO overlay is no longer needed and was removed
- MAVSDK is no longer installed system-wide: ARK-OS bundles its own under `/usr/lib/ark-os/mavsdk`, so install MAVSDK yourself if your code links against it
- Preinstalled gpiod, i2c-tools, usbutils, pciutils, v4l-utils, uhubctl, inxi, xdotool and x11-xserver-utils, plus the Python packages spidev, pyserial, dronecan, smbus2 and Jetson.GPIO
- The system clock now starts at the image build time, so desktop autologin works on a first boot without network time
- ARK-OS updated to v1.1.0
- The flasher is now fetched from the main branch and flashes any release; it is no longer attached to each release

## 6.2.2.1 - 2026-06-10

- Initial release on JetPack 6.2.2 (L4T R36.5.0): one NVMe image with Super mode for every Orin Nano and Orin NX module
- Ships with ARK-OS v1.0.0, MAVSDK 3.17.1 and jtop preinstalled
