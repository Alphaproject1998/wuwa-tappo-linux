// NtUserSendHardwareInput is only in Wine's private ntuser.h, which won't compile outside Wine itself.
#define __WINESRC__
#include <windows.h>
#include <winternl.h>
#include <ntuser.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static BOOL verbose;
static BOOL hardware;

static void send_key(WORD vk, WORD scan, BOOL extended, BOOL down, BOOL as_hardware)
{
	INPUT input = {0};
	BOOL sent;

	if (!vk) vk = (WORD)MapVirtualKeyW(scan | (extended ? 0xe000 : 0), MAPVK_VSC_TO_VK_EX);

	input.type = INPUT_KEYBOARD;
	input.ki.wVk = vk;
	input.ki.wScan = scan;
	input.ki.dwFlags = (extended ? KEYEVENTF_EXTENDEDKEY : 0) | (down ? 0 : KEYEVENTF_KEYUP);
	input.ki.time = GetTickCount();

	// Declared BOOL, but it passes back send_hardware_message's NTSTATUS, so zero is success.
	sent = as_hardware ? !NtUserSendHardwareInput(NULL, 0, &input, 0) : SendInput(1, &input, sizeof(input)) == 1;
	if (!sent) {
		fprintf(stderr, "sending vk %#x failed\n", vk);
		return;
	}
	if (verbose) fprintf(stderr, "key vk=%#x scan=%#x ext=%d down=%d hw=%d\n", vk, scan, extended, down, as_hardware);
}

static void move_cursor(int root_x, int root_y)
{
	int x = root_x + GetSystemMetrics(SM_XVIRTUALSCREEN);
	int y = root_y + GetSystemMetrics(SM_YVIRTUALSCREEN);

	// SetCursorPos warps X's pointer too, so Wine's next look at X agrees with it.
	// An injected mouse move only lasts until that look, about 100 ms.
	if (!SetCursorPos(x, y)) {
		fprintf(stderr, "moving the cursor to %d,%d failed\n", x, y);
		return;
	}
	if (verbose) fprintf(stderr, "cursor x=%d y=%d\n", x, y);
}

static void end_menus(void)
{
	HWND menu = NULL;

	// A menu ends on a click Wine sees outside it, and Wine never sees one on a Wayland window.
	// WM_CANCELMODE is what EndMenu posts to the menu loop, and it works from another process.
	while ((menu = FindWindowExW(NULL, menu, (LPCWSTR)(ULONG_PTR)0x8000, NULL))) {
		if (!IsWindowVisible(menu)) continue;
		PostMessageW(menu, WM_CANCELMODE, 0, 0);
		if (verbose) fprintf(stderr, "ended menu %p\n", menu);
	}
}

static void handle_line(char *line)
{
	unsigned int vk, scan, extended, down;
	char mode[8] = "";
	int x, y;

	if (sscanf(line, "key %x %x %u %u %7s", &vk, &scan, &extended, &down, mode) >= 4) {
		send_key((WORD)vk, (WORD)scan, extended != 0, down != 0, hardware || !strcmp(mode, "hw"));
		return;
	}
	if (sscanf(line, "move %d %d", &x, &y) == 2) {
		move_cursor(x, y);
		return;
	}
	if (!strcmp(line, "endmenu\n")) {
		end_menus();
		return;
	}
	fprintf(stderr, "ignored: %s", line);
}

int main(int argc, char **argv)
{
	char line[128];
	int i;

	for (i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--verbose")) verbose = TRUE;
		else if (!strcmp(argv[i], "--hardware")) hardware = TRUE;
	}

	// The bridge works in X pixels, which a prefix set to another DPI would otherwise scale.
	SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

	while (fgets(line, sizeof(line), stdin)) handle_line(line);
	return 0;
}
