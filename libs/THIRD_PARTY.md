# 第三方组件声明

本目录下的 DLL 为系统监控程序读取 CPU 温度/功耗所需的第三方 .NET 库，
随项目附带（通过 pythonnet 进程内加载），并非本项目自有代码。

## LibreHardwareMonitorLib.dll

- 项目：LibreHardwareMonitor — https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
- 许可证：**Mozilla Public License 2.0 (MPL-2.0)** —— https://www.mozilla.org/MPL/2.0/
- 用途：通过其内核驱动 WinRing0 读取 CPU 的 MSR（温度、RAPL 功耗）。
- 说明：本项目仅以二进制形式引用该库、未修改其源码；如需对应源码，
  请见上述项目地址。

## 依赖 DLL（随 LibreHardwareMonitorLib 一并需要）

HidSharp、DiskInfoToolkit、RAMSPDToolkit-NDD、BlackSharp.Core、
System.Memory、System.Buffers、System.Numerics.Vectors、
System.Runtime.CompilerServices.Unsafe、System.CodeDom、
System.Security.AccessControl、System.Security.Principal.Windows、
System.Threading.AccessControl —— 均来自 NuGet，遵循各自的开源许可证
（多为 MIT / MPL-2.0 / .NET Foundation 许可证）。

## 注意

- WinRing0 内核驱动可能被杀毒软件误报（因其可读底层硬件）。
- 系统开启「内存完整性 (HVCI)」时该驱动会被 Windows 拦截，导致读不到温度/功耗。
