# ocypus-a62-linux
#
# Manual install (no pacman):  sudo make install
#     -> /usr/local/bin, /etc/udev/rules.d, /usr/local/lib/systemd/user
# Packaged install (PKGBUILD): make DESTDIR=... PREFIX=/usr UDEVDIR=/usr/lib/udev/rules.d install

PREFIX   ?= /usr/local
BINDIR   ?= $(PREFIX)/bin
UNITDIR  ?= $(PREFIX)/lib/systemd/user
DATADIR  ?= $(PREFIX)/share/ocypus-a62
DOCDIR   ?= $(PREFIX)/share/doc/ocypus-a62
UDEVDIR  ?= /etc/udev/rules.d
DESTDIR  ?=

NAME = ocypus-a62

.PHONY: all build install uninstall check clean FORCE

all: build

build: $(NAME).service

# always re-render: BINDIR differs between manual and packaged installs
$(NAME).service: systemd/$(NAME).service.in FORCE
	sed 's|@BINDIR@|$(BINDIR)|g' $< > $@

FORCE:

check:
	python3 -m py_compile $(NAME).py
	python3 ./$(NAME).py --version

install: build
	install -Dm755 $(NAME).py                  $(DESTDIR)$(BINDIR)/$(NAME)
	install -Dm644 udev/70-$(NAME).rules       $(DESTDIR)$(UDEVDIR)/70-$(NAME).rules
	install -Dm644 $(NAME).service             $(DESTDIR)$(UNITDIR)/$(NAME).service
	install -Dm644 config.example.toml         $(DESTDIR)$(DATADIR)/config.example.toml
	install -Dm644 README.md                   $(DESTDIR)$(DOCDIR)/README.md
	install -Dm644 LICENSE                     $(DESTDIR)$(DOCDIR)/LICENSE
	@if [ -z "$(DESTDIR)" ]; then \
	  echo; echo "Installed. Next:"; \
	  echo "  sudo udevadm control --reload && sudo udevadm trigger -s hidraw"; \
	  echo "  ocypus-a62 detect"; \
	  echo "  systemctl --user enable --now ocypus-a62"; \
	fi

uninstall:
	@echo "Note: stop the service first as your desktop user: systemctl --user disable --now $(NAME)"
	rm -f $(DESTDIR)$(BINDIR)/$(NAME)
	rm -f $(DESTDIR)$(UDEVDIR)/70-$(NAME).rules
	rm -f $(DESTDIR)$(UNITDIR)/$(NAME).service
	rm -rf $(DESTDIR)$(DATADIR) $(DESTDIR)$(DOCDIR)
	@echo "Removed. Your config in ~/.config/ocypus-a62/ was left in place."
	@echo "Reload udev: sudo udevadm control --reload && sudo udevadm trigger -s hidraw"

clean:
	rm -f $(NAME).service
	rm -rf __pycache__ pkg src *.pkg.tar.zst
