# JetPack 6.2.2 Argus Regression

**JetPack 6 only.** The JetPack 7 image installs the camera stack at the BSP stamp, with no pin, repack or hold. The repro below has not been rerun on R39.

This page tracks [issue #107](https://github.com/ARK-Electronics/ark_jetson_kernel/issues/107). Camera pipelines that work on our old images based on R36.4.3 misbehave on the R36.5.0 / JetPack 6.2.2 base. **Status: we reproduced it on the bench, traced the root cause to NVIDIA's closed camera userspace, and fixed it in this repo by pinning that stack to the last good release (2026-07-17).** The kernel, the device tree and the RCE firmware that we build and flash are cleared. Nothing on the BSP side of this repo is at fault.

## Symptom

The stock 36.5.0 userspace misbehaves in two distinct ways. Neither happens on 36.4.3 or 36.4.4.

1. **Every session teardown is dirty.** Each run prints `ERROR: ... GstNvArgusCameraSrc: CANCELLED / Argus Correctable Error Status` at `Setting pipeline to NULL`. The frames themselves are fine. This is the exact log Gremsy pasted in #107.
2. **A relaunch occasionally fails hard**, about 1 in 60–120 relaunches on our bench. The pipeline starts but delivers no frames, and logs `nvbuf_utils: dmabuf_fd -1 mapped entry NOT found` and `NvBufSurfaceFromFd Failed`. The client's teardown RPC then times out with `(Argus) Error Timeout ... ClientSocketManager`, and **nvargus-daemon segfaults**. This is the chain on the daemon side, verbatim from the bench: `SCF: Error Timeout ... CaptureServiceEvent wait` → `Error: Camera HwEvents wait, this may indicate a hardware timeout occured` → `InvalidState: 2 buffers still pending during EGLStreamProducer destruction` → `waitForIdle() timed out` → `(NvCameraUtils) Error InvalidState: Mutex not initialized (EGLStreamProducer.cpp)` → `Main process exited, code=killed, status=11/SEGV`. systemd restarts the daemon in about 1 s. A client that connects in that window gets `Cannot create camera provider`, and then service recovers. Each event costs about 40 s of video. Several forum reports describe setups where the daemon wedges instead of dying, and there it stays dead until a reboot.

You can grep for these signatures: `Argus Correctable Error Status`, `NvBufSurfaceFromFd`, `Error: Camera HwEvents wait`, `Mutex not initialized`, `Cannot create camera provider`, and `status=11/SEGV` in the journal.

## Affected versions

| L4T | JetPack | Camera userspace | Verdict |
|---|---|---|---|
| 36.4.3 | 6.2 | original | good — forum bisect, Gremsy, and our bench |
| 36.4.4 | 6.2.1 | ≈36.4.3 | good — **bench-validated 100/100 clean** (this is the pin) |
| 36.4.7 | 6.2.1 apt refresh | `libnvscf.so` + `libnvodm_imager.so` rebuilt, all else byte-identical to 36.4.4 | regressed — NVIDIA: "Suppose it's regressing for R36.4.7" |
| 36.5.0 | 6.2.2 | further changes on the 36.4.7 line | regressed — bench: 248/252 dirty teardowns, 3 daemon segfaults in 252 launches |

These are the main external threads:

- [368915](https://forums.developer.nvidia.com/t/jetson-orin-nano-jetpack-6-2-2-36-5-nvarguscamerasrc-fails-to-load-when-launched-for-a-second-time/368915) reports a black screen on relaunch on an Orin Nano, with stock sensors and no encoder. NVIDIA suggested the rtcpu power workaround there.
- [358915](https://forums.developer.nvidia.com/t/jetson-orin-nano-developer-kit-jetpack-6-2-1-36-4-7-nvarguscamerasrc-fails-to-load-the-camera-with-id-1-when-launched-for-the-second-time/358915) has the bisect: 36.4.3 good, 36.4.7 bad, 36.5.0 still bad.
- [374682](https://forums.developer.nvidia.com/t/argus-timeout-when-capturing-frames-still-occurs-in-jetpack-6-2-2-r36-5-0/374682) reports SCF timeouts and hangs on 6.2.2. It is the closest to our crash.
- Further reports are [375178](https://forums.developer.nvidia.com/t/jetpack-6-2-1-three-cameras-using-libargus-close-and-reopen-cause-system-crash/375178), [375615](https://forums.developer.nvidia.com/t/jetson-orin-nx-16gb-jp6-2-2ga-argus-camera-frame-capture-abnormality-issue/375615?page=2) and [Stereolabs 11481](https://community.stereolabs.com/t/nvargus-daemon-sensor-could-not-be-opened-imagerguid-15-on-multi-camera-kill-and-restart-after-updating-to-zed-sdk-5-4-jetpack-6-2-l4t-36-5/11481).

The regression is not among the known issues in the [R36.5 release notes](https://docs.nvidia.com/jetson/archives/r36.5/ReleaseNotes/Jetson_Linux_Release_Notes_r36.5.pdf). NVIDIA has repeatedly failed to reproduce it internally and has committed to no fix.

## Bench results (2026-07-17)

The setup was a PAB_V3 carrier with an Orin NX 16GB and dual IMX219, using the stock driver and our baked dual overlay. The image was current main (`ac62335`, R36.5.0 base) with rtsp-server disabled.

The method was to cycle `nvarguscamerasrc` sessions, alternating between sensor 0 and sensor 1. Each session captured 90 frames at 1080p to JPEG and tore down cleanly on EOS. A 10 s idle gap between launches let the RCE runtime-suspend, since its autosuspend delay is 5 s.

Each launch got a verdict. PASS means frames and a clean teardown. DIRTY means frames with Argus errors, which is signature #1. FAIL means no frames or a hang, which is signature #2.

| Configuration | Launches | PASS | DIRTY | FAIL (daemon SEGV events) |
|---|---|---|---|---|
| stock 36.5.0, rtcpu `auto` (baseline) | 72 | 1 | 69 | 2 (1) |
| stock 36.5.0, rtcpu `on` (NVIDIA's forum workaround) | 120 | 0 | 119 | 1 (1) |
| stock 36.5.0, single camera only, rtcpu `auto` | 12 | 0 | 12 | 0 |
| **36.4.3 userspace quartet** on the same kernel/fw | 150 | **150** | 0 | 0 |
| **36.4.4 userspace quartet** on the same kernel/fw | 100 | **100** | 0 | 0 |
| repacked `+ark1` 36.4.4 set (the shipped fix), post-install | 20 | 20 | 0 | 0 |

The data forces these conclusions:

- **The rtcpu runtime-PM workaround from thread 368915 does not work here.** The daemon crashed with the RCE pinned `active` for the whole run, and `pre=active` was logged on every iteration. Do not ship it. It may still matter for the black screen on Orin Nano in that thread, but it does not fix the regression.
- **The regression is entirely in NVIDIA's closed camera userspace.** We swapped only `nvidia-l4t-camera`, `nvidia-l4t-multimedia`, `nvidia-l4t-multimedia-utils` and `nvidia-l4t-gstreamer` to 36.4.x. The 36.5.0 kernel, the OOT modules, the DT and the flashed RCE firmware stayed the same, and runtime PM went back to `auto`. That produced 250/250 clean launches with pristine teardowns.
- **The kernel side is cleared**, including the capture-ivc channel semaphore that is new in 36.5.0. It was loaded and active during all 250 clean launches, and dmesg stayed silent through every failure, so the crashes never touched the kernel logs. Its error path leaks the semaphore, through a missing `up()` on `-EBADF` in `tegra_capture_ivc_notify_chan_id()`. That remains a latent NVIDIA bug, but it does not trigger this regression.
- The two failure signatures come from the same userspace. Both vanish together with the version swap.
- A partial swap does not work. 36.4.x `nvidia-l4t-camera` alone against 36.5.0 multimedia fails with `(Argus) Error BadParameter: Invalid surface count`, because the NvBufSurface ABI differs. The four debs must move as a set.

## The fix (shipped in this repo)

`--provision` now pins the camera userspace stack to `NV_CAMERA_STACK_VERSION` in versions.env. It is currently `36.4.4-20250616085344`, which is JetPack 6.2.1, the newest release that ran clean on the bench.

The 36.4.x debs declare `nvidia-l4t-core (<< 36.5-0)` and depend on exact stamps of cuda and nvsci, so provision.sh repacks them in `relax_l4t_deps`. It relaxes the core cap, removes the version from exact deps outside the set, retargets exact deps inside the set, and restamps the version to `NV_CAMERA_PIN_VERSION`, which is `36.5.99-20250616085344+ark1`.

The debs then install as one ordinary `apt-get install --allow-downgrades` transaction, and the dpkg and apt state stays consistent: `apt-get check` is clean. They are held with `apt-mark hold`, so an upgrade on the device against NVIDIA's repo can't drag them back to the regressed stamp.

You can fix a device that is already flashed with 6.2.2.x in place, with the same four repacked debs: `sudo apt-get install -y --allow-downgrades ./ark1_*.deb && sudo apt-mark hold nvidia-l4t-gstreamer nvidia-l4t-camera nvidia-l4t-multimedia nvidia-l4t-multimedia-utils && sudo systemctl restart nvargus-daemon`.

On each BSP bump, run the repro below again against the new stock stack. Once NVIDIA ships a fixed userspace, drop the pin by setting `NV_CAMERA_STACK_VERSION` back to the BSP stamp. The pin has one known cost: the camera stack stops receiving NVIDIA's 36.5.x security and bug updates.

### Why the version is restamped, not just suffixed

`nvidia-jetpack-runtime` declares `nvidia-l4t-gstreamer (>> 36.5-0), (<< 36.6-0)`, which no truthful 36.4.4 stamp can satisfy. `apt install nvidia-jetpack` failed outright with "you have requested an impossible situation". `36.5.99` sits inside that window and above NVIDIA's newest 36.5.x, so the metapackage resolves and apt still never wants to upgrade the set. Nothing else in that dependency tree is tighter. `nvidia-l4t-jetson-multimedia-api` asks only for `(>> 36.0) (<< 37.0)` on camera, multimedia and multimedia-utils, and the CUDA, TensorRT and VPI side doesn't depend on them at all.

The contents are still `NV_CAMERA_STACK_VERSION`. The `+ark1` suffix and the real `20250616085344` build stamp in the debian revision are what say so. When you drop the pin, set both variables back to the BSP stamp together.

For Gremsy, in #107: their custom IMX586 driver is not the cause, because stock sensors reproduce it. They can apply the same pinned userspace on a release based on 36.5.0, which is the better option, since it gives them the current kernel and working cameras. Or they can stay on `b2275f3` (R36.4.3) until NVIDIA fixes 36.5.x.

This is worth filing on the NVIDIA forum with the bench data. That means the chain from `HwEvents wait` through the EGLStreamProducer `Mutex not initialized` to the SEGV, the bisect with 36.4.3 and 36.4.4 good and 36.4.7 and 36.5.0 bad, and the fact that the 36.4.7 refresh rebuilt only `libnvscf.so` and `libnvodm_imager.so`. The report should also note that the rtcpu workaround does not help on Orin NX.

## Reproducing it

You need two IMX219s, or any two sensors the stock image supports, and an image based on 36.5.0. Stop rtsp-server so that the cameras are free. The dirty teardown shows on the *first* `gst-launch-1.0 nvarguscamerasrc ... ! fakesink` run. The crash needs cycling: alternate the sensors with an idle gap and wait for a FAIL. On one bench day it came at iteration 54 and again 66 launches later. NVIDIA's own attempts to reproduce it failed, so expect variance. Watch `sudo journalctl -u nvargus-daemon -f` for the SEGV.

This is the test harness behind all the numbers above. It is also what to run again after a BSP bump:

```bash
#!/usr/bin/env bash
# argus_test.sh — cycle nvarguscamerasrc sessions, verdict per launch.
# Usage: [RUN=name] [TEARDOWN=int] [CAPS=...] [THRESH=bytes] ./argus_test.sh <iter> <gap_s> <sensor-id...>
set -u
ITER=${1:?iterations}; GAP=${2:?gap}; shift 2
SENSORS=("$@"); [ ${#SENSORS[@]} -gt 0 ] || SENSORS=(0)
CAPS=${CAPS:-'video/x-raw(memory:NVMM), width=1920, height=1080, format=NV12'}
THRESH=${THRESH:-100000}   # black 1080p JPEG ≈ 10-20 kB, real lit scene ≈ 1 MB
RTCPU=/sys/devices/platform/bc00000.rtcpu/power
OUT=$HOME/argus_${RUN:-run}; rm -rf "$OUT"; mkdir -p "$OUT"
echo "OUT=$OUT teardown=${TEARDOWN:-eos} sensors=${SENSORS[*]} gap=${GAP}s iter=$ITER thresh=$THRESH" | tee "$OUT/summary.txt"
sudo journalctl -u nvargus-daemon -f > "$OUT/nvargus.log" 2>&1 &
JP=$!
sudo dmesg -wT > "$OUT/dmesg.log" 2>&1 &
DP=$!
pass=0; dirty=0; fail=0
for ((i=1;i<=ITER;i++)); do
    id=${SENSORS[$(( (i-1) % ${#SENSORS[@]} ))]}
    jpg="$OUT/f$(printf %03d "$i")_cam$id.jpg"; log="$OUT/r$(printf %03d "$i")_cam$id.log"
    st=$(cat "$RTCPU/runtime_status" 2>/dev/null || echo n/a)
    if [ "${TEARDOWN:-eos}" = int ]; then
        timeout -k 5 -s INT --preserve-status 8 gst-launch-1.0 -e nvarguscamerasrc sensor-id="$id" \
            ! "$CAPS" ! nvvidconv ! 'video/x-raw, format=I420' ! jpegenc ! multifilesink location="$jpg" \
            > "$log" 2>&1; rc=$?
    else
        timeout -k 5 30 gst-launch-1.0 -e nvarguscamerasrc sensor-id="$id" num-buffers=90 \
            ! "$CAPS" ! nvvidconv ! 'video/x-raw, format=I420' ! jpegenc ! multifilesink location="$jpg" \
            > "$log" 2>&1; rc=$?
    fi
    size=$(stat -c %s "$jpg" 2>/dev/null || echo 0)
    sig=$(grep -oE "Argus Correctable Error Status|NvBufSurfaceFromFd Failed|No cameras available|Error Timeout|InvalidState|EBUSY|cannot be opened" "$log" | sort | uniq -c | awk '{$1=$1};1' | tr '\n' ';')
    v=PASS
    if [ "$size" -lt "$THRESH" ] || [ "$rc" -ge 124 ]; then v=FAIL; fail=$((fail+1))
    elif [ -n "$sig" ]; then v=DIRTY; dirty=$((dirty+1))
    else pass=$((pass+1)); fi
    [ "$v" = PASS ] && rm -f "$jpg"
    echo "$(date +%T) i=$i cam=$id pre=$st rc=$rc jpg=$size $v [$sig]" >> "$OUT/summary.txt"
    sleep "$GAP"
done
sudo kill "$JP" "$DP" 2>/dev/null
echo "DONE PASS=$pass DIRTY=$dirty FAIL=$fail" >> "$OUT/summary.txt"
```

These phases produced the numbers:

1. `RUN=p1 ./argus_test.sh 12 10 0` ran a single camera.
2. `RUN=p2 ./argus_test.sh 60 10 0 1` alternated the two cameras, and hit the first crash at i=54.
3. `echo on > .../power/control` and then `RUN=won ./argus_test.sh 120 10 0 1` tested the workaround, and crashed at i=86.
4. The same alternating run was then repeated on each candidate userspace.

When a launch fails, classify the failure before you reboot:

- Retry at once, to see whether it was transient.
- Run `sudo systemctl status nvargus-daemon`, to see a SEGV and the restart counter.
- Run `v4l2-ctl --stream-mmap`, to see whether the pure V4L2 path is still alive.
- Read dmesg. It should be silent, and kernel noise would be new information.

## Background: how the root cause was narrowed

A comparison of the release artifacts pointed at userspace before the bench confirmed it:

- The 36.4.7 refresh is where the forum bisect first sees the regression. It left every `nvidia-l4t-camera` binary byte-identical to 36.4.4 **except `libnvscf.so` and `libnvodm_imager.so`**.
- The camrtc ABI headers between the kernel and the RCE are unchanged from 36.4.3 to 36.5.0.
- The rtcpu DT, including `nvidia,autosuspend-delay-ms = <5000>`, is byte-identical across the releases.
- The camera diffs on the kernel side are compat shims plus the announced VI leak and error-recovery fixes. They add one new piece of synchronization, the per-channel semaphore in capture-ivc, which is absent from all 36.4.x builds. We verified that with `nm -u capture-ivc.ko | grep down_timeout` on NVIDIA's prebuilt debs, and the bench then cleared it of blame.

NVIDIA's apt pool keeps all the 36.4.x debs, which is what makes the pin reproducible. The pool is `repo.download.nvidia.com/jetson/t234` plus `common`, in dists r36.4 and r36.5.

## Results log

| Date | Board / release | Cameras | Phase & command | PASS/DIRTY/FAIL | Notes |
|---|---|---|---|---|---|
| 2026-07-17 | PAB_V3, Orin NX 16GB, main `ac62335` (36.5.0) | 2× IMX219 | p1: single cam ×12, gap 10 | 0/12/0 | dirty teardowns from run 1 |
| 2026-07-17 | same | same | p2: alternate ×60, gap 10 | 1/57/2 | i=54 no-frames+hang, daemon SEGV; i=55 "Cannot create camera provider" during restart |
| 2026-07-17 | same, rtcpu `on` | same | won: alternate ×120 | 0/119/1 | i=86 identical SEGV with RCE never suspended — workaround refuted |
| 2026-07-17 | same, 36.4.3 userspace | same | u343: alternate ×150 | 150/0/0 | teardowns clean, rtcpu `auto` |
| 2026-07-17 | same, 36.4.4 userspace | same | u344: alternate ×100 | 100/0/0 | pin candidate confirmed |
| 2026-07-17 | same, repacked `+ark1` 36.4.4 via plain apt | same | final: alternate ×20 | 20/0/0 | shipped-image state; `apt-get check` clean, set held |
