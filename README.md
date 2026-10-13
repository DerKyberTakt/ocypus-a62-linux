# ocypus-a62-linux

Native Linux support for the digital display on the **Ocypus IOTA A62** CPU
air cooler. Reads the CPU package temperature from the kernel's `hwmon`
interface and writes it to the cooler's display over USB HID. No Ocypus
Digital, no Wine, no Windows.

Built on **Omarchy** (Arch Linux, Hyprland) and packaged for Arch. It should
run on any systemd-based distribution with Python 3.11 or newer, but only
Omarchy has been tested.

```
  /sys/class/hwmon/*/temp*_input        ocypus-a62 (user service)        /dev/hidrawN
  coretemp "Package id 0"  ──────────▶  read, round, build 64-byte  ──────────▶  IOTA A62
                           every 2 s    report, HIDIOCSFEATURE ioctl    USB 1a2c:434d, interface 1
```

## Highlights

- **No dependencies.** One Python file using `os`, `fcntl`, `signal`, `sys`
  and `time`. No venv, no pip, no hidapi, no libusb. `tomllib` is imported
  only if a config file exists.
- **Small.** 4.6 MB private memory and 5.3 MB proportional (PSS) for the
  installed service, measured from `/proc/<pid>/smaps_rollup` on Python
  3.14; systemd's `MemoryCurrent` reports 4.9 MB. The bare interpreter is
  3.5 MB of that. CPU use is one sysfs read and one 64-byte
  ioctl every 2 seconds.
- **Runs as your user.** One udev rule grants the active seat user access to
  the display's HID node and nothing else. No root daemon, no `0666` device
  nodes, no extra groups.
- **Finds the device itself.** Matches the USB vendor/product ID and the HID
  interface number in sysfs. Never hardcodes `/dev/hidrawN`, never opens the
  controller's keyboard interface.
- **Keeps going.** Waits quietly if the cooler is absent or the permission is
  not applied yet, rescans after a write error, logs each condition once
  rather than every tick, and sends the blank report on clean shutdown.
- **Packaged.** `makepkg -si` on Arch, `make install` elsewhere, clean
  uninstall either way.

## Hardware

The IOTA A62's display controller enumerates as:

```
Bus 001 Device 006: ID 1a2c:434d China Resource Semico Co., Ltd USB Gaming Keyboard
```

It presents two HID interfaces. Interface 0 is a boot-protocol keyboard. It
has not been observed sending keys, but Hyprland and libinput will list it as
a keyboard; that is the device's own firmware and is harmless. Interface 1
carries additional keyboard, consumer-control and system-control reports
plus a vendor-defined
collection (usage page `0xFF01`) with **report ID `0x07`**, a 63-byte feature
report and a 63-byte output report. That collection is the display.

Connect the cooler's USB cable to a USB 2.0 internal header, as the
manufacturer specifies.

### Protocol

One 64-byte packet (report ID plus 63 data bytes), sent as a HID feature
report:

| Byte | Value | Meaning |
|-----:|-------|---------|
| 0 | `0x07` | report ID |
| 1 | `0xFF` | display on |
| 2 | `0xFF` | display on |
| 4 | 0-9 | tens digit |
| 5 | 0-9 | ones digit |
| 7 | `0x00` / `0x01` | Celsius / Fahrenheit flag |
| others | `0x00` | |

An all-zero packet with only byte 0 set to `0x07` blanks the display. No
initialization sequence is sent.

What is verified on a physical IOTA A62 (`bcdDevice 1.11`, Intel i7-8700K,
Omarchy, 2026-10-12): the report ID and packet length match the device's own
HID descriptor, and the Celsius digit layout rendered the test pattern 12,
then 34, then the live temperature correctly. See
[Known limitations](#known-limitations) for what is not yet verified. The
byte layout was first worked out by the community projects listed under
[Credits](#credits).

## Install

### Arch Linux / Omarchy

From a release:

```bash
git clone https://github.com/DerKyberTakt/ocypus-a62-linux
cd ocypus-a62-linux
makepkg -si
```

The `PKGBUILD` fetches the tagged release tarball from GitHub. A rolling
`ocypus-a62-linux-git` package lives in `aur/` for the AUR.

The package installs:

| Path | Purpose |
|------|---------|
| `/usr/bin/ocypus-a62` | daemon and CLI |
| `/usr/lib/udev/rules.d/70-ocypus-a62.rules` | uaccess ACL on the display node |
| `/usr/lib/systemd/user/ocypus-a62.service` | user service |
| `/usr/share/ocypus-a62/config.example.toml` | documented config |
| `/usr/share/doc/ocypus-a62/` | this README and the license |

pacman's systemd hook reloads udev. If the cooler was already plugged in,
apply the rule to the existing device node once, then enable the service:

```bash
sudo udevadm trigger -s hidraw
ocypus-a62 detect
systemctl --user enable --now ocypus-a62
```

### Other distributions

```bash
sudo make install          # /usr/local/bin, /etc/udev/rules.d, /usr/local/lib/systemd/user
sudo udevadm control --reload && sudo udevadm trigger -s hidraw
systemctl --user enable --now ocypus-a62
```

Needs Python 3.11 or newer and systemd.

## Use

```bash
ocypus-a62 detect   # find the display node and the CPU sensor, check permissions
ocypus-a62 test     # show 12, then 34, then the live temperature
ocypus-a62 show 42  # send a fixed value
ocypus-a62 blank    # clear the display
ocypus-a62 --help
```

`detect` output on a working install:

```
Ocypus display: /dev/hidraw8  (mode 0660, writable)
CPU sensor:     hwmon4 coretemp 'Package id 0' = 31.0 C
```

`--unit c|f`, `--interval`, `--method`, `--device` and `-v` override the
config for one invocation. `run` is what the service executes.

## Configure

```bash
mkdir -p ~/.config/ocypus-a62
cp /usr/share/ocypus-a62/config.example.toml ~/.config/ocypus-a62/config.toml
$EDITOR ~/.config/ocypus-a62/config.toml
systemctl --user restart ocypus-a62
```

| Key | Default | Notes |
|-----|---------|-------|
| `interval` | `2.0` | seconds between updates, minimum 0.5 |
| `unit` | `"c"` | `"c"` or `"f"` |
| `method` | `"feature"` | `"feature"` or `"output"` |
| `blank_on_exit` | `true` | blank the display when the service stops |
| `device` | unset | force a `/dev/hidrawN`; leave unset |
| `[sensor] hwmon` | `"coretemp"` | hwmon device name, `"k10temp"` on AMD |
| `[sensor] label` | `"Package id 0"` | `temp*_label` to read, `"Tctl"` on AMD |

If the label is not found, the hottest input of that hwmon device is used and
a warning is logged once. `/etc/ocypus-a62/config.toml` is read if the user
file does not exist.

## Logs and troubleshooting

```bash
systemctl --user status ocypus-a62
journalctl --user -u ocypus-a62 -f
ocypus-a62 -v run            # foreground with debug output
```

**`detect` says NOT FOUND.** Run `lsusb | grep 1a2c`. If nothing is listed,
the cooler is not enumerating. Check the cable and the header, and look at
`journalctl -k | grep -i 'usb 1-'` for enumeration errors.

**`detect` says NOT writable.** The udev rule has not been applied. Check
that the rule file exists, then run `sudo udevadm control --reload && sudo
udevadm trigger -s hidraw`. `udevadm info /dev/hidrawN | grep TAGS` should
include `uaccess`. The ACL is bound to your active local seat, so a session
started over SSH does not get it. For a one-off test, `sudo ocypus-a62 test`
bypasses the rule.

**The service logs "waiting for device permission" right after login.** The
ACL is applied when logind activates your seat, which can be a moment after
the service starts. The daemon retries every interval and logs once when it
connects.

**`test` runs without error but the display stays blank.** Try
`ocypus-a62 --method output test`. If that works, set `method = "output"`
in the config. If neither works, open an issue with the output of
`lsusb -v -d 1a2c:434d` and `usbhid-dump -m 1a2c:434d`.

**Digits look swapped or wrong.** The test pattern makes this obvious. Open an
issue with your firmware revision (`bcdDevice` from `lsusb -v -d 1a2c:434d`).

**Wrong sensor.** Compare `sensors` output with `[sensor]` in the config.

## Uninstall

Arch:

```bash
systemctl --user disable --now ocypus-a62
sudo pacman -R ocypus-a62-linux
rm -rf ~/.config/ocypus-a62          # optional
```

Other:

```bash
systemctl --user disable --now ocypus-a62
sudo make uninstall
sudo udevadm control --reload
rm -rf ~/.config/ocypus-a62          # optional
```

## Known limitations

- Two digits. Values below 0 or above 99 are clamped.
- Reads one hwmon sensor. Ocypus Digital can switch the display to GPU
  temperature; here you would point `[sensor]` at a GPU hwmon device instead.
- Verified on hardware: Celsius digits via the feature report, unprivileged
  access through the udev rule, service start, stop, blank-on-exit and
  restart. Not verified on hardware yet: the Fahrenheit flag, the `output`
  report fallback, that the blank report visibly clears the display, and
  behaviour across reboot, replug and suspend/resume. The code handles these
  paths; the display has not been watched doing them. Reports welcome.
- Tested on one IOTA A62 (`bcdDevice 1.11`). Other firmware revisions may
  differ.
- Not integrated with liquidctl, OpenRGB or CoolerControl. A search of their
  issue trackers in October 2026 found no Ocypus support.

## Development

```bash
make check                 # byte-compile and version check
./ocypus-a62.py detect     # run from the checkout without installing
make build                 # render the systemd unit for PREFIX
sudo make install          # install the working tree outside pacman
```

To test the release `PKGBUILD` before a tag exists on GitHub, place a
tarball named `ocypus-a62-linux-<ver>.tar.gz` with a top-level
`ocypus-a62-linux-<ver>/` directory next to the PKGBUILD; `makepkg` uses it
instead of downloading. Delete that tarball before running `updpkgsums`,
or the checksum will be computed from the local file instead of GitHub's.

The project is a single file on purpose. Keep it that way unless a second
device needs support.

## Credits

The USB ID and byte layout were first worked out by the community:
[moyunkz/ocypus-a40-digital-linux](https://github.com/moyunkz/ocypus-a40-digital-linux),
[Muyoouu/ocypus-a62-digital-linux](https://github.com/Muyoouu/ocypus-a62-digital-linux),
[aryahajari/ocypus-a62-linux](https://github.com/aryahajari/ocypus-a62-linux),
[MikhailNikitin99/Ocypus-A40-LCD](https://github.com/MikhailNikitin99/Ocypus-A40-LCD) and
[f1v3nt5/ocypus-c70](https://github.com/f1v3nt5/ocypus-c70).
This project re-implements the protocol against the device's own HID
descriptor, with a focus on a dependency-free, packaged, user-level install
for Arch and Omarchy.

Ocypus is a trademark of its owner. This project is not affiliated with or
endorsed by Ocypus.

## License

MIT. See [LICENSE](LICENSE).
