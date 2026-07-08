import os
import sys
import threading
import time

import psutil

from ..pawnio import ensure_pawnio


class CpuSensors:
    def __init__(self):
        self.temp = None
        self.temp_source = None
        self.power = None
        self.freq = None
        self.per_core_freqs = []
        self._stop = False
        self._lhm = None
        self._lhm_hw = None
        self._lhm_st = None
        if sys.platform.startswith("win"):
            ensure_pawnio()
            threading.Thread(target=self._loop, daemon=True).start()

    def _init_lhm(self):
        from ..utils import res_path

        libs = res_path("libs", "LHM")
        if not os.path.exists(os.path.join(libs, "LibreHardwareMonitorLib.dll")):
            return
        if libs not in sys.path:
            sys.path.insert(0, libs)
        os.environ["PATH"] = libs + os.pathsep + os.environ.get("PATH", "")
        try:
            from pythonnet import load

            load("netfx")
        except Exception:
            return
        try:
            import clr

            clr.AddReference("LibreHardwareMonitorLib")
            from LibreHardwareMonitor.Hardware import Computer, HardwareType, SensorType

            comp = Computer()
            comp.IsCpuEnabled = True
            comp.Open()
            self._lhm = comp
            self._lhm_hw = HardwareType
            self._lhm_st = SensorType
        except Exception:
            self._lhm = None

    def _loop(self):
        self._init_lhm()

        pdh = qh = ch = ch_freq = ch_core = None
        base_mhz = None
        try:
            import win32pdh

            pdh = win32pdh
            qh = win32pdh.OpenQuery()
            ch = win32pdh.AddEnglishCounter(qh, r"\Energy Meter(*)\Power")
            ch_freq = win32pdh.AddEnglishCounter(
                qh, r"\Processor Information(_Total)\% Processor Performance"
            )
            ch_core = win32pdh.AddEnglishCounter(
                qh, r"\Processor Information(*)\% Processor Performance"
            )
            win32pdh.CollectQueryData(qh)
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                ) as k:
                    base_mhz = winreg.QueryValueEx(k, "~MHz")[0]
            except Exception:
                f = psutil.cpu_freq()
                base_mhz = (f.max or f.current) if f else None
        except Exception:
            pdh = None

        _temp_valid = 0
        _temp_last = None

        while not self._stop:
            if pdh is not None:
                try:
                    pdh.CollectQueryData(qh)
                    arr = pdh.GetFormattedCounterArray(ch, pdh.PDH_FMT_DOUBLE)
                    pkg = sum(
                        v for n, v in arr.items() if n.lower().endswith("_pkg") and v
                    )
                    self.power = pkg / 1000.0 if pkg else None
                except Exception:
                    pass
                if ch_freq is not None and base_mhz:
                    try:
                        _, perf = pdh.GetFormattedCounterValue(
                            ch_freq, pdh.PDH_FMT_DOUBLE
                        )
                        if perf and perf > 0:
                            self.freq = base_mhz * perf / 100.0
                    except Exception:
                        pass
                if ch_core is not None and base_mhz:
                    try:
                        raw = pdh.GetFormattedCounterArray(ch_core, pdh.PDH_FMT_DOUBLE)
                        cores = []
                        for name, pct in raw.items():
                            if name == "_Total":
                                continue
                            parts = name.split(",")
                            if len(parts) == 2:
                                try:
                                    cores.append(
                                        (int(parts[1]), base_mhz * pct / 100.0)
                                    )
                                except ValueError:
                                    pass
                        cores.sort(key=lambda x: x[0])
                        self.per_core_freqs = [c[1] for c in cores]
                    except Exception:
                        pass

            # read temperature via LHM (debounced: require 2 consecutive valid samples)
            _temp_valid = max(0, _temp_valid - 1)
            if self._lhm is not None:
                try:
                    for hw in self._lhm.Hardware:
                        if hw.HardwareType == self._lhm_hw.Cpu:
                            hw.Update()
                            for s in hw.Sensors:
                                if (
                                    s.SensorType == self._lhm_st.Temperature
                                    and s.Value is not None
                                ):
                                    _temp_last = s.Value
                                    if s.Name == "CPU Package":
                                        _temp_valid = 3
                                        self.temp = s.Value
                                        self.temp_source = "LHM"
                                    elif _temp_valid < 2 and s.Name in (
                                        "Core Max",
                                        "Core Average",
                                    ):
                                        _temp_valid = 2
                                        self.temp = s.Value
                                        self.temp_source = "LHM"
                except Exception:
                    pass

            if _temp_valid == 0:
                self.temp = None
                self.temp_source = None

            time.sleep(1)

        try:
            if pdh is not None:
                pdh.CloseQuery(qh)
        except Exception:
            pass

    def stop(self):
        self._stop = True
