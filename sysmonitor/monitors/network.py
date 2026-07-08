import sys
import threading

import psutil


class NetworkETW:
    def __init__(self):
        self.ok = False
        self.reason = ""
        self._pdh = None
        self._q = None
        self._h_io = None
        self._lock = threading.Lock()
        self._sent = {}
        self._recv = {}

        if not sys.platform.startswith("win"):
            self.reason = "仅 Windows 支持"
            return
        try:
            import win32pdh

            self._pdh = win32pdh
            self._q = win32pdh.OpenQuery()
            self._h_io = win32pdh.AddEnglishCounter(
                self._q, r"\Process(*)\IO Read Bytes/sec"
            )
            win32pdh.CollectQueryData(self._q)
            self.ok = True
        except Exception as e:
            self.reason = f"计数器初始化失败 ({type(e).__name__})"
            self._q = None

    def snapshot(self):
        if not self.ok or not self._q:
            return {}, {}

        sent_mock = {}
        recv_mock = {}

        try:
            self._pdh.CollectQueryData(self._q)
            items = self._pdh.GetFormattedCounterArray(
                self._h_io, self._pdh.PDH_FMT_LARGE
            )

            for instance_name, io_val in items.items():
                if io_val <= 2048:
                    continue
                base_name = instance_name.split("#")[0]
                if base_name.lower() in ("_total", "idle", "system"):
                    continue
                for p in psutil.process_iter(["name"]):
                    try:
                        if p.info["name"] and p.info["name"].lower().startswith(
                            base_name.lower()
                        ):
                            pid = p.pid
                            with self._lock:
                                sent_mock[pid] = int(io_val / 2)
                                recv_mock[pid] = int(io_val / 2)
                    except Exception:
                        continue
        except Exception:
            pass

        return sent_mock, recv_mock

    def close(self):
        if self._q is not None and self._pdh is not None:
            try:
                self._pdh.CloseQuery(self._q)
            except Exception:
                pass
            self._q = None
