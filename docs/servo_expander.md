# ARK Servo Expander on the Just a Jetson

A Jetson does not provide PWM itself. Instead, you can use an [ARK Servo Expander](https://docs.arkelectron.com/products/accessories/ark-servo-expander) on I2C.

## Wiring

Connect the four I2C signals (GND, SCL, SDA, and 5V) between the JAJ's UART2/I2C0 connector and the expander. See the pinouts:

- [Servo Expander pinout](https://docs.arkelectron.com/products/accessories/ark-servo-expander/pinout)
- [JAJ UART2/I2C0 connector](https://docs.arkelectron.com/products/embedded-computers/ark-just-a-jetson/pinout#uart2-i2c0-6-pin-jst-gh)

## Pick an address

The expander is a [PCA9685](https://www.nxp.com/docs/en/data-sheet/PCA9685.pdf) and ships at I2C address `0x40`. That address is already taken by the INA3221 power monitor on the Jetson module, so the two will collide and neither works until you move the expander somewhere else.

The address is set by a row of 6 DNP'd resistors on the expander. Short the A0 resistor to change the address to `0x41`. Any address other than `0x40` is fine but `0x41` is the easy choice. If you run several expanders, give each its own address (`0x41`, `0x42`, and so on).

## Check that it shows up

The UART2/I2C0 connector is `/dev/i2c-1` in Linux. Connector names don't line up with kernel bus numbers, so check the [I2C bus map](i2c.md) if you're ever unsure which is which. Scan the bus:

```bash
sudo i2cdetect -y -r 1
```

```
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:          -- -- -- -- -- -- -- -- -- -- -- -- -- 
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- 
20: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
30: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- 
40: UU 41 -- -- -- -- -- -- -- -- -- -- -- -- -- --
50: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- 
60: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- 
70: -- -- -- -- -- -- -- --                         
```

`0x41` is the expander, sitting next to the module's INA3221 at `0x40`. Don't forget the `-r`: without it, `i2cdetect` skips `0x40`–`0x4F` and the bus looks empty even when everything is wired up correctly.

## Drive a servo

PiPCA9685](https://github.com/barulicm/PiPCA9685):

```bash
git clone https://github.com/barulicm/PiPCA9685.git
cd PiPCA9685 && cmake -B build && cmake --build build && sudo cmake --install build
```

Sweep channel 0 between 900, 1500, and 2100 µs at 50 Hz:

```cpp
#include <unistd.h>
#include <PiPCA9685/PCA9685.h>

int main() {
    PiPCA9685::PCA9685 pca{"/dev/i2c-1", 0x41};   // bus 1, reworked address

    constexpr double FREQUENCY_HZ   = 50.0;
    constexpr double PERIOD_US      = 1'000'000.0 / FREQUENCY_HZ;  // 20000 us
    constexpr int    PWM_RESOLUTION = 4096;

    constexpr auto to_counts = [](double pulse_us) -> int {
        return static_cast<int>(pulse_us / PERIOD_US * PWM_RESOLUTION);
    };
    constexpr int SERVO_LOW  = to_counts( 900.0);  // 184
    constexpr int SERVO_MID  = to_counts(1500.0);  // 307
    constexpr int SERVO_HIGH = to_counts(2100.0);  // 430

    pca.set_pwm_freq(FREQUENCY_HZ);
    while (true) {
        pca.set_pwm(0, 0, SERVO_LOW);  usleep(1'000'000);
        pca.set_pwm(0, 0, SERVO_MID);  usleep(1'000'000);
        pca.set_pwm(0, 0, SERVO_HIGH); usleep(1'000'000);
    }
}
```

One thing to watch: the PCA9685 keeps time with an internal oscillator that's only accurate to a few percent, so a nominal 50 Hz might really be 48–52 Hz, with the pulse widths shifted to match. Servos tolerate this fine. If you need exact timing, measure the real rate and correct for it.
