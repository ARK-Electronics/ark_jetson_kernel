## Installing the OS from binaries
A single setup script is provided for your convenience. It will download the prebuilt release (35.3.1) and apply
the modfied device tree binaries (dtb).
```
./prebuilt_setup.sh
```
Alternatively you can visit the [nvidia official documentation](https://docs.nvidia.com/jetson/archives/r35.3.1/DeveloperGuide/text/IN/QuickStart.html#to-flash-the-jetson-developer-kit-operating-software).

---

## Building from source
If you want to further modify the device tree, you will need to build the kernel from source. A helper script is
provided that will download the necessary files.
```
./source_build_setup.sh
```
Alternatively you can visit the [nvidia official documentation](https://docs.nvidia.com/jetson/archives/r35.3.1/DeveloperGuide/text/SD/Kernel/KernelCustomization.html#building-the-kernel).
```
