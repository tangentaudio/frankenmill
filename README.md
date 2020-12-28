# frankenmill
Documentation, configs, and other info for my "FrankenMill" CNC milling machine.

## The "FrankenMill" mechanicals
* Base machine is a circa 2009 Grizzly G0463, aka Sieg X3, manual mill (now discontinued, see https://www.grizzly.com/products/grizzly-6-x-22-3-4-hp-mill-drill/g0463)
* ProMiCa MX3 ball screw & stepper mechanical CNC conversion (ProMiCa is now defunct, see https://web.archive.org/web/20091014231200/http://cnckits.com.au/product_mx3.php)
* Automation Direct NEMA23 (X, Y) and NEMA34 (Z) stepper motors
* Tormach PCNC770 head and 10,000RPM spindle grafted on with a custom conversion (see https://www.cnczone.com/forums/vertical-mill-lathe-project-log/201298-tormach-engineering-software-forum.html)

## CNC control
* LinuxCNC 2.6.0 with GUI customizations
* 3.5" form factor Atom-based embedded PC
* parallel-port based Mesa 7i43 FPGA motion controller 
* custom integration PC board
* Emerson Commander SK VFD for spindle drive, RS485 communication to controller (see https://github.com/tangentaudio/cmdrsk_vfd for LinuxCNC component driver)
