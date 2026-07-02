# 系统监控小工具 (PyQt6)

一个轻量的桌面端系统监控程序，展示系统配置并实时显示 CPU / 内存 / GPU 占用，支持**多 GPU**。

## 功能

- **系统配置**：操作系统、主机名、CPU 型号、物理/逻辑核心数、内存总量、GPU 列表
- **CPU**：总占用率 + 每个逻辑核心的占用率 + 当前频率/温度/功耗（温度功耗见下文）
- **GPU**：每卡使用率/显存/温度/功耗/核心频率/编解码利用率（编解码合并显示）
- **内存**：物理内存与交换分区的使用量/占比
- **网络**：总上下行速率，并在同一行右侧列出**占用网络最高的前几个进程**（需管理员，见下文）
- **GPU（多卡适配）**：每张卡单独一栏，显示使用率、显存占用、温度、功耗、**编码/解码（NVENC/NVDEC）利用率**，并在同一行末尾列出**占用该卡显存的进程名及占用量**（取前 5）
- **实时折线图**：CPU 总占用与每张 GPU 使用率的历史曲线（QPainter 自绘，约 60 秒窗口，无需额外依赖）
- **系统托盘**：点窗口 X 最小化到托盘（不退出），双击托盘图标恢复，右键菜单退出；托盘提示实时显示 CPU/内存占用
- **分区刷新频率**：CPU/GPU 使用率等跟随顶部「刷新间隔」下拉（100/250/500/1000ms）；
  内存与 GPU 显存固定 100ms（看着实时）；网络固定 1000ms（速率更稳）
- 深色界面，占用率按 绿/黄/红 着色

### CPU 功耗 / 温度

两项数据来源不同，方案也不同：

**功耗（开箱即用，无需任何特权/驱动）**
- 通过 Windows 内置的 **EMI 能量计量接口**暴露的性能计数器
  `\Energy Meter(*_PKG)\Power`（用 `win32pdh` 读取，值单位 mW，÷1000=W）。
- 由微软自己的签名驱动提供，**不受「内存完整性 (HVCI)」限制**，普通权限即可读。
- 这是 NZXT CAM 等工具显示 CPU 功率的同类途径。

**温度（需关闭内存完整性）**
- CPU 温度来自芯片内部 DTS 传感器，只存在于 **MSR 寄存器**，必须经内核驱动读取。
- 本程序通过 **pythonnet 进程内加载 `libs/LibreHardwareMonitorLib.dll`**（MPL-2.0），
  由其 WinRing0 驱动读 MSR 拿到「CPU Package」温度。需 .NET Framework 4.7.2+ 和管理员权限。
- **⚠️ 内存完整性 (HVCI) 会拦截 WinRing0**：开启时温度显示 **N/A**。如需温度，到
  「Windows 安全中心 → 设备安全性 → 内核隔离」关闭内存完整性并**重启**（自行权衡安全）。
- 注意：功耗不受此限制，关不关 HVCI 都能显示。`libs/` 目录随项目附带 LHM 及其依赖 DLL。

## 安装

```powershell
cd G:\claude_server\sysmonitor
pip install -r requirements.txt
```

> 没有 NVIDIA 显卡也能运行，GPU 区域会提示"未检测到"。

## 运行

```powershell
python monitor.py
```

## GPU 支持说明

- 当前 GPU 监控基于 **NVML**（NVIDIA Management Library），通过 `nvidia-ml-py` 调用。
  自动枚举所有 NVIDIA 显卡，逐卡显示，天然支持多 GPU。
- AMD / Intel 显卡暂不在 NVML 覆盖范围内；如有需要可后续接入
  `pyadl`（AMD）或厂商 SDK，`GpuBackend` 类已预留扩展点（`kind` 字段）。

### 每进程显存占用

- Windows 在 **WDDM 驱动模式**（消费级 / 工作站显卡的默认模式）下，NVML 与 `nvidia-smi`
  都**不暴露每进程显存**（`nvidia-smi` 会显示 “No running processes found”）。
- 因此本程序在 Windows 上改用系统自带的 **GPU 性能计数器**
  （`\GPU Process Memory(*)\Dedicated Usage`，即任务管理器的数据源）读取每进程显存。
- `luid` 与各卡的对应关系通过 **CUDA driver API**（`nvcuda.dll` 的 `cuDeviceGetLuid` +
  `cuDeviceGetPCIBusId`）动态获取，再与 NVML 的 PCI busId 精确匹配——不写死、不靠顺序假设，
  核显/软件适配器因不在 CUDA 设备列表中而自动排除。
- 读取计数器时使用底层 `PdhGetFormattedCounterArrayW`（数组）而非 `win32pdh` 的字典接口，
  以保留并累加同一进程在一张卡上的多块分配（典型如 WSL2 的 `vmwp.exe`，否则会被漏掉）。
- 进程信息每约 1 秒刷新一次（与界面刷新解耦），仅统计专用显存 ≥ 5 MB 的进程。
- WSL2 里的 GPU 占用会归属到宿主侧的虚拟机进程 `vmwp.exe`，可据此判断是 WSL 在占用。

### 每进程网络流量

- Windows 没有按进程的网络流量性能计数器，psutil 也不提供；任务管理器走的是 **ETW 内核追踪**。
- 本程序通过 `pywintrace` 订阅 `Microsoft-Windows-Kernel-Network`，按 PID 累加收发字节，
  在网络区右侧（暗色，与总量用 `│` 分隔）显示占用最高的前几个进程。
- **程序启动时会自动尝试 UAC 提权**（ETW 会话需要管理员权限）：
  - 同意提权 → 提权实例接管，网络区显示「总量 │ 按进程明细」的完整布局；
  - 拒绝 / 取消 → 当前实例继续以普通权限运行，网络区退回只显示总上下行速率的简要单行布局。
  - 两种情况程序都能正常打开，不会崩溃。
- 提权实例带 `--elevated` 参数以避免重复弹窗；提权失败不影响除「按进程网络流量」外的所有功能。

## 打包为 exe（可选）

```powershell
pip install pyinstaller
python -m PyInstaller --noconsole --onefile --name SysMonitor --add-data "libs/*.dll;libs" monitor.py
```
