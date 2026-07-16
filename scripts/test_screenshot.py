import pyautogui
import time
import sys

time.sleep(2)

found = None
for w in pyautogui.getAllWindows():
    title = w.title
    if "系统监控" in title or "SysMonitor" in title or "sysmonitor" in title:
        found = w
        break

if found:
    found.activate()
    time.sleep(1)
    img = pyautogui.screenshot(
        region=(found.left, found.top, found.width, found.height)
    )
    out = "screenshot.png"
    img.save(out)
    print(f"Screenshot saved: {found.width}x{found.height}, title={found.title}")
else:
    all_titles = [w.title for w in pyautogui.getAllWindows() if w.title]
    print(
        f"Target window not found. Visible windows: {all_titles[:20]}", file=sys.stderr
    )
    img = pyautogui.screenshot()
    img.save("screenshot.png")
    print("Full-screen fallback saved", file=sys.stderr)

sys.exit(0)
