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
DESKTOP_DIR ?= $(shell xdg-user-dir DESKTOP 2> /dev/null || echo "$(HOME)/Desktop")
MENU_ENTRY ?= yes

.PHONY: all runtime prefix webview2 app ole32 service corners-exclusion wine-leftovers launcher autostart desktop-shortcut logout-script install uninstall clean

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

# make app lets winemenubuilder run for Tappo's icon, and it would also hand six Windows file types to this prefix under the system's Wine.
# Kuro's installer always puts a shortcut on the desktop, and Wine links the prefix's Desktop to the real one, so the prefix gets a folder of its own.
prefix: $(RUNTIME_STAMP)
	mkdir -p "$(WINEPREFIX)"
	[ -f "$(WINEPREFIX)/system.reg" ] || $(WINE_ENV) WINEDLLOVERRIDES="mscoree=;winemenubuilder.exe=" "$(WINE)" wineboot -u
	$(WINE_ENV) "$(WINE)" winecfg -v win10
	$(WINE_ENV) "$(WINE)" reg add 'HKCU\Software\Wine\AppDefaults\msedgewebview2.exe' /v Version /t REG_SZ /d win8 /f
	$(WINE_ENV) "$(WINE)" reg add 'HKCU\Software\Wine\X11 Driver' /v ScreenDepth /t REG_SZ /d 32 /f
	$(WINE_ENV) "$(WINE)" reg add 'HKCU\Software\Wine\FileOpenAssociations' /v Enable /t REG_SZ /d N /f
	for desktop in "$(WINEPREFIX)"/drive_c/users/*/Desktop; do [ ! -L "$$desktop" ] || { rm -f "$$desktop" && mkdir "$$desktop"; }; done

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

wine-leftovers:
	rm -f "$(APPLICATIONS)/wine/Programs/WuwaTappoGlobal/Wuwa Tappo.desktop" "$(HOME)/.config/menus/applications-merged/wine-Programs-WuwaTappoGlobal-Wuwa Tappo.menu" "$(HOME)/.local/share/desktop-directories/wine-Programs-WuwaTappoGlobal.directory"
	[ ! -d "$(APPLICATIONS)/wine/Programs/WuwaTappoGlobal" ] || rmdir --ignore-fail-on-non-empty "$(APPLICATIONS)/wine/Programs/WuwaTappoGlobal"
	! grep -qsF '"WINEPREFIX=$(WINEPREFIX)"' "$(DESKTOP_DIR)/Wuwa Tappo.desktop" || rm -f "$(DESKTOP_DIR)/Wuwa Tappo.desktop"
	! grep -qsaF 'WuwaTappoGlobal' "$(DESKTOP_DIR)/Wuwa Tappo.lnk" || rm -f "$(DESKTOP_DIR)/Wuwa Tappo.lnk"
	grep -lsF '"WINEPREFIX=$(WINEPREFIX)"' "$(APPLICATIONS)"/wine-extension-*.desktop | xargs -r -d '\n' rm -f

launcher: $(RUNTIME_STAMP) wine-leftovers
	mkdir -p "$(APPLICATIONS)"
	sed -e 's|@PROJECT@|$(CURDIR)|' -e 's|@WINEPREFIX@|$(WINEPREFIX)|' -e 's|@NO_DISPLAY@|$(if $(filter no,$(MENU_ENTRY)),true,false)|' runtime/wuwa-tappo.desktop.in > "$(APPLICATIONS)/wuwa-tappo.desktop"
	[ -z "$(WINE_ICON)" ] || install -Dm644 "$(WINE_ICON)" "$(ICONS)/256x256/apps/wuwa-tappo.png"
	$(WINE_ENV) "$(WINE)" reg add 'HKCU\Software\Wine\DllOverrides' /v winemenubuilder.exe /t REG_SZ /d '' /f
	update-desktop-database "$(APPLICATIONS)"
	! command -v kbuildsycoca6 > /dev/null || kbuildsycoca6 2> /dev/null
	xdg-mime default wuwa-tappo.desktop x-scheme-handler/wuwa-buddy

autostart: launcher
	mkdir -p "$(AUTOSTART)"
	sed -e '/^NoDisplay=/d' "$(APPLICATIONS)/wuwa-tappo.desktop" > "$(AUTOSTART)/wuwa-tappo.desktop"

# Plasma and GNOME only start a desktop file from the desktop once it's executable, and GNOME also wants it marked trusted.
desktop-shortcut: launcher
	mkdir -p "$(DESKTOP_DIR)"
	sed -e '/^NoDisplay=/d' "$(APPLICATIONS)/wuwa-tappo.desktop" > "$(DESKTOP_DIR)/wuwa-tappo.desktop"
	chmod +x "$(DESKTOP_DIR)/wuwa-tappo.desktop"
	gio set "$(DESKTOP_DIR)/wuwa-tappo.desktop" metadata::trusted true 2> /dev/null || true

logout-script:
	mkdir -p "$(LOGOUT_SCRIPTS)"
	ln -sfn "$(CURDIR)/runtime/tappo-quit" "$(LOGOUT_SCRIPTS)/00-wuwa-tappo-quit"

install: ole32 service launcher logout-script

uninstall: wine-leftovers
	-systemctl --user disable --now tappo-bridge.service
	rm -f "$(UNIT_DIR)/tappo-bridge.service" "$(APPLICATIONS)/wuwa-tappo.desktop" "$(AUTOSTART)/wuwa-tappo.desktop" "$(DESKTOP_DIR)/wuwa-tappo.desktop" "$(LOGOUT_SCRIPTS)/00-wuwa-tappo-quit" "$(ICONS)/256x256/apps/wuwa-tappo.png"
	rm -f "$(ICONS)"/*/apps/*_WuwaTappo.0.png
	rm -rf "$(APPLICATIONS)/wine/Programs/WuwaTappoGlobal"
	systemctl --user daemon-reload
	update-desktop-database "$(APPLICATIONS)"
	! command -v kbuildsycoca6 > /dev/null || kbuildsycoca6 2> /dev/null

clean:
	rm -rf build
