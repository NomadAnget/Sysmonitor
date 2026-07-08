import sys
import threading
import time

import psutil


class CpuSensors:
    def __init__(self):
        self.temp = None
        self.temp_source = None
        self.power = None
        self.freq = None
        self._stop = False
        if sys.platform.startswith("win"):
            threading.Thread(target=self._loop, daemon=True).start()

    def _get_perf_temp(self):
        try:
            import win32com.client

            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
            data = wmi.ExecQuery(
                "SELECT * FROM Win32_PerfFormattedData_Counters_ThermalZoneInformation"
            )
            for d in data:
                hp = getattr(d, "HighPrecisionTemperature", None)
                if hp and hp > 0:
                    c = (hp / 10.0) - 273.15
                    if -50 < c < 150:
                        return c
                t = getattr(d, "Temperature", None)
                if t and t > 0:
                    c = t - 273.15
                    if -50 < c < 150:
                        return c
        except Exception:
            pass
        return None

    def _get_acpi_temp(self):
        try:
            import win32com.client

            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\wmi")
            zones = wmi.ExecQuery("SELECT * FROM MSAcpi_ThermalZoneTemperature")
            best = None
            for z in zones:
                k = z.CurrentTemperature
                if k and k > 0:
                    c = (k / 10.0) - 273.15
                    name = str(getattr(z, "InstanceName", "") or "")
                    is_cpu = "cpu" in name.lower()
                    if is_cpu and 0 < c < 120:
                        return c
                    if best is None or is_cpu:
                        best = c
            if best is not None:
                return best
            return None
        except Exception:
            return None

    def _loop(self):
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

        self.per_core_freqs = []

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
