import glob
import os
import struct
import sys

# Wine only mmaps sections that start on a page boundary in the file, so msedge.dll's 512-byte layout gave every WebView2 process its own 340 MB copy.
# Wine also relocates an ASLR DLL in each process, which made half of .rdata private again.

PAGE = 0x1000
SECURITY_DIRECTORY = 4
DEBUG_DIRECTORY = 6
IMAGE_FILE_DLL = 0x2000
DYNAMIC_BASE = 0x40


def align_up(value, alignment):
	return (value + alignment - 1) & ~(alignment - 1)


def parse(data):
	pe = struct.unpack_from('<I', data, 0x3c)[0]
	if data[pe:pe + 4] != b'PE\0\0':
		return None
	optional = pe + 24
	optional_size = struct.unpack_from('<H', data, pe + 20)[0]
	magic = struct.unpack_from('<H', data, optional)[0]
	return {
		'pe': pe,
		'optional': optional,
		'sections_at': optional + optional_size,
		'section_count': struct.unpack_from('<H', data, pe + 6)[0],
		'characteristics': struct.unpack_from('<H', data, pe + 22)[0],
		'directories': optional + (112 if magic == 0x20b else 96),
		'section_alignment': struct.unpack_from('<I', data, optional + 32)[0],
		'file_alignment': struct.unpack_from('<I', data, optional + 36)[0],
		'headers_size': struct.unpack_from('<I', data, optional + 60)[0],
		'dll_characteristics': struct.unpack_from('<H', data, optional + 70)[0],
	}


def section_headers(data, layout):
	for i in range(layout['section_count']):
		at = layout['sections_at'] + i * 40
		virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from('<IIII', data, at + 8)
		yield at, virtual_size, virtual_address, raw_size, raw_pointer


def is_aligned(data, layout):
	if layout['file_alignment'] < PAGE:
		return False
	return all(not raw_size or raw_pointer % PAGE == 0 for _, _, _, raw_size, raw_pointer in section_headers(data, layout))


def wants_fixed_base(layout):
	return layout['characteristics'] & IMAGE_FILE_DLL and layout['dll_characteristics'] & DYNAMIC_BASE


def pe_checksum(data):
	# 2^16 is 1 mod 0xffff, so the folded sum of 16-bit words is the whole file read as one little-endian number, mod 0xffff.
	step = 1 << 20
	view = memoryview(data)
	total = 0
	for start in range(0, len(data), step):
		total += int.from_bytes(view[start:start + step], 'little') % 0xffff
	folded = total % 0xffff or 0xffff
	return (folded + len(data)) & 0xffffffff


def realign(data, layout):
	if layout['section_alignment'] < PAGE:
		return None

	sections = sorted((raw_pointer, raw_size, at) for at, _, _, raw_size, raw_pointer in section_headers(data, layout) if raw_size and raw_pointer)
	if not sections or sections[0][0] < layout['headers_size']:
		return None
	for (pointer, size, _), (next_pointer, _, _) in zip(sections, sections[1:]):
		if pointer + size > next_pointer:
			return None

	headers_size = align_up(layout['headers_size'], PAGE)
	out = bytearray(data[:layout['headers_size']])
	out += bytes(headers_size - len(out))
	moves = []
	for pointer, size, at in sections:
		moves.append((pointer, pointer + size, len(out)))
		struct.pack_into('<II', out, at + 16, align_up(size, PAGE), len(out))
		out += data[pointer:pointer + size]
		out += bytes(align_up(len(out), PAGE) - len(out))

	sections_end = sections[-1][0] + sections[-1][1]
	if sections_end < len(data):
		# The certificate table that usually sits here has to start on 8 bytes.
		out += bytes(align_up(len(out), 8) - len(out))
		moves.append((sections_end, len(data), len(out)))
		out += data[sections_end:]

	def moved(offset):
		for start, end, new_start in moves:
			if start <= offset < end:
				return new_start + offset - start
		raise ValueError(f'file offset {offset:#x} is outside every section')

	security_at = layout['directories'] + SECURITY_DIRECTORY * 8
	security_offset, security_size = struct.unpack_from('<II', data, security_at)
	if security_size:
		struct.pack_into('<I', out, security_at, moved(security_offset))

	debug_rva, debug_size = struct.unpack_from('<II', data, layout['directories'] + DEBUG_DIRECTORY * 8)
	for _, _, virtual_address, raw_size, raw_pointer in section_headers(data, layout):
		if debug_size and virtual_address <= debug_rva < virtual_address + raw_size:
			first_entry = moved(raw_pointer + debug_rva - virtual_address)
			for entry in range(first_entry, first_entry + debug_size, 28):
				pointer = struct.unpack_from('<I', out, entry + 24)[0]
				if pointer:
					struct.pack_into('<I', out, entry + 24, moved(pointer))

	symbols_at = layout['pe'] + 12
	symbols = struct.unpack_from('<I', data, symbols_at)[0]
	if symbols:
		struct.pack_into('<I', out, symbols_at, moved(symbols))

	struct.pack_into('<I', out, layout['optional'] + 36, PAGE)
	struct.pack_into('<I', out, layout['optional'] + 60, headers_size)
	return out


def rewrite(data):
	layout = parse(data)
	if not layout:
		return None
	out = bytearray(data) if is_aligned(data, layout) else realign(data, layout)
	if out is None:
		return None

	if wants_fixed_base(layout):
		struct.pack_into('<H', out, layout['optional'] + 70, layout['dll_characteristics'] & ~DYNAMIC_BASE)
	checksum_at = layout['optional'] + 64
	struct.pack_into('<I', out, checksum_at, 0)
	struct.pack_into('<I', out, checksum_at, pe_checksum(out))
	return out


def align_file(path):
	with open(path, 'rb') as file:
		head = file.read(PAGE)
	layout = parse(head)
	if layout and layout['sections_at'] + layout['section_count'] * 40 <= len(head):
		if is_aligned(head, layout) and not wants_fixed_base(layout):
			return False

	data = open(path, 'rb').read()
	try:
		out = rewrite(data)
	except ValueError as error:
		print(f'align-pe: left {path} as it is: {error}', file=sys.stderr)
		return False
	if out is None:
		print(f'align-pe: left {path} as it is, its layout is not one this handles', file=sys.stderr)
		return False

	# A new file leaves EdgeCore's hardlink on the original, for Edge's updater.
	temporary = f'{path}.tappo-align'
	with open(temporary, 'wb') as file:
		file.write(out)
	os.chmod(temporary, os.stat(path).st_mode & 0o7777)
	os.replace(temporary, path)
	return True


def main():
	prefix = sys.argv[1]
	paths = sorted(glob.glob(f'{prefix}/drive_c/Program Files (x86)/Microsoft/EdgeWebView/Application/*/msedge.dll'))
	paths.append(f'{prefix}/drive_c/Program Files/WuwaTappoGlobal/WuwaTappo.exe')
	for path in paths:
		if os.path.exists(path) and align_file(path):
			print(f'align-pe: rewrote {path}')


main()
