# frankenmill
Documentation, configs, and other info for my "FrankenMill" CNC milling machine.

## The "FrankenMill" mechanicals
* (2009) Base machine is a Grizzly G0463 (aka Sieg X3) manual mill (now discontinued, see https://www.grizzly.com/products/grizzly-6-x-22-3-4-hp-mill-drill/g0463)
* (2009) ProMiCa MX3 ball screw & stepper mechanical CNC conversion (ProMiCa is now defunct, see https://web.archive.org/web/20091014231200/http://cnckits.com.au/product_mx3.php)
* (2013) Custom grafting of Tormach PCNC770 head with 1HP 10,000RPM max belt-drive spindle (see https://www.cnczone.com/forums/vertical-mill-lathe-project-log/201298-tormach-engineering-software-forum.html)
* (2023) Custom linear rail conversion for colum (Z axis) with upgraded 25mm diameter ball screw to improve poor dovetails and badly designed Z axis from factory
* (2026) Custom double-ballnut conversion for X and Y axes to improve backlash from original ProMiCa kit
* Automation Direct NEMA23 (X, Y) open-loop stepper motors with 2:1 belt drive, StepperOnline NEMA34 (Z) closed-loop stepper motor with brake, direct drive via coupler.

## CNC control
* LinuxCNC 2.9.8
* 3.5" form factor Celeron-based embedded PC
* Mesa 7i80HD-16 Ethernet-based FPGA controller, Mesa 7i42TA breakout/protection board, Mesa 7i37-COM isolated I/O board
* Emerson Commander SK VFD for spindle drive, isolated RS485 modbus communication with controller PC (see https://github.com/tangentaudio/cmdrsk_vfd for LinuxCNC component driver)
* Gecko G203V open-loop stepper drivers (X, Y), StepperOnline closed-loop stepper driver (Z)
* 75V linear power supply for stepper drives
* 5V, 12V, 24V DIN-rail power supplies
* E-stop safety relay
