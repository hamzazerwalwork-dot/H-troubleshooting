#!/usr/bin/env python3
"""
iphone_diag.py - USB diagnostic tool for iPhone (tested design: iPhone 13)

Requires libimobiledevice command-line tools:
  macOS:    brew install libimobiledevice
  Windows:  download the libimobiledevice Windows build (or use WSL) and add to PATH
            (Apple Devices app or iTunes must be installed for the USB driver)
  Linux:    sudo apt install libimobiledevice-utils usbmuxd

Usage:
  python3 iphone_diag.py            # run all checks, save report
  python3 iphone_diag.py --no-logs  # skip crash log pull (faster)
"""
import argparse
import datetime
import plistlib
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REQUIRED = ["idevice_id", "ideviceinfo", "idevicediagnostics", "idevicecrashreport"]

findings = []   # (level, message) level: OK / WARN / BAD
lines = []      # report text
DEMO = False    # --demo feeds made-up data so the program can be tested without a phone


def demo_plist(cmd):
    """Fake responses that mimic an iPhone 13 running iOS 26.6.1."""
    if cmd[0] == "ideviceinfo" and "com.apple.disk_usage" in cmd:
        return {"TotalDataCapacity": 119_000_000_000, "TotalDataAvailable": 14_000_000_000}
    if cmd[0] == "ideviceinfo":
        return {"ProductType": "iPhone14,5", "ProductVersion": "26.6.1",
                "BuildVersion": "DEMO", "DeviceName": "Demo iPhone 13",
                "SerialNumber": "DEMO000000", "ActivationState": "Activated",
                "PasswordProtected": True}
    if cmd[0] == "idevicediagnostics":
        return {"IORegistry": {"DesignCapacity": 3227, "NominalChargeCapacity": 2650,
                               "CycleCount": 612, "Temperature": 3120,
                               "Voltage": 4010, "IsCharging": False}}
    return None


def demo_crash_logs(outdir):
    names = (["panic-full-2026-10-01-120000.000.ips"] * 1 +
             ["panic-full-2026-10-04-093000.000.ips"] * 1 +
             [f"SpringBoard-2026-10-0{i}-101010.ips" for i in range(1, 5)] +
             [f"Safari-2026-10-0{i}-111111.ips" for i in range(1, 3)])
    seen = {}
    for n in names:
        # make each name unique so files do not overwrite each other
        seen[n] = seen.get(n, 0) + 1
        stem, ext = n.rsplit(".", 1)
        (Path(outdir) / f"{stem}_{seen[n]}.{ext}").write_text("demo")


def out(text=""):
    print(text)
    lines.append(text)


def flag(level, msg):
    findings.append((level, msg))
    icon = {"OK": "[ OK ]", "WARN": "[WARN]", "BAD": "[FAIL]"}[level]
    out(f"  {icon} {msg}")


def run(cmd, timeout=60):
    if DEMO:
        if cmd[0] == "idevice_id":
            return 0, b"00008110-000A1B2C3D4E5F60\n", ""
        if cmd[0] == "idevicecrashreport":
            demo_crash_logs(cmd[-1])
            return 0, b"", ""
        return 0, b"", ""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr.decode(errors="replace")
    except subprocess.TimeoutExpired:
        return 124, b"", "timed out"
    except FileNotFoundError:
        return 127, b"", "tool not found"


def get_plist(cmd):
    if DEMO:
        return demo_plist(cmd)
    code, stdout, err = run(cmd)
    if code != 0 or not stdout.strip():
        return None
    try:
        return plistlib.loads(stdout)
    except Exception:
        return None


def check_tools():
    if DEMO:
        return
    missing = [t for t in REQUIRED if shutil.which(t) is None]
    if missing:
        out("Missing tools: " + ", ".join(missing))
        out("Install libimobiledevice first (see the top of this file).")
        sys.exit(1)


def check_connection():
    out("== 1. Connection ==")
    code, stdout, err = run(["idevice_id", "-l"])
    ids = stdout.decode().split()
    if not ids:
        flag("BAD", "No iPhone detected. Try another cable/USB port, unlock the phone, "
                    "and tap 'Trust This Computer'.")
        return False
    flag("OK", f"iPhone detected (UDID {ids[0][:8]}...)")
    return True


def check_device_info():
    out("\n== 2. Device info ==")
    info = get_plist(["ideviceinfo", "-x"])
    if not info:
        flag("BAD", "Could not read device info. Unlock the phone and accept the Trust prompt.")
        return
    model = info.get("ProductType", "?")
    out(f"  Model id:      {model}   (iPhone 13 = iPhone14,5)")
    out(f"  iOS version:   {info.get('ProductVersion', '?')} (build {info.get('BuildVersion', '?')})")
    out(f"  Name:          {info.get('DeviceName', '?')}")
    out(f"  Serial:        {info.get('SerialNumber', '?')}")
    out(f"  Activated:     {info.get('ActivationState', '?')}")
    if info.get("PasswordProtected") is False:
        flag("WARN", "No passcode is set on the phone.")
    if info.get("ActivationState") not in (None, "Activated"):
        flag("BAD", f"Activation state is {info.get('ActivationState')}.")
    out("  Tip: compare the iOS version with Settings > General > Software Update.")


def check_battery():
    out("\n== 3. Battery ==")
    data = get_plist(["idevicediagnostics", "ioregentry", "AppleSmartBattery"])
    if not data:
        flag("WARN", "Could not read battery registry (unlock phone and retry).")
        return
    reg = data.get("IORegistry", data)
    design = reg.get("DesignCapacity")
    nominal = reg.get("NominalChargeCapacity") or reg.get("AppleRawMaxCapacity")
    cycles = reg.get("CycleCount")
    temp = reg.get("Temperature")
    volt = reg.get("Voltage")
    charging = reg.get("IsCharging")

    if cycles is not None:
        out(f"  Cycle count:   {cycles}")
        if cycles > 1000:
            flag("WARN", f"{cycles} cycles is high; Apple rates iPhone batteries for ~1000.")
        else:
            flag("OK", "Cycle count is within normal range.")
    if design and nominal:
        health = round(nominal / design * 100, 1)
        out(f"  Capacity:      {nominal} / {design} mAh  ({health}%)")
        if health < 80:
            flag("BAD", f"Battery health ~{health}% - below 80%, replacement recommended.")
        elif health < 90:
            flag("WARN", f"Battery health ~{health}% - showing some wear.")
        else:
            flag("OK", f"Battery health ~{health}%.")
    else:
        flag("WARN", "Capacity values unavailable; check Settings > Battery > Battery Health.")
    if temp is not None:
        c = temp / 100.0
        out(f"  Temperature:   {c:.1f} C")
        if c > 40:
            flag("WARN", f"Battery is warm ({c:.1f} C). Let it cool and re-test.")
    if volt is not None:
        out(f"  Voltage:       {volt} mV")
    if charging is not None:
        out(f"  Charging now:  {charging}")


def check_storage():
    out("\n== 4. Storage ==")
    d = get_plist(["ideviceinfo", "-q", "com.apple.disk_usage", "-x"])
    if not d:
        flag("WARN", "Could not read storage info.")
        return
    total = d.get("TotalDataCapacity")
    free = d.get("TotalDataAvailable")
    if total and free:
        gb = 1000 ** 3
        pct = free / total * 100
        out(f"  Free: {free / gb:.1f} GB of {total / gb:.1f} GB ({pct:.0f}% free)")
        if pct < 10:
            flag("BAD", "Under 10% free. Delete apps/media; low storage causes lag and crashes.")
        elif pct < 15:
            flag("WARN", "Under 15% free; consider freeing space.")
        else:
            flag("OK", "Storage headroom is fine.")


def check_crash_logs():
    out("\n== 5. Crash logs ==")
    tmp = Path(tempfile.mkdtemp(prefix="iphone_crash_"))
    code, _, err = run(["idevicecrashreport", "-e", "-k", str(tmp)], timeout=180)
    files = [f for f in tmp.rglob("*") if f.is_file()]
    if code != 0 and not files:
        flag("WARN", f"Could not pull crash logs: {err.strip()[:120]}")
        return
    out(f"  Pulled {len(files)} log file(s) to {tmp}")
    kinds = Counter()
    panics = 0
    for f in files:
        name = f.name
        if "panic" in name.lower():
            panics += 1
        base = name.split("-20")[0].split(".")[0]
        kinds[base] += 1
    if kinds:
        out("  Most frequent:")
        for name, n in kinds.most_common(5):
            out(f"    {n:3d} x {name}")
    if panics >= 3:
        flag("BAD", f"{panics} kernel panic logs found - often a hardware fault. Contact Apple Support.")
    elif panics:
        flag("WARN", f"{panics} kernel panic log(s) found. Watch if it repeats.")
    else:
        flag("OK", "No kernel panics found.")
    top = kinds.most_common(1)
    if top and top[0][1] >= 10:
        flag("WARN", f"'{top[0][0]}' crashed {top[0][1]} times - update or reinstall that app/process.")


def manual_checklist():
    out("\n== 6. Manual checks (do these on the phone) ==")
    for item in [
        "Touch: draw across the whole screen in Notes; look for dead zones.",
        "Display: view full white and black screens for dead pixels/discoloration.",
        "Speaker/mic: record a Voice Memo and play it back; test a call on speaker.",
        "Cameras: test rear, ultra-wide, front, flashlight; check for blur or black screen.",
        "Face ID: Settings > Face ID & Passcode > re-scan.",
        "Buttons: volume up/down, side button, ring/silent switch.",
        "Proximity sensor: screen should go dark during a call at your ear.",
        "Charging: try another cable and adapter; clean the Lightning port gently with a toothpick.",
        "Wi-Fi/Bluetooth/cellular: toggle Airplane Mode; test another network.",
    ]:
        out(f"  [ ] {item}")


def summary():
    out("\n== Summary ==")
    bad = [m for l, m in findings if l == "BAD"]
    warn = [m for l, m in findings if l == "WARN"]
    if bad:
        out(f"  {len(bad)} problem(s):")
        for m in bad:
            out(f"    - {m}")
    if warn:
        out(f"  {len(warn)} warning(s):")
        for m in warn:
            out(f"    - {m}")
    if not bad and not warn:
        out("  Everything the software could check looks healthy.")
    out("\n  If problems continue: back up, then try Restore iPhone, or start a free")
    out("  remote diagnostic at getsupport.apple.com.")


def main():
    ap = argparse.ArgumentParser(description="iPhone USB diagnostic")
    ap.add_argument("--no-logs", action="store_true", help="skip crash log pull")
    ap.add_argument("--demo", action="store_true",
                    help="use made-up iPhone 13 data (no phone or tools needed)")
    args = ap.parse_args()

    global DEMO
    DEMO = args.demo

    check_tools()
    out(f"iPhone Diagnostic Report - {datetime.datetime.now():%Y-%m-%d %H:%M}")
    out("=" * 50)
    if DEMO:
        out("*** DEMO MODE: sample data, not a real phone ***")
    if not check_connection():
        summary()
        return
    check_device_info()
    check_battery()
    check_storage()
    if not args.no_logs:
        check_crash_logs()
    manual_checklist()
    summary()

    report = Path.cwd() / f"iphone_report_{datetime.datetime.now():%Y%m%d_%H%M}.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport saved to {report}")


if __name__ == "__main__":
    main()
