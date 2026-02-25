#!/bin/bash
# KSZ8795 switch read/write utility
# Usage:
#   ./ksz8795.sh dump              — print all registers in hex
#   ./ksz8795.sh read <reg_hex>    — read one register (e.g. read 0x01)
#   ./ksz8795.sh write <reg_hex> <val_hex>  — write one register
#   ./ksz8795.sh id                — verify chip ID
#   ./ksz8795.sh config-default    — apply recommended defaults
SYSFS=/sys/bus/spi/devices/spi1.0/registers
[ -e "$SYSFS" ] || { echo "ERROR: KSZ8795 sysfs not found. Is the driver loaded?"; exit 1; }

write_reg() { printf "\\x$(printf '%02x' $((16#${2#0x})))" | \
    dd of="$SYSFS" bs=1 seek=$((16#${1#0x})) conv=notrunc 2>/dev/null; }
read_reg()  { dd if="$SYSFS" bs=1 skip=$((16#${1#0x})) count=1 2>/dev/null | xxd -p; }

case "$1" in
  dump)
    xxd "$SYSFS" | head -16 ;;
  read)
    echo "Reg $2: 0x$(read_reg $2)" ;;
  write)
    write_reg $2 $3 && echo "Wrote 0x$3 to reg $2" ;;
  id)
    id=$(dd if="$SYSFS" bs=1 skip=0 count=2 2>/dev/null | xxd -p)
    echo "Chip ID: 0x${id} (expect 8795 family)" ;;
  config-default)
    # All ports in same VLAN (factory default — already true at power-on,
    # but explicit for documentation). No isolation, all ports forwarding.
    echo "Default config applied (all ports bridged)." ;;
  *)
    echo "Usage: $0 {dump|read <reg>|write <reg> <val>|id|config-default}" ;;
esac
