# Mesa 7i80HD Modified Bitfiles for FrankenMill X3

For low-jitter stepper pulse generation with the ethernet-based 7i80HD on
LinuxCNC, it's necessary to use the DPLL module.  For some reason this
isn't built into most of the stock bitfiles.  The output from the stepgen is
very jittery without the DPLL, quite audible in the stepper motors.

## Frankenmill Usage

The currently used bitfile is `7i80hd_16_svst8_4D.bit` which is a modified
version of SVST8_4 that includes DPLL.

```
Configuration Name: HOSTMOT2

General configuration information:

  BoardName : MESA7I80
  FPGA Size: 16 KGates
  FPGA Pins: 256
  Number of IO Ports: 3
  Width of one I/O port: 24
  Clock Low frequency: 100.0000 MHz
  Clock High frequency: 200.0000 MHz
  IDROM Type: 3
  Instance Stride 0: 4
  Instance Stride 1: 64
  Register Stride 0: 256
  Register Stride 1: 256

Modules in configuration:

  Module: DPLL
  There are 1 of DPLL in configuration
  Version: 0
  Registers: 7
  BaseAddress: 7000
  ClockFrequency: 100.000 MHz
  Register Stride: 256 bytes
  Instance Stride: 4 bytes

  Module: WatchDog
  There are 1 of WatchDog in configuration
  Version: 0
  Registers: 3
  BaseAddress: 0C00
  ClockFrequency: 100.000 MHz
  Register Stride: 256 bytes
  Instance Stride: 4 bytes

  Module: IOPort
  There are 3 of IOPort in configuration
  Version: 0
  Registers: 5
  BaseAddress: 1000
  ClockFrequency: 100.000 MHz
  Register Stride: 256 bytes
  Instance Stride: 4 bytes

  Module: QCount
  There are 8 of QCount in configuration
  Version: 2
  Registers: 5
  BaseAddress: 3000
  ClockFrequency: 100.000 MHz
  Register Stride: 256 bytes
  Instance Stride: 4 bytes

  Module: PWM
  There are 8 of PWM in configuration
  Version: 0
  Registers: 5
  BaseAddress: 4100
  ClockFrequency: 200.000 MHz
  Register Stride: 256 bytes
  Instance Stride: 4 bytes

  Module: StepGen
  There are 4 of StepGen in configuration
  Version: 2
  Registers: 10
  BaseAddress: 2000
  ClockFrequency: 100.000 MHz
  Register Stride: 256 bytes
  Instance Stride: 4 bytes

  Module: LED
  There are 1 of LED in configuration
  Version: 0
  Registers: 1
  BaseAddress: 0200
  ClockFrequency: 100.000 MHz
  Register Stride: 256 bytes
  Instance Stride: 4 bytes

Configuration pin-out:

IO Connections for P1
Pin#                  I/O   Pri. func    Sec. func       Chan      Pin func        Pin Dir

 1                      0   IOPort       QCount           1        Quad-B          (In)
 3                      1   IOPort       QCount           1        Quad-A          (In)
 5                      2   IOPort       QCount           0        Quad-B          (In)
 7                      3   IOPort       QCount           0        Quad-A          (In)
 9                      4   IOPort       QCount           1        Quad-IDX        (In)
11                      5   IOPort       QCount           0        Quad-IDX        (In)
13                      6   IOPort       PWM              1        PWM             (Out)
15                      7   IOPort       PWM              0        PWM             (Out)
17                      8   IOPort       PWM              1        Dir             (Out)
19                      9   IOPort       PWM              0        Dir             (Out)
21                     10   IOPort       PWM              1        /Enable         (Out)
23                     11   IOPort       PWM              0        /Enable         (Out)
25                     12   IOPort       QCount           3        Quad-B          (In)
27                     13   IOPort       QCount           3        Quad-A          (In)
29                     14   IOPort       QCount           2        Quad-B          (In)
31                     15   IOPort       QCount           2        Quad-A          (In)
33                     16   IOPort       QCount           3        Quad-IDX        (In)
35                     17   IOPort       QCount           2        Quad-IDX        (In)
37                     18   IOPort       PWM              3        PWM             (Out)
39                     19   IOPort       PWM              2        PWM             (Out)
41                     20   IOPort       PWM              3        Dir             (Out)
43                     21   IOPort       PWM              2        Dir             (Out)
45                     22   IOPort       PWM              3        /Enable         (Out)
47                     23   IOPort       PWM              2        /Enable         (Out)

IO Connections for P2
Pin#                  I/O   Pri. func    Sec. func       Chan      Pin func        Pin Dir

 1                     24   IOPort       QCount           5        Quad-B          (In)
 3                     25   IOPort       QCount           5        Quad-A          (In)
 5                     26   IOPort       QCount           4        Quad-B          (In)
 7                     27   IOPort       QCount           4        Quad-A          (In)
 9                     28   IOPort       QCount           5        Quad-IDX        (In)
11                     29   IOPort       QCount           4        Quad-IDX        (In)
13                     30   IOPort       PWM              5        PWM             (Out)
15                     31   IOPort       PWM              4        PWM             (Out)
17                     32   IOPort       PWM              5        Dir             (Out)
19                     33   IOPort       PWM              4        Dir             (Out)
21                     34   IOPort       PWM              5        /Enable         (Out)
23                     35   IOPort       PWM              4        /Enable         (Out)
25                     36   IOPort       QCount           7        Quad-B          (In)
27                     37   IOPort       QCount           7        Quad-A          (In)
29                     38   IOPort       QCount           6        Quad-B          (In)
31                     39   IOPort       QCount           6        Quad-A          (In)
33                     40   IOPort       QCount           7        Quad-IDX        (In)
35                     41   IOPort       QCount           6        Quad-IDX        (In)
37                     42   IOPort       PWM              7        PWM             (Out)
39                     43   IOPort       PWM              6        PWM             (Out)
41                     44   IOPort       PWM              7        Dir             (Out)
43                     45   IOPort       PWM              6        Dir             (Out)
45                     46   IOPort       PWM              7        /Enable         (Out)
47                     47   IOPort       PWM              6        /Enable         (Out)

IO Connections for P3
Pin#                  I/O   Pri. func    Sec. func       Chan      Pin func        Pin Dir

 1                     48   IOPort       StepGen          0        Step/Table1     (Out)
 3                     49   IOPort       StepGen          0        Dir/Table2      (Out)
 5                     50   IOPort       StepGen          0        Table3          (Out)
 7                     51   IOPort       StepGen          0        Table4          (Out)
 9                     52   IOPort       StepGen          0        Table5          (Out)
11                     53   IOPort       StepGen          0        Table6          (Out)
13                     54   IOPort       StepGen          1        Step/Table1     (Out)
15                     55   IOPort       StepGen          1        Dir/Table2      (Out)
17                     56   IOPort       StepGen          1        Table3          (Out)
19                     57   IOPort       StepGen          1        Table4          (Out)
21                     58   IOPort       StepGen          1        Table5          (Out)
23                     59   IOPort       StepGen          1        Table6          (Out)
25                     60   IOPort       StepGen          2        Step/Table1     (Out)
27                     61   IOPort       StepGen          2        Dir/Table2      (Out)
29                     62   IOPort       StepGen          2        Table3          (Out)
31                     63   IOPort       StepGen          2        Table4          (Out)
33                     64   IOPort       StepGen          2        Table5          (Out)
35                     65   IOPort       StepGen          2        Table6          (Out)
37                     66   IOPort       StepGen          3        Step/Table1     (Out)
39                     67   IOPort       StepGen          3        Dir/Table2      (Out)
41                     68   IOPort       StepGen          3        Table3          (Out)
43                     69   IOPort       StepGen          3        Table4          (Out)
45                     70   IOPort       StepGen          3        Table5          (Out)
47                     71   IOPort       StepGen          3        Table6          (Out)
```

## Program the Bitfile

To program the bitfile, use mesaflash:

```
mesaflash --device 7i80 --addr 192.168.1.121 --write 7i80hd_16_svst8_4D.bit
```

then reload the FPGA to run the new bitfile:

```
mesaflash --device 7i80 --addr 192.168.1.121 --reload
```

The `.HAL` file needs to have a couple lines added to use the DPLL:

```
setp hm2_7i80.0.dpll.01.timer-us -100
setp hm2_7i80.0.stepgen.timer-number 1
```