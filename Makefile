# adbshot -- install and uninstall.
#
#   sudo make install                 for every user on the machine
#   make install PREFIX=~/.local      just for you, no root needed
#
# Ordering here is not cosmetic:
#   * install writes every file, THEN starts the unit. The other way round
#     starts a daemon whose package is half installed.
#   * uninstall stops and disables the unit BEFORE removing the GSettings
#     schema. Removing the schema under a live unit makes the daemon abort on
#     startup, crash-loop, fail the unit, and take D-Bus activation down with
#     it -- a silently bricked install on the way out.

PREFIX  ?= /usr/local
# make never expands `~` (that is a shell thing), and a relative prefix would
# make the derived paths disagree with the USER_INSTALL test below. Normalise
# once, here, so every path downstream is absolute and consistent.
# `override` is required: a variable set on the command line beats every
# ordinary assignment in the makefile, so a plain `PREFIX :=` here would be
# silently discarded for exactly the invocations that need normalising.
override PREFIX := $(abspath $(patsubst ~/%,$(HOME)/%,$(PREFIX)))
APP_ID   = io.github.acosta290.AdbShot
UNIT     = adbshot.service
DCONF    = /io/github/acosta290/AdbShot/
# Shown in software centres and the AppStream metadata. Point this at your
# own repository before publishing:  make install HOMEPAGE=https://...
HOMEPAGE ?= https://github.com/acosta290/adbshot

BINDIR    = $(PREFIX)/bin
LIBDIR    = $(PREFIX)/lib/adbshot
DATADIR   = $(PREFIX)/share
SCHEMADIR = $(DATADIR)/glib-2.0/schemas
APPSDIR   = $(DATADIR)/applications
METADIR   = $(DATADIR)/metainfo
DBUSDIR   = $(DATADIR)/dbus-1/services
ICONDIR   = $(DATADIR)/icons/hicolor/scalable/apps
SYMDIR    = $(DATADIR)/icons/hicolor/symbolic/apps
DOCDIR    = $(DATADIR)/doc/adbshot

# A prefix inside $HOME means a personal install: no root, and systemd user
# units belong in the user's own config dir rather than under the prefix.
USER_INSTALL := $(if $(filter $(HOME)%,$(PREFIX)),1,)

# sudo resets HOME to /root, so `sudo make install PREFIX=~/.local` would take
# the system branch and scatter root-owned files through the user's home.
SUDO_HOME := $(if $(SUDO_USER),$(shell getent passwd $(SUDO_USER) | cut -d: -f6),)
ifneq ($(SUDO_HOME),)
  ifneq ($(filter $(SUDO_HOME)%,$(PREFIX)),)
    $(error PREFIX $(PREFIX) is inside $(SUDO_USER)'s home. Run this WITHOUT sudo: \
      make install PREFIX=$(PREFIX))
  endif
endif

ifdef USER_INSTALL
  UNITDIR = $(HOME)/.config/systemd/user
  RUN     =
else
  UNITDIR      = $(PREFIX)/lib/systemd/user
  SYSUSERWANTS = /etc/systemd/user/graphical-session.target.wants
  # Per-user actions must run as the human who typed sudo, not as root -- and
  # `runuser` alone is not enough: without XDG_RUNTIME_DIR and the session bus
  # address, `systemctl --user` fails with "Failed to connect to bus".
  REAL_USER := $(if $(SUDO_USER),$(SUDO_USER),$(if $(USER),$(USER),$(shell id -un)))
  REAL_UID  := $(shell id -u $(REAL_USER) 2>/dev/null)
  RUN        = runuser -u $(REAL_USER) -- env \
               XDG_RUNTIME_DIR=/run/user/$(REAL_UID) \
               DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(REAL_UID)/bus
endif

SUBST = sed -e 's|@BINDIR@|$(BINDIR)|g' -e 's|@DOCDIR@|$(DOCDIR)|g' -e 's|@APP_ID@|$(APP_ID)|g' -e 's|@HOMEPAGE@|$(HOMEPAGE)|g'

.PHONY: all help schemas run install uninstall purge check lint enable disable clean templates postinstall-check

all: help

help:
	@echo "adbshot"
	@echo "  make run                       run from this checkout, nothing installed"
	@echo "  sudo make install              install for every user on this machine"
	@echo "  make install PREFIX=~/.local   install just for you, no root needed"
	@echo "  make check                     verify the installation"
	@echo "  sudo make uninstall            remove it, keeping screenshots and settings"
	@echo "  sudo make purge                remove it and reset the settings too"

clean:
	rm -rf build
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

schemas:
	@mkdir -p build/schemas
	@cp data/$(APP_ID).gschema.xml build/schemas/
	@glib-compile-schemas build/schemas/
	@echo "schema compiled into build/schemas"

run: schemas
	@./bin/adbshot

# Phony on purpose: the outputs depend on PREFIX and HOMEPAGE, which make
# cannot see in a timestamp. A stale stamp would bake the previous PREFIX into
# the unit, the .desktop and the D-Bus service file.
templates:
	@mkdir -p build
	@$(SUBST) data/$(UNIT).in                   > build/$(UNIT)
	@$(SUBST) data/$(APP_ID).service.in         > build/$(APP_ID).service
	@$(SUBST) data/$(APP_ID).desktop.in         > build/$(APP_ID).desktop
	@$(SUBST) data/$(APP_ID).metainfo.xml.in    > build/$(APP_ID).metainfo.xml

install: templates
ifndef USER_INSTALL
	@test "$$(id -u)" = "0" || { echo "A system-wide install needs root."; \
	  echo "  sudo make install            (every user)"; \
	  echo "  make install PREFIX=~/.local (just you, no root)"; exit 1; }
endif
	install -d $(LIBDIR)/adbshot $(BINDIR) $(SCHEMADIR) $(APPSDIR) $(METADIR) \
	           $(DBUSDIR) $(ICONDIR) $(SYMDIR) $(UNITDIR) $(DOCDIR)
	install -m 644 src/adbshot/*.py                 $(LIBDIR)/adbshot/
	install -m 755 bin/adbshot                      $(BINDIR)/adbshot
	install -m 644 data/$(APP_ID).gschema.xml       $(SCHEMADIR)/
	install -m 644 build/$(APP_ID).desktop          $(APPSDIR)/
	install -m 644 build/$(APP_ID).metainfo.xml     $(METADIR)/
	install -m 644 build/$(APP_ID).service          $(DBUSDIR)/
	install -m 644 build/$(UNIT)                    $(UNITDIR)/
	install -m 644 data/icons/$(APP_ID).svg         $(ICONDIR)/
	install -m 644 data/icons/$(APP_ID)-symbolic.svg $(SYMDIR)/
	install -m 644 README.md LICENSE                $(DOCDIR)/
	glib-compile-schemas $(SCHEMADIR)
	-update-desktop-database $(APPSDIR)
	-gtk-update-icon-cache -f -t $(DATADIR)/icons/hicolor
ifdef USER_INSTALL
	-systemctl --user daemon-reload
	-systemctl --user reset-failed $(UNIT)
	@# Tolerated rather than fatal: in a container, over SSH with no session
	@# bus, or on a first boot there may be nothing to enable against, and
	@# aborting here would leave a half-copied install behind. The
	@# post-install check reports it instead.
	-systemctl --user enable --now $(UNIT)
else
	install -d $(SYSUSERWANTS)
	ln -sf $(UNITDIR)/$(UNIT) $(SYSUSERWANTS)/$(UNIT)
	-$(RUN) systemctl --user daemon-reload
	-$(RUN) systemctl --user reset-failed $(UNIT)
	-$(RUN) systemctl --user start $(UNIT)
endif
ifndef USER_INSTALL
	# Root just wrote build/ inside the source tree; hand it back so a later
	# non-root `make` in this checkout does not fail on permissions.
	-chown -R $(REAL_USER) build
endif
	@echo
	@echo "  adbshot installed. The Super+X shortcut registers itself on first run."
	@$(MAKE) --no-print-directory postinstall-check PREFIX=$(PREFIX)
	@echo "  Verify with:  adbshot --self-check"
	@echo

# A prefix outside XDG_DATA_DIRS is the quiet failure mode of a custom install:
# the binary is there, but the GSettings schema, the .desktop and the D-Bus
# activation file are invisible, so the app aborts and the hotkey does nothing.
postinstall-check:
	@# Two subtleties, both of which produce a false warning if ignored:
	@#  * entries may carry a trailing slash ("/usr/local/share/"), so normalise;
	@#  * XDG_DATA_HOME (default ~/.local/share) is searched by GLib in addition
	@#    to XDG_DATA_DIRS and is never listed in it, so add it to the haystack.
	@case ":$$(printf '%s:%s' "$${XDG_DATA_DIRS:-/usr/local/share:/usr/share}" \
	          "$${XDG_DATA_HOME:-$$HOME/.local/share}" \
	          | sed 's#/\{1,\}:#:#g; s#/\{1,\}$$##'):" in \
	  *":$(DATADIR):"*) ;; \
	  *) echo ""; \
	     echo "  WARNING  $(DATADIR) is not in XDG_DATA_DIRS."; \
	     echo "           The schema, .desktop and D-Bus service will not be found."; \
	     echo "           Add to your shell profile, then log out and back in:"; \
	     echo "             export XDG_DATA_DIRS=\"$(DATADIR):\$$XDG_DATA_DIRS\""; \
	     echo "";; \
	esac
	@case ":$$(printf '%s' "$$PATH" | sed 's#/\{1,\}:#:#g; s#/\{1,\}$$##'):" in \
	  *":$(BINDIR):"*) ;; \
	  *) echo ""; \
	     echo "  WARNING  $(BINDIR) is not on your PATH."; \
	     echo "           Add to your shell profile, then open a new terminal:"; \
	     echo "             export PATH=\"$(BINDIR):\$$PATH\""; \
	     echo "";; \
	esac

uninstall:
ifndef USER_INSTALL
	@test "$$(id -u)" = "0" || { echo "run: sudo make uninstall"; exit 1; }
endif
	-$(RUN) $(BINDIR)/adbshot --uninstall-hotkey
	-$(RUN) systemctl --user stop $(UNIT)
	-$(RUN) systemctl --user disable $(UNIT)
ifdef USER_INSTALL
	rm -f $(UNITDIR)/graphical-session.target.wants/$(UNIT)
else
	rm -f $(SYSUSERWANTS)/$(UNIT)
endif
	rm -f $(UNITDIR)/$(UNIT) $(DBUSDIR)/$(APP_ID).service $(APPSDIR)/$(APP_ID).desktop
	rm -f $(METADIR)/$(APP_ID).metainfo.xml $(ICONDIR)/$(APP_ID).svg $(SYMDIR)/$(APP_ID)-symbolic.svg
	rm -f $(BINDIR)/adbshot $(SCHEMADIR)/$(APP_ID).gschema.xml
	rm -rf $(LIBDIR) $(DOCDIR)
	glib-compile-schemas $(SCHEMADIR)
	-update-desktop-database $(APPSDIR)
	-gtk-update-icon-cache -f -t $(DATADIR)/icons/hicolor
	-$(RUN) systemctl --user daemon-reload
	-$(RUN) systemctl --user reset-failed $(UNIT)
	@echo
	@echo "  adbshot removed. Your screenshots and settings were left alone."
	@echo "  To drop the settings too:  make purge"
	@echo

purge: uninstall
	-$(RUN) dconf reset -f $(DCONF)
	@echo "  settings reset."

enable:
	systemctl --user enable --now $(UNIT)

disable:
	systemctl --user disable --now $(UNIT)

check: lint
	@adbshot --self-check 2>/dev/null || ./bin/adbshot --self-check

lint:
	@python3 tools/check-gettext.py
	@python3 -m compileall -q src/adbshot >/dev/null && echo "ok: every module compiles"
