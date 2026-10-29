#!/usr/bin/env python3
import argparse
import asyncio
import ctypes
import os
import signal
import sys
from pathlib import Path

import evdev
from evdev import ecodes

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

# F13 and F14, because they type nothing into whichever Tappo window Wine last focused.
STAND_INS = {
	'left': (0x7c, 0x64),
	'right': (0x7d, 0x65),
}

# Up to KEY_F12 an evdev code is its PC set 1 scan code; these need the E0 prefix instead.
EXTENDED_SCAN_CODES = {
	ecodes.KEY_KPENTER: 0x1c, ecodes.KEY_RIGHTCTRL: 0x1d, ecodes.KEY_KPSLASH: 0x35, ecodes.KEY_SYSRQ: 0x37,
	ecodes.KEY_RIGHTALT: 0x38, ecodes.KEY_HOME: 0x47, ecodes.KEY_UP: 0x48, ecodes.KEY_PAGEUP: 0x49,
	ecodes.KEY_LEFT: 0x4b, ecodes.KEY_RIGHT: 0x4d, ecodes.KEY_END: 0x4f, ecodes.KEY_DOWN: 0x50,
	ecodes.KEY_PAGEDOWN: 0x51, ecodes.KEY_INSERT: 0x52, ecodes.KEY_DELETE: 0x53,
	ecodes.KEY_LEFTMETA: 0x5b, ecodes.KEY_RIGHTMETA: 0x5c, ecodes.KEY_COMPOSE: 0x5d,
}

MOUSE_BUTTONS = {ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE, ecodes.BTN_SIDE, ecodes.BTN_EXTRA}

INPUT_OUTPUT = 1
IS_VIEWABLE = 2
BUTTON_MASKS = 0x1f00
SHAPE_BOUNDING = 0
SHAPE_INPUT = 2

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
		self.query_tree = bind(x11, 'XQueryTree', ctypes.c_int, pointer, window, *[pointer] * 4)
		self.window_attributes = bind(x11, 'XGetWindowAttributes', ctypes.c_int, pointer, window, pointer)
		self.class_hint = bind(x11, 'XGetClassHint', ctypes.c_int, pointer, window, pointer)
		self.free = bind(x11, 'XFree', ctypes.c_int, pointer)
		self.shape_rectangles = bind(
			xext, 'XShapeGetRectangles', ctypes.POINTER(XRectangle), pointer, window, ctypes.c_int, pointer, pointer,
		)
		self.get_monitors = bind(xrandr, 'XRRGetMonitors', ctypes.POINTER(XRRMonitorInfo), pointer, window, ctypes.c_int, pointer)
		self.free_monitors = bind(xrandr, 'XRRFreeMonitors', None, ctypes.POINTER(XRRMonitorInfo))

		# Otherwise a window closing mid-scan would end the bridge, since Xlib's default handler exits on any error.
		self.ignore_errors = X_ERROR_HANDLER(lambda display, error: 0)
		bind(x11, 'XSetErrorHandler', pointer, X_ERROR_HANDLER)(self.ignore_errors)

		self.display = bind(x11, 'XOpenDisplay', pointer, ctypes.c_char_p)(None)
		if not self.display:
			raise OSError(f'cannot open X display {os.environ.get("DISPLAY", "(DISPLAY not set)")}')
		self.root = bind(x11, 'XDefaultRootWindow', window, pointer)(self.display)

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
		self.helper = None
		self.readers = {}
		self.pressed = {}
		self.last_hand = 'right'
		self.x = None
		self.watcher = None

	async def start(self):
		await self.start_helper()
		self.scanner = asyncio.create_task(self.scan_devices())
		if self.options.cursor_poll > 0:
			try:
				self.x = XDisplay()
			except OSError as error:
				log(f'not moving the cursor off X windows: {error}')
			else:
				self.watcher = asyncio.create_task(self.watch_pointer())

	async def stop(self):
		self.scanner.cancel()
		if self.watcher:
			self.watcher.cancel()
			self.x.close()
		for reader in self.readers.values():
			reader.cancel()
		self.readers.clear()
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
		owner_class = self.options.process_name.lower()
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

			areas = self.x.input_areas(owner_class)
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

	def stand_in_for(self, code):
		if code in MOUSE_BUTTONS:
			if self.options.mouse_hand == 'off':
				return None
			return (*STAND_INS[self.options.mouse_hand], 0)
		if self.options.real_keys:
			if code in EXTENDED_SCAN_CODES:
				return 0, EXTENDED_SCAN_CODES[code], 1
			return (0, code, 0) if code <= ecodes.KEY_F12 else None
		if code > ecodes.KEY_MICMUTE:
			return None
		if code == ecodes.KEY_SPACE:
			hand = 'left' if self.last_hand == 'right' else 'right'
		else:
			hand = 'left' if code in LEFT_HAND_KEYS else 'right'
		self.last_hand = hand
		return (*STAND_INS[hand], 0)

	async def forward(self, code, down):
		if down:
			stand_in = self.stand_in_for(code)
			if not stand_in:
				return
			self.pressed[code] = stand_in
		else:
			stand_in = self.pressed.pop(code, None)
			if not stand_in:
				return

		vk, scan, extended = stand_in
		await self.send(f'key {vk:x} {scan:x} {extended} {int(down)}')

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
				await session.stop()
				session = None
				missing_since = None

		try:
			await asyncio.wait_for(stopping.wait(), options.poll)
		except asyncio.TimeoutError:
			pass

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
	asyncio.run(run(parse_options()))
