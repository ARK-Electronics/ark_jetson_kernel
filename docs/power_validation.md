# JAJ VDD_IN Power Validation

Agent runbook. Answers: **what sustained VDD_IN power does an Orin NX 16GB reach on a Just A Jetson, and does it hold up in the 40W and MAXN_SUPER profiles?**

Everything runs over the USB link. The only human step is plugging the board in.

## Preconditions

- JAJ + Orin NX 16GB (P3767-0000), heatsink and fan fitted, fan on the carrier header, open air.
- Powered from the XT60 or the Molex 5V input, booted, connected to the host over USB.
- Host can reach it: `ssh jetson@jetson.local` (see `scripts/add_ssh_config.sh` for an `ssh jetson` alias, `ssh-copy-id` to skip the password).

### Bringing up the link when SSH does not work

Two failures are common enough to check before anything else. `lsusb | grep 0955` showing `0955:7020` means the board is booted and enumerated, so the problem is host-side.

- **No IPv4 on the USB interface.** L4T exposes two gadget interfaces (`rndis_host` and `cdc_ncm`); the host needs a DHCP lease on one. If `nmcli device status` shows them `disconnected`, activate the profile explicitly: `nmcli connection up ark-jetson-usb ifname <cdc_ncm iface>`. That profile ships with `autoconnect: no`, and a legacy `jetson-usb` profile may still be pinned to a stale `enx<mac>` from an older board — it autoconnects to an interface that no longer exists and silently wins nothing.
- **Password auth.** `scripts/configure_user.sh` sets `jetson` / `jetson`. Agents without a key should use `sshpass`, and sudo on the target is *not* passwordless — install a `SUDO_ASKPASS` helper once and use `sudo -A` throughout.

No meter is needed. The module's own INA3221 measures the VDD_IN bus voltage directly at the module, which is the number a DMM at TP2 would have given — at 8 mV resolution, plenty to check the 4.75V floor.

## What the agent cannot establish

State these as limitations in the final report rather than glossing them:

- **Ambient temperature.** Not measurable from the board. Use idle Tj after a 5-minute settle as the proxy and report it as such. If the customer asked about 25C specifically, the answer is conditional on that proxy.
- **Board input power** unless the carrier INA238 read in Phase 2 works. That chip has no kernel driver in our defconfig, so it's a best-effort raw I2C read.

## Phase 0 — Connect and identify

```bash
ssh jetson@jetson.local '
  cat /proc/device-tree/model; echo
  tr "\0" "\n" < /proc/device-tree/compatible
  head -1 /etc/nv_tegra_release
  cat /etc/ark_jetson_kernel
  ls -l /etc/nvpmodel.conf
  sudo nvpmodel -q
  uptime
'
```

Expected: model ends `... ARK JAJ Jetson Carrier Super`, compatible contains `p3767-0000-super`, `/etc/nvpmodel.conf` → `nvpmodel_p3767_0000_super.conf`, and `nvpmodel -q` reports **mode 4 (40W)** — that conf sets `PM_CONFIG DEFAULT=4`.

If any differ, stop and report. A non-super conf has no mode 4 and the run matrix below does not apply.

Confirm the fan is actually turning — a stalled fan invalidates every number that follows:

```bash
ssh jetson@jetson.local '
  systemctl is-active nvfancontrol
  grep -H . /sys/class/hwmon/hwmon*/rpm 2>/dev/null
'
```

## Phase 1 — Load toolchain

The image ships `stress` but no CUDA toolkit — only `libcuda.so`, the driver. CPU-only load will not clear 25W on this module, so the GPU burner has to be built on-device, which needs `nvcc`.

Give the Jetson internet first (host side):

```bash
./scripts/share_wifi.sh
ssh jetson@jetson.local 'ping -c2 8.8.8.8'   # see docs/share_wifi.md if DNS fails
```

`share_wifi.sh` hardcodes a `wl*` interface as the uplink. If the host's internet is on wired ethernet instead, NAT by hand — and **append rather than flushing FORWARD**, since flushing it breaks Docker networking on any host with bridges:

```bash
UP=<uplink iface>; USBIF=<usb iface>
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -s 192.168.55.0/24 -o $UP -j MASQUERADE
sudo iptables -I FORWARD 1 -i $USBIF -o $UP -j ACCEPT
sudo iptables -I FORWARD 2 -i $UP -o $USBIF -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

Then find and install the smallest toolchain that works. Do not assume package names — discover them:

```bash
ssh jetson@jetson.local '
  sudo apt-get update
  apt-cache search --names-only "^cuda-nvcc-" ; apt-cache search --names-only "^cuda-cudart-dev-"
'
```

Install the matching set for the installed CUDA version (12.6 on JetPack 6.2):
`sudo apt-get install -y cuda-nvcc-12-6 cuda-cudart-dev-12-6 libcublas-12-6 libcublas-dev-12-6`.
About 2GB total. Fall back to `cuda-toolkit-12-6` only if the split packages are unavailable.

**cuBLAS is not optional.** A hand-written FFMA kernel measured 6.9W of GPU draw on this module; the tensor-core HGEMM below measured 16.4W for the same clocks. Skipping cuBLAS to save the download understates the answer by roughly 10W, which is the difference between "under 25W" and "over 25W".

If there is no network at all, run the matrix CPU-only and label the result a **floor, not a ceiling**. It does not answer the question; say so rather than reporting a low number as the finding.

Build the burner:

```bash
ssh jetson@jetson.local 'cat > /tmp/gpu_gemm.cu' <<'EOF'
// Tensor-core FP16 GEMM loop — heaviest sustained GPU load available without a model.
#include <cublas_v2.h>
#include <cuda_fp16.h>
#include <cstdio>
#include <cstdlib>
#include <ctime>

int main(int argc, char **argv)
{
	int secs = (argc > 1) ? atoi(argv[1]) : 300;
	const int N = 4096;
	size_t bytes = (size_t)N * N * sizeof(__half);
	__half *A, *B, *C;
	cublasHandle_t h;
	time_t t0;
	long n = 0;

	cudaMalloc(&A, bytes);
	cudaMalloc(&B, bytes);
	cudaMalloc(&C, bytes);
	cudaMemset(A, 0x3c, bytes);
	cudaMemset(B, 0x3c, bytes);

	cublasCreate(&h);
	cublasSetMathMode(h, CUBLAS_TENSOR_OP_MATH);

	const __half one = __float2half(1.f), zero = __float2half(0.f);
	t0 = time(NULL);

	while (time(NULL) - t0 < secs) {
		for (int i = 0; i < 20; i++)
			cublasHgemm(h, CUBLAS_OP_N, CUBLAS_OP_N, N, N, N,
				    &one, A, N, B, N, &zero, C, N);
		cudaDeviceSynchronize();
		n += 20;
	}

	printf("%ld GEMMs\n", n);
	return 0;
}
EOF

ssh jetson@jetson.local '
  /usr/local/cuda-12.6/bin/nvcc -O3 -arch=sm_87 -I/usr/local/cuda-12.6/include /tmp/gpu_gemm.cu \
    -L/usr/local/cuda-12.6/lib64 -lcublas -o /tmp/gpu_gemm &&
  LD_LIBRARY_PATH=/usr/local/cuda-12.6/lib64 /tmp/gpu_gemm 5'
```

`nvcc` is not on `PATH` after a package-only install — use the absolute path, and export `LD_LIBRARY_PATH` when running, or the binary will not find `libcublas`.

## Phase 2 — Instruments

Module rails, on-module INA3221 (i2c-1, 0x40, 5 mOhm shunts):

```bash
ssh jetson@jetson.local '
  H=$(echo /sys/bus/i2c/drivers/ina3221/*/hwmon/hwmon*)
  grep -H . $H/in[1-3]_label
  grep -H . $H/curr1_crit $H/curr1_max $H/curr1_crit_alarm $H/curr1_max_alarm
'
```

`in1` = VDD_IN, `in2` = VDD_CPU_GPU_CV, `in3` = VDD_SOC. Bus voltage `inN_input` (mV), current `currN_input` (mA).

**Re-read `curr1_crit` / `curr1_max` after every mode change in Phase 3.**

On this module both read **7784 mA in every mode** — 0, 3 and 4 alike. That is a fixed over-current ceiling, roughly 40.2W at a 5.17V rail, wired to the `soctherm_oc` hwmon device. It is *not* a per-mode power budget: the nvpmodel wattage names are DVFS caps (core count and max frequencies) and program no budget of their own, which is why the shipped conf files and `tegra234-p3767.dtsi` contain no limit values. Both statements matter and they are easy to conflate — there *is* a hardware guard, it just does not track the mode you selected.

Board input power, carrier INA238 — best effort, no driver in our defconfig, so raw reads:

```bash
ssh jetson@jetson.local '
  sudo i2cdetect -y -r 7          # -r: auto mode skips 0x40-0x4F on this bus
  sudo i2cget -y 7 0x45 0x3e w    # manufacturer ID, byte-swapped: expect 0x4954 -> 0x5449 "TI"
'
```

This works. On a JAJ rev 2.0 the reads come back `0x4954` and `0x8123`, which byte-swap to `0x5449` ("TI") and `0x2381` (INA238) — the device is real and at the expected address, so the derived numbers can be trusted.

Sample `VBUS` (0x05) and `VSHUNT` (0x04). Both are big-endian on the wire and `i2cget -y ... w` returns little-endian, so swap every read:

```bash
swap () { printf '%d' $(( ((0x${1#0x} & 0xFF) << 8) | (0x${1#0x} >> 8) )); }
```

`VBUS` LSB = 3.125 mV. `VSHUNT` is signed 16-bit (subtract 65536 above 32767), LSB = 5 uV at the default ADCRANGE=0, across R9 = 1 mOhm, so **5 mA per LSB**. Board input watts = VBAT_IN x that current.

`i2cget` is a process spawn per read and slows the log loop noticeably — sample it every 5th iteration, not every second. If the ID read fails, drop this channel and say so rather than reporting a computed number from an unconfirmed device.

## Phase 3 — Run matrix

Modes 0, 3 and 4 all keep 8 cores online with identical FBP/TPC masks, so switching is **live — no reboot and no interactive prompt.** Verify each switch took before loading.

Modes 1 and 2 drop to 4 cores, need a reboot, and prompt interactively. Without a tty they **fail silently and leave the previous mode in place** — `nvpmodel -q` then reports the old mode, which reads like a successful switch. Never infer a mode change from the absence of an error; always confirm against `nvpmodel -q` and the resulting `scaling_max_freq`. The matrix below only uses 0/3/4 for this reason.

Install the logger:

```bash
ssh jetson@jetson.local 'cat > /tmp/power_log.sh' <<'EOF'
#!/usr/bin/env bash
# power_log.sh <seconds> — CSV of module VDD_IN plus clocks and Tj, 1 Hz
H=$(echo /sys/bus/i2c/drivers/ina3221/*/hwmon/hwmon*)
G=$(echo /sys/class/devfreq/*.gpu)
echo "s,vdd_in_mV,vdd_in_mA,vdd_in_mW,cpu_gpu_cv_mW,soc_mW,cpu0_kHz,gpu_Hz,tj_mC"
for ((s = 0; s < ${1:-360}; s++)); do
	read -r v  < "$H/in1_input";   read -r i  < "$H/curr1_input"
	read -r v2 < "$H/in2_input";   read -r i2 < "$H/curr2_input"
	read -r v3 < "$H/in3_input";   read -r i3 < "$H/curr3_input"
	read -r c  < /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq
	read -r g  < "$G/cur_freq"
	tj=$(cat /sys/class/thermal/thermal_zone*/temp | sort -n | tail -1)
	printf '%d,%d,%d,%d,%d,%d,%d,%d,%d\n' "$s" "$v" "$i" \
		$((v * i / 1000)) $((v2 * i2 / 1000)) $((v3 * i3 / 1000)) \
		"$c" "$g" "$tj"
	sleep 1
done
EOF
ssh jetson@jetson.local 'chmod +x /tmp/power_log.sh'
```

Save the starting state once, so Phase 5 can restore it:

```bash
ssh jetson@jetson.local 'sudo nvpmodel -q | tail -1 > /tmp/orig_mode.txt; sudo jetson_clocks --store /tmp/orig_clocks'
```

Idle baseline, 5 minutes with no load. Its final Tj is the ambient proxy.

**Unlock the clocks first.** Taking this with `jetson_clocks` still applied from an earlier step pins CPU and GPU at max while idle and inflates the result — measured 7.5W / 62C locked versus 4.7W / 59C unlocked on the same board. The locked number is not an ambient proxy.

```bash
ssh jetson@jetson.local 'sudo -A jetson_clocks --restore /tmp/orig_clocks; /tmp/power_log.sh 300' > jaj-idle.csv
```

Then, for each mode in **3, 4, 0** and each clock setting in **auto, locked**:

```bash
MODE=4; CLK=locked        # iterate over 3,4,0 x auto,locked = 6 runs
ssh jetson@jetson.local "
  sudo nvpmodel -m $MODE
  sleep 5
  sudo nvpmodel -q | grep -q 'NV Power Mode' || exit 1
  [ $CLK = locked ] && sudo jetson_clocks || sudo jetson_clocks --restore /tmp/orig_clocks
  H=\$(echo /sys/bus/i2c/drivers/ina3221/*/hwmon/hwmon*)
  grep -H . \$H/curr1_crit \$H/curr1_max
  ( stress --cpu 8 --vm 2 --vm-bytes 512M --timeout 340s >/dev/null 2>&1 & )
  ( /tmp/gpu_burn 340 >/dev/null 2>&1 & )
  sleep 2
  /tmp/power_log.sh 330
" > jaj-m$MODE-$CLK.csv
```

Between runs, idle 3 minutes so each starts from a comparable Tj.

**Give the load a wide margin over the logger.** The logger's real period is about 1.05s, not 1.00s — the sysfs reads and the `cat | sort` for Tj cost ~50ms per iteration. Over a 240-sample run that drift accumulates to ~12s, so a load sized at `duration + 15s` **exits before the logger does** and the last several samples are unloaded. They then contaminate any naive "mean of the last 60 samples". Size the load at roughly twice the log duration and compute steady state only over samples still under load (see Phase 5).

**Abort conditions.** Stop the matrix and report immediately if any run shows `vdd_in_mV` below 4750, a `curr1_*_alarm` reading 1, or the SSH session dropping (check `uptime` and `last reboot` — an unplanned reboot is the answer, not a glitch).

## Phase 4 — Soak

Take whichever mode produced the highest sustained power and run it for 45 minutes:

```bash
ssh jetson@jetson.local '/tmp/power_log.sh 2700' > jaj-soak.csv    # with load running as above
ssh jetson@jetson.local '
  last reboot | head -3
  dmesg -T | grep -iE "throttl|undervolt|reset|soctherm|oc[0-9]"
  H=$(echo /sys/bus/i2c/drivers/ina3221/*/hwmon/hwmon*)
  grep -H . $H/curr1_crit_alarm $H/curr1_max_alarm
'
```

## Phase 5 — Reduce and restore

Per CSV. Steady state is the last 60 samples **that are still under load** — gated at 60% of peak power, which drops the unloaded tail described in Phase 3:

```bash
for f in jaj-*.csv; do
  grep -E '^[0-9]+,' "$f" > /tmp/_r.csv
  pk=$(awk -F, '{if($4>p)p=$4} END{print p}' /tmp/_r.csv)
  st=$(awk -F, -v pk="$pk" '$4 > pk*0.6' /tmp/_r.csv | tail -n 60 |
       awk -F, '{s+=$4} END{if(NR)printf "%.1f", s/NR/1000}')
  awk -F, -v n="$(basename "$f" .csv)" -v st="$st" -v pk="$pk" '
    $4 > pk*0.6 {if($3>a)a=$3; if(v==0||$2<v)v=$2; if($9>t)t=$9; if($7>c)c=$7; if($8>g)g=$8}
    {if($10+$11>0)al=1}
    END{printf "%-14s steady=%sW peak=%.1fW peak=%.2fA min=%.3fV maxTj=%.1fC cpu=%.0f gpu=%.0f alarm=%s\n",
        n, st, pk/1000, a/1000, v/1000, t/1000, c/1000, g/1000000, (al?"YES":"no")}' /tmp/_r.csv
done
```

Restore:

```bash
ssh jetson@jetson.local '
  sudo jetson_clocks --restore /tmp/orig_clocks
  sudo nvpmodel -m 4
  sudo nvpmodel -q
'
./scripts/stop_share_wifi.sh
```

## Measured result — JAJ rev 2.0 + Orin NX 16GB, 2026-08-11

ARK build `ffd7afd`, L4T R36.5.0, 20V into the XT60, stock heatsink and fan, `nvfancontrol` active. Load = `stress --cpu 8 --vm 4` plus the tensor-core HGEMM above. Idle Tj settled at 58.4C, which is the only ambient proxy available — **the 25C ambient in the customer's question was not verified.**

| Run | Steady W | Peak W | Peak A | Min rail | Max Tj | CPU | GPU |
|---|---|---|---|---|---|---|---|
| idle (clocks auto) | 4.6 | 5.2 | 1.01 | 5.160 | 58.4 | — | 306 |
| mode 3 (25W) | 16.9 | 17.1 | 3.34 | 5.104 | 71.0 | 1498 | 408 |
| mode 4 (40W) | 27.6 | 28.2 | 5.60 | 5.040 | 86.1 | 1498 | 1173 |
| mode 0 (MAXN_SUPER) | 29.4 | 30.2 | 6.02 | 5.024 | 89.3 | 1984 | 1173 |
| **mode 0, 45 min soak** | **29.0** | **30.3** | **6.05** | **5.016** | **92.0** | 1984 | 1173 |

`jetson_clocks` changed nothing measurable in any mode (within 0.3W) — nvpmodel's caps are already binding.

Findings:

- **VDD_IN is 5.17V at idle, 5.02V at 6A.** About 29 mOhm of effective source impedance and 270 mV of margin to the module's 4.75V minimum. The rail is not the limit.
- **The module draws 6.05A peak — well past the ~5A commonly cited for VDD_IN.** No over-current alarm fired; the INA3221 CRIT/WARN ceiling is 7784 mA and was never approached.
- **MAXN_SUPER reaches ~29W sustained, not 40W.** Over a 45-minute soak power held 28.9-29.2W with CPU pinned at 1984 MHz and GPU at 1173 MHz for the entire run — minimum observed clocks equal maximum observed clocks, so **nothing throttled**. Tj plateaued at ~91C. No reboots, no undervolt or throttle events in dmesg.
- **Mode 4 is not the peak.** Mode 0 beats it by ~2W because mode 4 caps CPU at 1497.6 MHz while MAXN_SUPER uncaps to 1984 MHz; both run the GPU at 1173 MHz.
- **Mode 3 (25W) lands at 17W**, nowhere near its own label — its 408 MHz GPU cap binds long before power does.
- Board input at the XT60 was 37.0W for 29.0W at the module, so roughly 75% end-to-end including the carrier 3V3/1V8 rails, peripherals and fan.

The gap between ~29W measured and the 40W label is workload, not carrier: this load drives CPU and GPU only. DLA, PVA, NVENC and NVDEC are all idle, and the published reports that reach 32-33W drive those too. Treat 29W as a firm lower bound on what the JAJ sustains, not as the module's ceiling.

## Reading the result

| Observation | Conclusion |
|---|---|
| Sustained >5000 mA / >25W, bus voltage holds above 4.75V, no resets | Module draws past the 5A figure and the carrier supplies it. Report the measured watts. Do not call it 40W unless it measured 40W. |
| Plateau near 5000 mA with clocks below the mode's cap | Something is limiting. `curr1_crit` / `curr1_max` and the thermal trips say which. |
| Plateau well under 25W, clocks at cap, Tj low | The workload is the limit, not the board. Add NVENC or DLA load before concluding anything. |
| Bus voltage sagging toward 4.75V, or resets | The 5V source is the limit. Record which input was used: the XT60 path is an 8A part, the Molex input is rated 6A, and `VDD_5V_IN` is shared with the carrier 3V3, 1V8 and two 1.5A 5V peripheral rails. |
| Mode 0 and mode 4 produce the same power | Expected. Mode 0 uncaps frequencies the silicon cannot reach anyway under this load. |

Whatever the numbers, the answer to the customer is unchanged on the spec question: NVIDIA requires a minimum 8.0V on VDD_IN for MAXN_SUPER at 40W, and JAJ is a 5V carrier. Measured power above 25W means the board does it, not that it is supported.
