# Power & Performance

## Clock speeds

Show the current settings:
```
sudo jetson_clocks --show
```
Store the current settings:
```
sudo jetson_clocks --store
```
Maximize performance:
```
sudo jetson_clocks
```
Restore the previous settings:
```
sudo jetson_clocks --restore
```

## Super Mode

After flashing, enable MAXN SUPER mode (default flash target is already the Super config).

Orin Nano modules:
```
sudo nvpmodel -m 2
```
Orin NX modules:
```
sudo nvpmodel -m 0
```
