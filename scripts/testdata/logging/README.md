These unmodified text fixtures come from the Ubuntu 22.04 sample rootfs bundled
with NVIDIA Jetson Linux R36.5.0. Original license notices are retained. They
contain package defaults only, with no device identities or runtime logs.

`test_rsyslog_kernel_profile.py` checks the pinned hashes and verifies that
unsupported configuration cannot enable the optional logging change. These
fixtures are test inputs and are never installed into a product image.

Copyright attribution from the same installed packages is retained in
[systemd.copyright](licenses/systemd.copyright) and
[rsyslog.copyright](licenses/rsyslog.copyright). These package-wide notices also
list upstream files that are not part of these fixtures. Complete license texts
are included in [LGPL-2.1](licenses/LGPL-2.1), [GPL-3](licenses/GPL-3), and
[Apache-2.0](licenses/Apache-2.0). The systemd unit/configuration files retain
their LGPL-2.1-or-later headers; the rsyslog defaults come from its Ubuntu/Debian
package. The copyright and license files are copied byte-for-byte from the
sample rootfs, without changing the tested fixture files.
