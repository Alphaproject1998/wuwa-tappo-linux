import hashlib
import struct
import sys

# Wine bug 60225: RevokeDragDrop releases a drop target another process registered, so Tappo crashed revoking WebView2's windows.
# As RegisterDragDrop already does, a window owned by another process now gets DRAGDROP_E_INVALIDHWND.

EXPECTED_SHA256 = sys.argv[3]
IMAGE_BASE = 0x180000000
TEXT_HEADER = 0x188
PATCH_SITE = 0x39227
CAVE = 0x58a40
INVALID_HWND_PATH = 0x39350
RESUME = 0x39238
IMP_IS_WINDOW = 0x953c0
IMP_GET_WINDOW_THREAD_PROCESS_ID = 0x953b0

ORIGINAL_SITE = bytes.fromhex('4889d9ff1590c10500' '85c0' '0f8418010000')


def rel32(target, next_instruction):
	return struct.pack('<i', target - next_instruction)


def build_cave():
	code = bytearray()

	def emit(opcode, target=None):
		code.extend(opcode)
		if target is not None:
			code.extend(rel32(target, CAVE + len(code) + 4))

	emit(bytes.fromhex('4889d9'))
	emit(bytes.fromhex('ff15'), IMP_IS_WINDOW)
	emit(bytes.fromhex('85c0'))
	emit(bytes.fromhex('0f84'), INVALID_HWND_PATH)
	emit(bytes.fromhex('c744243000000000'))
	emit(bytes.fromhex('4889d9'))
	emit(bytes.fromhex('488d542430'))
	emit(bytes.fromhex('ff15'), IMP_GET_WINDOW_THREAD_PROCESS_ID)
	emit(bytes.fromhex('65488b042530000000'))
	emit(bytes.fromhex('8b4040'))
	emit(bytes.fromhex('3b442430'))
	emit(bytes.fromhex('0f85'), INVALID_HWND_PATH)
	emit(bytes.fromhex('e9'), RESUME)
	return bytes(code)


def main():
	source, destination = sys.argv[1], sys.argv[2]
	data = bytearray(open(source, 'rb').read())

	digest = hashlib.sha256(data).hexdigest()
	if digest != EXPECTED_SHA256:
		sys.exit(f'unexpected ole32.dll ({digest}), offsets would be wrong')
	if data[PATCH_SITE:PATCH_SITE + len(ORIGINAL_SITE)] != ORIGINAL_SITE:
		sys.exit('patch site does not match')

	cave = build_cave()
	if any(data[CAVE:CAVE + len(cave) + 16]):
		sys.exit('cave is not empty')
	data[CAVE:CAVE + len(cave)] = cave

	site = bytes.fromhex('e9') + rel32(CAVE, PATCH_SITE + 5)
	site += b'\x90' * (len(ORIGINAL_SITE) - len(site))
	data[PATCH_SITE:PATCH_SITE + len(site)] = site

	text_vsize = struct.unpack_from('<I', data, TEXT_HEADER + 8)[0]
	needed = (CAVE + len(cave) - 0x1000 + 0xf) & ~0xf
	if needed > text_vsize:
		struct.pack_into('<I', data, TEXT_HEADER + 8, needed)

	# Without the builtin signature Wine treats the file as a native DLL instead of swapping in its own copy.
	if data[0x40:0x50] != b'Wine builtin DLL':
		sys.exit('builtin signature not where expected')
	data[0x40:0x50] = b'\0' * 16

	open(destination, 'wb').write(data)
	print(f'patched {len(cave)} cave bytes at {IMAGE_BASE + CAVE:#x}, site {IMAGE_BASE + PATCH_SITE:#x}')


main()
