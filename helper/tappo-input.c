// NtUserSendHardwareInput is only in Wine's private ntuser.h, which won't compile outside Wine itself.
#define __WINESRC__
#include <windows.h>
#include <winternl.h>
#include <ntuser.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define STAND_IN_CLASS "TappoFullscreenStandIn"

enum {
	WM_STAND_IN_COVER = WM_APP + 1,
	WM_STAND_IN_UNCOVER,
	WM_STAND_IN_REFOCUS,
	WM_STAND_IN_RELEASE,
};

struct window_search {
	ULONG_PTR x_window;
	HWND found;
};

static BOOL verbose;
static BOOL hardware;
static HWND stand_in;

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

static BOOL CALLBACK match_x_window(HWND hwnd, LPARAM lparam)
{
	struct window_search *search = (struct window_search *)lparam;

	if ((ULONG_PTR)GetPropA(hwnd, "__wine_x11_whole_window") != search->x_window) return TRUE;
	search->found = hwnd;
	return FALSE;
}

static void take_foreground(HWND hwnd) {
	if (GetForegroundWindow() == hwnd) return;
	// Wine refuses SetForegroundWindow from a process that isn't in front once the front one has newer input, and handing the desktop the foreground counts as input.
	// The internal call skips that check; it's the one winex11 uses for the window manager's own focus changes.
	if (!NtUserSetForegroundWindowInternal(hwnd)) {
		fprintf(stderr, "making %p Wine's foreground window failed\n", hwnd);
		return;
	}
	if (verbose) fprintf(stderr, "foreground %p\n", hwnd);
}

static LRESULT CALLBACK stand_in_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam)
{
	static BOOL covering;
	struct window_search search;
	HWND foreground;

	switch (message) {
	case WM_STAND_IN_COVER:
		covering = TRUE;
		SetWindowPos(
			hwnd, NULL, (short)LOWORD(wparam), (short)HIWORD(wparam), LOWORD(lparam), HIWORD(lparam),
			SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW
		);
		take_foreground(hwnd);
		return 0;
	case WM_STAND_IN_UNCOVER:
		covering = FALSE;
		SetWindowPos(hwnd, NULL, 0, 0, 1, 1, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOMOVE);
		// Tappo only leaves game mode for a windowed app in front, and Wine's stale foreground can be one of Tappo's own windows.
		if (GetForegroundWindow() != GetDesktopWindow()) take_foreground(hwnd);
		return 0;
	case WM_ACTIVATE:
		if (LOWORD(wparam) == WA_INACTIVE && covering) PostMessageA(hwnd, WM_STAND_IN_REFOCUS, 0, 0);
		break;
	case WM_STAND_IN_REFOCUS:
		// Wine hands its foreground to the desktop whenever the window manager activates a window that isn't Wine's.
		foreground = GetForegroundWindow();
		if (covering && (!foreground || foreground == GetDesktopWindow())) take_foreground(hwnd);
		return 0;
	case WM_STAND_IN_RELEASE:
		if (GetForegroundWindow() != hwnd) return 0;
		// Wine only follows _NET_ACTIVE_WINDOW off this window, and if the window manager left that alone, the keys for the window X focused would come here.
		search.x_window = wparam;
		search.found = NULL;
		EnumWindows(match_x_window, (LPARAM)&search);
		if (search.found) take_foreground(search.found);
		return 0;
	case WM_CLOSE:
	case WM_SYSCOMMAND:
		// It has Wine's keyboard while it's in front, so an Alt+F4 sent with --real-keys would close it.
		return 0;
	}
	return DefWindowProcA(hwnd, message, wparam, lparam);
}

static DWORD WINAPI stand_in_thread(void *ready)
{
	WNDCLASSA window_class = {0};
	MSG message;

	window_class.lpfnWndProc = stand_in_proc;
	window_class.hInstance = GetModuleHandleA(NULL);
	window_class.lpszClassName = STAND_IN_CLASS;
	RegisterClassA(&window_class);
	// Tappo counts an app as fullscreen when Wine's foreground window is visible and covers a screen.
	// winex11 holds back mapping a layered window until its layered attributes are set, so with none this one never reaches X: nothing shows, and KWin is never asked to focus it.
	// Transparent as well, so Wine's own hit tests go straight through it.
	stand_in = CreateWindowExA(
		WS_EX_LAYERED | WS_EX_TRANSPARENT, STAND_IN_CLASS, "", WS_POPUP, 0, 0, 1, 1, NULL, NULL, window_class.hInstance, NULL
	);
	SetEvent(ready);
	if (!stand_in) return 1;
	while (GetMessageA(&message, NULL, 0, 0) > 0) DispatchMessageA(&message);
	return 0;
}

static BOOL start_stand_in(void)
{
	HANDLE ready, thread;

	if (stand_in) return TRUE;
	ready = CreateEventA(NULL, TRUE, FALSE, NULL);
	thread = CreateThread(NULL, 0, stand_in_thread, ready, 0, NULL);
	if (thread) {
		WaitForSingleObject(ready, INFINITE);
		CloseHandle(thread);
	}
	CloseHandle(ready);
	if (!stand_in) fprintf(stderr, "creating the fullscreen stand-in window failed\n");
	return stand_in != NULL;
}

static void cover_screen(int root_x, int root_y, int width, int height)
{
	int x = root_x + GetSystemMetrics(SM_XVIRTUALSCREEN);
	int y = root_y + GetSystemMetrics(SM_YVIRTUALSCREEN);

	if (!start_stand_in()) return;
	PostMessageA(stand_in, WM_STAND_IN_COVER, MAKEWPARAM(x, y), MAKELPARAM(width, height));
	if (verbose) fprintf(stderr, "covering %d,%d %dx%d\n", x, y, width, height);
}

static void handle_line(char *line)
{
	unsigned int vk, scan, extended, down, x_window;
	char mode[8] = "";
	int x, y, width, height;

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
	if (sscanf(line, "fullscreen %d %d %d %d", &x, &y, &width, &height) == 4) {
		cover_screen(x, y, width, height);
		return;
	}
	if (!strcmp(line, "windowed\n")) {
		if (stand_in) PostMessageA(stand_in, WM_STAND_IN_UNCOVER, 0, 0);
		return;
	}
	if (sscanf(line, "focus %x", &x_window) == 1) {
		if (stand_in) PostMessageA(stand_in, WM_STAND_IN_RELEASE, x_window, 0);
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
