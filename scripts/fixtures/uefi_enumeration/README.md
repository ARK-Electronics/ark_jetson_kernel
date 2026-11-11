# UEFI enumeration regression fixture

`vendor.inc` contains five unchanged functions extracted from NVIDIA's pinned
R36.5 source revision `79ad0c17aa4f13fdd5164d5b026fa49436b34891`; source paths,
full-file SHA-256 values and the upstream license identifier are recorded in its
header. The complete upstream BSD-2-Clause-Patent terms are in [LICENSE](LICENSE).
Only extraction and CRLF-to-LF normalization were applied. The test pins the
fixture hash so vendor logic cannot silently change with the mocks.

`harness.c.in` supplies mock DT, allocation, protocol and controller-start APIs.
It inserts the actual candidate selector and phase case from the reviewed patch,
then runs the real compatibility lookup, enumeration and driver initializer.
It reproduces the original final-node binding error with an active NVMe thread,
verifies normal compatibility exclusion and zero accepted nodes, and checks that
DT-load, allocation and genuine controller-start failures still reach the
unchanged initialization assertion. It does not simulate PCIe electrical behavior.

Run `python3 scripts/test_uefi_pcie_filter.py` from the repository root.
