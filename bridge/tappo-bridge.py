#!/usr/bin/env python3
import argparse
import asyncio
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


class Session:
	def __init__(self, options, prefix):
		self.options = options
		self.prefix = prefix
		self.helper = None
		self.readers = {}
		self.pressed = {}
		self.last_hand = 'right'

	async def start(self):
		await self.start_helper()
		self.scanner = asyncio.create_task(self.scan_devices())

	async def stop(self):
		self.scanner.cancel()
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

		if self.helper.returncode is not None:
			if not find_app(self.options.process_name, self.prefix):
				return
			log(f'helper exited with {self.helper.returncode}, restarting')
			await self.start_helper()

		vk, scan, extended = stand_in
		try:
			self.helper.stdin.write(f'key {vk:x} {scan:x} {extended} {int(down)}\n'.encode())
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
