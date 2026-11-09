#!/usr/bin/env bash
set -euo pipefail

project=$(dirname "$(realpath "$0")")
installed_prefix=$(sed -n 's|^ExecStart=.* --prefix "\(.*\)"$|\1|p' "$HOME/.config/systemd/user/tappo-bridge.service" 2> /dev/null || true)
prefix=$(realpath -m "${WINEPREFIX:-${installed_prefix:-$HOME/Games/wuwa-tappo}}")
webview2_installer=
tappo_installer=
autostart=ask
desktop_shortcut=ask
menu_entry=keep
keep_installers=false
assume_yes=false
uninstall=false
tappo_installed_now=false
wine_shortcut_found=false
desktop=$(xdg-user-dir DESKTOP 2> /dev/null || echo "$HOME/Desktop")

arch_packages=(wine python-evdev base-devel curl zstd)
fedora_packages=(wine python3-evdev gcc gcc-c++ make curl zstd)
webview2_url='https://go.microsoft.com/fwlink/?linkid=2124701'
webview2_download=build/MicrosoftEdgeWebView2RuntimeInstallerX64.exe
tappo_page='https://download-wutheringwaves-wuwatappo.kurogames-global.com/download/en/'
tappo_activity_config='https://api-wutheringwaves-wuwatappo.kurogames-global.com/api/common/activity-config'

usage() {
	cat <<EOF
Usage: ./install.sh [options]

Sets up Wuwa Tappo under the patched Wine in this folder, or brings an existing setup up to date.
Steps that are already done are skipped, so it's safe to run again.

Options:
  --prefix DIR           Wine prefix to use (default: \$WINEPREFIX, then the one already set up, then ~/Games/wuwa-tappo)
  --webview2 FILE        use this WebView2 standalone installer instead of downloading it
  --tappo FILE           use this Tappo installer instead of downloading the latest one
  --autostart            start Tappo when you log in
  --no-autostart         don't, and remove the autostart entry if there is one
  --desktop-shortcut     put a shortcut on your desktop without asking
  --no-desktop-shortcut  don't, and remove it if it's there
  --no-menu-entry        keep Tappo out of the app menu (logging in still works)
  --menu-entry           put it back in
  --keep-installers      keep the downloaded installers in build/ instead of deleting them
  -y, --yes              don't ask anything, take the defaults
  --uninstall            remove everything this installed, and offer to delete the prefix
  -h, --help             show this
EOF
}

say() {
	printf '\n\033[1m==> %s\033[0m\n' "$*"
}

warn() {
	printf '\033[33mwarning:\033[0m %s\n' "$*" >&2
}

die() {
	printf '\033[31merror:\033[0m %s\n' "$*" >&2
	exit 1
}

ask() {
	local question=$1 default=$2 hint answer
	[ "$default" = y ] && hint='[Y/n]' || hint='[y/N]'
	if $assume_yes || [ ! -t 0 ]; then
		[ "$default" = y ]
		return
	fi
	read -r -p "$question $hint " answer
	case "${answer:-$default}" in
		[Yy]*) return 0 ;;
		*) return 1 ;;
	esac
}

run_make() {
	make --no-print-directory WINEPREFIX="$prefix" DESKTOP_DIR="$desktop" MENU_ENTRY="$menu_entry" "$@"
}

tappo_running() {
	pgrep -u "$UID" -f '\\WuwaTappo\.exe( |$)' > /dev/null
}

wait_for_tappo_to_quit() {
	tappo_running || return 0
	say "Wuwa Tappo is running. Quit it from its tray icon to carry on."
	while tappo_running; do
		sleep 2
	done
}

download() {
	local url=$1 destination=$2
	[ -s "$destination" ] && return
	mkdir -p "$(dirname "$destination")"
	# The WebView2 installer keeps one name across versions, so a .part from an earlier run could be a different file.
	rm -f "$destination.part"
	curl -fL -C - --retry 5 --retry-all-errors --retry-delay 2 -o "$destination.part" "$url"
	mv "$destination.part" "$destination"
}

latest_tappo_url() {
	python3 -I - "$tappo_activity_config" <<'EOF'
import json
import sys
import urllib.request


def fetch(url):
	with urllib.request.urlopen(url, timeout=15) as response:
		return json.load(response)


try:
	assets_host = fetch(sys.argv[1])['data']['assets_host']
	host = fetch(f'{assets_host}/app/json-host/latest-global_host.json')
	print(fetch(host['url'])['platforms']['windows-x86_64']['url'])
except Exception as error:
	sys.exit(f'could not find the latest Tappo installer: {error}')
EOF
}

downloaded_tappo_installer() {
	local directory candidates=()
	shopt -s nullglob
	for directory in "$(xdg-user-dir DOWNLOAD 2> /dev/null || true)" "$HOME/Downloads"; do
		[ -n "$directory" ] && candidates+=("$directory"/WuwaTappo-global_*_x64-setup.exe)
	done
	shopt -u nullglob
	[ ${#candidates[@]} -gt 0 ] || return 1
	printf '%s\n' "${candidates[@]}" | sort -uV | tail -n 1
}

check_packages() {
	if command -v pacman > /dev/null; then
		local missing=()
		mapfile -t missing < <(pacman -T "${arch_packages[@]}" || true)
		[ ${#missing[@]} -gt 0 ] || return 0
		say "Missing packages: ${missing[*]}"
		ask "Install them with sudo pacman?" y || die "install them, then run this again"
		sudo pacman -S --needed "${missing[@]}"
	elif command -v dnf5 > /dev/null || command -v dnf > /dev/null; then
		local dnf_cmd="dnf"
		command -v dnf5 > /dev/null && dnf_cmd="dnf5"
		local missing=() pkg
		for pkg in "${fedora_packages[@]}"; do
			if [ "$pkg" = "wine" ]; then
				if ! rpm -q wine > /dev/null 2>&1 && ! rpm -q wine-core > /dev/null 2>&1; then
					missing+=("wine")
				fi
			elif ! rpm -q "$pkg" > /dev/null 2>&1; then
				missing+=("$pkg")
			fi
		done
		[ ${#missing[@]} -gt 0 ] || return 0
		say "Missing packages: ${missing[*]}"
		ask "Install them with sudo $dnf_cmd?" y || die "install them, then run this again"
		sudo "$dnf_cmd" install "${missing[@]}"
	else
		warn "neither pacman nor dnf found, so packages aren't checked; you need Wine's libraries, python-evdev, gcc, make, curl and zstd"
	fi
}

check_input_group() {
	[[ " $(id -nG) " == *' input '* ]] && return
	if [[ " $(id -nG "$USER") " == *' input '* ]]; then
		warn "you're in the input group, but this session started before that; log out and back in so Tappo hears other apps"
		return
	fi
	if ! new_setup; then
		warn "you're not in the input group, so Tappo will only count clicks on itself; sudo usermod -aG input $USER adds you"
		return
	fi
	say "The bridge reads your keyboards and mice, which needs your user in the input group."
	echo "That lets every program you run read them too, so think about it on a shared machine."
	if ask "Add $USER to input with sudo usermod?" y; then
		sudo usermod -aG input "$USER"
		warn "log out and back in once this finishes, or Tappo will only count clicks on itself"
	else
		warn "skipped; Tappo will only count clicks on itself"
	fi
}

webview2_installed() {
	compgen -G "$prefix/drive_c/Program Files (x86)/Microsoft/EdgeWebView/Application/*/msedgewebview2.exe" > /dev/null
}

tappo_installed() {
	[ -f "$prefix/drive_c/Program Files/WuwaTappoGlobal/WuwaTappo.exe" ]
}

# Preferences only get asked about on a new setup, so a no isn't asked again on every update.
new_setup() {
	$tappo_installed_now || [ -z "$installed_prefix" ]
}

install_webview2() {
	if webview2_installed; then
		say "WebView2 is already installed"
		return
	fi
	local installer=$webview2_installer
	if [ -z "$installer" ]; then
		say "Downloading Microsoft's WebView2 standalone installer"
		installer=$webview2_download
		download "$webview2_url" "$installer"
	fi
	say "Installing WebView2, which takes about a minute"
	if [ -t 1 ]; then
		run_with_webview2_progress run_make webview2 WEBVIEW2_INSTALLER="$(realpath "$installer")"
	else
		run_make webview2 WEBVIEW2_INSTALLER="$(realpath "$installer")"
	fi
	webview2_installed || die "the WebView2 installer finished without installing WebView2"
}

# Microsoft's installer shows nothing with /silent, so the bar follows its updater's log, and make's output goes to a file so the bar keeps its line.
run_with_webview2_progress() {
	local output=build/webview2-install.log watcher status=0
	python3 -I - "$prefix/drive_c/ProgramData/Microsoft/EdgeUpdate/Log/MicrosoftEdgeUpdate.log" <<'EOF' &
import math
import os
import shutil
import signal
import sys
import time

log_path = sys.argv[1]
# The seconds come from one install here, so a step's first line in the log moves the bar on to that step however long the last one really took.
phases = [
	(None, 3, "Starting Microsoft's installer"),
	('[Install files]', 1.6, "Setting up Edge's updater"),
	('[InstallApps]', 30.5, 'Getting ready'),
	('[PackageCache::Put]', 3.4, 'Copying WebView2'),
	('[Running installer]', 22.8, 'Unpacking WebView2'),
	('[InstallerResult]', 1, 'Finishing up'),
]
total_seconds = sum(seconds for _, seconds, _ in phases)
starts = []
for index in range(len(phases)):
	starts.append(100 * sum(seconds for _, seconds, _ in phases[:index]) / total_seconds)
starts.append(100)
fancy = (sys.stdout.encoding or '').lower().replace('-', '') == 'utf8'
parent = os.getppid()
finished = None


def finish(signum, frame):
	global finished
	finished = signum == signal.SIGUSR1


def creep(progress):
	if progress <= 1:
		return 0.9 * progress
	return 0.9 + 0.09 * (1 - math.exp(1 - progress))


def log_size():
	try:
		return os.path.getsize(log_path)
	except OSError:
		return 0


def new_log_text():
	try:
		with open(log_path, 'rb') as log:
			log.seek(offset if log_size() >= offset else 0)
			return log.read().decode('utf-16-le', 'ignore')
	except OSError:
		return ''


def draw(percent, label, end=''):
	elapsed = int(time.monotonic() - started)
	tail = f' {int(percent):3d}%  {label}  {elapsed // 60}:{elapsed % 60:02d}'
	columns = shutil.get_terminal_size().columns
	width = min(40, columns - len(tail) - 1)
	if width < 10:
		line = tail.strip()[:columns - 1]
	elif fancy:
		halves = round(percent / 100 * width * 2)
		filled = '━' * (halves // 2) + '╸' * (halves % 2)
		line = f'{filled}\033[2m{"━" * (width - len(filled))}\033[0m{tail}'
	else:
		filled = '#' * round(percent / 100 * (width - 2))
		line = f'[{filled}{"-" * (width - 2 - len(filled))}]{tail}'
	sys.stdout.write(f'\r{line}\033[K{end}')
	sys.stdout.flush()


signal.signal(signal.SIGUSR1, finish)
signal.signal(signal.SIGUSR2, finish)
offset = log_size()
started = time.monotonic()
phase = 0
phase_started = started
shown = 0
while finished is None and os.getppid() == parent:
	text = new_log_text()
	for index in range(len(phases) - 1, phase, -1):
		if phases[index][0] in text:
			phase = index
			phase_started = time.monotonic()
			break
	now = time.monotonic()
	# Before the log says anything the whole minute is the estimate, which also covers a log that never shows up.
	if phase == 0:
		percent = 100 * creep((now - started) / total_seconds)
	else:
		percent = starts[phase] + (starts[phase + 1] - starts[phase]) * creep((now - phase_started) / phases[phase][1])
	shown = max(shown, percent)
	draw(shown, phases[phase][2])
	time.sleep(0.25)
if finished:
	draw(100, 'Done', '\n')
else:
	sys.stdout.write('\n')
EOF
	watcher=$!
	"$@" > "$output" 2>&1 || status=$?
	if [ "$status" -eq 0 ]; then
		kill -USR1 "$watcher" 2> /dev/null || true
	else
		kill -USR2 "$watcher" 2> /dev/null || true
	fi
	wait "$watcher" || true
	if [ "$status" -ne 0 ]; then
		cat "$output" >&2
		die "Microsoft's WebView2 installer failed, see above"
	fi
	rm -f "$output"
}

install_tappo() {
	if tappo_installed; then
		say "Wuwa Tappo is already installed, and it updates itself"
		return
	fi
	local installer=$tappo_installer url
	if [ -z "$installer" ] && url=$(latest_tappo_url); then
		installer="build/$(basename "$url")"
		case "$installer" in
			build/WuwaTappo-global_*_x64-setup.exe) ;;
			*) die "Kuro's download link doesn't look like a Tappo installer: $url" ;;
		esac
		say "Downloading $(basename "$url") from Kuro"
		download "$url" "$installer"
	fi
	if [ -z "$installer" ] && installer=$(downloaded_tappo_installer); then
		warn "using $installer instead"
	fi
	[ -n "$installer" ] || die "download the Tappo installer from $tappo_page and run this again, or pass it with --tappo"
	say "Running Kuro's installer. Click through it. If it starts Tappo when it's done, that copy gets closed again."
	run_make app TAPPO_INSTALLER="$(realpath "$installer")"
	tappo_installed || die "Kuro's installer finished without installing Tappo; run this again to retry"
	tappo_installed_now=true
}

wait_for_wine_icon() {
	for _ in {1..10}; do
		compgen -G "$HOME/.local/share/icons/hicolor/256x256/apps/*_WuwaTappo.0.png" > /dev/null && return
		sleep 1
	done
	warn "Wine didn't extract Tappo's icon, so the menu entry will have a generic one"
}

set_up_autostart() {
	local entry="$HOME/.config/autostart/wuwa-tappo.desktop"
	case "$autostart" in
		no)
			rm -f "$entry"
			return
			;;
		ask)
			[ -f "$entry" ] || { new_setup && ask "Start Tappo when you log in?" n; } || return 0
			;;
	esac
	run_make autostart
}

offer_desktop_shortcut() {
	if $wine_shortcut_found; then
		echo "Kuro's installer put a shortcut on your desktop that starts Tappo on your system's Wine, without any of these fixes, so that's gone."
		ask "Put one there that works?" y
	elif new_setup && [ -d "$desktop" ]; then
		ask "Put a shortcut to Tappo on your desktop?" y
	else
		return 1
	fi
}

set_up_desktop_shortcut() {
	case "$desktop_shortcut" in
		no)
			rm -f "$desktop/wuwa-tappo.desktop"
			return
			;;
		ask)
			[ -f "$desktop/wuwa-tappo.desktop" ] || offer_desktop_shortcut || return 0
			;;
	esac
	run_make desktop-shortcut
}

remove_installers() {
	local installer removed=false
	for installer in "$webview2_download" build/WuwaTappo-global_*_x64-setup.exe; do
		[ -f "$installer" ] || continue
		rm -f "$installer"
		removed=true
	done
	if $removed; then
		echo "Deleted the installers it downloaded, since they're only needed to set up a new prefix."
	fi
}

set_up_corners_exclusion() {
	new_setup || return 0
	local qdbus=
	for cmd in qdbus6 qdbus-qt6 qdbus; do
		if command -v "$cmd" > /dev/null; then
			qdbus="$cmd"
			break
		fi
	done
	[ -n "$qdbus" ] || return 0
	[ "$("$qdbus" org.kde.KWin /Effects org.kde.kwin.Effects.isEffectLoaded kwin4_effect_shapecorners 2> /dev/null)" = true ] || return 0
	case ",$(kreadconfig6 --file kwinrc --group Round-Corners --key Exclusions 2> /dev/null)," in
		*,wuwatappo.exe,*) return ;;
	esac
	ask "Rounded Corners is on, and it draws an outline round Tappo's panels. Exclude Tappo from it?" y || return 0
	run_make corners-exclusion
}

uninstall_everything() {
	wait_for_tappo_to_quit
	say "Removing the bridge, menu entry, autostart entry, desktop shortcut and logout script"
	run_make uninstall
	if [ -d "$prefix" ] && ask "Also delete the prefix in $prefix? That deletes Tappo and everything it saved." n; then
		[ -x build/wine-runtime/bin/wineserver ] && WINEPREFIX="$prefix" build/wine-runtime/bin/wineserver -k 2> /dev/null || true
		rm -rf "$prefix"
		echo "Deleted $prefix."
	fi
	echo "To get rid of the downloaded Wine as well, delete $project."
}

while [ $# -gt 0 ]; do
	case "$1" in
		--prefix)
			prefix=$(realpath -m "${2:?--prefix needs a directory}")
			shift
			;;
		--webview2)
			webview2_installer=$(realpath -m "${2:?--webview2 needs a file}")
			shift
			;;
		--tappo)
			tappo_installer=$(realpath -m "${2:?--tappo needs a file}")
			shift
			;;
		--autostart) autostart=yes ;;
		--no-autostart) autostart=no ;;
		--desktop-shortcut) desktop_shortcut=yes ;;
		--no-desktop-shortcut) desktop_shortcut=no ;;
		--menu-entry) menu_entry=yes ;;
		--no-menu-entry) menu_entry=no ;;
		--keep-installers) keep_installers=true ;;
		-y | --yes) assume_yes=true ;;
		--uninstall) uninstall=true ;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			usage >&2
			exit 1
			;;
	esac
	shift
done

for installer in "$webview2_installer" "$tappo_installer"; do
	[ -z "$installer" ] || [ -f "$installer" ] || die "$installer doesn't exist"
done
if [ "$menu_entry" = keep ]; then
	grep -qsx 'NoDisplay=true' "$HOME/.local/share/applications/wuwa-tappo.desktop" && menu_entry=no || menu_entry=yes
fi
cd "$project"

if $uninstall; then
	uninstall_everything
	exit 0
fi

wait_for_tappo_to_quit
check_packages
check_input_group

say "Getting the patched Wine runtime ready"
run_make runtime

if [ -f "$prefix/system.reg" ]; then
	say "Updating the prefix in $prefix"
else
	say "Creating the prefix in $prefix. If Wine offers to install Gecko, let it."
fi
run_make prefix

install_webview2
install_tappo
if $tappo_installed_now; then
	wait_for_wine_icon
	WINEPREFIX="$prefix" build/wine-runtime/bin/wineserver -k 2> /dev/null || true
	WINEPREFIX="$prefix" build/wine-runtime/bin/wineserver -w
fi

say "Installing the ole32 fix, the bridge, the menu entry and the logout script"
if grep -qsF "\"WINEPREFIX=$prefix\"" "$desktop/Wuwa Tappo.desktop" || grep -qsaF WuwaTappoGlobal "$desktop/Wuwa Tappo.lnk"; then
	wine_shortcut_found=true
fi
run_make install
cmp -s build/ole32.dll "$prefix/drive_c/windows/system32/ole32.dll" || die "the ole32 fix didn't stick in $prefix/drive_c/windows/system32"
set_up_autostart
set_up_desktop_shortcut
set_up_corners_exclusion
$keep_installers || remove_installers

say "All done"
if ask "Start Wuwa Tappo now?" y; then
	# Started from here directly, Tappo would live in the terminal's cgroup, and System Monitor lists it under the terminal instead of as an app.
	if command -v kstart > /dev/null; then
		kstart --application wuwa-tappo > /dev/null 2>&1
	else
		WINEPREFIX="$prefix" setsid -f runtime/tappo-launch > /dev/null 2>&1
	fi
	echo "It takes a few seconds to appear. Log in from its panel, and the browser hands the login back to it."
elif [ "$menu_entry" = yes ]; then
	echo "Start Wuwa Tappo from the app menu whenever you like."
fi
