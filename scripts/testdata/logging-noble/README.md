Ubuntu 24.04 R39.2.1 sample-rootfs logging configuration and native unit fixtures. Package notices and license texts are in licenses/. AppArmor include tests synthesize file contents and patch only their fixture audit hashes; production pins the complete reviewed rsyslog profile/include closure.

The optional `etc/systemd/journald.conf.d/10-ark-os.conf` fixture is the ARK-OS
1.2.0 Noble package policy from pinned source
`423570c1021174ca15801c5182ec288862865ad4`. Its persistent journal storage and
1 GiB limit remain unchanged by the kernel logging option. Tests cover absence,
exact preservation and rejection of modified or additional drop-ins.
