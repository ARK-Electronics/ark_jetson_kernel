# JetPack 6.2.2 Argus Regression

Tracking doc for [issue #107](https://github.com/ARK-Electronics/ark_jetson_kernel/issues/107): camera pipelines that work on our old R36.4.3-based images misbehave on the R36.5.0 / JetPack 6.2.2 base. **Status: reproduced on the bench, root-caused to NVIDIA's closed camera userspace, and fixed in this repo by pinning that stack to the last good release (2026-07-17).** The kernel, device tree, and RCE firmware we build/flash are exonerated — nothing in the BSP side of this repo is at fault.

## Symptom

On stock 36.5.0 userspace, two distinct misbehaviors, both absent on 36.4.3/36.4.4:

1. **Every session teardown is dirty**: `ERROR: ... GstNvArgusCameraSrc: CANCELLED / Argus Correctable Error Status` at `Setting pipeline to NULL`, on every single run (frames themselves are fine). This is the exact log Gremsy pasted in #107.
2. **Occasional hard relaunch failure** (~1 in 60–120 relaunches on our bench): the pipeline starts but delivers no frames (`nvbuf_utils: dmabuf_fd -1 mapped entry NOT found`, `NvBufSurfaceFromFd Failed`), the client's teardown RPC times out (`(Argus) Error Timeout ... ClientSocketManager`), and **nvargus-daemon segfaults**. Daemon-side chain, verbatim from the bench: `SCF: Error Timeout ... CaptureServiceEvent wait` → `Error: Camera HwEvents wait, this may indicate a hardware timeout occured` → `InvalidState: 2 buffers still pending during EGLStreamProducer destruction` → `waitForIdle() timed out` → `(NvCameraUtils) Error InvalidState: Mutex not initialized (EGLStreamProducer.cpp)` → `Main process exited, code=killed, status=11/SEGV`. systemd restarts the daemon (~1 s), a client connecting during the window gets `Cannot create camera provider`, and service recovers — net ~40 s video outage per event. On setups where the daemon wedges instead of dying (several forum reports), it's dead until reboot.

Grep-able signatures: `Argus Correctable Error Status`, `NvBufSurfaceFromFd`, `Error: Camera HwEvents wait`, `Mutex not initialized`, `status=11/SEGV` (journal), `Cannot create camera provider`.

## Affected versions

| L4T | JetPack | Camera userspace | Verdict |
|---|---|---|---|
| 36.4.3 | 6.2 | original | good — forum bisect, Gremsy, and our bench |
| 36.4.4 | 6.2.1 | ≈36.4.3 | good — **bench-validated 100/100 clean** (this is the pin) |
| 36.4.7 | 6.2.1 apt refresh | `libnvscf.so` + `libnvodm_imager.so` rebuilt, all else byte-identical to 36.4.4 | regressed — NVIDIA: "Suppose it's regressing for R36.4.7" |
| 36.5.0 | 6.2.2 | further changes on the 36.4.7 line | regressed — bench: 248/252 dirty teardowns, 3 daemon segfaults in 252 launches |

Key external threads: [368915](https://forums.developer.nvidia.com/t/jetson-orin-nano-jetpack-6-2-2-36-5-nvarguscamerasrc-fails-to-load-when-launched-for-a-second-time/368915) (Orin Nano relaunch black-screen, stock sensors, no encoder — NVIDIA suggested the rtcpu power workaround there), [358915](https://forums.developer.nvidia.com/t/jetson-orin-nano-developer-kit-jetpack-6-2-1-36-4-7-nvarguscamerasrc-fails-to-load-the-camera-with-id-1-when-launched-for-the-second-time/358915) (the 36.4.3-good/36.4.7-bad/36.5.0-still-bad bisect), [374682](https://forums.developer.nvidia.com/t/argus-timeout-when-capturing-frames-still-occurs-in-jetpack-6-2-2-r36-5-0/374682) (6.2.2 SCF timeouts + hangs, closest to our crash mode), [375178](https://forums.developer.nvidia.com/t/jetpack-6-2-1-three-cameras-using-libargus-close-and-reopen-cause-system-crash/375178), [375615](https://forums.developer.nvidia.com/t/jetson-orin-nx-16gb-jp6-2-2ga-argus-camera-frame-capture-abnormality-issue/375615?page=2), [Stereolabs 11481](https://community.stereolabs.com/t/nvargus-daemon-sensor-could-not-be-opened-imagerguid-15-on-multi-camera-kill-and-restart-after-updating-to-zed-sdk-5-4-jetpack-6-2-l4t-36-5/11481). Not listed in the [R36.5 release notes](https://docs.nvidia.com/jetson/archives/r36.5/ReleaseNotes/Jetson_Linux_Release_Notes_r36.5.pdf) known issues; NVIDIA has repeatedly failed to reproduce internally and committed to no fix.

## Bench results (2026-07-17)

Setup: PAB_V3 carrier, Orin NX 16GB, dual IMX219 (stock driver + our baked dual overlay), image = current main (`ac62335`, R36.5.0 base), rtsp-server disabled. Method: cycle `nvarguscamerasrc` sessions (90 frames @1080p to JPEG, clean EOS teardown), alternating sensor 0/1, 10 s idle gap so the RCE runtime-suspends between launches (5 s autosuspend). Per-launch verdicts: PASS = frames + clean teardown; DIRTY = frames but Argus errors (signature #1); FAIL = no frames / hang (signature #2).

| Configuration | Launches | PASS | DIRTY | FAIL (daemon SEGV events) |
|---|---|---|---|---|
| stock 36.5.0, rtcpu `auto` (baseline) | 72 | 1 | 69 | 2 (1) |
| stock 36.5.0, rtcpu `on` (NVIDIA's forum workaround) | 120 | 0 | 119 | 1 (1) |
| stock 36.5.0, single camera only, rtcpu `auto` | 12 | 0 | 12 | 0 |
| **36.4.3 userspace quartet** on the same kernel/fw | 150 | **150** | 0 | 0 |
| **36.4.4 userspace quartet** on the same kernel/fw | 100 | **100** | 0 | 0 |
| repacked `+ark1` 36.4.4 set (the shipped fix), post-install | 20 | 20 | 0 | 0 |

Conclusions the data forces:

- **The rtcpu runtime-PM workaround from thread 368915 is ineffective here.** The daemon crashed with the RCE pinned `active` the whole run (`pre=active` logged on every iteration). Do not ship it. It may still matter for the Orin-Nano black-screen manifestation in that thread, but it is not a fix for the regression.
- **The regression is entirely in NVIDIA's closed camera userspace.** Swapping only `nvidia-l4t-camera`, `nvidia-l4t-multimedia`, `nvidia-l4t-multimedia-utils`, and `nvidia-l4t-gstreamer` to 36.4.x — same 36.5.0 kernel, same OOT modules, same DT, same flashed RCE firmware, runtime PM back to `auto` — produced 250/250 clean launches with pristine teardowns.
- **The kernel side is exonerated**, including the new-in-36.5.0 capture-ivc channel semaphore (it was loaded and active during all 250 clean launches, and dmesg stayed silent through every failure — the crashes never touched the kernel logs). Its leaked-semaphore error path (missing `up()` on `-EBADF` in `tegra_capture_ivc_notify_chan_id()`) remains a latent NVIDIA bug but is not this regression's trigger.
- The two failure signatures come from the same userspace: both vanish together with the version swap.
- Partial swaps don't work: 36.4.x `nvidia-l4t-camera` alone against 36.5.0 multimedia fails with `(Argus) Error BadParameter: Invalid surface count` (NvBufSurface ABI skew). The four debs must move as a set.

## The fix (shipped in this repo)

`--provision` now pins the camera userspace stack to `NV_CAMERA_STACK_VERSION` (versions.env, currently `36.4.4-20250616085344` = JetPack 6.2.1, the newest bench-clean release). Because the 36.4.x debs declare `nvidia-l4t-core (<< 36.5-0)` and exact-stamp deps on cuda/nvsci, provision.sh repacks them (`relax_l4t_deps`): core cap relaxed, out-of-set exact deps unversioned, in-set exact deps retargeted, version suffixed `+ark1` for traceability. They then install as one ordinary `apt-get install --allow-downgrades` transaction — dpkg/apt state stays consistent (`apt-get check` clean) — and are `apt-mark hold` so an on-device upgrade against NVIDIA's repo can't drag them back to the regressed stamp.

Already-flashed 6.2.2.x devices can be fixed in place with the same four repacked debs: `sudo apt-get install -y --allow-downgrades ./ark1_*.deb && sudo apt-mark hold nvidia-l4t-gstreamer nvidia-l4t-camera nvidia-l4t-multimedia nvidia-l4t-multimedia-utils && sudo systemctl restart nvargus-daemon`.

On each BSP bump, rerun the repro below against the new stock stack; drop the pin (set `NV_CAMERA_STACK_VERSION` back to the BSP stamp) once NVIDIA ships a fixed userspace. Known tradeoff while pinned: the camera stack stops receiving NVIDIA's 36.5.x security/bug updates, and `nvidia-jetpack` metapackage installs that pull exact-version camera components may need the hold lifted.

For Gremsy (#107): their custom IMX586 driver is not the cause — stock sensors reproduce it. They can apply the same pinned userspace on a 36.5.0-based release (best: current kernel + working cameras), or stay on `b2275f3` (R36.4.3) until NVIDIA fixes 36.5.x.

Upstream: worth filing on the NVIDIA forum with the bench data — the `HwEvents wait` → EGLStreamProducer `Mutex not initialized` → SEGV chain, the 36.4.3/36.4.4-good vs 36.4.7/36.5.0-bad bisect, and the fact that the 36.4.7 refresh rebuilt only `libnvscf.so`/`libnvodm_imager.so` — and to note the rtcpu workaround does not help on Orin NX.

## Reproducing it

Two IMX219s (or any two stock-supported sensors), a 36.5.0-based image, rtsp-server stopped so the cameras are free. The dirty-teardown signature shows on the *first* `gst-launch-1.0 nvarguscamerasrc ... ! fakesink` run; the crash needs cycling — alternate sensors with an idle gap and wait for a FAIL (observed: iteration 54 and 66-launches-later on one bench day; NVIDIA's internal repro attempts failed, so expect variance). Watch `sudo journalctl -u nvargus-daemon -f` for the SEGV.

Test harness used for all numbers above (also handy for the BSP-bump retest):

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

Phases that produced the numbers: `RUN=p1 ./argus_test.sh 12 10 0` (single cam), `RUN=p2 ./argus_test.sh 60 10 0 1` (alternating — this hit the first crash at i=54), `echo on > .../power/control` then `RUN=won ./argus_test.sh 120 10 0 1` (workaround test — crashed at i=86), then the same alternating run on each candidate userspace. When a launch fails, classify before rebooting: immediate retry (transient?), `sudo systemctl status nvargus-daemon` (SEGV + restart counter?), `v4l2-ctl --stream-mmap` (pure V4L2 path still alive?), dmesg (should be silent — kernel noise would be new information).

## Background: how the root cause was narrowed

Artifact forensics that pointed at userspace before the bench confirmed it: the 36.4.7 refresh (where the forum bisect first sees the regression) left every `nvidia-l4t-camera` binary byte-identical to 36.4.4 **except `libnvscf.so` and `libnvodm_imager.so`**; the camrtc kernel↔RCE ABI headers are unchanged 36.4.3→36.5.0; the rtcpu DT (including `nvidia,autosuspend-delay-ms = <5000>`) is byte-identical across the releases; and the kernel-side camera diffs are compat shims plus the announced VI leak/error-recovery fixes, with one new piece of synchronization (the capture-ivc per-channel semaphore, absent from all 36.4.x builds — verified via `nm -u capture-ivc.ko | grep down_timeout` on NVIDIA's prebuilt debs) that the bench then cleared of blame. NVIDIA's apt pool (`repo.download.nvidia.com/jetson/t234` + `common`, dists r36.4/r36.5) retains all the 36.4.x debs, which is what makes the pin reproducible.

## Results log

| Date | Board / release | Cameras | Phase & command | PASS/DIRTY/FAIL | Notes |
|---|---|---|---|---|---|
| 2026-07-17 | PAB_V3, Orin NX 16GB, main `ac62335` (36.5.0) | 2× IMX219 | p1: single cam ×12, gap 10 | 0/12/0 | dirty teardowns from run 1 |
| 2026-07-17 | same | same | p2: alternate ×60, gap 10 | 1/57/2 | i=54 no-frames+hang, daemon SEGV; i=55 "Cannot create camera provider" during restart |
| 2026-07-17 | same, rtcpu `on` | same | won: alternate ×120 | 0/119/1 | i=86 identical SEGV with RCE never suspended — workaround refuted |
| 2026-07-17 | same, 36.4.3 userspace | same | u343: alternate ×150 | 150/0/0 | teardowns clean, rtcpu `auto` |
| 2026-07-17 | same, 36.4.4 userspace | same | u344: alternate ×100 | 100/0/0 | pin candidate confirmed |
| 2026-07-17 | same, repacked `+ark1` 36.4.4 via plain apt | same | final: alternate ×20 | 20/0/0 | shipped-image state; `apt-get check` clean, set held |
