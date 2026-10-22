# Optional JAJ C7 coldplug replay

`configure_fast_boot.py apply ROOTFS --parallel-jaj-pcie-coldplug` requires a
completed JAJ staging stamp and the audited systemd 249 vendor trigger unit.
Restore an existing profile before selecting different options, and retain its
other selected flags when applying the replacement profile.

The main trigger keeps its subsystem command and enumerates every device except
the exact sysname `141e0000.pcie`. Generated complementary fnmatch patterns work
with systemd 249 and filter before libudev reads the controller's `uevent` file.
A separate service, wanted without ordering by the main trigger, replays that
one platform device. It has no ordering before sysinit, basic, or multi-user.
No runtime helper or arbitrary sleep is installed.

This preserves the kernel's PCIe driver, full link-training timeout, and endpoint
probing. It does not disable the FFC port. The optimization avoids one blocking
metadata read in the main coldplug process: the kernel synchronizes that read
with an in-progress probe using the device lock. Other udev workers, global
settle calls, or journald metadata queries can still encounter the same lock.
Validate application and peripheral readiness with the intended PCIe hardware;
completing the main trigger earlier alone does not prove readiness.

The helper refuses existing custom trigger/replay overrides, dependency
directories and applicable drop-ins. Applying the same options or checking
status rechecks the vendor unit and installed profile. Restore removes its two
files and returns the saved default target. PAB and PAB_V3 are rejected: their
existing board device trees already disable the unconnected C7 controller.
