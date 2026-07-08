# Third-Party Components

This project bundles the following third-party binary components:

## LibreHardwareMonitorLib

- **Source**: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
- **License**: [MPL-2.0](https://www.mozilla.org/en-US/MPL/2.0/)
- **Version**: 0.9.6
- **Files**: `libs/LHM/LibreHardwareMonitorLib.dll`, `libs/LHM/System.Memory.dll`,
  `libs/LHM/System.Runtime.CompilerServices.Unsafe.dll`, `libs/LHM/System.Numerics.Vectors.dll`
- **Purpose**: Monitor temperature sensors, fan speeds, voltages, load and clock speeds.
  Used to read CPU and AMD GPU sensor data.

## PawnIO

- **Source**: https://github.com/namazso/PawnIO
- **Setup repo**: https://github.com/namazso/PawnIO.Setup
- **License**: [PawnIO License](https://github.com/namazso/PawnIO/blob/master/LICENSE)
- **Version**: 2.2.0
- **File**: `libs/PawnIO_setup.exe`
- **Purpose**: Scriptable kernel driver for hardware access. Required by
  LibreHardwareMonitorLib to read CPU temperature/frequency via MSR registers.
