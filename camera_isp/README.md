# Argus ISP overrides

`provision.sh` installs every `*.isp` file here into the image's `/var/nvidia/nvcam/settings/`.

Name a file `<badge>.isp`, after the `badge` of the camera module in `tegra-camera-platform`. Argus loads it only for that module.

| File | Camera | Sets |
|------|--------|------|
| `ark_hadron_rear_OV64B40.isp` | [FLIR Hadron EO](../docs/hadron.md) | Black level |
