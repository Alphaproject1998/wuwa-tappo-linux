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

static void send_key(WORD vk, WORD scan, BOOL extended, BOOL down)
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
	sent = hardware ? !NtUserSendHardwareInput(NULL, 0, &input, 0) : SendInput(1, &input, sizeof(input)) == 1;
	if (!sent) {
		fprintf(stderr, "sending vk %#x failed\n", vk);
		return;
	}
	if (verbose) fprintf(stderr, "key vk=%#x scan=%#x ext=%d down=%d\n", vk, scan, extended, down);
}

static void handle_line(char *line)
{
	unsigned int vk, scan, extended, down;

	if (sscanf(line, "key %x %x %u %u", &vk, &scan, &extended, &down) == 4) {
		send_key((WORD)vk, (WORD)scan, extended != 0, down != 0);
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

	while (fgets(line, sizeof(line), stdin)) handle_line(line);
	return 0;
}
