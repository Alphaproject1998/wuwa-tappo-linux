# wuwa-tappo-linux

Wuwa Tappo on Linux, done properly.

Kuro put out Wuwa Tappo, the Bongo Cat style Wuthering Waves desktop pet where little Resonators sit on your desktop and tap along to whatever you're typing. It's Windows only. I play on Linux. So this exists.

It gets Tappo running on KDE Plasma under Wine and behaving the way it does on Windows: it starts, it counts your taps from every app instead of only its own window, the black box around it is gone, and its panels actually work.

**So far it's only been tested on KDE Plasma.** Very little of it is tied to KDE: the Wine fixes, the transparency, the bridge and the menu entry should work anywhere. The clean shutdown at logout is the part that isn't, since it goes through Plasma's logout scripts. If you get it going on GNOME, Hyprland or anything else, I'd love to hear about it (see [Contributing](#contributing)).

Unofficial, and nothing to do with Kuro Games. None of the app is in this repo either; you install it yourself from Kuro's own installer, the same one Windows players get.

Here it is on my desktop, multiplayer included:

https://github.com/user-attachments/assets/b3ad5a2c-d901-4bc0-a26e-a653fe1cf484

## What's broken without it, and what fixes it

- **It crashes on startup.** Wine bug 60225 takes Tappo down as it starts. A patched `ole32.dll` goes into the prefix, and only Tappo uses it.
- **It only hears you while it has focus.** On Wayland, Wine never sees input aimed at other apps, so the pet only counted clicks on itself, which is the one time you're not typing. The bridge reads your keyboards and mice and sends Tappo a stand-in key for every press. More on that [below](#the-bridge).
- **It stays see-through after you hover it.** With "Turns see-through on hover" on, moving off the pet left it faded until you hovered another pet or a panel. Wine only finds out where the pointer is while it's over one of the windows Xwayland handles, so Tappo kept seeing it wherever it left. The bridge notices when the pointer has left them all and moves the cursor Wine sees to the nearest spot clear of them. Your real cursor stays where it is.
- **It sits in a black box.** Wine draws Tappo into windows with no alpha channel, so everything that should be see-through arrives as black. The prefix is set up so Wine gives everything in it 32-bit windows instead, and the transparency comes through the same as on Windows, soft edges included.
- **No capitals in text boxes, white flashes and blinking when you drag the pet, and a resize cursor over the whole settings panel.** All Wine bugs, fixed by patching a private copy of Wine that only Tappo uses. Your system Wine isn't touched.
- **The panels lag behind.** On NVIDIA, Wine copies each frame of a panel before it has finished arriving, so a tab or button shows its change late, or not until you move the mouse. The launcher turns off explicit sync in NVIDIA's EGL for Tappo only (`__NV_X11_DISABLE_EXPLICIT_SYNC=1`), so each frame is finished before Wine copies it.
- **It eats about 8 GB of RAM.** Nearly all of that was Wine: it gave every WebView2 process its own copy of a 355 MB DLL, and every renderer 400 MB of bookkeeping for memory it never touches. It's about 1.5 GB now, see [Memory](#memory).
- **You can't tell what's what in a process list.** Wine renames a process whenever a program names its main thread, so Tappo showed up as `main` and its WebView2 processes as `CrRendererMain`, `CrGpuMain` and friends. They keep their own names now: `WuwaTappo.exe`, `msedgewebview2.` (Linux cuts process names at 15 characters), and Tappo's Wine server runs as `tappo-wineserver`, the bridge as `tappo-bridge` and its helper as `tappo-input.exe`. The rest, `explorer.exe`, `services.exe` and so on, are Wine's own, running in Tappo's prefix.
- **No proper menu entry.** You get one that starts Tappo, catches the `wuwa-buddy://` link the browser login sends back, and can start it when you log in.

## Requirements

- KDE Plasma 6 on Wayland, ideally (see above). I'm on Arch with Plasma 6.8, so that's what it's tested on.
- Tested on an NVIDIA card only. AMD and Intel should behave the same, but I haven't been able to check, so the transparency in particular is unproven there.
- Arch, or something that can run Arch's Wine package. The Wine patches only fit one exact build (see [Wine version](#wine-version)), so the installer downloads that build from the Arch Linux Archive instead of using yours.
- A few packages:
  ```
  sudo pacman -S --needed wine python-evdev base-devel curl zstd
  ```
  `wine` is only there for the libraries the downloaded Wine needs.
- Your user in the `input` group, so the bridge can read your keyboards and mice. That lets every program you run read your keyboards too, so think about it on a shared machine.

The installer checks the last two and offers to sort them out. It also downloads the two Windows installers Tappo needs, both from their official sources: Microsoft's WebView2 runtime, and the latest Tappo from the same place [Kuro's download page](https://download-wutheringwaves-wuwatappo.kurogames-global.com/download/en/) gets it.

## Setup

```
git clone https://github.com/Alphaproject1998/wuwa-tappo-linux.git
cd wuwa-tappo-linux
./install.sh
```

It asks before anything that needs sudo. Kuro's installer opens partway through, so click through that, and if Wine offers to install Gecko while it sets up, let it. Kuro's installer may start Tappo when it finishes; the script closes that copy again, since the fixes aren't in yet. At the end it offers to start Tappo properly, and then you log in.

Steps that are already done get skipped, so if something goes wrong halfway, fix it and run it again.

`./install.sh --help` lists the options. The useful ones:

- `--prefix DIR` puts the Wine prefix somewhere other than `~/Games/wuwa-tappo`.
- `--tappo FILE` and `--webview2 FILE` use installers you already have instead of downloading them. Any Tappo version will do, since it updates itself.
- `--autostart` starts Tappo when you log in. You can switch it off again on the Autostart page in System Settings.

Besides the prefix, it installs the `ole32` fix, the bridge as a user service, a menu entry and a logout script. The logout script quits Tappo properly when you log out, restart or shut down from Plasma, the way Windows would, instead of leaving it to get killed, so your pet's state gets saved. It's listed under Logout Scripts on the Autostart page in System Settings, and `runtime/tappo-quit` does the same thing by hand.

If you use the Rounded Corners KWin effect, it also offers to exclude Tappo from it, since the effect draws an outline round Tappo's panels.

### By hand

The installer only runs `make` targets, so you can do the same yourself:

```
make runtime
make prefix
make webview2 WEBVIEW2_INSTALLER=$HOME/Downloads/MicrosoftEdgeWebView2RuntimeInstallerX64.exe
make app TAPPO_INSTALLER=$HOME/Downloads/WuwaTappo-global_1.0.2_x64-setup.exe
make install
```

- `make runtime` downloads Arch's `wine-11.20-1` package (about 80 MB), checks its hash and patches it into `build/wine-runtime`.
- `make prefix` creates the Wine prefix and sets it up for 32-bit windows. For a prefix other than `~/Games/wuwa-tappo`, pass `WINEPREFIX=/some/path` to every `make` command.
- `make webview2` and `make app` run the two installers. The WebView2 one is the **Evergreen Standalone Installer (x64)** from Microsoft's [WebView2 download page](https://developer.microsoft.com/microsoft-edge/webview2/).
- `make install` does the rest. Run it after `make app`, because the menu entry borrows the icon Wine pulled out of the installer. Each part of it is also its own target (`ole32`, `service`, `launcher`, `logout-script`).
- `make autostart` and `make corners-exclusion` are the optional extras.

### Updating

Quit Tappo from its tray icon first, then:

```
git pull
./install.sh
```

## The bridge

`bridge/tappo-bridge.py` runs as the `tappo-bridge` user service. It does nothing until Tappo is running, and stops again 15 seconds after it closes.

While Tappo runs, the bridge reads every keyboard and mouse, but what it passes on is only a stand-in: F13 for keys on the left half of the keyboard, F14 for the right half and for mouse clicks. Tappo only counts a key once it's let go, and ignores a key it already sees held, so a key pressed while another stand-in is down gets the next free one instead: the odd F-keys up to F23 on the left, the even ones up to F24 on the right. Tappo itself just alternates paws on every tap, so the split only matters if it ever starts going by which side of the keyboard you hit. It never sends what you actually typed, because Wine would deliver real keys to whichever Tappo window it last focused, even while you're typing somewhere else. Nobody wants half a Discord message turning up in Tappo's settings.

The stand-ins also mean Tappo can't keylog you. On Windows its keyboard hook sees every key you press in every app; here all it ever gets from the bridge is that some key went down on one side of the keyboard, so even if it wanted to record what you type, there's nothing to record. The only real keys it sees are the ones you type into its own windows.

It also watches the pointer through its own connection to Xwayland, so it can move the cursor Wine sees once yours has left every X window. The same connection tells it when a click or key went to one of Tappo's own windows, which Wine already passes to Tappo, so that one gets no stand-in and doesn't count twice.

Options (`python3 bridge/tappo-bridge.py --help` has the full list):

- `--mouse-hand left|right|off`: which stand-in a click sends, or ignore clicks.
- `--real-keys`: send your real keys instead of stand-ins. Tappo then sees everything you type, and so does whichever of its windows Wine last focused.
- `--ignore NAME`: skip input devices whose name contains NAME. Repeatable; the default skips YubiKeys.
- `--hardware-input`: inject through Wine's private hardware-input call, so Tappo can't tell the presses were injected.
- `--cursor-poll SECONDS`: how often to check whether the pointer has left every X window, 0.1 by default. 0 stops the bridge moving Wine's cursor at all.
- `--cursor-margin PIXELS`: how far from any X window that moved cursor lands, when there's room. 32 by default.
- `--cursor-recheck SECONDS`: how often to look for a window that has opened over the moved cursor, and move it again if one has. 1 by default, 0 never looks.
- `--direct-wait SECONDS`: how long to wait for Xwayland to report a press while one of Tappo's windows has the keyboard or the pointer, to tell whether Wine got it itself. 0.05 by default; 0 sends a stand-in for everything.

To change them, run `systemctl --user edit tappo-bridge.service` and override `ExecStart`.

## Memory

Tappo is seven WebView2 windows, so it was never going to be tiny, but most of the 8 GB it used was Wine's doing:

- Wine can only share a DLL between processes by mapping it straight from the file, and it can only do that when every section starts on a 4 KB boundary in the file. `msedge.dll` uses 512-byte boundaries, so each of the eleven WebView2 processes that load it read in its own 335 MB copy. Before each start the launcher runs `runtime/align-pe.py`, which rewrites `msedge.dll` in the prefix with its sections moved onto 4 KB boundaries, the code and data themselves untouched. It does the same for `WuwaTappo.exe`, whose 64 MB of built-in web files then only take up memory once they're read. It also clears the ASLR flag on `msedge.dll`, because Wine relocates an ASLR DLL separately in every process, which made part of it private to each one again. Edge's own copy in `EdgeCore` stays as it was, and both files get rewritten again after an update.
- Wine keeps a byte of bookkeeping for every 4 KB page a process reserves, and wrote all of them even for pages that are never used. Each WebView2 renderer reserves about 1.6 TB, so that was 400 MB of zeros per renderer. The patched Wine skips writes that wouldn't change anything.
- With transparent huge pages set to `always`, the kernel hands out a whole 2 MB the first time Chromium touches any part of a 2 MB stretch, where Windows works in 4 KB pages. The launcher turns them off for Tappo only. `TAPPO_HUGE_PAGES=1` leaves them on.
- The first time you open the settings panel, NVIDIA's driver takes about 270 MB in WebView2's GPU process and holds on to it until Tappo closes. Left alone, glibc kept nearer 400 MB, so the launcher caps it at two malloc arenas. Setting your own `GLIBC_TUNABLES` replaces that.

What's left is mostly WebView2's seven renderers, one per window including the five you rarely see, which WebView2 won't let share a process, and NVIDIA's driver.

## Known issues

- Inside Tappo's panels, the hand and text cursors show as the normal arrow. The panels are WebView2, which runs as its own process, and Wine can't apply a cursor that belongs to another process.
- While your pointer is over other apps, Tappo sees the cursor parked just clear of the X windows, not where it really is.
- The pet's state can reset when Tappo is killed rather than quit. Quitting from the tray or logging out of Plasma keeps it, but a shutdown that skips Plasma, such as `systemctl poweroff` from a terminal, still kills it.

## Contributing

Help is very welcome, whether that's a bug report, a test on a setup I don't have or a PR. The things I can't check myself:

- Other desktops. Everything but the logout script should work outside Plasma, and the logout script needs an equivalent there.
- AMD and Intel graphics.
- Distros other than Arch, as long as they can run Arch's Wine package.
- Newer Wine. Each new build means finding the patch sites again, and getting the fixes into Wine itself would make most of this repo unnecessary.

If something breaks, open an issue with what you ran and what happened. Tappo logs to `drive_c/users/<you>/AppData/Local/com.kuro.wuwatappo.global/logs` in the prefix, and `journalctl --user -u tappo-bridge` shows the bridge.

## Wine version

Everything is pinned to Arch's `wine 11.20-1`. The patches are binary patches written for that exact build, and `runtime/patch-runtime.py` and `ole32/patch-ole32.py` refuse to touch any file whose hash they don't recognise. Moving to a newer Wine means finding the same spots in the new binaries and updating the offsets, hashes and package pin. Each patch notes which Wine function it changes.

## Uninstalling

Quit Tappo first, then:

```
./install.sh --uninstall
```

That removes the bridge, the menu and autostart entries, the logout script and what Wine added to your menus when Tappo was installed. It then asks whether to delete the prefix too, which deletes Tappo and everything it saved, so the default is no. `make uninstall` is the same without the question, leaving the prefix alone. After that, delete this folder, which takes the downloaded Wine with it.

## About AI

This was built with some help from an AI coding assistant. It did much of the digging through Wine, helped make the binary patches and hunted for bugs and other issues. I tested every change on my own desktop. Binary patches are hard to check by eye, so if anything looks wrong, please open an issue.

## Licence

GNU General Public License, version 2 or (at your option) any later version. See [LICENSE](LICENSE).

Wuwa Tappo and Wuthering Waves belong to Kuro Games.
