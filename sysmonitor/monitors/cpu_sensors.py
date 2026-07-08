import ctypes
import os
import sys
import threading
import time

import psutil

from ..utils import res_path


class CpuSensors:
    def __init__(self):
        self.temp = None
        self.power = None
        self.freq = None
        self._stop = False
        self._wmi = None
        self._lhm_state = None
        self._is_admin = False
        if sys.platform.startswith("win"):
            try:
                self._is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:
                pass
            threading.Thread(target=self._loop, daemon=True).start()

    def _get_wmi_temp(self):
        try:
            if self._wmi is None:
                import win32com.client

                self._wmi = win32com.client.GetObject(
                    "winmgmts:{impersonationLevel=impersonate}!//./root/cimv2"
                )
            zones = self._wmi.ExecQuery(
                "SELECT * FROM Win32_PerfFormattedData_Counters_ThermalZoneInformation"
            )
            for z in zones:
                temp_k = z.Temperature
                if 200 < temp_k < 400:
                    return temp_k - 273.15
        except Exception:
            pass
        return None

    def _init_lhm(self):
        try:
            from pythonnet import load

            load("netfx")
            import clr

            libs = res_path("libs")
            if not os.path.exists(os.path.join(libs, "LibreHardwareMonitorLib.dll")):
                for cand in (
                    os.path.join(os.path.dirname(sys.executable), "libs"),
                    os.path.join(os.getcwd(), "libs"),
                ):
                    if os.path.exists(
                        os.path.join(cand, "LibreHardwareMonitorLib.dll")
                    ):
                        libs = cand
                        break
            if libs not in sys.path:
                sys.path.append(libs)
            os.environ["PATH"] = libs + os.pathsep + os.environ["PATH"]
            clr.AddReference("LibreHardwareMonitorLib")
            from LibreHardwareMonitor.Hardware import Computer, HardwareType, SensorType

            comp = Computer()
            comp.IsCpuEnabled = True
            comp.Open()
            return comp, HardwareType, SensorType
        except Exception:
            return None, None, None

    def _get_lhm_temp(self):
        comp, hw_type, sensor_type = self._lhm_state
        try:
            for hw in comp.Hardware:
                if hw.HardwareType == hw_type.Cpu:
                    hw.Update()
                    for s in hw.Sensors:
                        if (
                            s.Name == "CPU Package"
                            and s.SensorType == sensor_type.Temperature
                        ):
                            return float(s.Value)
        except Exception:
            pass
        return None

    def _loop(self):
        pdh = qh = ch = ch_freq = None
        base_mhz = None
        try:
            import win32pdh

            pdh = win32pdh
            qh = win32pdh.OpenQuery()
            ch = win32pdh.AddEnglishCounter(qh, r"\Energy Meter(*)\Power")
            ch_freq = win32pdh.AddEnglishCounter(
                qh, r"\Processor Information(_Total)\% Processor Performance"
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

        lhm_tried = False

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

            if self._is_admin:
                if not lhm_tried:
                    lhm_tried = True
                    self._lhm_state = self._init_lhm()
                if self._lhm_state[0] is not None:
                    temp = self._get_lhm_temp()
                    if temp is not None:
                        self.temp = temp
                        time.sleep(1)
                        continue
            temp = self._get_wmi_temp()
            if temp is not None:
                self.temp = temp

            time.sleep(1)

        try:
            if pdh is not None:
                pdh.CloseQuery(qh)
        except Exception:
            pass
        if self._lhm_state and self._lhm_state[0] is not None:
            try:
                self._lhm_state[0].Close()
            except Exception:
                pass

    def stop(self):
        self._stop = True
