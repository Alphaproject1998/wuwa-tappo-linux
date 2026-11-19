#!/usr/bin/env python3
import argparse
import collections
import datetime
import filecmp
import glob
import grp
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import pwd
import re
import shutil
import socket
import subprocess
import sys
import urllib.parse
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
RUNTIME = PROJECT / 'build' / 'wine-runtime'
HELPER = PROJECT / 'build' / 'tappo-input.exe.so'
ABOVE_FULLSCREEN_FILE = PROJECT / 'build' / 'above-fullscreen'
BRIDGE = PROJECT / 'bridge' / 'tappo-bridge.py'
HOME = Path.home()
REPOSITORY = 'Alphaproject1998/wuwa-tappo-linux'
APP_EXE = 'WuwaTappo.exe'
APP_ID = 'com.kuro.wuwatappo.global'
DEFAULT_PREFIX = HOME / 'Games' / 'wuwa-tappo'
WINE_SERVERS = ('wineserver', 'tappo-wineserver')
STAGE_TITLE = 'Wuwa Tappo'
CLICK_LAYER_TITLE = 'DesktopCatInput'
X_CLASSES = ('wuwatappo.exe', 'tappo-input.exe')
WINDOW_PROPERTIES = (
	'WM_WINDOW_ROLE', 'WM_STATE', '_NET_WM_STATE', '_NET_WM_WINDOW_OPACITY', '_WINE_TASKBAR_TAB', '_TAPPO_BRIDGE_HIDDEN',
	'XdndAware',
)
KWIN_GROUPS = ('Compositing', 'Windows')
BLUR_EFFECTS = {
	'Effect-blurplus': ('forceblur', 'forceblur_x11'),
	'Effect-better-blur-dx': ('better_blur_dx',),
}
SLEEP_UNITS = (
	'systemd-suspend.service', 'systemd-hibernate.service', 'systemd-hybrid-sleep.service',
	'systemd-suspend-then-hibernate.service',
)
TOOLS = ('xprop', 'xwininfo', 'xrandr', 'lspci', 'journalctl', 'coredumpctl', 'busctl')

VERSIONED_LIBRARIES = {
	'Mesa': 'libEGL_mesa.so.0', 'NVIDIA': 'libEGL_nvidia.so.0', 'NVIDIA EGL on X11': 'libnvidia-egl-xlib.so.1',
	'libX11': 'libX11.so.6', 'libXrandr': 'libXrandr.so.2',
}

# Checked by these exact names because the runtime is Arch's build, and it loads each from the system under the name Arch ships it as.
WINE_LIBRARIES = {
	'libX11.so.6': 'windows', 'libXext.so.6': 'window shapes', 'libXrandr.so.2': 'screens', 'libXinerama.so.1': 'screens',
	'libXi.so.6': 'input', 'libXcursor.so.1': 'cursors', 'libXcomposite.so.1': 'drawing', 'libXrender.so.1': 'drawing',
	'libXfixes.so.3': 'clipboard', 'libEGL.so.1': 'drawing', 'libGL.so.1': 'drawing', 'libfreetype.so.6': 'text',
	'libfontconfig.so.1': 'fonts', 'libgnutls.so.30': 'secure connections, so logging in', 'libdbus-1.so.3': 'devices',
	'libudev.so.1': 'controllers',
}

WINE_KEYS = (
	'Software\\Wine\\AppDefaults', 'Software\\Wine\\DllOverrides', 'Software\\Wine\\Direct3D', 'Software\\Wine\\Drivers',
	'Software\\Wine\\Explorer', 'Software\\Wine\\Wayland Driver', 'Software\\Wine\\X11 Driver',
)

ENVIRONMENT = re.compile(
	r'(?:WINE|DXVK_|VKD3D|MESA_|__NV_|__GL_|LIBGL_|TAPPO_|QT_QPA_|LC_)|'
	r'(?:GLIBC_TUNABLES|GDK_BACKEND|XDG_SESSION_TYPE|XDG_CURRENT_DESKTOP|WAYLAND_DISPLAY|DISPLAY|LANG)$'
)

CRASH_NAMES = (
	'tappo-bridge', 'tappo-input.exe', 'WuwaTappo.exe', 'msedgewebview2.', 'tappo-wineserve', 'wineserver',
	'explorer.exe', 'Xwayland',
)

WM_FAMILIES = (
	('kde', ('kwin', 'kde')), ('gnome', ('gnome', 'mutter')), ('hyprland', ('hyprland',)), ('sway', ('sway',)),
	('i3', ('i3',)),
)

TAPPO_UNITS = 'app-wuwa*tappo@*'
TAPPO_TAG = 'wuwa-tappo'
TAPPO_TIMESTAMP = re.compile(r'^\[[^]]*\]\[[^]]*\]')
TAPPO_LINE = re.compile(r': \[\d{4}-\d\d-\d\d\]\[\d\d:\d\d:\d\d\]\[[A-Z]+\]\[')
JOURNAL_TIMESTAMP = re.compile(r'^\S+ ')
JOURNAL_KEY = re.compile(r'^\S+ |\[\d+\]|(?<=: )[0-9a-f]{4}:')
NO_KEY = re.compile(r'(?!)')
RUN_ENDS = ('Consumed ', 'Deactivated successfully', 'Failed with result')
COMPOSITOR_LINES = re.compile(
	r'(?i)xwayland|\bx11\b|wuwatappo|tappo|\bwine|fatal|segfault|segmentation|crash|core dump|abort|lost connection|'
	r'broken pipe|\(EE\)'
)
# Narrower for logs that hold every app's output, where a line about some other app's crash could carry what it was showing.
SESSION_LINES = re.compile(r'(?i)xwayland|\bx11\b|wuwatappo|tappo|\bwine')
# Left out because with --real-keys, the helper's --verbose lines would be a record of everything typed.
HELPER_CHATTER = re.compile(r': (?:key vk=|cursor x=)')
UPDATE_SHOWN = re.compile(r'\[UPDATE\] event=window_shown version="([^"]*)"')
SETTINGS_KEY = re.compile(rb'_[a-z]+://[^\x00]+\x00\x01wuwatappo-settings')
X_WINDOW = re.compile(r'^\s*(0x[0-9a-f]+) (?:"(.*)"|\(has no name\)): \("([^"]*)"')

LOG_SIGNATURES = (
	('bridge', r'Traceback \(most recent call last\)', 'The bridge crashed'),
	('bridge', r'helper exited with|helper pipe closed', 'The bridge\'s helper inside Wine stopped'),
	('bridge', r'sending vk .* failed', 'Wine refused keys the bridge sent, so those taps didn\'t count'),
	('bridge', r'foreground window failed|stand-in window failed', 'The fullscreen stand-in failed, which Enter Game Mode in Fullscreen depends on'),
	('bridge', r'not keeping the pet on its screen', 'The bridge couldn\'t keep the pet on its screen'),
	('bridge', r'leaving the pet on .*, as ', 'The bridge gave up putting the pet back on its screen'),
	('bridge', r'not putting a copy of the pet', 'The bridge couldn\'t make the taskbar copy of the pet'),
	('bridge', r'not pinning the taskbar copy|not loading pin-copy|KWin would not (?:load|start)', 'KWin wouldn\'t run the script that tells the bridge when a fullscreen window covers the taskbar copy'),
	('wine', r'X connection to .* broken', 'Wine lost its connection to Xwayland'),
	('wine', r'X Error of failed request', 'An X error killed a Wine process'),
	('wine', r'wine: Unhandled|Unhandled page fault', 'A Wine process crashed'),
	('wine', r'err:module:import_dll', 'A Windows DLL failed to load'),
	('wine', r'no driver could be loaded', 'Wine couldn\'t load its display driver'),
	('tappo', r'\[NETWORK\] event=offline_confirmed', 'Tappo decided it was offline at some point'),
	('compositor', r'(?i)xwayland.*(?:crash|fatal|segfault|abort)', 'Xwayland crashed or aborted'),
)

Process = collections.namedtuple('Process', 'pid comm argv environ')
XWindow = collections.namedtuple('XWindow', 'id title rect viewable override_redirect depth properties')

SECTIONS = []


def section(title, logs=False):
	def register(gather):
		SECTIONS.append((title, gather, logs))
		return gather
	return register


def run(*command, timeout=20, env=None):
	try:
		result = subprocess.run(
			command, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors='replace', timeout=timeout, env=env,
		)
	except (OSError, subprocess.TimeoutExpired):
		return None
	return result.stdout.rstrip()


def read_text(path):
	try:
		return Path(path).read_text(encoding='utf-8', errors='replace')
	except OSError:
		return ''


def sha256(path):
	try:
		return hashlib.sha256(Path(path).read_bytes()).hexdigest()
	except OSError:
		return 'missing'


def gib(meminfo_value):
	try:
		return f'{int(meminfo_value.split()[0]) / 1048576:.1f} GiB'
	except (ValueError, IndexError):
		return 'unknown'


def ini(path):
	groups = {}
	values = None
	for line in read_text(path).splitlines():
		line = line.strip()
		if line.startswith('['):
			values = groups.setdefault(line[1:-1], {})
			continue
		key, separator, value = line.partition('=')
		if separator and values is not None:
			values[key.strip()] = value.strip()
	return groups


def os_release():
	values = {}
	for line in (read_text('/etc/os-release') or read_text('/usr/lib/os-release')).splitlines():
		key, separator, value = line.partition('=')
		if separator:
			values[key] = value.strip().strip('"\'')
	return values


def boot_time():
	match = re.search(r'^btime (\d+)$', read_text('/proc/stat'), re.M)
	return int(match.group(1)) if match else 0


def processes():
	found = []
	for entry in Path('/proc').iterdir():
		if not entry.name.isdigit():
			continue
		try:
			if entry.stat().st_uid != os.getuid():
				continue
			comm = (entry / 'comm').read_text().strip()
			argv = [part.decode(errors='replace') for part in (entry / 'cmdline').read_bytes().split(b'\0')]
		except OSError:
			continue
		environ = {}
		# Optional because a binary with file capabilities, like kwin_wayland with cap_sys_nice, hides its environment even from its own user, and skipping those processes lost the compositor.
		try:
			for line in (entry / 'environ').read_bytes().split(b'\0'):
				key, separator, value = line.partition(b'=')
				if separator:
					environ[key.decode(errors='replace')] = value.decode(errors='replace')
		except OSError:
			pass
		found.append(Process(int(entry.name), comm, argv, environ))
	return found


def parent_pid(pid):
	try:
		return int(read_text(f'/proc/{pid}/stat').rsplit(')', 1)[1].split()[1])
	except (ValueError, IndexError):
		return None


def cgroup_unit(pid):
	return read_text(f'/proc/{pid}/cgroup').strip().rsplit('/', 1)[-1]


def program_name(process):
	return process.argv[0].replace('\\', '/').rsplit('/', 1)[-1]


def is_tappo(process):
	return program_name(process).lower() == APP_EXE.lower()


def wine_prefix(process):
	return Path(process.environ.get('WINEPREFIX') or HOME / '.wine').resolve()


def in_prefix(process, prefix):
	name = program_name(process).lower()
	if not (name.endswith(('.exe', '.exe.so')) or name in WINE_SERVERS):
		return False
	return wine_prefix(process) == prefix.resolve()


def pss_kib(pid):
	for line in read_text(f'/proc/{pid}/smaps_rollup').splitlines():
		if line.startswith('Pss:'):
			return int(line.split()[1])
	return 0


def running_for(pid):
	try:
		started = int(read_text(f'/proc/{pid}/stat').rsplit(')', 1)[1].split()[19]) / os.sysconf('SC_CLK_TCK')
		uptime = float(read_text('/proc/uptime').split()[0])
	except (ValueError, IndexError):
		return 'an unknown time'
	seconds = int(uptime - started)
	return f'{seconds // 3600}h {seconds % 3600 // 60}m'


def started_at(pid):
	ticks = int(read_text(f'/proc/{pid}/stat').rsplit(')', 1)[1].split()[19])
	return boot_time() + ticks / os.sysconf('SC_CLK_TCK')


def find_prefix(requested, procs):
	if requested:
		return Path(requested).expanduser().resolve(), 'from --prefix'
	tappo = next((process for process in procs if is_tappo(process)), None)
	if tappo:
		return wine_prefix(tappo), 'the running Tappo\'s'
	unit = read_text(HOME / '.config' / 'systemd' / 'user' / 'tappo-bridge.service')
	match = re.search(r'--prefix "([^"]+)"', unit)
	if match:
		return Path(match.group(1)), 'the bridge service\'s'
	if os.environ.get('WINEPREFIX'):
		return Path(os.environ['WINEPREFIX']).resolve(), 'from $WINEPREFIX'
	return DEFAULT_PREFIX, 'the default'


def ewmh_name():
	check = re.search(r'window id # (0x[0-9a-f]+)', run('xprop', '-root', '_NET_SUPPORTING_WM_CHECK') or '')
	if not check:
		return None
	name = re.search(r'= "(.*)"', run('xprop', '-id', check.group(1), '_NET_WM_NAME') or '')
	return name.group(1) if name else None


def find_session(procs):
	by_pid = {process.pid: process for process in procs}
	xwayland = next((process for process in procs if process.comm == 'Xwayland'), None)
	compositor = by_pid.get(parent_pid(xwayland.pid)) if xwayland else None
	# niri runs Xwayland through xwayland-satellite rather than itself.
	if compositor and compositor.comm.startswith('xwayland-sat'):
		compositor = by_pid.get(parent_pid(compositor.pid))
	ewmh = ewmh_name()
	described = ' '.join(filter(None, (compositor.comm if compositor else '', ewmh, os.environ.get('XDG_CURRENT_DESKTOP'))))
	family = next((name for name, words in WM_FAMILIES if any(word in described.lower() for word in words)), None)
	return {'xwayland': xwayland, 'compositor': compositor, 'ewmh': ewmh, 'family': family}


def app_data(prefix, kind):
	return next(iter(sorted((prefix / 'drive_c' / 'users').glob(f'*/AppData/{kind}/{APP_ID}'))), None)


def tappo_log(prefix):
	local = app_data(prefix, 'Local')
	if not local:
		return []
	files = sorted((local / 'logs').glob('*.log'), key=lambda path: path.stat().st_mtime)
	return [line for path in files for line in read_text(path).splitlines()]


# Read from the .reg files, because wine reg would start a wineserver in the prefix, and on a fresh one that also applies the stock ole32 Wine has queued.
def parse_registry(text):
	keys = {}
	values = None
	for line in text.splitlines():
		if line.startswith('['):
			values = keys.setdefault(line[1:line.rfind(']')].replace('\\\\', '\\'), {})
			continue
		if values is None or not line.startswith(('"', '@')):
			continue
		match = re.match(r'("(?:[^"\\]|\\.)*"|@)=(.*)', line)
		if match:
			values[match.group(1).strip('"')] = match.group(2)
	return keys


def pe_layout(path):
	try:
		with open(path, 'rb') as file:
			header = file.read(0x1000)
	except OSError:
		return 'missing'
	pe = int.from_bytes(header[0x3c:0x40], 'little')
	if int.from_bytes(header[pe + 60:pe + 64], 'little') >= 0x1000:
		return 'laid out for sharing'
	return 'not laid out yet (the launcher does that on the next start)'


def runtime_patcher():
	# Read from the patcher, so a file it starts patching later, or a new patch in one it already does, is checked here too.
	sys.dont_write_bytecode = True
	spec = importlib.util.spec_from_file_location('patch_runtime', PROJECT / 'runtime' / 'patch-runtime.py')
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def above_fullscreen():
	# The Makefile's own default when the file isn't there.
	try:
		return ABOVE_FULLSCREEN_FILE.read_text().strip() != 'no'
	except OSError:
		return True


def missing_sites(path, sites):
	try:
		data = Path(path).read_bytes()
	except OSError:
		return [start for start, _, _ in sites]
	return [start for start, _, replacement in sites if data[start:start + len(replacement)] != replacement]


def bridge_constant(name):
	match = re.search(rf"^{name} = b?'([^']*)'$", read_text(BRIDGE), re.M)
	return match.group(1) if match else None


def bridge_libraries():
	return dict.fromkeys(re.findall(r"CDLL\('([^']+)'\)", read_text(BRIDGE)), 'the bridge')


def bridge_argument(report, name):
	argv = report.bridge.argv if report.bridge else []
	for index, value in enumerate(argv):
		if value == name and index + 1 < len(argv):
			return argv[index + 1]
		if value.startswith(f'{name}='):
			return value.partition('=')[2]
	return None


def git_blob(path):
	data = path.read_bytes()
	return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def source_files():
	found = []
	for folder, subfolders, files in os.walk(PROJECT):
		subfolders[:] = sorted(name for name in subfolders if not name.startswith('.') and name not in ('build', '__pycache__'))
		found += [Path(folder) / name for name in sorted(files) if not name.startswith('.')]
	return found


def varint(data, index):
	value = shift = 0
	while True:
		byte = data[index]
		index += 1
		value |= (byte & 0x7f) << shift
		shift += 7
		if byte < 0x80:
			return value, index


def read_settings(prefix):
	local = app_data(prefix, 'Local')
	if not local:
		return {}
	# WebView2 keeps Tappo's settings in LevelDB, where each save is appended, so the newest is the last match in the highest-numbered file.
	storage = local / 'EBWebView' / 'Default' / 'Local Storage' / 'leveldb'
	for path in sorted([*storage.glob('*.log'), *storage.glob('*.ldb')], reverse=True):
		try:
			data = path.read_bytes()
		except OSError:
			continue
		for match in reversed(list(SETTINGS_KEY.finditer(data))):
			try:
				length, start = varint(data, match.end())
				value = data[start:start + length]
				settings = json.loads(value[1:].decode('latin-1' if value[0] == 1 else 'utf-16-le'))
			except (ValueError, IndexError):
				continue
			if isinstance(settings, dict):
				# Only numbers and switches, so nothing Kuro adds to this later can put anything personal in a report.
				return {name: value for name, value in settings.items() if isinstance(value, (bool, int, float))}
	return {}


def x_window(window_id, title):
	info = run('xwininfo', '-id', window_id) or ''

	def number(label):
		match = re.search(rf'{label}:\s+(-?\d+)', info)
		return int(match.group(1)) if match else 0

	listed = run('xprop', '-id', window_id, '-notype', *WINDOW_PROPERTIES) or ''
	properties = dict(re.findall(r'^(\w+) = (.*)$', listed, re.M))
	wm_state = re.search(r'window state: (\w+)', listed)
	if wm_state:
		properties['WM_STATE'] = wm_state.group(1)
	return XWindow(
		window_id, title, (number('Absolute upper-left X'), number('Absolute upper-left Y'), number('Width'), number('Height')),
		'Map State: IsViewable' in info, 'Override Redirect State: yes' in info, number('Depth'), properties,
	)


def tappo_windows():
	tree = run('xwininfo', '-root', '-tree')
	if tree is None:
		return None
	return [
		x_window(match.group(1), match.group(2)) for match in map(X_WINDOW.match, tree.splitlines())
		if match and match.group(3) in X_CLASSES
	]


def window_line(window):
	x, y, width, height = window.rect
	parts = [f'{width}x{height}+{x}+{y}', 'viewable' if window.viewable else 'unmapped', f'depth {window.depth}']
	if window.override_redirect:
		parts.append('override-redirect')
	parts += [f'{name} {re.sub(r"_(?:KDE_)?NET_WM_STATE_", "", value)}'.rstrip() for name, value in window.properties.items()]
	return f'  {window.id} "{window.title or "(no name)"}": {", ".join(parts)}'


def x_monitors():
	found = re.findall(r'(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)\s+(\S+)$', run('xrandr', '--listmonitors') or '', re.M)
	return [(name, (int(x), int(y), int(width), int(height))) for width, height, x, y, name in found]


def screen_of(rect, monitors):
	def shared(monitor):
		left, top, width, height = monitor[1]
		return (
			max(0, min(rect[0] + rect[2], left + width) - max(rect[0], left))
			* max(0, min(rect[1] + rect[3], top + height) - max(rect[1], top))
		)

	best = max(monitors, key=shared, default=None)
	return best[0] if best and shared(best) else 'no screen'


def saved_screen(report):
	state_home = os.environ.get('XDG_STATE_HOME') or HOME / '.local' / 'state'
	path = bridge_argument(report, '--screen-file') or Path(state_home) / 'tappo-bridge' / 'screens.json'
	try:
		homes = json.loads(read_text(path))
	except ValueError:
		return None
	if not isinstance(homes, dict):
		return None
	return next((screen for prefix, screen in homes.items() if Path(prefix).resolve() == report.prefix.resolve()), None)


def other_fullscreen(copy, monitors):
	stacking = re.findall(r'0x[0-9a-f]+', run('xprop', '-root', '-notype', '_NET_CLIENT_LIST_STACKING') or '')
	found = []
	for index, window_id in enumerate(stacking):
		state = run('xprop', '-id', window_id, '-notype', '_NET_WM_STATE', 'WM_CLASS') or ''
		owner = re.search(r'WM_CLASS = "([^"]*)"', state)
		if '_NET_WM_STATE_FULLSCREEN' not in state or (owner and owner.group(1) in X_CLASSES):
			continue
		line = f'  {owner.group(1) if owner else "unknown"} on {screen_of(x_window(window_id, None).rect, monitors)}'
		if copy and copy.id in stacking:
			line += ', above the taskbar copy' if index > stacking.index(copy.id) else ', under the taskbar copy'
		found.append(line)
	return found


def taskbar_copy(report, stage, copy):
	role = bridge_constant('COPY_ROLE')
	if not role:
		return []
	if not above_fullscreen():
		return ['Taskbar copy: not used, the window manager lists the pet itself']
	mirroring = bool(report.bridge) and float(bridge_argument(report, '--mirror-poll') or 1) > 0
	hidden = '_TAPPO_BRIDGE_HIDDEN' in stage.properties
	lines = [
		f'Wine\'s taskbar mark on the pet: {"yes" if "_WINE_TASKBAR_TAB" in stage.properties else "no"}',
		f'Pet hidden behind its copy: {"yes" if hidden else "no"}',
		f'Taskbar copy: {copy.id if copy else "none"}',
	]
	if report.settings.get('showTaskbarIcon') and not copy and mirroring:
		report.flag('Show Taskbar Icon is on but the bridge has no copy of the pet, so there\'s no taskbar entry or OBS capture')
	if hidden and not (copy and report.bridge):
		report.flag('The pet has been left invisible, it\'s marked as hidden behind a taskbar copy that isn\'t there')
	if copy and copy.rect != stage.rect:
		report.flag('The taskbar copy isn\'t sitting on the pet, so something else moved it and the bridge couldn\'t put it back')
	if copy and report.bridge and report.pin_script is False and '--no-pin-script' not in report.bridge.argv:
		report.flag('The KWin script that tells the bridge when a fullscreen window covers the taskbar copy isn\'t loaded')
	return lines


class Report:
	def __init__(self, options):
		self.options = options
		self.procs = processes()
		self.prefix, self.prefix_source = find_prefix(options.prefix, self.procs)
		self.session = find_session(self.procs)
		self.ours = [process for process in self.procs if in_prefix(process, self.prefix)]
		self.tappo = next((process for process in self.ours if is_tappo(process)), None)
		self.bridge = next((process for process in self.procs if process.comm == 'tappo-bridge'), None)
		self.settings = read_settings(self.prefix)
		self.pin_script = None
		self.summary = {}
		self.flags = []

	def flag(self, message):
		if message not in self.flags:
			self.flags.append(message)

	def scan(self, source, lines):
		for where, pattern, meaning in LOG_SIGNATURES:
			if where == source and any(re.search(pattern, line) for line in lines):
				self.flag(f'{meaning} (see the {source} log below)')


def library_paths():
	for ldconfig in ('ldconfig', '/sbin/ldconfig', '/usr/sbin/ldconfig'):
		output = run(ldconfig, '-p') if shutil.which(ldconfig) else None
		if output:
			break
	else:
		return None
	paths = {}
	for line in output.splitlines():
		match = re.match(r'\s+(\S+) \(([^)]*)\) => (\S+)', line)
		if match and 'x86-64' in match.group(2):
			paths.setdefault(match.group(1), match.group(3))
	return paths


def owning_package(path):
	if not path:
		return None
	real = os.path.realpath(path)
	if real.startswith('/nix/store/'):
		return real.split('/')[3].split('-', 1)[-1]
	if shutil.which('pacman'):
		match = re.search(r'is owned by (\S+ \S+)', run('pacman', '-Qo', path) or '')
		return match.group(1) if match else None
	if shutil.which('rpm'):
		output = run('rpm', '-qf', '--qf', '%{NAME} %{VERSION}-%{RELEASE}\n', path) or ''
		return output.splitlines()[0] if output and 'not owned' not in output else None
	if shutil.which('dpkg'):
		for candidate in dict.fromkeys((path, real)):
			output = run('dpkg', '-S', candidate) or ''
			if ': ' in output:
				package = output.split(': ', 1)[0].split(', ')[0]
				return f'{package} {run("dpkg-query", "-W", "-f", "${Version}", package) or "?"}'
		return None
	if shutil.which('xbps-query'):
		output = run('xbps-query', '-o', path) or ''
		return output.split(': ', 1)[0] if ': ' in output else None
	if shutil.which('qfile'):
		output = run('qfile', '-v', path) or ''
		return output.split()[0] if output else None
	return None


def collapse(lines, timestamp):
	collapsed = []
	previous = None
	repeats = 0
	for line in lines + [None]:
		message = None if line is None else timestamp.sub('', line)
		if line is not None and message == previous:
			repeats += 1
			continue
		if repeats:
			collapsed.append(f'    (the same again {repeats} more time{"s" if repeats > 1 else ""})')
		repeats = 0
		previous = message
		if line is not None:
			collapsed.append(line)
	return collapsed


def distinct(lines, key=JOURNAL_KEY, timestamped=True):
	seen = {}
	kept = []
	for line in lines:
		normalised = key.sub('', line)
		if normalised in seen:
			seen[normalised][1] += 1
			seen[normalised][2] = line[11:19]
			continue
		seen[normalised] = [len(kept), 0, None]
		kept.append(line)
	for index, repeats, last in seen.values():
		if repeats and timestamped:
			kept[index] += f'  (+{repeats} more like it, the last at {last})'
		elif repeats:
			kept[index] += f'  (+{repeats} more like it)'
	return kept


def newest(lines, max_lines):
	if not max_lines or len(lines) <= max_lines:
		return lines
	return [f'({len(lines) - max_lines} earlier lines left out, --max-lines raises the limit)'] + lines[-max_lines:]


def journal_window(options):
	return ['--since', options.since] if options.since else ['-b']


def journal_lines(*arguments, limit=0, keep=None, drop=None):
	command = ['journalctl', '--user', '--no-pager', '--no-hostname', '-o', 'short-iso', *arguments]
	try:
		reader = subprocess.Popen(
			command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
			errors='replace',
		)
	except OSError:
		return None, True
	lines = []
	complete = True
	with reader:
		for line in reader.stdout:
			line = line.rstrip('\n')
			if line == '-- No entries --' or (keep and not keep.search(line)) or (drop and drop.search(line)):
				continue
			if limit and len(lines) >= limit:
				complete = False
				reader.kill()
				break
			lines.append(line)
	return lines, complete


def local_time(stamp):
	return datetime.datetime.fromtimestamp(stamp).astimezone().isoformat(timespec='seconds')


@section('Checkout')
def checkout(report):
	summary = report.summary
	if not (PROJECT / '.git').exists():
		summary['Checkout'] = 'downloaded, no git history'
		return [
			'Downloaded rather than cloned, so there\'s no version number to show.',
			'git log --find-object=<id> on a clone finds the commit each of these came from:',
			'',
			*[f'{git_blob(path)} {path.relative_to(PROJECT)}' for path in source_files()],
		]

	def git(*arguments):
		return run('git', '-C', str(PROJECT), *arguments)

	# Patch releases ship untagged and are only named in their commit's subject, so describe would give the last major or minor.
	# version = git('describe', '--tags', '--always', '--dirty') or 'unknown'
	release = re.match(
		r'(\S+) v(\d+\.\d+\.\d+)', git('log', '-1', '-E', '--grep=^v[0-9]+[.][0-9]+[.][0-9]+', '--format=%h %s') or '',
	)
	version = release.group(2) if release else 'unknown'
	since = git('rev-list', '--count', f'{release.group(1)}..HEAD') if release else None
	if since and since != '0':
		version += f' and {since} commit{"s" if since != "1" else ""} after it'
	commit = git('log', '-1', '--format=%h %s (%cs)') or 'unknown'
	changed = [line[3:] for line in (git('status', '--porcelain', '--untracked-files=no') or '').splitlines()]
	if changed:
		version += ', with local changes'
	summary['Checkout'] = f'{version} ({commit.split()[0]})'
	return [
		f'Version: {version}',
		f'Commit: {commit}',
		f'Branch: {git("rev-parse", "--abbrev-ref", "HEAD") or "unknown"}',
		f'Remote: {git("remote", "get-url", "origin") or "none"}',
		f'Changed files: {", ".join(changed) or "none"}',
	]


@section('Runtime')
def runtime(report):
	summary = report.summary
	wine = RUNTIME / 'bin' / 'wine'
	if not wine.exists():
		summary['Runtime'] = 'not built'
		report.flag('The patched Wine isn\'t built (./install.sh builds it)')
		return ['Not built yet (./install.sh or make runtime builds it)']

	version = run(str(wine), '--version') or 'unknown'
	lines = [f'Wine: {version}']
	stamp = RUNTIME / '.patched'
	if stamp.exists():
		patched = datetime.datetime.fromtimestamp(stamp.stat().st_mtime)
		lines.append(f'Patched: {patched:%Y-%m-%d %H:%M}')
		summary['Runtime'] = f'{version}, patched {patched:%Y-%m-%d}'
		# if (PROJECT / 'runtime' / 'patch-runtime.py').stat().st_mtime > stamp.stat().st_mtime:
		# 	lines.append('  runtime/patch-runtime.py has changed since, so ./install.sh hasn\'t been run after updating')
		# 	summary['Runtime'] += ', out of date'
	else:
		lines.append('Patched: no, patching never finished')
		summary['Runtime'] = f'{version}, unpatched'

	above = above_fullscreen()
	lines.append(f'Above fullscreen games: {"yes" if above else "no, built with --no-above-fullscreen"}')
	if not above:
		summary['Runtime'] += ', not above fullscreen games'

	lines.append('')
	patcher = runtime_patcher()
	total = absent = extra = 0
	for relative_path, patch in patcher.PATCHES.items():
		sites = patcher.patch_sites(patch, above)
		missing = missing_sites(RUNTIME / relative_path, sites)
		# Sites left out on purpose have to be absent too.
		left_out = [] if above else patch.get('above_fullscreen', [])
		unwanted = [start for start, _, _ in left_out if start not in missing_sites(RUNTIME / relative_path, left_out)]
		total += len(sites)
		absent += len(missing)
		extra += len(unwanted)
		line = f'{relative_path}: {sha256(RUNTIME / relative_path)}, {len(sites) - len(missing)} of {len(sites)} patch sites in place'
		if missing:
			line += f', missing {", ".join(hex(start) for start in missing)}'
		if unwanted:
			line += f', above-fullscreen sites in place though it was turned off: {", ".join(hex(start) for start in unwanted)}'
		lines.append(line)
	if absent:
		summary['Runtime'] += f', {absent} of {total} patch sites missing'
		report.flag(f'The patched Wine doesn\'t match this checkout, {absent} of its {total} patch sites aren\'t in place (./install.sh rebuilds it)')
	if extra:
		summary['Runtime'] += ', above-fullscreen patch in place though turned off'
		report.flag('The patched Wine still keeps the pet above fullscreen games though --no-above-fullscreen turned that off (./install.sh rebuilds it)')

	lines.append(f'build/ole32.dll: {sha256(PROJECT / "build" / "ole32.dll")}')
	lines.append(f'Input helper: {"built" if HELPER.exists() else "not built"}')
	source = PROJECT / 'helper' / 'tappo-input.c'
	if HELPER.exists() and source.stat().st_mtime > HELPER.stat().st_mtime:
		lines.append('  helper/tappo-input.c has changed since')
		report.flag('The input helper is older than its source (./install.sh rebuilds it)')
	return lines


@section('Prefix')
def prefix_details(report):
	prefix = report.prefix
	summary = report.summary
	lines = [f'Prefix: {prefix} ({report.prefix_source})']
	system_text = read_text(prefix / 'system.reg')
	if not system_text:
		lines.append('  It has no system.reg, so it hasn\'t been set up (./install.sh or make prefix)')
		report.flag('The Wine prefix hasn\'t been set up (./install.sh does that)')
		return lines

	arch = re.search(r'^#arch=(\S+)', system_text[:1024], re.M)
	system_keys = parse_registry(system_text)
	user_keys = parse_registry(read_text(prefix / 'user.reg'))
	windows = system_keys.get('Software\\Microsoft\\Windows NT\\CurrentVersion', {})

	def windows_value(name):
		return windows.get(name, '?').strip('"')

	lines.append(f'Arch: {arch.group(1) if arch else "unknown"}')
	lines.append(
		f'Windows: {windows_value("ProductName")} {windows_value("CurrentVersion")}, build {windows_value("CurrentBuild")}'
	)
	for keys in (user_keys, system_keys):
		for key, values in keys.items():
			dpi = values.get('LogPixels')
			if dpi:
				lines.append(f'DPI: {int(dpi[6:], 16) if dpi.startswith("dword:") else dpi} ({key})')

	lines += ['', 'Wine settings:']
	for key, values in sorted(user_keys.items()):
		if key == 'Software\\Wine' or key.startswith(WINE_KEYS):
			lines += [f'  {key}: {name}={value}' for name, value in values.items()]

	lines.append('')
	prefix_ole32 = prefix / 'drive_c' / 'windows' / 'system32' / 'ole32.dll'
	built_ole32 = PROJECT / 'build' / 'ole32.dll'
	if not prefix_ole32.exists():
		lines.append('ole32: missing from system32')
	elif built_ole32.exists() and filecmp.cmp(prefix_ole32, built_ole32, shallow=False):
		lines.append('ole32: the patched one')
	elif b'Wine builtin DLL' in prefix_ole32.read_bytes()[0x40:0x60]:
		lines.append('ole32: Wine\'s own, so the fix isn\'t in (./install.sh puts it back)')
		report.flag('The prefix has Wine\'s own ole32, so Tappo will crash at startup (./install.sh puts the fixed one back)')
	else:
		lines.append('ole32: neither the patched one nor Wine\'s own')

	application = prefix / 'drive_c' / 'Program Files (x86)' / 'Microsoft' / 'EdgeWebView' / 'Application'
	versions = sorted(
		(path.name for path in application.glob('*') if re.fullmatch(r'\d+(?:\.\d+)+', path.name)),
		key=lambda name: [int(part) for part in name.split('.')],
	)
	for version in versions:
		lines.append(f'WebView2 {version}: msedge.dll {pe_layout(application / version / "msedge.dll")}')
	if not versions:
		lines.append('WebView2: not installed')
		report.flag('WebView2 isn\'t installed in the prefix (./install.sh installs it)')
	summary['WebView2'] = versions[-1] if versions else 'not installed'

	exe = prefix / 'drive_c' / 'Program Files' / 'WuwaTappoGlobal' / APP_EXE
	lines.append(f'{APP_EXE}: {pe_layout(exe)}')
	log_lines = tappo_log(prefix)
	boots = [line for line in log_lines if '[APP] event=boot' in line]
	version = re.search(r' version="([^"]+)"', boots[-1]) if boots else None
	summary['Tappo'] = version.group(1) if version else 'unknown'
	lines.append(f'Tappo version at its last start: {summary["Tappo"]}')

	waiting = None
	for line in log_lines:
		if '[APP] event=boot' in line:
			waiting = None
		elif shown := UPDATE_SHOWN.search(line):
			waiting = shown.group(1)
	if waiting and report.tappo:
		lines.append(f'Waiting to install: Tappo {waiting}')
		report.flag(
			f'Tappo {waiting} is waiting to install. Until it is, no tap counts, the panel won\'t open and the menu only has Exit. '
			'Its update window can open behind everything else or on another screen'
		)

	roaming = app_data(prefix, 'Roaming')
	if roaming:
		lines.append(f'shortcuts.json: {read_text(roaming / "shortcuts.json").strip() or "not saved yet"}')
		lines.append(f'ui-locale.txt: {read_text(roaming / "ui-locale.txt").strip() or "not saved yet"}')
	return lines


@section('Tappo\'s settings')
def tappo_settings(report):
	if not report.settings:
		return ['Couldn\'t find them, Tappo may not have saved any yet']
	return [f'{name}: {value}' for name, value in report.settings.items()]


@section('Integration')
def integration(report):
	lines = ['Bridge service:']
	state = run(
		'systemctl', '--user', 'show', 'tappo-bridge.service', '-p', 'LoadState', '-p', 'UnitFileState',
		'-p', 'ActiveState', '-p', 'SubState', '-p', 'Result', '-p', 'NRestarts', '-p', 'ActiveEnterTimestamp',
	)
	lines += [f'  {line}' for line in (state or 'systemctl isn\'t available').splitlines()]
	values = dict(line.split('=', 1) for line in (state or '').splitlines() if '=' in line)
	if state and values.get('LoadState') != 'loaded':
		report.flag('The bridge service isn\'t installed, so Tappo only counts clicks on itself (./install.sh sets it up)')
	elif state and values.get('ActiveState') != 'active':
		report.flag(f'The bridge service is {values.get("ActiveState")}, so Tappo only counts clicks on itself')
	if int(values.get('NRestarts') or 0):
		report.flag(f'The bridge has restarted {values["NRestarts"]} times since it was last started by hand, so it has been crashing')
	unit = run('systemctl', '--user', 'cat', 'tappo-bridge.service')
	if unit:
		lines += ['', unit, '']

	applications = HOME / '.local' / 'share' / 'applications'
	menu = ini(applications / 'wuwa-tappo.desktop').get('Desktop Entry', {})
	lines.append(f'Menu entry: {menu.get("Exec", "missing")}')
	autostart = ini(HOME / '.config' / 'autostart' / 'wuwa-tappo.desktop').get('Desktop Entry')
	if autostart is None:
		lines.append('Autostart: off')
	elif autostart.get('Hidden') == 'true':
		lines.append('Autostart: switched off in the desktop\'s settings (Hidden=true)')
	else:
		lines.append(f'Autostart: {autostart.get("Exec", "on")}')
	if (applications / 'wine' / 'Programs' / 'WuwaTappoGlobal' / 'Wuwa Tappo.desktop').exists():
		lines.append('Wine\'s own menu entry: still there, and it starts Tappo on system Wine')
		report.flag('Wine\'s own menu entry for Tappo is still there, and it starts Tappo on system Wine (./install.sh removes it)')
	logout_script = HOME / '.config' / 'plasma-workspace' / 'shutdown' / '00-wuwa-tappo-quit'
	lines.append(f'Plasma logout script: {os.readlink(logout_script) if logout_script.is_symlink() else "missing"}')
	handler = run('xdg-mime', 'query', 'default', 'x-scheme-handler/wuwa-buddy')
	lines.append(f'wuwa-buddy:// handler: {handler or "none"}')
	if handler is not None and 'wuwa-tappo' not in handler:
		report.flag('wuwa-buddy:// links don\'t go to Tappo, so logging in can\'t finish (./install.sh sets that up)')
	return lines


def kwin_rule_applies(values):
	target = 'wuwatappo.exe wuwatappo.exe' if values.get('wmclasscomplete') == 'true' else 'wuwatappo.exe'
	pattern = values.get('wmclass', '')
	match = values.get('wmclassmatch', '0')
	if match == '0':
		return values.get('titlematch', '0') == '0'
	if match == '1':
		return pattern.lower() == target
	if match == '2':
		return pattern.lower() in target
	try:
		return match == '3' and re.search(pattern, target, re.I) is not None
	except re.error:
		return False


def config_lines(start, include, wanted):
	lines = []
	queue = [Path(start)]
	seen = set()
	while queue:
		path = queue.pop(0)
		if path in seen or not path.is_file():
			continue
		seen.add(path)
		for number, line in enumerate(read_text(path).splitlines(), 1):
			stripped = line.strip()
			if not stripped or stripped.startswith('#'):
				continue
			included = include.match(stripped)
			if included:
				target = Path(os.path.expanduser(included.group(1).strip().strip('"\'')))
				pattern = str(target if target.is_absolute() else path.parent / target)
				queue += [Path(found) for found in sorted(glob.glob(pattern))]
			elif wanted.search(stripped):
				lines.append(f'  {path.name}:{number}: {stripped}')
	return lines


@section('Window manager')
def window_manager(report):
	session = report.session
	family = session['family']
	compositor = session['compositor']
	lines = [
		f'Compositor: {compositor.comm if compositor else "none found (no Xwayland running, so maybe an X11 session)"}',
		f'Window manager as X clients see it: {session["ewmh"] or "unknown (needs xprop)"}',
		'',
	]

	if family == 'kde':
		qdbus = next((name for name in ('qdbus6', 'qdbus-qt6', 'qdbus') if shutil.which(name)), None)
		effects = run(qdbus, 'org.kde.KWin', '/Effects', 'org.kde.kwin.Effects.loadedEffects') if qdbus else None
		lines.append(f'KWin effects loaded: {", ".join(effects.split()) if effects else "unknown"}')
		kwinrc = ini(HOME / '.config' / 'kwinrc')
		plugins = sorted(
			key[:-len('Enabled')] for key, value in kwinrc.get('Plugins', {}).items()
			if key.endswith('Enabled') and value == 'true'
		)
		lines.append(f'KWin plugins switched on in kwinrc: {", ".join(plugins) or "none"}')
		lines += [
			f'kwinrc [{group}]: {", ".join(f"{key}={value}" for key, value in kwinrc[group].items())}'
			for group in KWIN_GROUPS if kwinrc.get(group)
		]
		pin_script = bridge_constant('PIN_SCRIPT_NAME')
		if pin_script:
			loaded = run('busctl', '--user', '--json=short', 'call', 'org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting', 'isScriptLoaded', '--', 's', pin_script)
			if loaded:
				report.pin_script = 'true' in loaded
			lines.append(f'KWin script {pin_script}: {"unknown" if report.pin_script is None else "loaded" if report.pin_script else "not loaded"}')
		exclusions = kwinrc.get('Round-Corners', {}).get('Exclusions')
		if exclusions is not None:
			lines.append(f'Rounded Corners exclusions: {exclusions}')
		for group, effect_ids in BLUR_EFFECTS.items():
			settings = kwinrc.get(group, {})
			loaded = any(effect in effects.split() for effect in effect_ids) if effects else any(effect in plugins for effect in effect_ids)
			if not loaded:
				continue
			matching = settings.get('BlurMatching', 'true')
			classes = [name for name in settings.get('WindowClasses', '').split('\\n') if name]
			lines.append(f'{group}: BlurMatching={matching}, WindowClasses={", ".join(classes) or "none"}')
			# In "all except" mode it blurs each window's whole frame and ignores X Shape, so Tappo's screen-sized windows blur the screen.
			if matching == 'false' and 'wuwatappo.exe' not in classes:
				report.flag(f'{group} blurs every window not listed, and Tappo isn\'t listed, so the whole screen gets blurred (make blur-exclusion)')
		rules = {
			group: values for group, values in ini(HOME / '.config' / 'kwinrulesrc').items()
			if group != 'General' and values
		}
		applying = [(group, values) for group, values in rules.items() if kwin_rule_applies(values)]
		lines.append(f'KWin window rules: {len(rules)}, of which {len(applying)} can apply to Tappo')
		for group, values in applying:
			settings = ', '.join(f'{key}={value}' for key, value in values.items() if key != 'Description')
			lines.append(f'  "{values.get("Description", group)}": {settings}')
	elif family == 'gnome':
		extensions = run('gsettings', 'get', 'org.gnome.shell', 'enabled-extensions')
		lines.append(f'GNOME Shell extensions switched on: {extensions or "unknown"}')
	elif family == 'hyprland':
		lines.append('Hyprland config lines that can touch Tappo or Xwayland:')
		lines += config_lines(
			HOME / '.config' / 'hypr' / 'hyprland.conf', re.compile(r'source\s*=\s*(.+)'),
			re.compile(r'(?i)^(?:windowrule\w*|layerrule)\b.*(?:tappo|wuwa|wine|xwayland|\.\*)|xwayland|force_zero_scaling'),
		) or ['  none']
	elif family in ('sway', 'i3'):
		start = HOME / '.config' / family / 'config'
		if family == 'i3' and not start.exists():
			start = HOME / '.i3' / 'config'
		lines.append(f'{family} config lines that can touch Tappo or Xwayland:')
		lines += config_lines(
			start, re.compile(r'include\s+(.+)'),
			re.compile(r'(?i)^(?:for_window|assign|no_focus)\b.*(?:tappo|wuwa|wine|xwayland|\.\*|\[all\])|^xwayland\b'),
		) or ['  none']
	else:
		lines.append(
			'This window manager\'s settings aren\'t read; any window rules or effects that apply to Xwayland or Wine '
			'windows are worth mentioning in the issue'
		)
	return lines


@section('Running now')
def running_now(report):
	ours, tappo, bridge, summary = report.ours, report.tappo, report.bridge, report.summary
	lines = []

	if tappo:
		lines.append(f'Tappo: running for {running_for(tappo.pid)}, in {cgroup_unit(tappo.pid)}')
		summary['Running'] = 'yes'
		wine_binary = os.path.realpath(f'/proc/{tappo.pid}/exe')
		lines.append(f'Tappo\'s Wine: {wine_binary}')
		if not wine_binary.startswith(f'{RUNTIME.resolve()}/'):
			report.flag(f'Tappo is running on a Wine outside this checkout\'s patched one ({wine_binary}), so none of the fixes apply')
	else:
		lines.append('Tappo: not running, so there\'s no live state here (if the problem is happening, run this while it does)')
		summary['Running'] = 'no'
	lines.append(f'Bridge: {" ".join(bridge.argv).strip() if bridge else "not running"}')
	if bridge and not any(Path(argument).resolve() == BRIDGE.resolve() for argument in bridge.argv if argument.endswith('tappo-bridge.py')):
		report.flag('The running bridge is from a different folder to this report, so the version and patch checks are about the wrong copy')
	elif bridge and started_at(bridge.pid) < BRIDGE.stat().st_mtime:
		report.flag('The bridge was started before its code last changed, so it\'s running the old code (systemctl --user restart tappo-bridge)')
	if tappo and not bridge:
		report.flag('Tappo is running but the bridge isn\'t, so it only counts clicks on itself')

	if ours:
		lines += ['', 'Processes in the prefix (memory is PSS):']
		usage = collections.defaultdict(lambda: [0, 0])
		for process in ours:
			usage[process.comm][0] += 1
			usage[process.comm][1] += pss_kib(process.pid)
		for comm, (count, kib) in sorted(usage.items(), key=lambda item: -item[1][1]):
			lines.append(f'  {comm} x{count}: {kib / 1024:.0f} MiB')
		total = sum(kib for _, kib in usage.values()) / 1024
		lines.append(f'  Total: {total:.0f} MiB')
		summary['Memory'] = f'{total:.0f} MiB PSS'

	if tappo:
		lines += ['', 'Tappo\'s environment (only what matters to Wine and graphics):']
		lines += [f'  {key}={value}' for key, value in sorted(tappo.environ.items()) if ENVIRONMENT.match(key)]
	return lines


@section('X windows and the pet')
def x_windows(report):
	if not report.tappo:
		return ['Tappo isn\'t running, so it has no windows to look at']
	windows = tappo_windows()
	if windows is None:
		return ['xwininfo isn\'t available, so there\'s no view of Tappo\'s windows']

	monitors = x_monitors()
	role = bridge_constant('COPY_ROLE')
	copy = next((window for window in windows if role and window.properties.get('WM_WINDOW_ROLE') == f'"{role}"'), None)
	stage = next((window for window in windows if window is not copy and window.title == STAGE_TITLE and window.viewable), None)
	click_layer = next((window for window in windows if window.title == CLICK_LAYER_TITLE and window.viewable), None)
	lines = ['Tappo\'s windows, top of the stack first:', *map(window_line, windows), '']

	if stage:
		screen = screen_of(stage.rect, monitors)
		kind = 'override-redirect' if stage.override_redirect else 'managed by the window manager'
		report.summary['Pet'] = f'on {screen}, {kind}'
		lines.append(f'Pet: on {screen}, {kind}')
		# Wine decides at window creation, so a mismatch means Tappo started on the other build.
		if role and stage.override_redirect != above_fullscreen():
			wanted = 'keep it above fullscreen games' if above_fullscreen() else 'leave it to the window manager'
			report.flag(f'The pet is {kind} though the runtime is built to {wanted}, so Tappo started before the last rebuild (restarting Tappo fixes it)')

		if click_layer:
			click_screen = screen_of(click_layer.rect, monitors)
			lines.append(f'Click layer: on {click_screen}')
			if click_screen != screen:
				report.flag(f'The pet is on {screen} but its click layer is on {click_screen}, so it shows but can\'t be clicked')
		else:
			lines.append('Click layer: not showing')
			if report.settings.get('gameMode') is False:
				report.flag('Game Mode is off but the pet has no click layer showing, so it can\'t be clicked')

		bridge_argv = report.bridge.argv if report.bridge else ()
		if any('XdndAware' in window.properties for window in (stage, click_layer) if window) and '--keep-drop-targets' not in bridge_argv:
			report.flag('Tappo\'s windows still take drag and drop, so dragging an image over the pet can stop it taking clicks until Tappo restarts')

		saved = saved_screen(report)
		lines.append(f'Screen the bridge keeps it on: {saved or "none saved"}')
		if saved and saved != screen and saved in (name for name, _ in monitors):
			report.flag(f'The pet is on {screen} but the bridge has it saved on {saved}')
		lines += taskbar_copy(report, stage, copy)
	else:
		report.summary['Pet'] = 'not showing'
		lines.append('Pet: its window isn\'t on screen')
		report.flag('Tappo is running but the pet\'s window isn\'t on screen, which is only expected if it was hidden with its shortcut')

	lines += ['', 'Other apps\' fullscreen X windows, bottom of the stack first:', *(other_fullscreen(copy, monitors) or ['  none'])]
	return lines


@section('Wine\'s view')
def wine_view(report):
	if not report.tappo:
		return ['Tappo isn\'t running, and asking Wine without it would start a Wine server in the prefix']
	if not HELPER.exists():
		return ['The input helper isn\'t built, so there\'s nothing to ask Wine with']
	output = run(
		str(RUNTIME / 'bin' / 'wine'), str(HELPER), '--state', timeout=30,
		env={**os.environ, 'WINEPREFIX': str(report.prefix), 'WINEDEBUG': '-all'},
	)
	if not output:
		return ['The helper gave nothing back, so it\'s probably older than this report (./install.sh rebuilds it)']
	if 'the work area isn\'t inside it' in output:
		report.flag('Wine has a screen whose work area isn\'t inside it, which pushes the pet off screen. Restarting Tappo clears it, and it needs reporting')
	return ['What Wine itself thinks, asked from inside the prefix:', ''] + output.splitlines()


@section('System')
def system(report):
	summary, session = report.summary, report.session
	release = os_release()
	distro = release.get('PRETTY_NAME') or release.get('NAME') or 'unknown'
	distro_id = ' '.join(filter(None, (release.get('ID'), release.get('VERSION_ID'))))
	desktop = os.environ.get('XDG_CURRENT_DESKTOP', 'unknown')
	session_type = os.environ.get('XDG_SESSION_TYPE', 'unknown')
	compositor = session['compositor']
	summary['Distro'] = distro
	summary['Kernel'] = platform.release()
	summary['Desktop'] = f'{desktop} ({session_type})'
	if compositor:
		summary['Compositor'] = compositor.comm

	meminfo = dict(line.split(':', 1) for line in read_text('/proc/meminfo').splitlines() if ':' in line)
	cpu = re.search(r'^model name\s*:\s*(.+)$', read_text('/proc/cpuinfo'), re.M)
	try:
		evdev = importlib.metadata.version('evdev')
	except importlib.metadata.PackageNotFoundError:
		evdev = 'not found'
	lines = [
		f'Distro: {distro} ({distro_id or "unknown"})',
		f'Kernel: {platform.release()} {platform.machine()}',
		f'Desktop: {desktop}, {session_type} session, '
		f'WAYLAND_DISPLAY {"set" if os.environ.get("WAYLAND_DISPLAY") else "unset"}, '
		f'DISPLAY {os.environ.get("DISPLAY") or "unset"}',
		f'Xwayland: {"running" if session["xwayland"] else "not running"}',
		f'CPU: {cpu.group(1) if cpu else "unknown"}, {os.cpu_count()} threads',
		f'Memory: {gib(meminfo.get("MemTotal", ""))} total, {gib(meminfo.get("MemAvailable", ""))} available',
		f'Transparent huge pages: {read_text("/sys/kernel/mm/transparent_hugepage/enabled").strip() or "unknown"}',
		f'Python: {platform.python_version()}, evdev {evdev}',
	]
	sleeps = read_text('/sys/power/suspend_stats/success').strip()
	if sleeps:
		lines.append(f'Sleeps since boot: {sleeps}, {read_text("/sys/power/suspend_stats/fail").strip() or "?"} failed')
		summary['Sleeps since boot'] = sleeps
	lines.append(f'Tools this report uses that aren\'t installed: {", ".join(tool for tool in TOOLS if not shutil.which(tool)) or "none"}')

	libraries = library_paths()
	evdev_spec = importlib.util.find_spec('evdev')
	compositor_binary = None
	if compositor:
		compositor_binary = compositor.argv[0] if compositor.argv[0].startswith('/') else shutil.which(compositor.comm)
	files = {
		'Xwayland': shutil.which('Xwayland'),
		compositor.comm if compositor else 'Compositor': compositor_binary,
		**{label: (libraries or {}).get(soname) for label, soname in VERSIONED_LIBRARIES.items()},
		'Python evdev': evdev_spec.origin if evdev_spec else None,
		'System Wine': shutil.which('wine'),
	}
	lines += ['', 'Versions, from the package that owns each file:']
	for label, path in files.items():
		package = owning_package(path)
		if path:
			lines.append(f'  {label}: {package or "not from a package this can ask"}')
		if package and label == 'Xwayland':
			summary['Xwayland'] = package
		if package and compositor and label == compositor.comm:
			summary['Compositor'] = f'{label}, {package}'
		if package and label == 'Mesa':
			summary['Mesa'] = package

	if libraries is None:
		lines += ['', 'Libraries Wine and the bridge load: couldn\'t check, there\'s no ldconfig cache']
		return lines
	missing = [f'{soname} ({purpose})' for soname, purpose in {**bridge_libraries(), **WINE_LIBRARIES}.items() if soname not in libraries]
	lines += ['', f'Libraries Wine and the bridge load: {"all found" if not missing else "missing " + ", ".join(missing)}']
	if missing:
		report.flag(f'Missing libraries: {", ".join(missing)}')
	return lines


@section('Graphics')
def graphics(report):
	summary = report.summary
	lines = []
	devices = []
	output = run('lspci', '-nnk')
	if output is None:
		lines.append('lspci isn\'t available (it comes with pciutils)')
	for block in re.split(r'\n(?=\S)', output or ''):
		first, *rest = block.splitlines() or ['']
		if not re.search(r'VGA compatible|3D controller|Display controller', first):
			continue
		lines.append(first)
		lines += [f'  {line.strip()}' for line in rest if 'Kernel driver in use' in line]
		devices.append(first.split(': ', 1)[-1])
	summary['GPU'] = '; '.join(devices) or 'unknown'

	nvidia = read_text('/proc/driver/nvidia/version').splitlines()
	if nvidia:
		lines.append(nvidia[0])
		driver = re.search(r'\s(\d+\.\d+(?:\.\d+)?)\s', nvidia[0])
		summary['NVIDIA driver'] = driver.group(1) if driver else nvidia[0]
	return lines


def kscreen_screens():
	rotations = {1: 'normal', 2: 'left', 4: 'upside down', 8: 'right'}
	screens = []
	for screen in json.loads(run('kscreen-doctor', '-j') or '')['outputs']:
		if not screen.get('connected'):
			continue
		mode = next((mode for mode in screen.get('modes', []) if mode.get('id') == screen.get('currentModeId')), {})
		screens.append({
			'name': screen['name'], 'width': screen['size']['width'], 'height': screen['size']['height'],
			'x': screen['pos']['x'], 'y': screen['pos']['y'], 'refresh': mode.get('refreshRate', 0),
			'scale': screen.get('scale'), 'rotation': rotations.get(screen.get('rotation'), screen.get('rotation')),
			'enabled': screen.get('enabled'), 'priority': screen.get('priority') or 99,
		})
	return sorted(screens, key=lambda screen: screen['priority'])


def hyprland_screens():
	transforms = ['normal', '90', '180', '270', 'flipped', 'flipped 90', 'flipped 180', 'flipped 270']
	return [{
		'name': screen['name'], 'width': screen['width'], 'height': screen['height'], 'x': screen['x'], 'y': screen['y'],
		'refresh': screen['refreshRate'], 'scale': screen['scale'], 'rotation': transforms[screen.get('transform', 0) % 8],
		'enabled': not screen.get('disabled'),
	} for screen in json.loads(run('hyprctl', 'monitors', 'all', '-j') or '')]


def sway_screens():
	screens = []
	for screen in json.loads(run('swaymsg', '-t', 'get_outputs', '-r') or ''):
		mode = screen.get('current_mode') or {}
		screens.append({
			'name': screen['name'], 'width': mode.get('width'), 'height': mode.get('height'), 'x': screen['rect']['x'],
			'y': screen['rect']['y'], 'refresh': mode.get('refresh', 0) / 1000, 'scale': screen.get('scale'),
			'rotation': screen.get('transform', 'normal'), 'enabled': screen.get('active'),
		})
	return screens


def niri_screens():
	screens = []
	for screen in json.loads(run('niri', 'msg', '--json', 'outputs') or '').values():
		logical = screen.get('logical') or {}
		modes = screen.get('modes', [])
		mode = modes[screen['current_mode']] if screen.get('current_mode') is not None else {}
		screens.append({
			'name': screen['name'], 'width': mode.get('width'), 'height': mode.get('height'), 'x': logical.get('x'),
			'y': logical.get('y'), 'refresh': mode.get('refresh_rate', 0) / 1000, 'scale': logical.get('scale'),
			'rotation': str(logical.get('transform', 'Normal')).strip('_').lower(), 'enabled': bool(mode),
		})
	return screens


def wlr_screens():
	screens = []
	for screen in json.loads(run('wlr-randr', '--json') or ''):
		mode = next((mode for mode in screen.get('modes', []) if mode.get('current')), {})
		screens.append({
			'name': screen['name'], 'width': mode.get('width'), 'height': mode.get('height'),
			'x': screen['position']['x'], 'y': screen['position']['y'], 'refresh': mode.get('refresh', 0),
			'scale': screen.get('scale'), 'rotation': screen.get('transform', 'normal'), 'enabled': screen.get('enabled'),
		})
	return screens


@section('Screens')
def displays(report):
	summary = report.summary
	lines = []
	sources = (
		('kscreen-doctor', kscreen_screens), ('hyprctl', hyprland_screens), ('swaymsg', sway_screens),
		('niri', niri_screens), ('wlr-randr', wlr_screens),
	)
	for tool, read_screens in sources:
		if not shutil.which(tool):
			continue
		try:
			screens = read_screens()
		except (ValueError, KeyError, TypeError, IndexError, AttributeError):
			continue
		lines.append(f'As the compositor sees them ({tool}):')
		for screen in screens:
			line = (
				f'{screen["name"]}: {screen["width"]}x{screen["height"]} at {screen["x"]},{screen["y"]}, '
				f'{screen["refresh"] or 0:.0f} Hz, scale {screen["scale"]}'
			)
			if 'priority' in screen:
				line += f', priority {screen["priority"]}'
			if str(screen['rotation']).lower() != 'normal':
				line += f', rotated {screen["rotation"]}'
			if not screen['enabled']:
				line += ', switched off'
			lines.append(line)
		summary['Screens'] = str(sum(1 for screen in screens if screen['enabled']))
		break
	else:
		lines.append('No tool for asking this compositor about its screens, so there\'s only Xwayland\'s and Wine\'s view')

	lines += ['', 'As Xwayland sees them:', run('xrandr', '--listmonitors') or 'xrandr isn\'t available']
	return lines


@section('Input')
def input_access(report):
	try:
		group = grp.getgrnam('input')
	except KeyError:
		return ['There\'s no input group on this system']
	user = pwd.getpwuid(os.getuid())
	if group.gr_gid in os.getgroups():
		membership = 'yes'
	elif user.pw_name in group.gr_mem or user.pw_gid == group.gr_gid:
		membership = 'yes, but not in this session yet (log out and back in)'
		report.flag('You\'re in the input group but this session started before that, so log out and back in')
	else:
		membership = 'no, so Tappo only counts clicks on itself'
		report.flag('You\'re not in the input group, so Tappo only counts clicks on itself (sudo usermod -aG input $USER, then log back in)')
	devices = sorted(Path('/dev/input').glob('event*'))
	readable = sum(os.access(device, os.R_OK) for device in devices)
	return [f'In the input group: {membership}', f'Input devices readable: {readable} of {len(devices)}']


@section('Tappo\'s log', logs=True)
def tappo_log_section(report):
	options = report.options
	lines = tappo_log(report.prefix)
	if not lines:
		return ['No log found in the prefix']
	starts = [index for index, line in enumerate(lines) if '[APP] event=boot' in line]
	if options.sessions and len(starts) > options.sessions:
		left_out = len(starts) - options.sessions
		lines = [f'({left_out} earlier runs left out, --sessions 0 includes them)'] + lines[starts[-options.sessions]:]
	report.scan('tappo', lines)
	return newest(collapse(lines, TAPPO_TIMESTAMP), options.max_lines)


def tappo_runs(options):
	output = run(
		'journalctl', '--user', '--no-pager', '-o', 'json', *journal_window(options), '-u', TAPPO_UNITS,
		'SYSLOG_IDENTIFIER=systemd',
	)
	runs = []
	for line in (output or '').splitlines():
		try:
			entry = json.loads(line)
		except ValueError:
			continue
		unit = entry.get('USER_UNIT')
		message = entry.get('MESSAGE')
		if not unit or not isinstance(message, str):
			continue
		stamp = int(entry.get('__REALTIME_TIMESTAMP', 0)) / 1e6
		if message.startswith('Started '):
			runs.append({'unit': unit, 'start': stamp, 'end': None, 'notes': []})
			continue
		current = next((item for item in reversed(runs) if item['unit'] == unit), None)
		if not current or message.startswith('Starting '):
			continue
		current['notes'].append(message.removeprefix(f'{unit}: '))
		if message.removeprefix(f'{unit}: ').startswith(RUN_ENDS):
			current['end'] = stamp
	return runs


def first_and_last(window, options):
	head, complete = journal_lines(*window, limit=options.wine_lines, drop=TAPPO_LINE)
	if head is None:
		return None
	if complete:
		return [f'All {len(head)} lines, each distinct one once:'] + distinct(head)
	tail, _ = journal_lines(*window, '-n', str(options.wine_lines), drop=TAPPO_LINE)
	return (
		[f'The first {len(head)} lines, each distinct one once:'] + distinct(head)
		+ ['', f'The last {len(tail)} lines, the same way:'] + distinct(tail)
	)


@section('Tappo and Wine output (journal)', logs=True)
def wine_output(report):
	options = report.options
	if not shutil.which('journalctl'):
		return ['journalctl isn\'t available, so there\'s no record of what Wine printed']
	runs = tappo_runs(options)
	if options.sessions:
		runs = runs[-options.sessions:]
	lines = ['Tappo\'s own lines are left out, they\'re in its log above.']
	for item in runs:
		window = ['-u', item['unit'], '--since', f'@{item["start"]:.6f}']
		if item['end']:
			window += ['--until', f'@{item["end"]:.6f}']
		ended = f'ended {local_time(item["end"])}' if item['end'] else 'still running'
		lines += ['', f'### Run started {local_time(item["start"])}, {ended}', '']
		if item['notes']:
			lines += [f'systemd: {note}' for note in item['notes']] + ['']
		output = first_and_last(window, options)
		if output is None:
			return ['journalctl isn\'t available']
		lines += output

	# tappo-launch tags its output this way when whatever started it wasn't sending it to the journal already.
	tagged = first_and_last(['-t', TAPPO_TAG, *journal_window(options)], options)
	if tagged and not tagged[0].startswith('All 0 '):
		lines += ['', '### Runs started outside systemd, from tappo-launch', ''] + tagged
	if len(lines) == 1:
		return [
			'Nothing logged. Tappo\'s output reaches the journal when it\'s started from its menu entry or autostart, '
			'not from a terminal, which keeps it.'
		]
	report.scan('wine', lines)
	return lines


def session_logs(options, family):
	sddm = HOME / '.local' / 'share' / 'sddm'
	logs = [
		(sddm / 'wayland-session.log', SESSION_LINES), (sddm / 'xorg-session.log', SESSION_LINES),
		(HOME / '.xsession-errors', SESSION_LINES),
	]
	if os.environ.get('XDG_SESSION_TYPE') == 'x11':
		logs += [(HOME / '.local' / 'share' / 'xorg' / 'Xorg.0.log', COMPOSITOR_LINES), (Path('/var/log/Xorg.0.log'), COMPOSITOR_LINES)]
	runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
	if family == 'hyprland' and runtime_dir:
		newest_log = sorted(Path(runtime_dir).glob('hypr/*/hyprland.log'), key=lambda path: path.stat().st_mtime)[-1:]
		logs += [(path, COMPOSITOR_LINES) for path in newest_log]
	since = 0 if options.since else boot_time()
	found = []
	for path, pattern in logs:
		try:
			if path.stat().st_mtime >= since:
				found.append((path, pattern))
		except OSError:
			continue
	return found


@section('Compositor and Xwayland', logs=True)
def compositor_log(report):
	options, session = report.options, report.session
	lines = []
	scanned = []
	compositor = session['compositor']
	if compositor:
		unit = cgroup_unit(compositor.pid)
		match = ['-u', unit] if unit.endswith('.service') else [f'_COMM={compositor.comm}']
		found, _ = journal_lines(*match, *journal_window(options), keep=COMPOSITOR_LINES)
		if found is None:
			lines.append('journalctl isn\'t available')
		else:
			lines.append(f'From the journal ({" ".join(match)}), lines about Xwayland, X11, Wine, Tappo or crashes:')
			scanned += found
			lines += newest(distinct(found), options.max_lines) or ['  none']
	for path, pattern in session_logs(options, session['family']):
		found = [line for line in read_text(path).splitlines() if pattern.search(line)]
		scanned += found
		lines += ['', f'From {path}:']
		lines += newest(distinct(found, NO_KEY, timestamped=False), options.max_lines) or ['  none']
	report.scan('compositor', scanned)
	return lines or ['No compositor found and no session log to read']


@section('Bridge (journal)', logs=True)
def bridge_journal(report):
	options = report.options
	lines, _ = journal_lines('-u', 'tappo-bridge*', *journal_window(options), drop=HELPER_CHATTER)
	if lines is None:
		return ['journalctl isn\'t available']
	report.scan('bridge', lines)
	return newest(collapse(lines, JOURNAL_TIMESTAMP), options.max_lines) or ['Nothing logged']


@section('Sleep and wake', logs=True)
def sleep_log(report):
	units = [argument for unit in SLEEP_UNITS for argument in ('-u', unit)]
	output = run('journalctl', '--no-pager', '--no-hostname', '-o', 'short-iso', *journal_window(report.options), *units)
	if output is None:
		return ['journalctl isn\'t available']
	return [line for line in output.splitlines() if 'sleep operation' in line] or [
		'None found, though an account that can\'t read the system journal sees none either'
	]


@section('Crashes', logs=True)
def crashes(report):
	session = report.session
	names = CRASH_NAMES + ((session['compositor'].comm,) if session['compositor'] else ())
	output = run('coredumpctl', '--no-pager', '--no-legend', '--since=-14d', 'list', *names, timeout=30)
	if output is None:
		return ['coredumpctl isn\'t available']
	if not output or 'No coredumps' in output:
		return ['None in the last 14 days']
	found = output.splitlines()
	report.flag(f'{len(found)} crash{"es" if len(found) > 1 else ""} in the last 14 days from Tappo, Wine, Xwayland or the compositor')
	return found


class Redactor:
	ACCOUNT_KEYS = (
		r'nick_?name|display_?name|user_?name|c?uid|user_?id|account(?:_?id)?|e_?mail|phone|token|access_token|'
		r'refresh_token|session(?:_?id)?|request_?id|room(?:_?(?:id|code|no))?|invite(?:_?code)?|client_?id|machine_?id|'
		r'device_?id|install(?:ation)?_?id'
	)
	# Needed because WebView2 logs its metrics client id hash through Wine's WER stub, and that hash identifies the install for as long as it exists.
	WER_IDS = re.compile(r'(L"\w*(?:ClientId|MachineId|DeviceId|InstallId)\w*", L")([^"]+)(?=")')
	EMPTY_VALUES = {'', '-', 'null', 'none', 'undefined', 'unknown', 'true', 'false'}
	COMMON_USER_NAMES = {'user', 'wine', 'tappo', 'root', 'home'}
	UNIT_SUFFIXES = 'service|scope|slice|target|socket|timer|mount|path|device|swap'

	def __init__(self, extra_words):
		self.counts = collections.Counter()
		self.learned = set()
		self.placeholders = {}
		self.extra_words = [word for word in extra_words if word.strip()]
		self.account_patterns = [
			re.compile(rf'((?<![A-Za-z0-9])(?:{self.ACCOUNT_KEYS})=)("[^"]*"|[^\s,;&"]+)', re.I),
			re.compile(rf'("(?:{self.ACCOUNT_KEYS})"\s*:\s*)("[^"]*"|[^\s,}}\]]+)', re.I),
			self.WER_IDS,
		]

		user = pwd.getpwuid(os.getuid())
		self.user_name = user.pw_name
		self.real_name = user.pw_gecos.split(',')[0].strip()
		self.homes = sorted({str(HOME), os.path.realpath(HOME)}, key=len, reverse=True)

		release = os_release()
		distro_words = {
			word.lower() for field in ('ID', 'ID_LIKE', 'NAME') for word in release.get(field, '').split()
		}
		hostname = socket.gethostname()
		self.host_names = [
			name for name in {hostname, hostname.split('.')[0]}
			if len(name) >= 3 and name.lower() not in distro_words | {'localhost'}
		]

	def learn(self, text):
		for pattern in self.account_patterns:
			for match in pattern.finditer(text):
				value = match.group(2).strip('"')
				if value.lower() in self.EMPTY_VALUES or len(value) < 2 or (value.isdigit() and len(value) < 6):
					continue
				self.learned.add(value)

	def replace(self, text, category, pattern, replacement, flags=0):
		text, count = re.subn(pattern, replacement, text, flags=flags)
		self.counts[category] += count
		return text

	# Numbered, so lines that share a value (a switch_start and its switch_complete) can still be paired up.
	def placeholder(self, value):
		return self.placeholders.setdefault(value, f'<redacted {len(self.placeholders) + 1}>')

	def account_value(self, match):
		value = match.group(2)
		if value.strip('"').lower() in self.EMPTY_VALUES:
			return match.group(0)
		self.counts['account details'] += 1
		replacement = self.placeholder(value.strip('"'))
		return match.group(1) + (f'"{replacement}"' if value.startswith('"') else replacement)

	def apply(self, text):
		for pattern in self.account_patterns:
			text = pattern.sub(self.account_value, text)
		for value in sorted(self.learned, key=len, reverse=True):
			text = self.replace(text, 'account details', rf'(?<!\w){re.escape(value)}(?!\w)', self.placeholder(value))
		for word in self.extra_words:
			text = self.replace(text, 'words you asked for', rf'(?<!\w){re.escape(word)}(?!\w)', '<redacted>', re.I)

		for home in self.homes:
			text = self.replace(text, 'home folder paths', rf'{re.escape(home)}(?![\w.-])', '~')
		name = re.escape(self.user_name)
		text = self.replace(text, 'user names', rf'((?:home|users)[/\\]+){name}(?![\w.-])', r'\1<user>', re.I)
		if len(self.user_name) >= 4 and self.user_name.lower() not in self.COMMON_USER_NAMES:
			text = self.replace(text, 'user names', rf'(?<!\w){name}(?!\w)', '<user>')
		if len(self.real_name) >= 3:
			text = self.replace(text, 'real names', rf'(?<!\w){re.escape(self.real_name)}(?!\w)', '<name>', re.I)
		for host in self.host_names:
			text = self.replace(text, 'host names', rf'(?<![\w.-]){re.escape(host)}(?![\w-])', '<host>', re.I)

		text = self.replace(text, 'tokens', r'(://)[^/\s:@]+:[^/\s@]+@', r'\1<redacted>@')
		text = self.replace(
			text, 'tokens',
			r'([?&#](?:code|state|token|access_token|id_token|refresh_token|key|sig|signature|auth|ticket|session)=)'
			r'[^&\s"\']+',
			r'\1<redacted>', re.I,
		)
		text = self.replace(text, 'tokens', r'\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}', '<token>')
		text = self.replace(
			text, 'email addresses',
			rf'(?<![\w.+-])(?!git@)[\w.+-]+@(?:[A-Za-z0-9-]+\.)+(?!(?:{self.UNIT_SUFFIXES})\b)[A-Za-z]{{2,}}\b',
			'<email>',
		)
		text = self.replace(text, 'MAC addresses', r'(?<![\w:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![\w:])', '<mac>')
		text = self.replace(
			text, 'IP addresses',
			r'(?<![\d.])(?<![A-Za-z]/)(?!127\.|0\.0\.0\.0)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])',
			'<ip>',
		)
		text = self.replace(
			text, 'IP addresses',
			r'(?<![\w:])(?:(?:[0-9A-Fa-f]{1,4}:){4,7}[0-9A-Fa-f]{1,4}|(?:[0-9A-Fa-f]{1,4}:){2,6}:[0-9A-Fa-f]{0,4})(?![\w:])',
			'<ip>',
		)
		return text


def ask(question):
	if not sys.stdin.isatty():
		return False
	try:
		answer = input(f'{question} [y/N] ')
	except EOFError:
		return False
	return answer.strip().lower().startswith('y')


def open_issue(summary_text):
	query = urllib.parse.urlencode({'template': 'bug_report.yml', 'system': summary_text})
	url = f'https://github.com/{REPOSITORY}/issues/new?{query}'
	try:
		opened = subprocess.run(
			['xdg-open', url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
		).returncode == 0
	except (OSError, subprocess.TimeoutExpired):
		opened = False
	if opened:
		print('Drag the report into the issue\'s "Report file" box.', file=sys.stderr)
	else:
		print(f'Couldn\'t open a browser, so here\'s the link: {url}', file=sys.stderr)


def parse_options():
	parser = argparse.ArgumentParser(
		description='Collect what a bug report about Wuwa Tappo on Linux needs into one text file, '
		'with personal details taken out, ready to attach to a GitHub issue.',
	)
	parser.add_argument(
		'-o', '--output',
		help='where to write the report, or - to print it (default: ~/wuwa-tappo-report-<date>.txt)',
	)
	parser.add_argument(
		'--prefix', help='Tappo\'s Wine prefix (default: the running Tappo\'s, else the one the bridge service uses)',
	)
	parser.add_argument(
		'--sessions', type=int, default=3,
		help='how many of Tappo\'s most recent runs to include, from its own log and from the journal, 0 for all of them (default: 3)',
	)
	parser.add_argument(
		'--since',
		help='how far back to look in the journal, as journalctl --since takes it, e.g. -2d or yesterday (default: this boot)',
	)
	parser.add_argument(
		'--max-lines', type=int, default=3000,
		help='most lines kept from Tappo\'s log, the bridge\'s and the compositor\'s, the newest ones, 0 for no limit (default: 3000)',
	)
	parser.add_argument(
		'--wine-lines', type=int, default=20000,
		help='how many lines of Wine\'s output to read from the start and from the end of each run, 0 to read it all, '
		'which can take minutes after a long run (default: 20000)',
	)
	parser.add_argument(
		'--redact', action='append', default=[], metavar='WORD',
		help='also take this out wherever it appears, e.g. your real name or a nickname; can be given more than once',
	)
	parser.add_argument('--no-logs', action='store_true', help='leave the logs out and only describe the setup')
	titles = ', '.join(title for title, _, _ in SECTIONS)
	parser.add_argument(
		'--only', action='append', default=[], metavar='WORD',
		help=f'only collect the sections with this in their title; can be given more than once. They are: {titles}',
	)
	parser.add_argument(
		'--skip', action='append', default=[], metavar='WORD', help='leave out the sections with this in their title; can be given more than once',
	)
	opening = parser.add_mutually_exclusive_group()
	opening.add_argument(
		'--open', action='store_true', help='open a new GitHub issue with the summary filled in, without asking',
	)
	opening.add_argument('--no-open', action='store_true', help='don\'t offer to open one')
	options = parser.parse_args()
	if min(options.sessions, options.max_lines, options.wine_lines) < 0:
		parser.error('--sessions, --max-lines and --wine-lines can\'t be negative')
	return options


def wanted(title, options):
	lowered = title.lower()
	if options.only and not any(word.lower() in lowered for word in options.only):
		return False
	return not any(word.lower() in lowered for word in options.skip)


def main():
	options = parse_options()
	if os.geteuid() == 0:
		sys.exit('Run this as yourself, not with sudo, or it looks in root\'s home for everything.')

	report = Report(options)
	print('Collecting...', file=sys.stderr)
	parts = []
	for title, gather, is_log in SECTIONS:
		if (is_log and options.no_logs) or not wanted(title, options):
			continue
		try:
			lines = gather(report)
		except Exception as error:
			lines = [f'Collecting this failed: {error!r}']
		parts.append(f'## {title}\n\n' + '\n'.join(lines))

	now = datetime.datetime.now(datetime.timezone.utc)
	local = now.astimezone()
	summary_text = '\n'.join(f'{name}: {value}' for name, value in report.summary.items())
	flags_text = '\n'.join(f'- {flag}' for flag in report.flags)
	header = (
		'# Wuwa Tappo on Linux report\n\n'
		f'Made by report.py on {now:%Y-%m-%d %H:%M} UTC, {local:%H:%M} on this machine (UTC{local:%z}), which is what the logs below go by.\n'
		'Personal details are swapped for <redacted N>, <user>, <host>, <email>, <ip> and the like, '
		'and the home folder shows as ~.'
	)
	text = '\n\n'.join([
		header, f'## Summary\n\n{summary_text}', f'## Worth a look\n\n{flags_text or "Nothing the checks know about."}', *parts,
	]) + '\n'

	redactor = Redactor(options.redact)
	redactor.learn(text)
	text = redactor.apply(text)
	taken_out = ', '.join(f'{count} {category}' for category, count in redactor.counts.items() if count)
	flags_text = redactor.apply(flags_text)
	summary_text = redactor.apply(summary_text)
	if flags_text:
		summary_text += f'\n\nWorth a look:\n{flags_text}'

	if options.output == '-':
		sys.stdout.write(text)
	else:
		output = Path(options.output).expanduser() if options.output else HOME / f'wuwa-tappo-report-{now:%Y%m%d-%H%M%S}.txt'
		output.write_text(text, encoding='utf-8')
		print(f'Wrote {output}', file=sys.stderr)
	print(f'Took out: {taken_out or "nothing"}.', file=sys.stderr)
	print(
		'It\'s plain text, so give it a read before you upload it, and run this again with --redact WORD '
		'for anything that slipped through.', file=sys.stderr,
	)
	if flags_text:
		print(f'\nWorth a look, any of these could be the whole problem:\n{flags_text}\n', file=sys.stderr)

	if options.no_open or options.output == '-':
		return
	if options.open or ask('Open a new GitHub issue with the summary filled in?'):
		open_issue(summary_text)


main()
