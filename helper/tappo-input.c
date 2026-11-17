// NtUserSendHardwareInput is only in Wine's private ntuser.h, which won't compile outside Wine itself.
#define __WINESRC__
#include <windows.h>
#include <winternl.h>
#include <ntuser.h>
#include <tlhelp32.h>
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

struct process {
	DWORD pid;
	char name[64];
};

static BOOL verbose;
static BOOL hardware;
static HWND stand_in;
static struct process processes[256];
static int process_count;

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

static const char *utf8(const WCHAR *text, char *buffer, int size)
{
	if (!WideCharToMultiByte(CP_UTF8, 0, text, -1, buffer, size, NULL, NULL)) buffer[0] = 0;
	return buffer;
}

static void load_processes(void)
{
	PROCESSENTRY32W entry;
	HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
	BOOL more;

	if (snapshot == INVALID_HANDLE_VALUE) return;
	entry.dwSize = sizeof(entry);
	for (more = Process32FirstW(snapshot, &entry); more; more = Process32NextW(snapshot, &entry)) {
		if (process_count == sizeof(processes) / sizeof(processes[0])) break;
		processes[process_count].pid = entry.th32ProcessID;
		utf8(entry.szExeFile, processes[process_count].name, sizeof(processes[process_count].name));
		process_count++;
	}
	CloseHandle(snapshot);
}

static const char *process_name(DWORD pid)
{
	int i;

	for (i = 0; i < process_count; i++) {
		if (processes[i].pid == pid) return processes[i].name;
	}
	return "?";
}

static void print_rect(const char *label, const RECT *rect)
{
	printf("%s%d,%d %dx%d", label, (int)rect->left, (int)rect->top, (int)(rect->right - rect->left), (int)(rect->bottom - rect->top));
}

static BOOL CALLBACK print_monitor(HMONITOR monitor, HDC hdc, RECT *clip, LPARAM lparam)
{
	MONITORINFOEXW info;
	DEVMODEW mode;
	char device[64];
	const RECT *area = &info.rcMonitor, *work = &info.rcWork;

	memset(&info, 0, sizeof(info));
	info.cbSize = sizeof(info);
	if (!GetMonitorInfoW(monitor, (MONITORINFO *)&info)) return TRUE;

	printf("Monitor %s%s: ", utf8(info.szDevice, device, sizeof(device)), info.dwFlags & MONITORINFOF_PRIMARY ? " (primary)" : "");
	print_rect("", area);
	print_rect(", work area ", work);
	memset(&mode, 0, sizeof(mode));
	mode.dmSize = sizeof(mode);
	if (EnumDisplaySettingsW(info.szDevice, ENUM_CURRENT_SETTINGS, &mode)) {
		printf(", mode %ux%u at %u Hz", (unsigned)mode.dmPelsWidth, (unsigned)mode.dmPelsHeight, (unsigned)mode.dmDisplayFrequency);
	}
	// Each screen getting another's work area is how Wine's stale display links showed after a sleep, and it pushed the pet off screen.
	if (work->left < area->left || work->top < area->top || work->right > area->right || work->bottom > area->bottom ||
		work->right <= work->left || work->bottom <= work->top) {
		printf(", and the work area isn't inside it");
	}
	printf("\n");
	return TRUE;
}

static BOOL CALLBACK print_window(HWND hwnd, LPARAM lparam)
{
	WCHAR text[256];
	char class_name[256], title[512];
	DWORD pid = 0;
	RECT rect;

	if (!GetClassNameW(hwnd, text, 256)) text[0] = 0;
	utf8(text, class_name, sizeof(class_name));
	if (lparam && !strcmp(class_name, "IME")) return TRUE;
	// InternalGetWindowText doesn't send the window a message, so a hung Tappo can't hang this as well.
	if (!InternalGetWindowText(hwnd, text, 256)) text[0] = 0;
	utf8(text, title, sizeof(title));
	GetWindowThreadProcessId(hwnd, &pid);
	SetRectEmpty(&rect);
	GetWindowRect(hwnd, &rect);

	printf("%s%p %s \"%s\" \"%s\" %s ", lparam ? "  " : "Foreground: ", hwnd, process_name(pid), class_name, title, IsWindowVisible(hwnd) ? "visible" : "hidden");
	print_rect("", &rect);
	printf(
		" style %#x exstyle %#x owner %p\n", (unsigned)GetWindowLongW(hwnd, GWL_STYLE), (unsigned)GetWindowLongW(hwnd, GWL_EXSTYLE),
		GetWindow(hwnd, GW_OWNER)
	);
	return TRUE;
}

static void print_state(void)
{
	HWND foreground = GetForegroundWindow();
	POINT cursor;

	load_processes();
	printf(
		"DPI %u, virtual screen %d,%d %dx%d\n", (unsigned)GetDpiForSystem(), GetSystemMetrics(SM_XVIRTUALSCREEN),
		GetSystemMetrics(SM_YVIRTUALSCREEN), GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN)
	);
	EnumDisplayMonitors(NULL, NULL, print_monitor, 0);
	if (GetCursorPos(&cursor)) printf("Cursor: %d,%d\n", (int)cursor.x, (int)cursor.y);
	if (foreground) print_window(foreground, 0);
	else printf("Foreground: none\n");
	printf("Top-level windows:\n");
	EnumWindows(print_window, 1);
	fflush(stdout);
}

int main(int argc, char **argv)
{
	char line[128];
	BOOL state = FALSE;
	int i;

	for (i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--verbose")) verbose = TRUE;
		else if (!strcmp(argv[i], "--hardware")) hardware = TRUE;
		else if (!strcmp(argv[i], "--state")) state = TRUE;
	}

	// The bridge works in X pixels, which a prefix set to another DPI would otherwise scale.
	SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

	if (state) {
		print_state();
		return 0;
	}

	while (fgets(line, sizeof(line), stdin)) handle_line(line);
	return 0;
}
