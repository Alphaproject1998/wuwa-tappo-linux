#!/usr/bin/env python3
import argparse
import asyncio
import ctypes
import json
import os
import re
import string
import signal
import sys
import time
from pathlib import Path

import evdev
from evdev import ecodes

PR_SET_NAME = 15

BUILD_DIR = Path(__file__).resolve().parent.parent / 'build'
DEFAULT_HELPER = BUILD_DIR / 'tappo-input.exe.so'
DEFAULT_WINE = BUILD_DIR / 'wine-runtime' / 'bin' / 'wine'

LEFT_HAND_KEYS = {
	ecodes.KEY_ESC, ecodes.KEY_F1, ecodes.KEY_F2, ecodes.KEY_F3, ecodes.KEY_F4, ecodes.KEY_F5, ecodes.KEY_F6,
	ecodes.KEY_GRAVE, ecodes.KEY_1, ecodes.KEY_2, ecodes.KEY_3, ecodes.KEY_4, ecodes.KEY_5,
	ecodes.KEY_TAB, ecodes.KEY_Q, ecodes.KEY_W, ecodes.KEY_E, ecodes.KEY_R, ecodes.KEY_T,
	ecodes.KEY_CAPSLOCK, ecodes.KEY_A, ecodes.KEY_S, ecodes.KEY_D, ecodes.KEY_F, ecodes.KEY_G,
	ecodes.KEY_LEFTSHIFT, ecodes.KEY_102ND, ecodes.KEY_Z, ecodes.KEY_X, ecodes.KEY_C, ecodes.KEY_V, ecodes.KEY_B,
	ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTMETA, ecodes.KEY_LEFTALT,
}

# F13 to F24, because they type nothing into whichever Tappo window Wine last focused.
# Tappo ignores a press of a key it already holds and counts the release, so every key held at once needs its own.
STAND_INS = {
	'left': [(0x7c, 0x64, 0), (0x7e, 0x66, 0), (0x80, 0x68, 0), (0x82, 0x6a, 0), (0x84, 0x6c, 0), (0x86, 0x6e, 0)],
	'right': [(0x7d, 0x65, 0), (0x7f, 0x67, 0), (0x81, 0x69, 0), (0x83, 0x6b, 0), (0x85, 0x6d, 0), (0x87, 0x76, 0)],
}
OTHER_HAND = {'left': 'right', 'right': 'left'}
STAND_IN_SCAN_CODES = {vk: scan for keys in STAND_INS.values() for vk, scan, _ in keys}

FUNCTION_KEYS = [
	ecodes.KEY_F1, ecodes.KEY_F2, ecodes.KEY_F3, ecodes.KEY_F4, ecodes.KEY_F5, ecodes.KEY_F6,
	ecodes.KEY_F7, ecodes.KEY_F8, ecodes.KEY_F9, ecodes.KEY_F10, ecodes.KEY_F11, ecodes.KEY_F12,
	ecodes.KEY_F13, ecodes.KEY_F14, ecodes.KEY_F15, ecodes.KEY_F16, ecodes.KEY_F17, ecodes.KEY_F18,
	ecodes.KEY_F19, ecodes.KEY_F20, ecodes.KEY_F21, ecodes.KEY_F22, ecodes.KEY_F23, ecodes.KEY_F24,
]

# Every key Tappo's shortcut recorder accepts, as the virtual key it saves; it reads the physical key, so the US layout holds.
SHORTCUT_KEYS = {
	**{code: 0x70 + index for index, code in enumerate(FUNCTION_KEYS)},
	**{getattr(ecodes, f'KEY_{name}'): ord(name) for name in string.ascii_uppercase + string.digits},
	ecodes.KEY_SPACE: 0x20, ecodes.KEY_PAGEUP: 0x21, ecodes.KEY_PAGEDOWN: 0x22, ecodes.KEY_END: 0x23,
	ecodes.KEY_HOME: 0x24, ecodes.KEY_LEFT: 0x25, ecodes.KEY_UP: 0x26, ecodes.KEY_RIGHT: 0x27, ecodes.KEY_DOWN: 0x28,
	ecodes.KEY_INSERT: 0x2d, ecodes.KEY_DELETE: 0x2e, ecodes.KEY_SEMICOLON: 0xba, ecodes.KEY_EQUAL: 0xbb,
	ecodes.KEY_COMMA: 0xbc, ecodes.KEY_MINUS: 0xbd, ecodes.KEY_DOT: 0xbe, ecodes.KEY_SLASH: 0xbf,
	ecodes.KEY_GRAVE: 0xc0, ecodes.KEY_LEFTBRACE: 0xdb, ecodes.KEY_BACKSLASH: 0xdc, ecodes.KEY_RIGHTBRACE: 0xdd,
	ecodes.KEY_APOSTROPHE: 0xde,
}

MODIFIER_BITS = {
	ecodes.KEY_LEFTALT: 1, ecodes.KEY_RIGHTALT: 1, ecodes.KEY_LEFTCTRL: 2, ecodes.KEY_RIGHTCTRL: 2,
	ecodes.KEY_LEFTSHIFT: 4, ecodes.KEY_RIGHTSHIFT: 4, ecodes.KEY_LEFTMETA: 8, ecodes.KEY_RIGHTMETA: 8,
}
MODIFIER_KEYS = {1: (0xa4, 0x38, 0), 2: (0xa2, 0x1d, 0), 4: (0xa0, 0x2a, 0), 8: (0x5b, 0x5b, 1)}

SHORTCUT_NAMES = ('gameMode', 'switchScreen', 'togglePet')
DEFAULT_NAMED_SHORTCUTS = {'gameMode': (0, 0x76), 'switchScreen': (0, 0x77), 'togglePet': (0, 0x78)}
DEFAULT_SHORTCUTS = set(DEFAULT_NAMED_SHORTCUTS.values())
SHORTCUT_CODES = {vk: code for code, vk in SHORTCUT_KEYS.items()}
STAGE_TITLE = 'Wuwa Tappo'
SLEEP_JUMP = 5

UPDATE_WINDOW_TITLE = '强制更新'
UPDATE_SHOWN = re.compile(rb'\[UPDATE\] event=window_shown version="([^"]*)"')
APP_BOOT = b'[APP] event=boot '
SWITCH_START = b'[DISPLAY] event=switch_start '
SWITCH_UNANSWERED = 10
LOG_TAIL = 1 << 20

# Up to KEY_F12 an evdev code is its PC set 1 scan code; these need the E0 prefix instead.
EXTENDED_SCAN_CODES = {
	ecodes.KEY_KPENTER: 0x1c, ecodes.KEY_RIGHTCTRL: 0x1d, ecodes.KEY_KPSLASH: 0x35, ecodes.KEY_SYSRQ: 0x37,
	ecodes.KEY_RIGHTALT: 0x38, ecodes.KEY_HOME: 0x47, ecodes.KEY_UP: 0x48, ecodes.KEY_PAGEUP: 0x49,
	ecodes.KEY_LEFT: 0x4b, ecodes.KEY_RIGHT: 0x4d, ecodes.KEY_END: 0x4f, ecodes.KEY_DOWN: 0x50,
	ecodes.KEY_PAGEDOWN: 0x51, ecodes.KEY_INSERT: 0x52, ecodes.KEY_DELETE: 0x53,
	ecodes.KEY_LEFTMETA: 0x5b, ecodes.KEY_RIGHTMETA: 0x5c, ecodes.KEY_COMPOSE: 0x5d,
}

MOUSE_BUTTONS = {ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE, ecodes.BTN_SIDE, ecodes.BTN_EXTRA}
BUTTON_STATE = {ecodes.BTN_LEFT: 0x100, ecodes.BTN_MIDDLE: 0x200, ecodes.BTN_RIGHT: 0x400}

INPUT_OUTPUT = 1
IS_VIEWABLE = 2
BUTTON_MASKS = 0x1f00
SHAPE_BOUNDING = 0
SHAPE_INPUT = 2
POINTER_ROOT = 1
ANY_PROPERTY_TYPE = 0
CLIENT_MESSAGE = 33
SUBSTRUCTURE_MASKS = (1 << 19) | (1 << 20)
X_SUCCESS = 0
ATOM_NAMES = ('WM_STATE', '_NET_WM_STATE', '_NET_WM_STATE_FULLSCREEN', '_NET_WM_PID')

X_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


class XRectangle(ctypes.Structure):
	_fields_ = [
		('x', ctypes.c_short), ('y', ctypes.c_short),
		('width', ctypes.c_ushort), ('height', ctypes.c_ushort),
	]


class XWindowAttributes(ctypes.Structure):
	_fields_ = [
		('x', ctypes.c_int), ('y', ctypes.c_int), ('width', ctypes.c_int), ('height', ctypes.c_int),
		('border_width', ctypes.c_int), ('depth', ctypes.c_int), ('visual', ctypes.c_void_p),
		('root', ctypes.c_ulong), ('window_class', ctypes.c_int), ('bit_gravity', ctypes.c_int),
		('win_gravity', ctypes.c_int), ('backing_store', ctypes.c_int), ('backing_planes', ctypes.c_ulong),
		('backing_pixel', ctypes.c_ulong), ('save_under', ctypes.c_int), ('colormap', ctypes.c_ulong),
		('map_installed', ctypes.c_int), ('map_state', ctypes.c_int), ('all_event_masks', ctypes.c_long),
		('your_event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long),
		('override_redirect', ctypes.c_int), ('screen', ctypes.c_void_p),
	]


class XClassHint(ctypes.Structure):
	_fields_ = [('res_name', ctypes.c_void_p), ('res_class', ctypes.c_void_p)]


class XClientMessageEvent(ctypes.Structure):
	_fields_ = [
		('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int), ('display', ctypes.c_void_p),
		('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong), ('format', ctypes.c_int),
		('data', ctypes.c_long * 5),
	]


class XRRMonitorInfo(ctypes.Structure):
	_fields_ = [
		('name', ctypes.c_ulong), ('primary', ctypes.c_int), ('automatic', ctypes.c_int),
		('noutput', ctypes.c_int), ('x', ctypes.c_int), ('y', ctypes.c_int),
		('width', ctypes.c_int), ('height', ctypes.c_int), ('mwidth', ctypes.c_int), ('mheight', ctypes.c_int),
		('outputs', ctypes.c_void_p),
	]


def log(message):
	print(message, file=sys.stderr, flush=True)


def find_app(process_name, prefix):
	wanted = process_name.lower()
	for entry in Path('/proc').iterdir():
		if not entry.name.isdigit():
			continue
		try:
			argv = (entry / 'cmdline').read_bytes().split(b'\0')
			program = argv[0].decode(errors='replace').replace('\\', '/').rsplit('/', 1)[-1]
			if program.lower() != wanted:
				continue
			environ = dict(
				line.split(b'=', 1) for line in (entry / 'environ').read_bytes().split(b'\0') if b'=' in line
			)
		except (OSError, IndexError):
			continue
		app_prefix = environ.get(b'WINEPREFIX', str(Path.home() / '.wine').encode()).decode()
		if prefix and Path(app_prefix).resolve() != Path(prefix).resolve():
			continue
		return int(entry.name), app_prefix
	return None


def wine_prefix_of(pid):
	entry = Path('/proc') / str(pid)
	try:
		program = (entry / 'cmdline').read_bytes().split(b'\0')[0]
		if not program.lower().endswith(b'.exe'):
			return None
		environ = dict(
			line.split(b'=', 1) for line in (entry / 'environ').read_bytes().split(b'\0') if b'=' in line
		)
		return environ.get(b'WINEPREFIX', str(Path.home() / '.wine').encode()).decode()
	except (OSError, ValueError):
		return None


def scan_code(code):
	if code in EXTENDED_SCAN_CODES:
		return EXTENDED_SCAN_CODES[code], 1
	if code <= ecodes.KEY_F12:
		return code, 0
	return STAND_IN_SCAN_CODES.get(SHORTCUT_KEYS.get(code), 0), 0


class Shortcuts:
	def __init__(self, prefix):
		self.prefix = Path(prefix)
		self.path = None
		self.stamp = None
		self.bindings = DEFAULT_SHORTCUTS
		self.named = dict(DEFAULT_NAMED_SHORTCUTS)

	def binding(self, name):
		return DEFAULT_NAMED_SHORTCUTS[name] if self.current() is DEFAULT_SHORTCUTS else self.named.get(name)

	def current(self):
		if not self.path:
			found = sorted(self.prefix.glob('drive_c/users/*/AppData/Roaming/com.kuro.wuwatappo*'))
			if not found:
				return DEFAULT_SHORTCUTS
			self.path = found[0] / 'shortcuts.json'
		try:
			stamp = self.path.stat().st_mtime_ns
		except OSError:
			return DEFAULT_SHORTCUTS
		if stamp == self.stamp:
			return self.bindings
		self.stamp = stamp
		try:
			saved = json.loads(self.path.read_text())
			named = {
				name: (saved[name]['modifiers'], saved[name]['key'])
				for name in SHORTCUT_NAMES if name in saved and saved[name]['key']
			}
			bindings = set(named.values())
		except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
			log(f'could not read {self.path}, keeping the shortcuts read before: {error}')
			return self.bindings
		# Tappo writes this file, so it decides which keys come through for real.
		# Only its three shortcuts count, and a key with no modifiers only if it's an F-key, or a binding could time your typing.
		typing = {binding for binding in bindings if not binding[0] and not 0x70 <= binding[1] <= 0x87}
		if typing:
			log(f'sending stand-ins for {sorted(typing)}: a shortcut with no modifiers has to be an F-key')
		self.bindings = bindings - typing
		self.named = {name: binding for name, binding in named.items() if binding not in typing}
		log(f'shortcuts: {sorted(self.bindings)}')
		return self.bindings


class UpdateNotice:
	# Every update Tappo finds is a forced one: until it's installed no tap counts and its menu has nothing but Exit.
	# Its update window is an ordinary one, so it can open behind everything else and leave the pet looking broken.
	def __init__(self, options):
		self.options = options
		self.session = None
		self.path = None
		self.offset = 0
		self.partial = b''
		self.waiting = None
		self.notified = False
		self.process = None
		self.answering = None

	async def check(self, session):
		if session is not self.session:
			await self.close()
			self.session = session
			self.path, self.offset, self.partial = None, 0, b''
			self.waiting, self.notified = None, False
		if not session:
			return
		self.read_log()
		if not self.waiting:
			await self.close()
			return
		version, seen = self.waiting
		if self.notified or asyncio.get_running_loop().time() - seen < self.options.update_notice_delay:
			return
		self.notified = True
		await self.show(version)

	def read_log(self):
		if not self.path:
			self.path = tappo_log(self.session.prefix)
			if not self.path:
				return
		try:
			size = self.path.stat().st_size
			if size < self.offset:
				self.offset, self.partial = 0, b''
			# Only what Tappo logged since it last started matters, and the log is never trimmed.
			self.offset = max(self.offset, size - LOG_TAIL)
			with self.path.open('rb') as log_file:
				log_file.seek(self.offset)
				data = log_file.read(size - self.offset)
		except OSError:
			return
		self.offset += len(data)
		*lines, self.partial = (self.partial + data).split(b'\n')
		for line in lines:
			if APP_BOOT in line:
				self.waiting = None
			elif shown := UPDATE_SHOWN.search(line):
				self.waiting = (shown[1].decode(errors='replace'), asyncio.get_running_loop().time())
				self.notified = False

	async def show(self, version):
		body = (
			f'Tappo {version} is ready to install. Until it is, Tappo won\'t count taps or open its panel, '
			'and its tray menu only has Exit.'
		)
		try:
			self.process = await asyncio.create_subprocess_exec(
				'notify-send', '-a', 'Wuwa Tappo', '-i', 'wuwa-tappo', '-u', self.options.update_notice,
				'--action=show=Show the update', 'Wuwa Tappo has an update waiting', body,
				stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
			)
		except OSError as error:
			log(f'could not say Tappo {version} is waiting to install: {error}')
			return
		log(f'Tappo {version} is waiting to install, notification sent')
		self.answering = asyncio.create_task(self.answer(self.process))

	async def answer(self, process):
		chosen = (await process.stdout.read()).split()
		await process.wait()
		# Once close() has let go of the process, the session's X connection may already be closed, and Xlib crashes on a closed display.
		if b'show' not in chosen or process is not self.process:
			return
		x = self.session and self.session.x
		window = x and x.client_titled(self.session.owner_class, UPDATE_WINDOW_TITLE)
		if window:
			x.activate(window)
		else:
			log('could not find Tappo\'s update window to bring up')

	async def close(self):
		process, self.process = self.process, None
		if not process or process.returncode is not None:
			return
		# SIGINT, because that is the only signal on which notify-send closes the notification it is waiting on.
		process.send_signal(signal.SIGINT)
		try:
			await asyncio.wait_for(process.wait(), 2)
		except asyncio.TimeoutError:
			process.kill()


def tappo_log(prefix):
	found = sorted(Path(prefix).glob('drive_c/users/*/AppData/Local/com.kuro.wuwatappo*/logs/Wuwa Tappo.log'))
	return found[0] if found else None


class ScreenKeeper:
	# Screens come back from a sleep one at a time, KWin moves windows as each one does, and Wine follows, so the pet could wake up on another screen.
	# Tappo also always starts on the main screen here.
	# Tappo's switch-screen shortcut is the one move Tappo itself keeps track of.
	def __init__(self, options):
		self.options = options
		self.session = None
		self.task = None
		self.path = Path(options.screen_file).expanduser() if options.screen_file else None

	def saved_home(self, session):
		if not self.path:
			return None
		try:
			homes = json.loads(self.path.read_text())
			return homes.get(session.prefix) if isinstance(homes, dict) else None
		except (OSError, ValueError):
			return None

	def save_home(self, session, home):
		if not self.path:
			return
		try:
			homes = json.loads(self.path.read_text())
			if not isinstance(homes, dict):
				homes = {}
		except (OSError, ValueError):
			homes = {}
		homes[session.prefix] = home
		try:
			self.path.parent.mkdir(parents=True, exist_ok=True)
			partial = self.path.with_name(f'{self.path.name}.part')
			partial.write_text(json.dumps(homes, indent='\t') + '\n')
			partial.replace(self.path)
		except OSError as error:
			log(f'could not remember the pet\'s screen in {self.path}: {error}')

	def log_size(self, session):
		path = tappo_log(session.prefix)
		try:
			return path, path.stat().st_size if path else 0
		except OSError:
			return None, 0

	def switched_since(self, mark):
		path, size = mark
		if not path:
			return True
		try:
			with path.open('rb') as log_file:
				log_file.seek(size)
				return SWITCH_START in log_file.read()
		except OSError:
			return True

	async def check(self, session):
		if session is self.session:
			return
		await self.close()
		self.session = session
		if session:
			self.task = asyncio.create_task(self.watch(session))

	async def close(self):
		task, self.task = self.task, None
		if not task:
			return
		task.cancel()
		try:
			await task
		except asyncio.CancelledError:
			pass

	async def watch(self, session):
		try:
			x = XDisplay()
		except OSError as error:
			log(f'not keeping the pet on its screen: {error}')
			return
		try:
			await self.keep(session, x)
		finally:
			x.close()

	async def keep(self, session, x):
		loop = asyncio.get_running_loop()
		home = self.saved_home(session)
		layout = last_stage = None
		displaced = home is not None
		changed = loop.time()
		tries = unanswered = 0
		clock = time.time() - loop.time()
		while True:
			await asyncio.sleep(self.options.screen_poll)
			screens = x.named_screens()
			# The monotonic clock stops while the PC sleeps and the wall clock doesn't, which gives a wake away even if the screens were back before this looked.
			woke = time.time() - loop.time() - clock > SLEEP_JUMP
			clock = time.time() - loop.time()
			stage = x.stage(session.owner_class)
			# The bridge's session outlives a quick restart of Tappo, so a new or returning stage is the only sign of one.
			restarted = stage is not None and stage != last_stage
			last_stage = stage
			if screens != layout or woke or restarted:
				layout, changed, tries, unanswered = screens, loop.time(), 0, 0
				displaced = displaced or home is not None
				continue
			if loop.time() - changed < self.options.screen_settle:
				continue
			current = stage and screen_named(x.area(stage), screens)
			if not current:
				continue
			# Without a change of screens, a move is the user's own, with the shortcut or the menu.
			if not displaced or current == home:
				if current != home:
					log(f'the pet is on {current}')
					self.save_home(session, current)
				home, displaced, tries, unanswered = current, False, 0, 0
				continue
			if home not in {name for name, _ in screens}:
				continue
			binding = session.shortcuts.binding('switchScreen')
			if binding and binding[1] not in SHORTCUT_CODES:
				binding = None
			if not binding or tries >= len(screens) or unanswered >= SWITCH_UNANSWERED:
				if not binding:
					reason = 'Tappo\'s switch-screen shortcut is unbound or not a key the bridge can send'
				elif unanswered >= SWITCH_UNANSWERED:
					reason = 'Tappo never answered its switch-screen shortcut'
				else:
					reason = 'it never came round'
				log(f'leaving the pet on {current}, as {reason}')
				# The saved screen stays as it was, so the next start tries again.
				home, displaced, tries, unanswered = current, False, 0, 0
				continue
			if not tries and not unanswered:
				log(f'the pet is on {current} rather than {home}, switching it back')
			# Straight after starting, Tappo can miss the shortcut, and only a press it logged a switch for counts as a try.
			mark = self.log_size(session)
			await self.press(session, binding)
			await asyncio.sleep(self.options.screen_switch_wait)
			if self.switched_since(mark):
				tries += 1
			else:
				unanswered += 1

	async def press(self, session, binding):
		modifiers, vk = binding
		key = (vk, *scan_code(SHORTCUT_CODES[vk]))
		needed = [modifier for bit, modifier in MODIFIER_KEYS.items() if modifiers & bit]
		for modifier in needed:
			await session.send_key(modifier, True, True)
		await session.send_key(key, True, True)
		await session.send_key(key, False, True)
		for modifier in reversed(needed):
			await session.send_key(modifier, False, True)


def screen_named(area, screens):
	if not area:
		return None
	best = max(screens, key=lambda screen: overlap(area, screen[1]), default=None)
	return best[0] if best and overlap(area, best[1]) else None


def wanted_device(device, ignore):
	if any(pattern.lower() in device.name.lower() for pattern in ignore):
		return False
	keys = set(device.capabilities().get(ecodes.EV_KEY, []))
	return ecodes.KEY_A in keys or ecodes.BTN_LEFT in keys


def bind(library, name, restype, *argtypes):
	function = getattr(library, name)
	function.restype = restype
	function.argtypes = argtypes
	return function


def inside(x, y, rectangles):
	return any(left <= x < right and top <= y < bottom for left, top, right, bottom in rectangles)


def overlap(first, second):
	width = min(first[2], second[2]) - max(first[0], second[0])
	height = min(first[3], second[3]) - max(first[1], second[1])
	return max(width, 0) * max(height, 0)


def owned_under(position, areas):
	return next((owned for area, owned in reversed(areas) if inside(*position, [area])), False)


def free_spot(origin, areas, screens, margin):
	blocked = [(left - margin, top - margin, right + margin, bottom + margin) for left, top, right, bottom in areas]
	# The nearest free point is either the origin or in line with an edge of a screen or a blocked area.
	xs, ys = {origin[0]}, {origin[1]}
	for left, top, right, bottom in blocked:
		xs.update((left - 1, right))
		ys.update((top - 1, bottom))
	for left, top, right, bottom in screens:
		xs.update((left, right - 1))
		ys.update((top, bottom - 1))
	free = [(x, y) for x in xs for y in ys if inside(x, y, screens) and not inside(x, y, blocked)]
	return min(free, key=lambda point: (point[0] - origin[0]) ** 2 + (point[1] - origin[1]) ** 2, default=None)


class XDisplay:
	def __init__(self):
		x11 = ctypes.CDLL('libX11.so.6')
		xext = ctypes.CDLL('libXext.so.6')
		xrandr = ctypes.CDLL('libXrandr.so.2')
		pointer = ctypes.c_void_p
		window = ctypes.c_ulong

		self.close_display = bind(x11, 'XCloseDisplay', ctypes.c_int, pointer)
		self.query_pointer = bind(x11, 'XQueryPointer', ctypes.c_int, pointer, window, *[pointer] * 7)
		self.get_input_focus = bind(x11, 'XGetInputFocus', ctypes.c_int, pointer, pointer, pointer)
		self.query_keymap = bind(x11, 'XQueryKeymap', ctypes.c_int, pointer, pointer)
		self.query_tree = bind(x11, 'XQueryTree', ctypes.c_int, pointer, window, *[pointer] * 4)
		self.window_attributes = bind(x11, 'XGetWindowAttributes', ctypes.c_int, pointer, window, pointer)
		self.class_hint = bind(x11, 'XGetClassHint', ctypes.c_int, pointer, window, pointer)
		self.free = bind(x11, 'XFree', ctypes.c_int, pointer)
		self.shape_rectangles = bind(
			xext, 'XShapeGetRectangles', ctypes.POINTER(XRectangle), pointer, window, ctypes.c_int, pointer, pointer,
		)
		self.get_monitors = bind(xrandr, 'XRRGetMonitors', ctypes.POINTER(XRRMonitorInfo), pointer, window, ctypes.c_int, pointer)
		self.free_monitors = bind(xrandr, 'XRRFreeMonitors', None, ctypes.POINTER(XRRMonitorInfo))
		self.atom_name = bind(x11, 'XGetAtomName', pointer, pointer, ctypes.c_ulong)
		self.intern_atom = bind(x11, 'XInternAtom', ctypes.c_ulong, pointer, ctypes.c_char_p, ctypes.c_int)
		self.get_property = bind(
			x11, 'XGetWindowProperty', ctypes.c_int, pointer, window, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
			ctypes.c_int, ctypes.c_ulong, *[pointer] * 5,
		)
		self.translate_coordinates = bind(
			x11, 'XTranslateCoordinates', ctypes.c_int, pointer, window, window, ctypes.c_int, ctypes.c_int, *[pointer] * 3,
		)
		self.send_event = bind(x11, 'XSendEvent', ctypes.c_int, pointer, window, ctypes.c_int, ctypes.c_long, pointer)
		self.flush = bind(x11, 'XFlush', ctypes.c_int, pointer)

		# Otherwise a window closing mid-scan would end the bridge, since Xlib's default handler exits on any error.
		self.ignore_errors = X_ERROR_HANDLER(lambda display, error: 0)
		bind(x11, 'XSetErrorHandler', pointer, X_ERROR_HANDLER)(self.ignore_errors)

		self.display = bind(x11, 'XOpenDisplay', pointer, ctypes.c_char_p)(None)
		if not self.display:
			raise OSError(f'cannot open X display {os.environ.get("DISPLAY", "(DISPLAY not set)")}')
		self.root = bind(x11, 'XDefaultRootWindow', window, pointer)(self.display)
		self.atoms = {name: self.intern_atom(self.display, name.encode(), False) for name in ATOM_NAMES}

	def close(self):
		self.close_display(self.display)

	def pointer(self):
		root, child = ctypes.c_ulong(), ctypes.c_ulong()
		x, y, window_x, window_y = (ctypes.c_int() for _ in range(4))
		buttons = ctypes.c_uint()
		self.query_pointer(
			self.display, self.root, *(ctypes.byref(value) for value in (root, child, x, y, window_x, window_y, buttons)),
		)
		return (x.value, y.value), child.value, buttons.value

	def focus(self):
		window, revert = ctypes.c_ulong(), ctypes.c_int()
		self.get_input_focus(self.display, ctypes.byref(window), ctypes.byref(revert))
		return window.value

	def focused_client(self):
		window = self.focus()
		# The focus can be on a child window, which has none of the window manager's properties, so the fullscreen state has to come from the managed one above it.
		while window and window not in (POINTER_ROOT, self.root):
			if self.property_values(window, 'WM_STATE') is not None:
				return window
			window = self.parent(window)
		return None

	def parent(self, window):
		root, parent = ctypes.c_ulong(), ctypes.c_ulong()
		children = ctypes.POINTER(ctypes.c_ulong)()
		count = ctypes.c_uint()
		if not self.query_tree(
			self.display, window, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(children), ctypes.byref(count),
		):
			return None
		if children:
			self.free(children)
		return parent.value

	def atom(self, name):
		if name not in self.atoms:
			self.atoms[name] = self.intern_atom(self.display, name.encode(), False)
		return self.atoms[name]

	def read_property(self, window, name, length):
		actual_type, actual_format = ctypes.c_ulong(), ctypes.c_int()
		count, remaining = ctypes.c_ulong(), ctypes.c_ulong()
		data = ctypes.c_void_p()
		status = self.get_property(
			self.display, window, self.atom(name), 0, length, False, ANY_PROPERTY_TYPE, ctypes.byref(actual_type),
			ctypes.byref(actual_format), ctypes.byref(count), ctypes.byref(remaining), ctypes.byref(data),
		)
		if status != X_SUCCESS or not actual_type.value:
			return None
		return actual_format.value, count.value, data

	def property_values(self, window, name, length=64):
		found = self.read_property(window, name, length)
		if not found:
			return None
		actual_format, count, data = found
		values = []
		# Read as unsigned longs because Xlib returns format-32 items as C longs, 64 bits each here.
		if actual_format == 32:
			values = list(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[:count])
		if data:
			self.free(data)
		return values

	def text(self, window, name):
		found = self.read_property(window, name, 256)
		if not found:
			return None
		actual_format, count, data = found
		value = ctypes.string_at(data, count).decode(errors='replace') if actual_format == 8 and data else None
		if data:
			self.free(data)
		return value

	def is_fullscreen(self, window):
		return self.atoms['_NET_WM_STATE_FULLSCREEN'] in (self.property_values(window, '_NET_WM_STATE') or [])

	def pid(self, window):
		values = self.property_values(window, '_NET_WM_PID')
		return values[0] if values else None

	def area(self, window):
		attributes = XWindowAttributes()
		if not self.window_attributes(self.display, window, ctypes.byref(attributes)):
			return None
		x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
		if not self.translate_coordinates(
			self.display, window, self.root, 0, 0, ctypes.byref(x), ctypes.byref(y), ctypes.byref(child),
		):
			return None
		return x.value, y.value, x.value + attributes.width, y.value + attributes.height

	def holds(self, code):
		if code in BUTTON_STATE:
			return bool(self.pointer()[2] & BUTTON_STATE[code])
		keys = ctypes.create_string_buffer(32)
		self.query_keymap(self.display, keys)
		keycode = code + 8
		return keycode < 256 and bool(keys.raw[keycode // 8] >> keycode % 8 & 1)

	def takes_pointer(self, window):
		attributes = XWindowAttributes()
		if not self.window_attributes(self.display, window, ctypes.byref(attributes)):
			return False
		if attributes.window_class == INPUT_OUTPUT:
			return True
		# KWin's InputOnly window spans the root, while Wine confines a game's pointer (ClipCursor) to one sized to the clip rectangle.
		# Parking then would warp the real pointer out of the game.
		root = XWindowAttributes()
		if not self.window_attributes(self.display, self.root, ctypes.byref(root)):
			return False
		return (attributes.width, attributes.height) != (root.width, root.height)

	def window_class(self, window):
		hint = XClassHint()
		if not self.class_hint(self.display, window, ctypes.byref(hint)):
			return None
		window_class = ctypes.string_at(hint.res_class).decode(errors='replace') if hint.res_class else None
		for value in (hint.res_name, hint.res_class):
			if value:
				self.free(value)
		return window_class

	def client_titled(self, owner_class, title):
		for window in self.property_values(self.root, '_NET_CLIENT_LIST', 4096) or []:
			if self.window_class(window) == owner_class and self.text(window, '_NET_WM_NAME') == title:
				return window
		return None

	def stage(self, owner_class):
		root, parent = ctypes.c_ulong(), ctypes.c_ulong()
		children = ctypes.POINTER(ctypes.c_ulong)()
		count = ctypes.c_uint()
		if not self.query_tree(
			self.display, self.root, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(children), ctypes.byref(count),
		):
			return None
		windows = list(children[:count.value])
		if children:
			self.free(children)
		attributes = XWindowAttributes()
		for window in windows:
			if self.window_class(window) != owner_class or self.text(window, '_NET_WM_NAME') != STAGE_TITLE:
				continue
			if self.window_attributes(self.display, window, ctypes.byref(attributes)) and attributes.map_state == IS_VIEWABLE:
				return window
		return None

	def named_screens(self):
		count = ctypes.c_int()
		monitors = self.get_monitors(self.display, self.root, 1, ctypes.byref(count))
		if not monitors:
			return ()
		screens = []
		for monitor in monitors[:count.value]:
			name = self.atom_name(self.display, monitor.name)
			rect = (monitor.x, monitor.y, monitor.x + monitor.width, monitor.y + monitor.height)
			screens.append((ctypes.string_at(name).decode(errors='replace') if name else '', rect))
			if name:
				self.free(name)
		self.free_monitors(monitors)
		# Sorted by name, because the order Xwayland lists screens in changes with every wake.
		return tuple(sorted(screens))

	def activate(self, window):
		# Source 2 is a pager, which window managers let through whatever their focus stealing prevention says.
		message = XClientMessageEvent(
			type=CLIENT_MESSAGE, window=window, message_type=self.atom('_NET_ACTIVE_WINDOW'), format=32,
			data=(ctypes.c_long * 5)(2, 0, 0, 0, 0),
		)
		self.send_event(self.display, self.root, False, SUBSTRUCTURE_MASKS, ctypes.byref(message))
		self.flush(self.display)

	def input_areas(self, owner_class):
		root, parent = ctypes.c_ulong(), ctypes.c_ulong()
		children = ctypes.POINTER(ctypes.c_ulong)()
		count = ctypes.c_uint()
		if not self.query_tree(
			self.display, self.root, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(children), ctypes.byref(count),
		):
			return []

		areas = []
		for window in children[:count.value]:
			attributes = XWindowAttributes()
			if not self.window_attributes(self.display, window, ctypes.byref(attributes)):
				continue
			# InputOnly windows never get a Wayland surface, and KWin's root-sized one would block every spot.
			if attributes.map_state != IS_VIEWABLE or attributes.window_class != INPUT_OUTPUT:
				continue
			# Wine cuts its windows down with the bounding shape alone, and X only takes input where that and the input shape overlap.
			# The pet's full-screen stage has an empty input shape, so it never takes the pointer.
			bounding = self.shape_extents(window, SHAPE_BOUNDING)
			input_shape = self.shape_extents(window, SHAPE_INPUT)
			if not bounding or not input_shape:
				continue
			left, top = max(bounding[0], input_shape[0]), max(bounding[1], input_shape[1])
			right, bottom = min(bounding[2], input_shape[2]), min(bounding[3], input_shape[3])
			if left < right and top < bottom:
				area = (attributes.x + left, attributes.y + top, attributes.x + right, attributes.y + bottom)
				areas.append((area, self.window_class(window) == owner_class))
		self.free(children)
		return areas

	def shape_extents(self, window, kind):
		count, ordering = ctypes.c_int(), ctypes.c_int()
		rectangles = self.shape_rectangles(self.display, window, kind, ctypes.byref(count), ctypes.byref(ordering))
		if not rectangles:
			return None
		shape = rectangles[:count.value]
		extents = (
			min(rectangle.x for rectangle in shape),
			min(rectangle.y for rectangle in shape),
			max(rectangle.x + rectangle.width for rectangle in shape),
			max(rectangle.y + rectangle.height for rectangle in shape),
		)
		self.free(rectangles)
		return extents

	def screens(self):
		count = ctypes.c_int()
		monitors = self.get_monitors(self.display, self.root, 1, ctypes.byref(count))
		if not monitors:
			return []
		screens = [
			(monitor.x, monitor.y, monitor.x + monitor.width, monitor.y + monitor.height)
			for monitor in monitors[:count.value]
		]
		self.free_monitors(monitors)
		return screens


class Session:
	def __init__(self, options, prefix):
		self.options = options
		self.prefix = prefix
		self.owner_class = options.process_name.lower()
		self.helper = None
		self.readers = {}
		self.pressed = {}
		self.held = set()
		self.shortcuts = Shortcuts(prefix)
		self.last_hand = 'right'
		self.x = None
		self.watcher = None
		self.fullscreen_watcher = None
		self.last_focus = None
		self.stand_in = False
		self.covering = False

	async def start(self):
		await self.start_helper()
		self.scanner = asyncio.create_task(self.scan_devices())
		uses_x = (
			self.options.cursor_poll > 0 or self.options.direct_wait > 0 or not self.options.no_menu_close
			or self.options.fullscreen_poll > 0
		)
		if uses_x:
			try:
				self.x = XDisplay()
			except OSError as error:
				log(
					'not moving the cursor off X windows, skipping presses Wine already got, closing menus '
					f'or telling Tappo about fullscreen apps: {error}'
				)
			else:
				if self.options.cursor_poll > 0:
					self.watcher = asyncio.create_task(self.watch_pointer())
				if self.options.fullscreen_poll > 0:
					self.fullscreen_watcher = asyncio.create_task(self.watch_fullscreen())

	async def stop(self):
		self.scanner.cancel()
		for watcher in (self.watcher, self.fullscreen_watcher):
			if watcher:
				watcher.cancel()
		for reader in self.readers.values():
			reader.cancel()
		self.readers.clear()
		if self.x:
			self.x.close()
		if self.helper and self.helper.returncode is None:
			self.helper.stdin.close()
			try:
				await asyncio.wait_for(self.helper.wait(), 5)
			except asyncio.TimeoutError:
				self.helper.kill()

	async def start_helper(self):
		arguments = [self.options.wine, str(self.options.helper)]
		if self.options.hardware_input:
			arguments.append('--hardware')
		if self.options.verbose:
			arguments.append('--verbose')
		self.helper = await asyncio.create_subprocess_exec(
			*arguments,
			stdin=asyncio.subprocess.PIPE,
			stdout=asyncio.subprocess.DEVNULL,
			env={**os.environ, 'WINEPREFIX': self.prefix, 'WINEDEBUG': os.environ.get('WINEDEBUG', '-all')},
		)
		self.last_focus = None
		self.stand_in = self.covering = False
		log(f'helper started in {self.prefix} (pid {self.helper.pid})')

	async def scan_devices(self):
		while True:
			for path in evdev.list_devices():
				if path in self.readers:
					continue
				try:
					device = evdev.InputDevice(path)
				except OSError:
					continue
				if not wanted_device(device, self.options.ignore):
					device.close()
					continue
				log(f'reading {device.name} ({path})')
				self.readers[path] = asyncio.create_task(self.read_device(path, device))
			await asyncio.sleep(3)

	async def watch_pointer(self):
		loop = asyncio.get_running_loop()
		parked = None
		parked_window = None
		checked = 0
		last_position = None
		while True:
			await asyncio.sleep(self.options.cursor_poll)
			position, window, buttons = self.x.pointer()
			moved, last_position = position != last_position, position
			if position == parked:
				# Xwayland's child at a parked spot can't be trusted, so a change in it only prompts a scan.
				due = self.options.cursor_recheck and loop.time() - checked >= self.options.cursor_recheck
				if window == parked_window and not due:
					continue
				parked_window = window
				checked = loop.time()
			# Dragging takes the pointer outside the pet's shape while X still tracks it, so a held button or a moving pointer hasn't left.
			elif moved or buttons & BUTTON_MASKS or (window and self.x.takes_pointer(window)):
				continue

			areas = self.x.input_areas(self.owner_class)
			# Only Tappo hit-tests the spot Wine remembers, so a stale one over another app or over nothing is left alone.
			if not owned_under(position, areas):
				continue
			blocked = [area for area, _ in areas]
			screens = self.x.screens()
			spot = free_spot(position, blocked, screens, self.options.cursor_margin) or free_spot(position, blocked, screens, 0)
			checked = loop.time()
			if not spot:
				if position != parked:
					log(f'nowhere clear of X windows to move the cursor to from {position[0]},{position[1]}')
				parked, parked_window = position, window
				continue
			parked, parked_window = spot, None
			await self.send(f'move {spot[0]} {spot[1]}')

	async def watch_fullscreen(self):
		# Tappo decides an app is fullscreen from Wine's foreground window, which a Linux app never is, so the helper's stand-in takes its place.
		while True:
			await asyncio.sleep(self.options.fullscreen_poll)
			state = self.focus_state()
			if state == self.last_focus:
				continue
			self.last_focus = state
			if state[0] == 'fullscreen':
				left, top, right, bottom = state[2]
				self.stand_in = self.covering = True
				await self.send(f'fullscreen {left} {top} {right - left} {bottom - top}')
			elif state[0] == 'prefix' and self.stand_in:
				await self.send(f'focus {state[1]:x}')
			elif state[0] == 'windowed' and self.covering:
				self.covering = False
				await self.send('windowed')

	def focus_state(self):
		window = self.x.focused_client()
		if not window:
			return ('windowed',)
		# Wine already tracks focus for its own windows, so for those the stand-in only has to get out of the way.
		if self.x.window_class(window) == self.owner_class or self.in_prefix(self.x.pid(window)):
			return ('prefix', window)
		if not self.x.is_fullscreen(window):
			return ('windowed',)
		area = self.x.area(window)
		screens = [screen for screen in self.x.screens() if area and overlap(area, screen)]
		if not screens:
			return ('windowed',)
		return ('fullscreen', window, max(screens, key=lambda screen: overlap(area, screen)))

	def in_prefix(self, pid):
		prefix = pid and wine_prefix_of(pid)
		return bool(prefix) and Path(prefix).resolve() == Path(self.prefix).resolve()

	async def read_device(self, path, device):
		try:
			async for event in device.async_read_loop():
				if event.type == ecodes.EV_KEY and event.value in (0, 1):
					await self.forward(event.code, event.value == 1)
		except OSError:
			log(f'lost {device.name} ({path})')
		finally:
			device.close()
			self.readers.pop(path, None)

	def stand_ins_for(self, code):
		if code in MOUSE_BUTTONS:
			if self.options.mouse_hand == 'off':
				return None
			hand = self.options.mouse_hand
			return STAND_INS[hand] + STAND_INS[OTHER_HAND[hand]]
		if self.options.real_keys:
			if code in EXTENDED_SCAN_CODES:
				return [(0, EXTENDED_SCAN_CODES[code], 1)]
			return [(0, code, 0)] if code <= ecodes.KEY_F12 else None
		if code > ecodes.KEY_MICMUTE:
			return None
		if code == ecodes.KEY_SPACE:
			hand = OTHER_HAND[self.last_hand]
		else:
			hand = 'left' if code in LEFT_HAND_KEYS else 'right'
		self.last_hand = hand
		return STAND_INS[hand] + STAND_INS[OTHER_HAND[hand]]

	async def landing(self, code):
		if not self.x or self.options.direct_wait <= 0:
			return 'elsewhere'
		window = self.x.pointer()[1] if code in MOUSE_BUTTONS else self.x.focus()
		if self.x.window_class(window) != self.owner_class:
			return 'elsewhere'
		# X keeps no state for the side buttons, so for those the window under the pointer has to do.
		if code in MOUSE_BUTTONS and code not in BUTTON_STATE:
			return 'wine'
		# Only Xwayland knows whether it has Wayland's focus, so the press counts as Wine's once X holds it, a few ms after evdev.
		loop = asyncio.get_running_loop()
		deadline = loop.time() + self.options.direct_wait
		while not self.x.holds(code):
			if loop.time() >= deadline:
				return 'missed'
			await asyncio.sleep(0.002)
		return 'wine'

	def outside_app(self):
		return self.x is not None and self.x.window_class(self.x.pointer()[1]) != self.owner_class

	async def forward(self, code, down):
		if not down:
			self.held.discard(code)
			sent = self.pressed.pop(code, None)
			if not sent:
				return
			key, hardware, modifiers = sent
			await self.send_key(key, False, hardware)
			for modifier in reversed(modifiers):
				await self.send_key(modifier, False, True)
			return

		modifiers = 0
		for held in self.held:
			modifiers |= MODIFIER_BITS.get(held, 0)
		self.held.add(code)

		landing = await self.landing(code)
		# Wine passes a press that X gave one of Tappo's windows straight to its hooks, so a stand-in would count it twice.
		if landing == 'wine':
			return
		if code in MOUSE_BUTTONS and not self.options.no_menu_close and self.outside_app():
			await self.send('endmenu')

		shortcuts = self.shortcuts.current()
		vk = SHORTCUT_KEYS.get(code)
		is_shortcut = vk and not self.options.no_shortcuts and (modifiers, vk) in shortcuts
		# Tappo has the keyboard but the compositor kept the key for a shortcut of its own, so the shortcut recorder would only see a stand-in.
		kept_from_tappo = landing == 'missed' and code in FUNCTION_KEYS
		if is_shortcut or kept_from_tappo:
			# The server matches RegisterHotKey on its own key state, so the modifiers have to be pressed in Wine too.
			# They stay down until the key is let go: a lone Alt or Win release would open the stale window's system menu.
			needed = []
			if is_shortcut and landing == 'elsewhere' and not self.options.real_keys:
				needed = [key for bit, key in MODIFIER_KEYS.items() if modifiers & bit]
			key = (vk, *scan_code(code))
			for modifier in needed:
				await self.send_key(modifier, True, True)
			# Tappo catches F12, which Windows won't register, with a hook that skips injected keys.
			await self.send_key(key, True, True)
			self.pressed[code] = (key, True, needed)
			return

		candidates = self.stand_ins_for(code)
		if not candidates:
			return
		# A stand-in that is also a shortcut would fire it on every tap.
		bound = {key for _, key in shortcuts}
		taken = {sent[0] for sent in self.pressed.values()}
		stand_in = next((key for key in candidates if key not in taken and key[0] not in bound), None)
		if not stand_in:
			return
		self.pressed[code] = (stand_in, False, [])
		await self.send_key(stand_in, True)

	async def send_key(self, key, down, hardware=False):
		vk, scan, extended = key
		await self.send(f'key {vk:x} {scan:x} {extended} {int(down)}{" hw" if hardware else ""}')

	async def send(self, command):
		if self.helper.returncode is not None:
			if not find_app(self.options.process_name, self.prefix):
				return
			log(f'helper exited with {self.helper.returncode}, restarting')
			await self.start_helper()

		try:
			self.helper.stdin.write(f'{command}\n'.encode())
			await self.helper.stdin.drain()
		except (BrokenPipeError, ConnectionResetError):
			log('helper pipe closed')


async def run(options):
	session = None
	missing_since = None
	notice = UpdateNotice(options) if options.update_notice != 'off' else None
	keeper = ScreenKeeper(options) if options.screen_poll > 0 else None
	stopping = asyncio.Event()

	loop = asyncio.get_running_loop()
	for signal_number in (signal.SIGINT, signal.SIGTERM):
		loop.add_signal_handler(signal_number, stopping.set)

	while not stopping.is_set():
		app = find_app(options.process_name, options.prefix)

		if app and not session:
			pid, prefix = app
			log(f'{options.process_name} running (pid {pid}), starting bridge')
			session = Session(options, prefix)
			await session.start()
		elif app:
			missing_since = None
		elif session:
			missing_since = missing_since or loop.time()
			if loop.time() - missing_since >= options.grace:
				log(f'{options.process_name} gone, stopping bridge')
				if notice:
					await notice.check(None)
				if keeper:
					await keeper.check(None)
				await session.stop()
				session = None
				missing_since = None

		if notice:
			await notice.check(session)
		if keeper:
			await keeper.check(session)

		try:
			await asyncio.wait_for(stopping.wait(), options.poll)
		except asyncio.TimeoutError:
			pass

	if notice:
		await notice.check(None)
	if keeper:
		await keeper.check(None)
	if session:
		await session.stop()


def parse_options():
	parser = argparse.ArgumentParser(
		description='Feed keyboard and mouse presses from every app into Wuwa Tappo running under Wine.',
	)
	parser.add_argument('--prefix', help='only bridge an app running in this Wine prefix (default: any)')
	parser.add_argument('--process-name', default='WuwaTappo.exe', help='Windows executable to watch for')
	parser.add_argument('--helper', type=Path, default=DEFAULT_HELPER, help='path to tappo-input.exe.so')
	parser.add_argument('--wine', default=str(DEFAULT_WINE), help='wine binary matching the one the app runs under (default: the patched runtime)')
	parser.add_argument(
		'--mouse-hand', choices=('left', 'right', 'off'), default='right',
		help='which stand-in a mouse click sends, or off to ignore clicks',
	)
	parser.add_argument(
		'--real-keys', action='store_true',
		help='send the real keys instead of a left/right stand-in; '
		'the app then sees everything typed, and Wine also delivers them to the last Tappo window it focused',
	)
	parser.add_argument(
		'--no-shortcuts', action='store_true',
		help='send stand-ins for the app\'s own shortcuts too (read from its shortcuts.json), '
		'which then only work while one of its windows has the keyboard',
	)
	parser.add_argument(
		'--ignore', action='append', default=None, metavar='NAME',
		help='skip input devices whose name contains NAME, repeatable (default: Yubico)',
	)
	parser.add_argument(
		'--hardware-input', action='store_true',
		help='send through Wine\'s private hardware-input call so the app cannot tell the presses were injected',
	)
	parser.add_argument(
		'--cursor-poll', type=float, default=0.1,
		help='seconds between checks for the pointer having left every X window, after which the cursor Wine sees '
		'is moved clear of them; 0 turns this off',
	)
	parser.add_argument(
		'--cursor-margin', type=int, default=32,
		help='pixels to keep between that moved cursor and any X window, when there is room',
	)
	parser.add_argument(
		'--cursor-recheck', type=float, default=1.0,
		help='seconds between looks for a window that has opened over the moved cursor, which Xwayland does not report; '
		'0 never looks',
	)
	parser.add_argument(
		'--direct-wait', type=float, default=0.05,
		help='seconds to wait for X to report a press made while one of the app\'s windows has the X keyboard focus or the pointer; '
		'X reporting it means Wine got the press itself, so no stand-in is sent; 0 always sends one',
	)
	parser.add_argument(
		'--no-menu-close', action='store_true',
		help='leave the app\'s menus open on a click outside them, which then never close for a click on a Wayland window',
	)
	parser.add_argument(
		'--update-notice', choices=('critical', 'normal', 'off'), default='critical',
		help='how to tell you the app has an update waiting, as it counts no taps until that is installed and its update '
		'window can open behind others: critical stays on screen until dismissed, normal goes to the notification history, '
		'off sends nothing',
	)
	parser.add_argument(
		'--update-notice-delay', type=float, default=60.0,
		help='seconds the app\'s update window can be open before the notice, so one you are already installing gets none',
	)
	parser.add_argument(
		'--screen-poll', type=float, default=1.0,
		help='seconds between checks of which screen the pet is on, so that when the screens change (a sleep, '
		'a monitor unplugged and back) it can be put back with the app\'s switch-screen shortcut; 0 turns this off',
	)
	parser.add_argument(
		'--screen-settle', type=float, default=5.0,
		help='seconds the screens have to stay as they are after a change before the pet is put back',
	)
	parser.add_argument(
		'--screen-switch-wait', type=float, default=3.0,
		help='seconds to give each screen switch before looking where the pet went',
	)
	state_home = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')
	parser.add_argument(
		'--screen-file', default=str(Path(state_home) / 'tappo-bridge' / 'screens.json'),
		help='where to remember which screen the pet is on, so it goes back there whenever Tappo starts; '
		'an empty string remembers it only until Tappo quits',
	)
	parser.add_argument(
		'--fullscreen-poll', type=float, default=0.5,
		help='seconds between checks for a fullscreen X window having the keyboard, which the helper then stands in for, '
		'so the app\'s "Enter Game Mode in Fullscreen" can see it; 0 turns this off',
	)
	parser.add_argument('--poll', type=float, default=2.0, help='seconds between checks for the app')
	parser.add_argument(
		'--grace', type=float, default=15.0,
		help='seconds the app may be gone before the bridge stops, long enough for a restart from the tray or an update',
	)
	parser.add_argument('--verbose', action='store_true', help='log every injected key from the helper')
	options = parser.parse_args()

	if options.ignore is None:
		options.ignore = ['Yubico']
	if not options.helper.exists():
		parser.error(f'helper not found at {options.helper}, build it with make')
	return options


if __name__ == '__main__':
	ctypes.CDLL(None).prctl(PR_SET_NAME, b'tappo-bridge')
	asyncio.run(run(parse_options()))
