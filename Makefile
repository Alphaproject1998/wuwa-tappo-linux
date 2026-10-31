WINEPREFIX ?= $(HOME)/Games/wuwa-tappo
WINE_PACKAGE := wine-11.20-1-x86_64.pkg.tar.zst
WINE_PACKAGE_URL ?= https://archive.archlinux.org/packages/w/wine/$(WINE_PACKAGE)
WINE_PACKAGE_SHA256 := 6dfd8bcc9b370de2942732a36d730e1b18a141218975315da816c8326c237644
OLE32_SHA256 := 338998d7dc5c19c4977d2501f628463db63e89177d40558d2349f964f1352a31
RUNTIME := build/wine-runtime
RUNTIME_STAMP := $(RUNTIME)/.patched
WINE := $(CURDIR)/$(RUNTIME)/bin/wine
WINESERVER := $(CURDIR)/$(RUNTIME)/bin/wineserver
WINE_ENV := WINEPREFIX="$(WINEPREFIX)" WINEDEBUG=-all
WEBVIEW2_INSTALLER ?=
TAPPO_INSTALLER ?=
UNIT_DIR := $(HOME)/.config/systemd/user
APPLICATIONS := $(HOME)/.local/share/applications
AUTOSTART := $(HOME)/.config/autostart
LOGOUT_SCRIPTS := $(HOME)/.config/plasma-workspace/shutdown
ICONS := $(HOME)/.local/share/icons/hicolor
WINE_ICON ?= $(firstword $(wildcard $(ICONS)/256x256/apps/*_WuwaTappo.0.png))

.PHONY: all runtime prefix webview2 app ole32 service corners-exclusion kwin-rules launcher autostart logout-script install uninstall clean

all: build/tappo-input.exe.so

build/$(WINE_PACKAGE):
	mkdir -p build
	curl -fL -C - --retry 5 --retry-all-errors --retry-delay 2 -o "$@.part" "$(WINE_PACKAGE_URL)"
	echo "$(WINE_PACKAGE_SHA256)  $@.part" | sha256sum -c --quiet || { rm -f "$@.part"; exit 1; }
	mv "$@.part" "$@"

$(RUNTIME_STAMP): build/$(WINE_PACKAGE) runtime/patch-runtime.py
	rm -rf $(RUNTIME) build/wine-extract
	mkdir -p build/wine-extract
	tar --zstd -xf build/$(WINE_PACKAGE) -C build/wine-extract usr
	mv build/wine-extract/usr $(RUNTIME)
	rmdir build/wine-extract
	python3 -I runtime/patch-runtime.py $(RUNTIME)
	ln -s wineserver $(RUNTIME)/bin/tappo-wineserver
	touch $@

runtime: $(RUNTIME_STAMP)

build/tappo-input.exe.so: helper/tappo-input.c $(RUNTIME_STAMP)
	"$(CURDIR)/$(RUNTIME)/bin/winegcc" -m64 -O2 -Wall -o build/tappo-input.exe $< -luser32 -lwin32u

prefix: $(RUNTIME_STAMP)
	mkdir -p "$(WINEPREFIX)"
	[ -f "$(WINEPREFIX)/system.reg" ] || $(WINE_ENV) WINEDLLOVERRIDES="mscoree=;winemenubuilder.exe=" "$(WINE)" wineboot -u
	$(WINE_ENV) "$(WINE)" winecfg -v win10
	$(WINE_ENV) "$(WINE)" reg add 'HKCU\Software\Wine\AppDefaults\msedgewebview2.exe' /v Version /t REG_SZ /d win8 /f
	$(WINE_ENV) "$(WINE)" reg add 'HKCU\Software\Wine\X11 Driver' /v ScreenDepth /t REG_SZ /d 32 /f

webview2: $(RUNTIME_STAMP)
	@[ -n "$(WEBVIEW2_INSTALLER)" ] || { echo 'set WEBVIEW2_INSTALLER to the WebView2 standalone installer (x64)' >&2; exit 1; }
	$(WINE_ENV) "$(WINE)" "$(WEBVIEW2_INSTALLER)" /silent /install

app: $(RUNTIME_STAMP)
	@[ -n "$(TAPPO_INSTALLER)" ] || { echo 'set TAPPO_INSTALLER to WuwaTappo-global_<version>_x64-setup.exe' >&2; exit 1; }
	$(WINE_ENV) WINEDLLOVERRIDES=winemenubuilder.exe=b "$(WINE)" "$(TAPPO_INSTALLER)"

# Setting up a prefix queues a stock ole32 to replace the in-use one at the next session start, so start one before copying.
ole32: $(RUNTIME_STAMP)
	python3 -I ole32/patch-ole32.py $(RUNTIME)/lib/wine/x86_64-windows/ole32.dll build/ole32.dll $(OLE32_SHA256)
	-WINEPREFIX="$(WINEPREFIX)" "$(WINESERVER)" -k 2> /dev/null
	$(WINE_ENV) "$(WINE)" reg add 'HKCU\Software\Wine\AppDefaults\WuwaTappo.exe\DllOverrides' /v ole32 /t REG_SZ /d native,builtin /f
	cp build/ole32.dll "$(WINEPREFIX)/drive_c/windows/system32/ole32.dll"

service: build/tappo-input.exe.so
	mkdir -p "$(UNIT_DIR)"
	sed -e 's|@PROJECT@|$(CURDIR)|' -e 's|@WINEPREFIX@|$(WINEPREFIX)|' systemd/tappo-bridge.service.in > "$(UNIT_DIR)/tappo-bridge.service"
	systemctl --user daemon-reload
	systemctl --user enable tappo-bridge.service
	systemctl --user restart tappo-bridge.service

corners-exclusion:
	current="$$(kreadconfig6 --file kwinrc --group Round-Corners --key Exclusions 2> /dev/null)"; \
	case ",$$current," in *,wuwatappo.exe,*) ;; *) kwriteconfig6 --file kwinrc --group Round-Corners --key Exclusions "$${current:+$$current,}wuwatappo.exe" ;; esac; \
	qdbus=qdbus6; command -v "$$qdbus" > /dev/null || qdbus=qdbus-qt6; command -v "$$qdbus" > /dev/null || qdbus=qdbus; \
	"$$qdbus" org.kde.KWin /Effects org.kde.kwin.Effects.reconfigureEffect kwin4_effect_shapecorners 2> /dev/null || true

kwin-rules:
	rules="$$(kreadconfig6 --file kwinrulesrc --group General --key rules 2> /dev/null)"; \
	case ",$$rules," in \
		*,wuwa-tappo,*) ;; \
		*) \
			count="$$(kreadconfig6 --file kwinrulesrc --group General --key count --default 0 2> /dev/null)"; \
			kwriteconfig6 --file kwinrulesrc --group General --key count "$$((count + 1))"; \
			kwriteconfig6 --file kwinrulesrc --group General --key rules "$${rules:+$$rules,}wuwa-tappo"; \
			;; \
	esac; \
	kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key Description "Wuwa Tappo"; \
	kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key wmclass "wuwatappo.exe"; \
	kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key wmclassmatch "1"; \
	kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key wmclasscomplete --type bool "false"; \
	kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key skiptaskbar --type bool "true"; \
	kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key skiptaskbarrule "2"; \
	kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key skippager --type bool "true"; \
	kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key skippagerrule "2"; \
	kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key skipswitcher --type bool "true"; \
	kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key skipswitcherrule "2"; \
	qdbus=qdbus6; command -v "$$qdbus" > /dev/null || qdbus=qdbus-qt6; command -v "$$qdbus" > /dev/null || qdbus=qdbus; \
	"$$qdbus" org.kde.KWin /KWin org.kde.KWin.reconfigure 2> /dev/null || true

launcher: $(RUNTIME_STAMP)
	mkdir -p "$(APPLICATIONS)"
	sed -e 's|@PROJECT@|$(CURDIR)|' -e 's|@WINEPREFIX@|$(WINEPREFIX)|' runtime/wuwa-tappo.desktop.in > "$(APPLICATIONS)/wuwa-tappo.desktop"
	[ -z "$(WINE_ICON)" ] || install -Dm644 "$(WINE_ICON)" "$(ICONS)/256x256/apps/wuwa-tappo.png"
	$(WINE_ENV) "$(WINE)" reg add 'HKCU\Software\Wine\DllOverrides' /v winemenubuilder.exe /t REG_SZ /d '' /f
	rm -f "$(APPLICATIONS)/wine/Programs/WuwaTappoGlobal/Wuwa Tappo.desktop" "$(HOME)/.config/menus/applications-merged/wine-Programs-WuwaTappoGlobal-Wuwa Tappo.menu"
	update-desktop-database "$(APPLICATIONS)"
	! command -v kbuildsycoca6 > /dev/null || kbuildsycoca6 2> /dev/null
	xdg-mime default wuwa-tappo.desktop x-scheme-handler/wuwa-buddy

autostart: launcher
	mkdir -p "$(AUTOSTART)"
	cp "$(APPLICATIONS)/wuwa-tappo.desktop" "$(AUTOSTART)/wuwa-tappo.desktop"

logout-script:
	mkdir -p "$(LOGOUT_SCRIPTS)"
	ln -sfn "$(CURDIR)/runtime/tappo-quit" "$(LOGOUT_SCRIPTS)/00-wuwa-tappo-quit"

install: ole32 service launcher logout-script

uninstall:
	-systemctl --user disable --now tappo-bridge.service
	rm -f "$(UNIT_DIR)/tappo-bridge.service" "$(APPLICATIONS)/wuwa-tappo.desktop" "$(AUTOSTART)/wuwa-tappo.desktop" "$(LOGOUT_SCRIPTS)/00-wuwa-tappo-quit" "$(ICONS)/256x256/apps/wuwa-tappo.png"
	rm -f "$(ICONS)"/*/apps/*_WuwaTappo.0.png "$(HOME)/.config/menus/applications-merged/wine-Programs-WuwaTappoGlobal-Wuwa Tappo.menu" "$(HOME)/.local/share/desktop-directories/wine-Programs-WuwaTappoGlobal.directory"
	rm -rf "$(APPLICATIONS)/wine/Programs/WuwaTappoGlobal"
	rules="$$(kreadconfig6 --file kwinrulesrc --group General --key rules 2> /dev/null)"; \
	case ",$$rules," in \
		*,wuwa-tappo,*) \
			new_rules="$$(printf '%s\n' "$$rules" | sed -E -e 's/(^|,)wuwa-tappo(,|$$)/\1/' -e 's/^,//' -e 's/,$$//')"; \
			count="$$(kreadconfig6 --file kwinrulesrc --group General --key count --default 1 2> /dev/null)"; \
			kwriteconfig6 --file kwinrulesrc --group General --key count "$$((count > 0 ? count - 1 : 0))"; \
			kwriteconfig6 --file kwinrulesrc --group General --key rules "$$new_rules"; \
			for key in Description wmclass wmclassmatch wmclasscomplete skiptaskbar skiptaskbarrule skippager skippagerrule skipswitcher skipswitcherrule; do \
				kwriteconfig6 --file kwinrulesrc --group wuwa-tappo --key "$$key" --delete 2> /dev/null || true; \
			done; \
			qdbus=qdbus6; command -v "$$qdbus" > /dev/null || qdbus=qdbus-qt6; command -v "$$qdbus" > /dev/null || qdbus=qdbus; \
			"$$qdbus" org.kde.KWin /KWin org.kde.KWin.reconfigure 2> /dev/null || true; \
			;; \
	esac
	systemctl --user daemon-reload
	update-desktop-database "$(APPLICATIONS)"
	! command -v kbuildsycoca6 > /dev/null || kbuildsycoca6 2> /dev/null

clean:
	rm -rf build
