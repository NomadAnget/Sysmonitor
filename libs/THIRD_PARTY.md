# 第三方组件声明

本目录下的 DLL 为系统监控程序读取 CPU 温度所需的第三方 .NET 库，
随项目以二进制形式附带（经 pythonnet 进程内加载），未修改其源码。
许可证信息来自各 NuGet 包官方元数据。

## MPL-2.0 (Mozilla Public License 2.0)

| 组件 | 版本 | 项目地址 |
|------|------|---------|
| LibreHardwareMonitorLib | 0.9.6 | https://github.com/LibreHardwareMonitor/LibreHardwareMonitor |
| DiskInfoToolkit | 2.1.1 | LibreHardwareMonitor 组织 |
| RAMSPDToolkit-NDD | 1.5.0 | LibreHardwareMonitor 组织 |
| BlackSharp.Core | 1.1.2 | LibreHardwareMonitor 依赖 |

MPL-2.0 为文件级 copyleft：以二进制引用无需开源；仅当修改上述库源码
并分发时，需公开被修改文件。全文: https://www.mozilla.org/MPL/2.0/

## Apache License 2.0

| 组件 | 版本 | 版权 |
|------|------|------|
| HidSharp | 2.6.4 | Copyright 2010-2025 James F. Bellinger |

全文: https://www.apache.org/licenses/LICENSE-2.0

## MIT (Microsoft .NET 库)

System.Buffers 4.6.1、System.CodeDom 10.0.9、System.Memory 4.6.3、
System.Numerics.Vectors 4.6.1、System.Runtime.CompilerServices.Unsafe 6.1.2、
System.Security.AccessControl 6.0.1、System.Security.Principal.Windows 5.0.0、
System.Threading.AccessControl 10.0.9

Copyright (c) .NET Foundation and Contributors。
全文: https://opensource.org/licenses/MIT

## 备注

- 本项目对以上组件均为**未修改的二进制引用**，三类许可证下闭源分发均合规，
  义务为保留本声明文件。
- LibreHardwareMonitorLib 内嵌 WinRing0 内核驱动，可能被杀毒软件误报；
  系统开启「内存完整性 (HVCI)」时该驱动会被 Windows 拦截（CPU 温度显示 N/A）。
