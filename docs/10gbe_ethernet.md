# Auvidea M20E M.2 10GbE Ethernet

The [Auvidea M20E](https://auvidea.eu/product/m20e-m-2-10gbe/) is an M.2 (M-key) 10 Gigabit Ethernet adapter. It uses the Realtek RTL8127 chipset. Since it occupies the M.2 slot normally used by the NVMe drive, the Jetson must boot from a USB drive instead.

## Prerequisites

- Auvidea M20E M.2 10GbE adapter
- USB thumb drive (for booting the Jetson OS)
- ARK Jetson carrier board with M.2 M-key slot

## Step 1: Flash to USB Drive

Flash the ARK kernel image onto a USB thumb drive instead of NVMe:

```
./flash.sh --usb
```

## Step 2: Install the M20E

1. Remove the NVMe drive from the M.2 M-key slot
2. Install the Auvidea M20E adapter in its place
3. Boot the Jetson from the USB drive

## Step 3: Install Realtek RTL8127 Driver

The RTL8127 driver is not included in the default kernel. Install it on the Jetson after booting:

```bash
sudo apt install build-essential dkms
```

Download the driver from Realtek:
- [RTL8127 Driver Download (r8127-11.015.00)](https://www.realtek.com/Download/ToDownload?type=direct&downloadid=4636)

Build and install:

```bash
tar xf r8127-11.015.00.tar.bz2
cd r8127-11.015.00/
make
sudo make install
sudo depmod -a
sudo modprobe r8127
```

The 10GbE interface should now appear in `ip link`. You can verify with:

```bash
ip link show
ethtool <interface_name>
```
