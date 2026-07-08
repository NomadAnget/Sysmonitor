# SysMonitor — PyQt6 系统监控工具

Windows 桌面系统监控面板：CPU / 内存 / 网络 / 多 GPU 实时监控。

## 功能

**CPU**
- 总占用 + 每逻辑核心占用（进度条网格）+ 历史曲线
- 温度：Windows 内置性能计数器（无需管理员权限）
- 功耗：Windows EMI 能量计量计数器
- 实时频率：每核实时频率显示
- 进程排行：CPU 消耗最高的进程列表

**内存**
- 物理 / 交换分区占用，内存条频率
- 内存占用最高的进程排行

**网络**
- 总上下行速率
- 每进程网络流量排行

**GPU（多卡自适应）**
- 每卡独立卡片：使用率 + 历史曲线、显存占用
- 温度 / 功耗 / 核心频率 / 编解码利用率
- PCIe 链路状态
- 每进程显存占用

**界面**
- 三态主题：随系统 / 暗色 / 浅色
- Windows 11 Mica 云母材质
- 窗口置顶开关
- 系统托盘
- 刷新频率可调

## 运行

```powershell
uv sync
python -m sysmonitor
```

**无需管理员权限。** 无需额外驱动或 DLL。

## 数据来源

| 数据 | 来源 | 条件 |
|------|------|------|
| CPU 温度 | `Win32_PerfFormattedData_Counters_ThermalZoneInformation` | 免管理员 |
| CPU 功耗 | EMI 能量计数器 | 免管理员 |
| CPU 频率 | PDH `% Processor Performance` | 免管理员 |
| 内存 | psutil | — |
| 网络 | psutil + PDH IO 计数器 | 免管理员 |
| GPU | NVML (nvidia-ml-py) | 仅 NVIDIA |

## 构建 exe

```powershell
.\build.ps1
```