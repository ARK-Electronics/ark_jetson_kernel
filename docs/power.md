# Power & Performance

## TL;DR

ARK carriers run the Jetson from a 5V rail. Everything below follows from that.

- An **Orin NX** boots in 40W mode already — you do not need to enable anything. An **Orin Nano** boots in 25W mode and does benefit from being switched up.
- `sudo nvpmodel -m 0` (MAXN_SUPER on Orin NX; mode **2** on Orin Nano) is the fastest setting and is worth using.
- The mode names are clock caps, not power caps. A "40W" mode does not draw 40W unless your workload asks for it.
- We measured **29W sustained** on a Just A Jetson with an Orin NX 16GB, with no throttling over 45 minutes.

## Power modes

Modes come from `/etc/nvpmodel.conf`. Check and change them with:

```bash
sudo nvpmodel -q          # which mode am I in
sudo nvpmodel -m 0        # switch to MAXN_SUPER
```

Orin NX 16GB:

| ID | Name | CPU cores | CPU max | GPU max | Switches live? |
|---|---|---|---|---|---|
| 0 | MAXN_SUPER | 8 | 1984 MHz | uncapped | yes |
| 1 | 10W | 4 | — | — | no, needs reboot |
| 2 | 15W | 4 | — | — | no, needs reboot |
| 3 | 25W | 8 | 1498 MHz | 408 MHz | yes |
| 4 | 40W | 8 | 1498 MHz | 1173 MHz | yes (**default**) |

**Orin Nano is different in two ways.** MAXN_SUPER is mode **2** there, not 0, and Nano boots into mode 1 (25W) rather than its top mode. So on an Orin Nano `sudo nvpmodel -m 2` genuinely gains you something; on an Orin NX you are already near the top. Always check `nvpmodel -q` rather than assuming.

**The wattage names are labels for clock limits, not power budgets.** Mode 3 caps the GPU at 408 MHz; that is what makes it "25W", not any power measurement. Nothing in the mode definition meters power.

**Modes 1 and 2 fail silently over SSH.** They drop to 4 cores, which needs a reboot, and the prompt cannot appear without a terminal. `nvpmodel -q` then keeps reporting the *old* mode, which looks exactly like success. Only modes 0, 3 and 4 switch live.

## Locking clocks

`nvpmodel` sets the ceiling; the governor still scales below it. To pin everything at the ceiling:

```bash
sudo jetson_clocks --store    # save current settings first
sudo jetson_clocks            # lock to max
sudo jetson_clocks --restore  # put them back
sudo jetson_clocks --show
```

In our testing this changed sustained power by less than 0.3W, because the nvpmodel caps were already the binding limit. It mainly helps latency and run-to-run consistency, not throughput.

## What we measured

Just A Jetson rev 2.0 + Orin NX 16GB (P3767-0000), L4T R36.5.0, stock heatsink and fan, 20V into the XT60. Load was 8 CPU threads plus a continuous tensor-core FP16 GEMM. Power read from the module's own INA3221 on the VDD_IN rail.

| Mode | Sustained | Peak | Peak current | VDD_IN under load | Max Tj |
|---|---|---|---|---|---|
| idle | 4.6W | 5.2W | 1.01A | 5.160V | 58C |
| 3 — 25W | 16.9W | 17.1W | 3.34A | 5.104V | 71C |
| 4 — 40W | 27.6W | 28.2W | 5.60A | 5.040V | 86C |
| 0 — MAXN_SUPER | 29.4W | 30.2W | 6.02A | 5.024V | 89C |
| 0 — 45 min soak | 29.0W | 30.3W | 6.05A | 5.016V | 92C |

Over the 45-minute soak the CPU held 1984 MHz and the GPU 1173 MHz for every single sample — minimum observed clock equalled maximum observed clock. **Nothing throttled.** No over-current alarm, no reboot, no undervolt or throttle events in the kernel log. Board input at the XT60 was 37.0W for 29.0W delivered to the module, so about 75% end to end including the carrier's 3.3V and 1.8V rails, peripherals and fan.

Two results worth calling out:

- **MAXN_SUPER beats 40W mode**, by about 2W. Mode 4 caps the CPU at 1498 MHz; MAXN_SUPER lets it run to 1984 MHz. Both run the GPU at 1173 MHz.
- **25W mode lands at 17W**, nowhere near its label, because its 408 MHz GPU cap binds long before power does.

## Common questions

**Why didn't it reach 40W?**
Because our load only drove the CPU and GPU. The DLA, PVA, and the video encoder and decoder were all idle, and they are a large share of the budget. Published results that reach 32-33W drive those too. Treat 29W as a floor for what the board sustains, not as the module's ceiling.

**Is 5V enough?**
For everything we tested, yes. The rail measured 5.168V idle and 5.016V at 6A — roughly 29 milliohms of source impedance and 270mV of margin above the module's 4.75V minimum. The rail was never the limiting factor.

There is one caveat, and it matters if you are designing to the spec sheet. NVIDIA's design guide states that **MAXN_SUPER at 40W requires a minimum of 8.0V on VDD_IN**. We ran MAXN_SUPER on 5V without issue, but we peaked at 30W — we never entered the regime that rule governs. Reaching a true 40W on a 5V rail would mean roughly 7.7A through VDD_IN, which we did not test and which the design guide advises against. Our data shows MAXN_SUPER is usable at 5V; it does not show the 8V requirement is unnecessary.

**I read that VDD_IN is limited to about 5A.**
That figure comes from NVIDIA forum posts, not from a datasheet — the Orin NX datasheet publishes no maximum VDD_IN current at all, and the design guide defers to a datasheet spec that does not exist. We measured 6.05A sustained for 45 minutes with no alarm and no ill effect. Note that not failing for 45 minutes is not the same as being within a long-term derating limit, so treat this as an observation rather than permission to design past it.

**Which power input should I use?**
The XT60 accepts a wide input and feeds an 8A buck, so it has the most headroom. The Molex Clik-Mate 5V input is rated 6A, which caps total board draw around 30W — enough for the module but with little left over. Both merge through ideal-diode ORing onto the same 5V rail, which the module shares with the carrier's 3.3V, 1.8V and two 1.5A 5V peripheral rails.

**How hot is too hot?**
Our soak plateaued at 91C junction temperature with the stock fan in open air and never throttled. If yours throttles, that is thermal, not electrical — check airflow and fan operation before suspecting the power path.

## Monitoring

`jtop` is installed and is the easiest option. For scripting, read the module's INA3221 directly:

```bash
H=$(echo /sys/bus/i2c/drivers/ina3221/*/hwmon/hwmon*)
grep -H . $H/in[1-3]_label      # in1 = VDD_IN, in2 = VDD_CPU_GPU_CV, in3 = VDD_SOC
cat $H/in1_input                # VDD_IN bus voltage, mV
cat $H/curr1_input              # VDD_IN current, mA
```

Multiply the two for module power. `curr1_crit` and `curr1_max` are a fixed over-current ceiling of 7784 mA wired to the SoC's throttle logic; it is the same value in every power mode, so it is a hardware guard rather than a per-mode budget.
