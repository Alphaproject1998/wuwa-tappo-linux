import hashlib
import struct
import sys

# In these builds file offsets equal virtual addresses, so each number below is both where to write and where to jump.
TEXT_SEGMENT_SIZES_AT = 0x98

WIN32U_CALL_HWND_PARAM = 0x131ee0
WIN32U_DELETE_OBJECT = 0x9aa20
WIN32U_SET_WINDOW_RGN_BODY = 0x136d34
WIN32U_RESUME = 0x136d3f
WIN32U_PROLOGUE = bytes.fromhex('554889e54881ece0000000')
WIN32U_CAVE = 0x13f7b0
WIN32U_TEXT_SEGMENT = 0xb000
WIN32U_TEXT_SEGMENT_SIZE = 0x1347ad

WINEX11_CREATE_CURSOR_CHECK = 0x26724
WINEX11_CACHE_CURSOR = 0x26758
WINEX11_GIVE_UP = 0x26729
WINEX11_X_CREATE_FONT_CURSOR = 0x6f440
WINEX11_CAVE = 0x51b30
WINEX11_TEXT_SEGMENT = 0x7000
WINEX11_TEXT_SEGMENT_SIZE = 0x4ab21
XC_LEFT_PTR = 68

WINESERVER_DETACH_CREATE_INPUT = 0x3a718
WINESERVER_CREATE_THREAD_INPUT = 0x35490
WINESERVER_CAVE = 0x7b370
WINESERVER_TEXT_SEGMENT = 0x3000
WINESERVER_TEXT_SEGMENT_SIZE = 0x78361
WINESERVER_TEXT_SEGMENT_SIZES_AT = 0x108

NTDLL_SET_PAGE_VPROT_MEMSET = 0x631e0
NTDLL_SET_PAGE_VPROT_TAIL_MEMSET = 0x6321f
NTDLL_MEMSET_GOT = 0x93be8
NTDLL_THREAD_NAME_PID_CHECK = 0x5c96e
NTDLL_THREAD_NAME_OTHER_PROCESS = 0x5c97d
NTDLL_THREAD_NAME_DONE = 0x5c99d
NTDLL_THREAD_NAME_WRITE = 0x5c9a8
NTDLL_FILL_CAVE = 0x71d50
NTDLL_NAME_CAVE = 0x71de0
NTDLL_TEXT_SEGMENT = 0x8000
NTDLL_TEXT_SEGMENT_SIZE = 0x69d41
NTDLL_RODATA = 0x72000


def rel32(target, next_instruction):
	return struct.pack('<i', target - next_instruction)


def assembler(base):
	code = bytearray()

	def emit(opcode, target=None):
		code.extend(opcode)
		if target is not None:
			code.extend(rel32(target, base + len(code) + 4))

	return code, emit


def grow_text_segment(segment, size, end, sizes_at=TEXT_SEGMENT_SIZES_AT):
	return (sizes_at, struct.pack('<QQ', size, size), struct.pack('<QQ', end - segment, end - segment))


def stage_region_sites():
	cave, emit = assembler(WIN32U_CAVE)
	emit(bytes.fromhex('575652'))
	emit(bytes.fromhex('ba0a000000'))
	emit(bytes.fromhex('48c7c6ecffffff'))
	emit(bytes.fromhex('e8'), WIN32U_CALL_HWND_PARAM)
	emit(bytes.fromhex('5a5e5f'))
	emit(bytes.fromhex('2520000800'))
	emit(bytes.fromhex('3d20000800'))
	emit(bytes.fromhex('74') + bytes([len(WIN32U_PROLOGUE) + 5]))
	emit(WIN32U_PROLOGUE)
	emit(bytes.fromhex('e9'), WIN32U_RESUME)
	emit(bytes.fromhex('4885f6740a'))
	emit(bytes.fromhex('504889f7'))
	emit(bytes.fromhex('e8'), WIN32U_DELETE_OBJECT)
	emit(bytes.fromhex('58'))
	emit(bytes.fromhex('b801000000c3'))

	jump = bytes.fromhex('e9') + rel32(WIN32U_CAVE, WIN32U_SET_WINDOW_RGN_BODY + 5)
	jump += bytes.fromhex('660f1f440000')
	return [
		(WIN32U_SET_WINDOW_RGN_BODY, WIN32U_PROLOGUE, jump),
		(WIN32U_CAVE, bytes(len(cave)), bytes(cave)),
		grow_text_segment(WIN32U_TEXT_SEGMENT, WIN32U_TEXT_SEGMENT_SIZE, WIN32U_CAVE + len(cave)),
	]


def fallback_cursor_sites():
	cave, emit = assembler(WINEX11_CAVE)
	emit(bytes.fromhex('4885c0'))
	emit(bytes.fromhex('0f85'), WINEX11_CACHE_CURSOR)
	emit(bytes.fromhex('488b3b'))
	emit(bytes.fromhex('be') + struct.pack('<I', XC_LEFT_PTR))
	emit(bytes.fromhex('ff15'), WINEX11_X_CREATE_FONT_CURSOR)
	emit(bytes.fromhex('488945d0'))
	emit(bytes.fromhex('4885c0'))
	emit(bytes.fromhex('0f85'), WINEX11_CACHE_CURSOR)
	emit(bytes.fromhex('e9'), WINEX11_GIVE_UP)

	return [
		(WINEX11_CREATE_CURSOR_CHECK, bytes.fromhex('4885c0752f'), bytes.fromhex('e9') + rel32(WINEX11_CAVE, WINEX11_CREATE_CURSOR_CHECK + 5)),
		(WINEX11_CAVE, bytes(len(cave)), bytes(cave)),
		grow_text_segment(WINEX11_TEXT_SEGMENT, WINEX11_TEXT_SEGMENT_SIZE, WINEX11_CAVE + len(cave)),
	]


def thread_input_detach_sites():
	cave, emit = assembler(WINESERVER_CAVE)
	emit(bytes.fromhex('4d85ff743f'))
	emit(bytes.fromhex('31c9498b4500'))
	emit(bytes.fromhex('4c39e87427'))
	emit(bytes.fromhex('488b50104c8b4018'))
	emit(bytes.fromhex('4c39f274054d39f0750380c901'))
	emit(bytes.fromhex('4c39fa74054d39f8750380c902'))
	emit(bytes.fromhex('488b00ebd4'))
	emit(bytes.fromhex('f6c1017408f6c10275084d89fe'))
	emit(bytes.fromhex('e9'), WINESERVER_CREATE_THREAD_INPUT)
	emit(bytes.fromhex('31c0c3'))

	call = WINESERVER_DETACH_CREATE_INPUT
	return [
		(call, bytes.fromhex('e8') + rel32(WINESERVER_CREATE_THREAD_INPUT, call + 5), bytes.fromhex('e8') + rel32(WINESERVER_CAVE, call + 5)),
		(WINESERVER_CAVE, bytes(len(cave)), bytes(cave)),
		grow_text_segment(WINESERVER_TEXT_SEGMENT, WINESERVER_TEXT_SEGMENT_SIZE, WINESERVER_CAVE + len(cave), WINESERVER_TEXT_SEGMENT_SIZES_AT),
	]


def page_vprot_fill_sites():
	cave, emit = assembler(NTDLL_FILL_CAVE)
	emit(bytes.fromhex('400fb6c6' '49b80101010101010101' '4c0fafc0' '4c8d0c17'))
	emit(bytes.fromhex('4c39cf7365'))
	emit(bytes.fromhex('4989fa' '4981caff0f0000' '49ffc2' '4d39ca' '4d0f47d1' '4889f9'))
	emit(bytes.fromhex('4c8d5908' '4d39d3770a' '4c39017513' '4c89d9ebed'))
	emit(bytes.fromhex('4c39d17331' '38017505' '48ffc1ebf2'))
	emit(bytes.fromhex('4151415256' '4c89d2' '4829fa'))
	emit(bytes.fromhex('ff15'), NTDLL_MEMSET_GOT)
	emit(bytes.fromhex('5e415a4159' '400fb6c6' '49b80101010101010101' '4c0fafc0'))
	emit(bytes.fromhex('4c89d7eb96'))
	emit(bytes.fromhex('c3'))

	call, tail = NTDLL_SET_PAGE_VPROT_MEMSET, NTDLL_SET_PAGE_VPROT_TAIL_MEMSET
	return [
		(call, bytes.fromhex('ff15020a0300'), bytes.fromhex('e8') + rel32(NTDLL_FILL_CAVE, call + 5) + b'\x90'),
		(tail, bytes.fromhex('ff25c3090300'), bytes.fromhex('e9') + rel32(NTDLL_FILL_CAVE, tail + 5) + b'\x90'),
		(NTDLL_FILL_CAVE, bytes(len(cave)), bytes(cave)),
	]


def main_thread_name_sites():
	cave, emit = assembler(NTDLL_NAME_CAVE)
	emit(bytes.fromhex('3b850cffffff'))
	emit(bytes.fromhex('0f84'), NTDLL_THREAD_NAME_DONE)
	emit(bytes.fromhex('3b8508ffffff' '448b950cffffff'))
	emit(bytes.fromhex('0f84'), NTDLL_THREAD_NAME_WRITE)
	emit(bytes.fromhex('e9'), NTDLL_THREAD_NAME_OTHER_PROCESS)

	check = NTDLL_THREAD_NAME_PID_CHECK
	return [
		(check, bytes.fromhex('3b8508ffffff' '448b950cffffff' '742b'), bytes.fromhex('e9') + rel32(NTDLL_NAME_CAVE, check + 5) + b'\x90'),
		(NTDLL_NAME_CAVE, bytes(len(cave)), bytes(cave)),
	]


PATCHES = {
	'bin/wineserver': {
		'sha256': 'c9a0c615ddd8ba168b9fdf5efaaa4769cb2369782c766b579157ed0a5520977e',
		'sites': [
			# assign_thread_input() clears the key state of the input it attaches to, and Tappo's keyboard hook attaches on every key, so a held Shift read as up.
			(0x35b16, bytes.fromhex('0f1180f4000000'), bytes.fromhex('eb46')),
			# detach_thread_input() split a thread off even while other attachments still linked it, cutting Tappo's UI thread off from WebView2's cursors.
			# Now a linked thread keeps the shared input, and the other thread gets the fresh one only if it has no links left.
			*thread_input_detach_sites(),
		],
	},
	'lib/wine/x86_64-unix/ntdll.so': {
		'sha256': '98ef0d24e92466eda08a5585f5a4af5514c02d8c0d36d241d26af81fedfda0d7',
		'sites': [
			# Each page of a new view gets a protection byte even if only reserved, and a WebView2 renderer reserves about 1.6 TB, so each one held 400 MB of zeros.
			# set_page_vprot() now skips any 4 KB of the table that already holds the value.
			*page_vprot_fill_sites(),
			# set_native_thread_name() renamed main threads too, which on Linux renames the whole process, so Tappo showed up as "main" and WebView2 as CrRendererMain.
			# The main thread now keeps the exe name, as in Chromium's own Linux port.
			*main_thread_name_sites(),
			grow_text_segment(NTDLL_TEXT_SEGMENT, NTDLL_TEXT_SEGMENT_SIZE, NTDLL_RODATA),
		],
	},
	'lib/wine/x86_64-unix/win32u.so': {
		'sha256': 'ba376f9895f2ab99d7d01b61ab4cae05ddabc30d8c354d150584c7c81f4d7c12',
		'sites': [
			# window_surface_create() filled new surfaces white, and the stage's surface is never drawn but is flushed over WebView2 on every SetWindowRgn.
			(0x23d0d, bytes.fromhex('beff000000'), bytes.fromhex('be00000000')),
			# Even zeroed, that flush blinked the pet out while dragged.
			# The stage is WS_EX_TRANSPARENT|WS_EX_LAYERED and already has an empty input shape, so SetWindowRgn on such a window now just frees the region.
			*stage_region_sites(),
		],
	},
	'lib/wine/x86_64-unix/winex11.so': {
		'sha256': '4ef402a67c64004db1bbf2d6f3e60eabdf3a5e3de10e2dc721d4da4e5a06605f',
		'sites': [
			# WebView2's cursors reach set_window_cursor() as another process's handles, which create_cursor() can't read, so the panel kept a stale resize cursor; those now give the arrow.
			*fallback_cursor_sites(),
		],
	},
}


def patch_file(path, patch):
	data = bytearray(open(path, 'rb').read())

	digest = hashlib.sha256(data).hexdigest()
	if digest != patch['sha256']:
		sys.exit(f'unexpected {path} ({digest}), offsets would be wrong')

	for start, original, replacement in patch['sites']:
		if data[start:start + len(original)] != original:
			sys.exit(f'{path}: patch site {start:#x} does not match')
		data[start:start + len(replacement)] = replacement

	open(path, 'wb').write(data)


def main():
	runtime = sys.argv[1]
	for relative_path, patch in PATCHES.items():
		patch_file(f'{runtime}/{relative_path}', patch)
	print(f'patched {len(PATCHES)} files in {runtime}')


main()
