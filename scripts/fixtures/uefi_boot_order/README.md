# R39 single-boot order regression fixture

`vendor-sections.json` contains the unchanged NVIDIA source portions needed to
apply the reviewed patch and compile the registration and failure callbacks.
Original line positions are retained as metadata; tests fill omitted lines with
blank lines before applying the actual patch. Source revision, full-file SHA256
and copyright notices are embedded in the fixture. [LICENSE](LICENSE) contains
the complete BSD-2-Clause-Patent terms from that pinned source.

The C harness mocks firmware services, not the patched ordering implementation.
It checks new and existing launcher options, unchanged normal registration,
malformed/read-only variables and release-visible boot failure status.
