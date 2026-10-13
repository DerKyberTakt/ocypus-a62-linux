#!/usr/bin/python3 -IS
"""ocypus-a62 - drive the Ocypus IOTA A62 cooler display from Linux hwmon.

Standard library only, and deliberately a small subset of it: no argparse,
logging, dataclasses, pathlib or glob, because each of those costs 1-4 MB of
resident memory in a daemon that otherwise needs almost none. The shebang runs
the interpreter in isolated mode (-I) without site.py (-S), which keeps the
script's own directory and any user site-packages off sys.path.

Protocol (verified against the live HID report descriptor on an IOTA A62):
  The cooler enumerates as USB 1a2c:434d "SEMICO USB Gaming Keyboard".
  Interface 0 is a boot keyboard and is never touched.
  Interface 1 carries a vendor-defined collection (usage page 0xFF01) with
  report ID 0x07: a 63-byte feature report and a 63-byte output report.
  64-byte packet (report ID + 63 data bytes):
      [0] 0x07  report ID
      [1] 0xFF  [2] 0xFF  display on
      [4] tens digit     [5] ones digit
      [7] 0x00 = Celsius, 0x01 = Fahrenheit
  An all-zero packet with only byte 0 = 0x07 blanks the display.
"""

import fcntl
import os
import signal
import sys
import time

__version__ = "0.2.0"

USB_VID = 0x1A2C
USB_PID = 0x434D
DISPLAY_INTERFACE = 1
REPORT_ID = 0x07
REPORT_LEN = 64  # report ID + 63 data bytes, per the HID descriptor

HWMON = "/sys/class/hwmon"
HIDRAW_SYS = "/sys/class/hidraw"

USAGE = """\
usage: ocypus-a62 [options] <command>

Show CPU temperature on the Ocypus IOTA A62 cooler display.

commands:
  detect          find the display and the CPU sensor, check permissions
  test [--hold S] send 12, then 34, then the live temperature (default hold 3 s)
  show N          send a fixed two-digit value
  blank           blank the display
  run             run as a daemon (used by the systemd user service)

options:
  -c, --config FILE   config file (default: ~/.config/ocypus-a62/config.toml)
  -v, --verbose       debug output
  --unit c|f          override unit
  --interval SECONDS  override update interval (minimum 0.5)
  --method feature|output
                      override HID report type
  --device /dev/hidrawN
                      override auto-detection
  --version           print version and exit
  -h, --help          this text
"""


# --------------------------------------------------------------------------
# Minimal logging to stderr (journald adds timestamps and the unit name)
# --------------------------------------------------------------------------

VERBOSE = False


def log(level, msg):
    if level == "DEBUG" and not VERBOSE:
        return
    sys.stderr.write(f"{level}: {msg}\n")
    sys.stderr.flush()


class Throttle:
    """Emit a message once per state change instead of every tick."""

    __slots__ = ("last",)

    def __init__(self):
        self.last = {}

    def __call__(self, key, level, msg):
        if self.last.get(key) != msg:
            self.last[key] = msg
            log(level, msg)

    def clear(self, key):
        self.last.pop(key, None)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

class Config:
    __slots__ = ("interval", "unit", "method", "blank_on_exit", "device",
                 "sensor_hwmon", "sensor_label")

    def __init__(self):
        self.interval = 2.0
        self.unit = "c"               # "c" or "f"
        self.method = "feature"       # "feature" or "output"
        self.blank_on_exit = True
        self.device = None            # explicit /dev/hidrawN, else auto
        self.sensor_hwmon = "coretemp"   # k10temp on AMD
        self.sensor_label = "Package id 0"  # Tctl on AMD

    def validate(self):
        if self.unit not in ("c", "f"):
            die(f"config: unit must be 'c' or 'f', got {self.unit!r}")
        if self.method not in ("feature", "output"):
            die(f"config: method must be 'feature' or 'output', got {self.method!r}")
        if self.interval < 0.5:
            die("config: interval must be at least 0.5 seconds")


def die(msg, code=2):
    log("ERROR", msg)
    sys.exit(code)


def config_paths():
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return [os.path.join(xdg, "ocypus-a62", "config.toml"),
            "/etc/ocypus-a62/config.toml"]


def load_config(explicit):
    cfg = Config()
    for path in ([explicit] if explicit else config_paths()):
        if not os.path.isfile(path):
            continue
        import tomllib  # only paid for when a config file exists
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        cfg.interval = float(data.get("interval", cfg.interval))
        cfg.unit = str(data.get("unit", cfg.unit)).lower()
        cfg.method = str(data.get("method", cfg.method)).lower()
        cfg.blank_on_exit = bool(data.get("blank_on_exit", cfg.blank_on_exit))
        cfg.device = data.get("device", cfg.device)
        sensor = data.get("sensor", {})
        cfg.sensor_hwmon = str(sensor.get("hwmon", cfg.sensor_hwmon))
        cfg.sensor_label = str(sensor.get("label", cfg.sensor_label))
        log("DEBUG", f"loaded config from {path}")
        break
    return cfg


# --------------------------------------------------------------------------
# sysfs helpers
# --------------------------------------------------------------------------

def read_text(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def listdir_sorted(path, prefix):
    try:
        return sorted(n for n in os.listdir(path) if n.startswith(prefix))
    except OSError:
        return []


# --------------------------------------------------------------------------
# Device discovery
# --------------------------------------------------------------------------

def find_display_node(vid=USB_VID, pid=USB_PID, interface=DISPLAY_INTERFACE):
    """Return /dev/hidrawN for the display interface, or None.

    Matches the HID bus/vendor/product ID from sysfs, then confirms the USB
    interface number on the parent interface directory, so the keyboard
    interface of the same controller is never selected.
    """
    want = f"HID_ID=0003:{vid:08X}:{pid:08X}"
    for name in listdir_sorted(HIDRAW_SYS, "hidraw"):
        hid_dev = os.path.join(HIDRAW_SYS, name, "device")
        if want not in read_text(os.path.join(hid_dev, "uevent")).upper():
            continue
        iface_dir = os.path.realpath(os.path.join(hid_dev, ".."))
        try:
            if int(read_text(os.path.join(iface_dir, "bInterfaceNumber")), 16) != interface:
                continue
        except ValueError:
            continue
        return "/dev/" + name
    return None


# --------------------------------------------------------------------------
# HID transport
# --------------------------------------------------------------------------

def hidiocsfeature(size):
    # _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06, size) -> 0xC0404806 for 64 bytes
    return (3 << 30) | (size << 16) | (ord("H") << 8) | 0x06


def build_report(value, unit):
    value = max(0, min(99, int(value)))
    rep = bytearray(REPORT_LEN)
    rep[0] = REPORT_ID
    rep[1] = 0xFF
    rep[2] = 0xFF
    rep[4] = value // 10
    rep[5] = value % 10
    rep[7] = 0x01 if unit == "f" else 0x00
    return rep


def blank_report():
    rep = bytearray(REPORT_LEN)
    rep[0] = REPORT_ID
    return rep


class Display:
    __slots__ = ("node", "method", "fd")

    def __init__(self, node, method="feature"):
        self.node = node
        self.method = method
        self.fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)

    def send(self, report):
        if self.method == "feature":
            fcntl.ioctl(self.fd, hidiocsfeature(len(report)), report)
        else:
            n = os.write(self.fd, bytes(report))
            if n != len(report):
                raise OSError(f"short write: {n}/{len(report)} bytes")

    def show(self, value, unit):
        self.send(build_report(value, unit))

    def blank(self):
        self.send(blank_report())

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Temperature source
# --------------------------------------------------------------------------

class Sensor:
    """CPU temperature from /sys/class/hwmon, selected by name and label."""

    __slots__ = ("hwmon_name", "label", "inputs", "description")

    def __init__(self, hwmon_name, label):
        self.hwmon_name = hwmon_name
        self.label = label
        self.inputs = []
        self.description = ""
        self._locate()

    def _locate(self):
        for hw in listdir_sorted(HWMON, "hwmon"):
            hw_path = os.path.join(HWMON, hw)
            if read_text(os.path.join(hw_path, "name")) != self.hwmon_name:
                continue
            labels = [n for n in listdir_sorted(hw_path, "temp") if n.endswith("_label")]
            for lab in labels:
                if read_text(os.path.join(hw_path, lab)) == self.label:
                    self.inputs = [os.path.join(hw_path, lab.replace("_label", "_input"))]
                    self.description = f"{hw} {self.hwmon_name} '{self.label}'"
                    return
            inputs = [os.path.join(hw_path, n) for n in listdir_sorted(hw_path, "temp")
                      if n.endswith("_input")]
            if inputs:
                self.inputs = inputs
                self.description = (f"{hw} {self.hwmon_name} max of {len(inputs)} inputs "
                                    f"(label '{self.label}' not found)")
                log("WARNING", f"sensor label {self.label!r} not found under "
                               f"{self.hwmon_name}, using max of all inputs")
                return
        raise FileNotFoundError(f"no hwmon device named {self.hwmon_name!r}")

    def read_celsius(self):
        vals = []
        for p in self.inputs:
            raw = read_text(p)
            if raw:
                vals.append(int(raw) / 1000.0)
        if not vals:
            raise OSError("sensor returned no readings")
        return max(vals)

    @staticmethod
    def convert(celsius, unit):
        if unit == "f":
            return int(round(celsius * 9 / 5 + 32))
        return int(round(celsius))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def open_display(cfg):
    node = cfg.device or find_display_node()
    if node is None:
        raise FileNotFoundError(
            f"no Ocypus display found (USB {USB_VID:04x}:{USB_PID:04x} "
            f"interface {DISPLAY_INTERFACE})")
    return Display(node, cfg.method)


def cmd_detect(cfg, _opts):
    node = cfg.device or find_display_node()
    if node is None:
        print(f"Ocypus display: NOT FOUND (looking for USB {USB_VID:04x}:{USB_PID:04x}, "
              f"HID interface {DISPLAY_INTERFACE})")
        print("Check 'lsusb' for ID 1a2c:434d and that the cooler's USB cable is on a "
              "USB 2.0 header.")
        return 1
    mode = os.stat(node).st_mode & 0o777
    writable = os.access(node, os.W_OK)
    print(f"Ocypus display: {node}  (mode {mode:04o}, "
          f"{'writable' if writable else 'NOT writable by this user'})")
    try:
        s = Sensor(cfg.sensor_hwmon, cfg.sensor_label)
        print(f"CPU sensor:     {s.description} = {s.read_celsius():.1f} C")
    except (FileNotFoundError, OSError) as exc:
        print(f"CPU sensor:     ERROR {exc}")
        return 1
    if not writable:
        print("Install the udev rule (see README) or run this command with sudo for a "
              "one-off test.")
        return 2
    return 0


def cmd_show(cfg, opts):
    disp = open_display(cfg)
    try:
        disp.show(opts["value"], cfg.unit)
        print(f"sent {opts['value']:02d} ({cfg.unit.upper()}) to {disp.node} "
              f"via {cfg.method} report")
    finally:
        disp.close()
    return 0


def cmd_blank(cfg, _opts):
    disp = open_display(cfg)
    try:
        disp.blank()
        print(f"sent blank report to {disp.node}")
    finally:
        disp.close()
    return 0


def cmd_test(cfg, opts):
    """Send two distinct patterns, then the live temperature.

    12 then 34 are chosen so a swapped or shifted digit layout is obvious.
    """
    disp = open_display(cfg)
    sensor = Sensor(cfg.sensor_hwmon, cfg.sensor_label)
    hold = opts["hold"]
    try:
        for val in (12, 34):
            disp.show(val, cfg.unit)
            print(f"[{disp.node}] display should now read  {val}  ... holding {hold:.0f}s")
            time.sleep(hold)
        c = sensor.read_celsius()
        v = Sensor.convert(c, cfg.unit)
        disp.show(v, cfg.unit)
        print(f"[{disp.node}] display should now read  {v}  "
              f"(live {sensor.description}: {c:.1f} C)")
        print("Leaving the live value on the display. Run 'ocypus-a62 blank' to clear it.")
    finally:
        disp.close()
    return 0


def cmd_run(cfg, _opts):
    stop = False

    def _on_signal(signum, _frame):
        nonlocal stop
        stop = True
        log("INFO", f"received {signal.Signals(signum).name}, shutting down")

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    throttle = Throttle()
    disp = None
    sensor = None
    last_sent = None
    log("INFO", f"ocypus-a62 {__version__} starting (interval {cfg.interval:.1f}s, "
                f"unit {cfg.unit.upper()}, {cfg.method} report)")

    while not stop:
        try:
            if sensor is None:
                sensor = Sensor(cfg.sensor_hwmon, cfg.sensor_label)
                log("INFO", f"temperature source: {sensor.description}")
            if disp is None:
                disp = open_display(cfg)
                throttle.clear("device")
                log("INFO", f"display connected: {disp.node}")
                last_sent = None
            celsius = sensor.read_celsius()
            value = Sensor.convert(celsius, cfg.unit)
            disp.show(value, cfg.unit)
            if value != last_sent:
                log("DEBUG", f"display <- {value} ({celsius:.1f} C)")
                last_sent = value
        except FileNotFoundError as exc:
            # cooler unplugged / not yet enumerated, or sensor missing
            if disp is not None:
                disp.close()
                disp = None
            throttle("device", "WARNING", f"waiting for device: {exc}")
        except PermissionError as exc:
            # node exists but the uaccess ACL is not applied yet (early login)
            if disp is not None:
                disp.close()
                disp = None
            throttle("device", "WARNING", f"waiting for device permission: {exc}")
        except OSError as exc:
            if disp is not None:
                disp.close()
                disp = None
            throttle("device", "WARNING", f"write failed ({exc}), will rescan")
        # interruptible sleep
        deadline = time.monotonic() + cfg.interval
        while not stop and time.monotonic() < deadline:
            time.sleep(min(0.25, deadline - time.monotonic()))

    if disp is not None:
        if cfg.blank_on_exit:
            try:
                disp.blank()
                log("INFO", "display blanked")
            except OSError as exc:
                log("WARNING", f"could not blank display on exit: {exc}")
        disp.close()
    return 0


# --------------------------------------------------------------------------
# Argument parsing (by hand; argparse costs ~1.5 MB resident)
# --------------------------------------------------------------------------

def parse_args(argv):
    opts = {"config": None, "unit": None, "interval": None, "method": None,
            "device": None, "cmd": None, "value": None, "hold": 3.0}
    i = 0

    def need(flag):
        nonlocal i
        i += 1
        if i >= len(argv):
            die(f"{flag} requires a value", 2)
        return argv[i]

    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            sys.stdout.write(USAGE)
            sys.exit(0)
        elif a == "--version":
            print(f"ocypus-a62 {__version__}")
            sys.exit(0)
        elif a in ("-v", "--verbose"):
            global VERBOSE
            VERBOSE = True
        elif a in ("-c", "--config"):
            opts["config"] = need(a)
        elif a == "--unit":
            opts["unit"] = need(a).lower()
        elif a == "--interval":
            opts["interval"] = need(a)
        elif a == "--method":
            opts["method"] = need(a).lower()
        elif a == "--device":
            opts["device"] = need(a)
        elif a == "--hold":
            opts["hold"] = need(a)
        elif a.startswith("-"):
            die(f"unknown option {a}\n{USAGE}", 2)
        elif opts["cmd"] is None:
            opts["cmd"] = a
        elif opts["cmd"] == "show" and opts["value"] is None:
            opts["value"] = a
        else:
            die(f"unexpected argument {a}\n{USAGE}", 2)
        i += 1

    if opts["cmd"] not in ("detect", "test", "show", "blank", "run"):
        die(f"missing or unknown command\n{USAGE}", 2)
    try:
        if opts["interval"] is not None:
            opts["interval"] = float(opts["interval"])
        opts["hold"] = float(opts["hold"])
        if opts["cmd"] == "show":
            if opts["value"] is None:
                die("show requires a value, e.g. 'show 42'", 2)
            opts["value"] = int(opts["value"])
    except ValueError as exc:
        die(f"bad number: {exc}", 2)
    return opts


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    cfg = load_config(opts["config"])
    if opts["unit"]:
        cfg.unit = opts["unit"]
    if opts["interval"] is not None:
        cfg.interval = opts["interval"]
    if opts["method"]:
        cfg.method = opts["method"]
    if opts["device"]:
        cfg.device = opts["device"]
    cfg.validate()

    handlers = {"detect": cmd_detect, "test": cmd_test, "show": cmd_show,
                "blank": cmd_blank, "run": cmd_run}
    try:
        return handlers[opts["cmd"]](cfg, opts)
    except PermissionError as exc:
        log("ERROR", f"{exc} (install the udev rule, or use sudo for a one-off test)")
        return 2
    except FileNotFoundError as exc:
        log("ERROR", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
