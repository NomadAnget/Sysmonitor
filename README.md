# SysMonitor — 轻量级 Windows 硬件监控面板

CPU / 内存 / 网络 / 多 GPU 一目了然，无需安装即开即用。

| 随系统 | 暗色 | 浅色 |
|:---:|:---:|:---:|
| ![随系统](docs/theme-system.png) | ![暗色](docs/theme-dark.png) | ![浅色](docs/theme-light.png) |

## 亮点

- **零驱动** — CPU 功耗、频率、每进程显存/网络均走 Windows 内置计数器，免管理员权限
- **全 GPU 覆盖** — NVIDIA 全指标（NVML）+ AMD 核心指标（LibreHardwareMonitor）自动切换
- **每进程溯源** — 显存按 CUDA LUID→PCI BusId 精确归卡，网络流量按进程排行
- **原生美学** — Windows 11 Mica 云母材质 + 三态主题（系统/暗/浅），实时跟随系统强调色
- **即开即用** — 单文件 exe，无运行时依赖，加载动画直观反馈启动进度

## 功能

**系统配置** — 操作系统 / 主机名 / CPU 型号（注册表 `ProcessorNameString`，与任务管理器一致） / 核心数 / 内存总量 / 全部 GPU 型号与显存

**CPU**
- 总占用 + 每逻辑核心占用（进度条网格）+ 历史曲线
- 温度 — LibreHardwareMonitor 读取（需管理员权限 + .NET Framework，无权限时安全降级）
- 功耗 — Windows EMI 能量计量计数器（`\Energy Meter(*_PKG)\Power`），无需驱动
- 实时频率 — `% Processor Performance` × 标称频率，睿频/降频实时反映
- 高占用进程排行

**内存**
- 物理 / 交换分区占用、内存条频率（WMI）
- 内存占用最高的进程排行（后台线程，不阻塞 UI）
- 显存占用同步显示在 GPU 卡片中

**网络**
- 总上下行速率，实时 KB/s
- 每进程流量排行（PDH `\Process(*)\IO Read Bytes/sec`）

**GPU（多卡自适应）**
- 每卡独立卡片：使用率 + 历史曲线、显存占用
- 温度 / 功耗 / 核心频率
- NVIDIA 专属：NVENC/NVDEC 编解码利用率、PCIe 链路（`PCIe x8 4.0 @ x1 1.1` 格式）实时显示在标题
- 每进程显存占用 — Windows `GPU Process Memory` 计数器（任务管理器同源），按 CUDA LUID ↔ PCI BusId 精确归卡

**界面**
- 三态主题：随系统（实时跟随深浅色与强调色变化）/ 暗色 / 浅色，一键切换
- Windows 11 Mica 云母材质，原生 DWM 渲染
- 窗口置顶开关；高度按核心数 / GPU 数动态计算
- 系统托盘：关闭最小化到托盘，双击恢复，右键退出
- CPU/GPU 刷新间隔可调（100–1000ms），内存与进程固定 500/1000ms
- 加载界面（Splash）：启动时显示初始化进度（PawnIO 驱动 → CPU 传感器 → GPU 检测 → 网络监控）

## 快速开始

```powershell
uv sync
uv run python -m sysmonitor     # 或 python main.py
```

无需管理员权限即可使用大部分功能。CPU 温度读取需管理员权限 + .NET Framework（自动降级）。

## 构建 exe

```powershell
.\build.ps1
```

一键编译为 [Nuitka](https://nuitka.net/) 原生单文件 `SysMonitor.exe`，含 `--windows-uac-admin` 提权清单。

构建前自动执行 `scripts/get-deps.ps1` 下载 LHM DLL 与 PawnIO 驱动安装包。

推 tag 即自动发版：

```powershell
git tag v1.x.x
git push origin master v1.x.x
```

- **GitHub Actions** — `windows-latest` runner，产物上传 artifact 并附加到 Release
- **Forgejo Actions** — 自托管 Windows runner，内网 API 直传 Release

## 数据来源

| 数据 | 来源 | 条件 |
|------|------|------|
| CPU 总占用 / 每核心 | `psutil` | — |
| CPU 温度 | LibreHardwareMonitor（.NET / LHM） | 管理员权限 + .NET Framework 4.7.2+，无权限时降级（不显示） |
| CPU 功耗 | EMI 能量计数器 `\Energy Meter(*_PKG)\Power` | 免管理员 |
| CPU 实时频率 | PDH `% Processor Performance` × 标称频率 | 免管理员 |
| 内存 / 交换占用 | `psutil` | — |
| 内存频率 | WMI `Win32_PhysicalMemory` | 免管理员 |
| 网络总速率 | `psutil.net_io_counters` | — |
| 每进程网络流量 | PDH `\Process(*)\IO Read Bytes/sec` | 免管理员（近似值，按进程名聚合） |
| GPU 全部指标（NVIDIA） | NVML（`nvidia-ml-py`） | NVIDIA 显卡 |
| GPU 核心指标（AMD） | LibreHardwareMonitor（.NET / LHM） | AMD 显卡 + 管理员权限 |
| 每进程显存占用 | PDH `\GPU Process Memory(*)\Dedicated Usage` | 免管理员，WDDM 模式任意 GPU |

## 项目结构

```
sysmonitor/
├── core/           # 入口、窗口、单例、加载界面
├── data/           # 数据采集与监控后端
│   └── monitors/   # GPU / 网络 / CPU 传感器
├── ui/             # 界面组件（仪表行、曲线、核心网格）
└── utils/          # 配置、工具、PawnIO 驱动、提权
```

## 许可证

[MIT](LICENSE)
