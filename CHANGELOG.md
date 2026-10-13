# Changelog

## 0.2.0 - 2026-10-13

- Daemon rewritten without `argparse`, `logging`, `dataclasses`, `pathlib`
  and `glob`; private memory down from 10.3 MB to 4.6 MB.
- Interpreter runs with `-I -S` (isolated, no site.py).
- Separate "waiting for device permission" message for the login ACL race.
- `--interval` on the command line is validated like the config value.
- Release PKGBUILD builds from the tagged GitHub tarball; `aur/` holds the
  rolling `ocypus-a62-linux-git` package.
- Dropped the redundant `udev` dependency (provided by `systemd` on Arch).

## 0.1.0 - 2026-10-12

- First working release. Display confirmed on an IOTA A62 (`bcdDevice 1.11`).
