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
- **"Enter Game Mode in Fullscreen" never kicks in.** Tappo checks whether the window in front covers a screen, but the only windows Wine knows about are its own, so as far as Tappo could tell nothing fullscreen was ever in front. While a fullscreen app has the keyboard, the bridge puts an invisible window the size of that screen in front for Wine, which is all Tappo looks at.
- **It sits in a black box.** Wine draws Tappo into windows with no alpha channel, so everything that should be see-through arrives as black. The prefix is set up so Wine gives everything in it 32-bit windows instead, and the transparency comes through the same as on Windows, soft edges included.
- **No capitals in text boxes, white flashes and blinking when you drag the pet, a resize cursor over the whole settings panel, a tray icon that ignores clicks, a screen switch that blacks the screen out, steals focus from the app you're in and sometimes bounces the pet straight back, and none of Tappo's pop-up messages ever showing.** All Wine bugs, fixed by patching a private copy of Wine that only Tappo uses. Your system Wine isn't touched.
- **The panels lag behind.** On NVIDIA, Wine copies each frame of a panel before it has finished arriving, so a tab or button shows its change late, or not until you move the mouse. The launcher turns off explicit sync in NVIDIA's EGL for Tappo only (`__NV_X11_DISABLE_EXPLICIT_SYNC=1`), so each frame is finished before Wine copies it.
- **It eats about 8 GB of RAM.** Nearly all of that was Wine: it gave every WebView2 process its own copy of a 355 MB DLL, and every renderer 400 MB of bookkeeping for memory it never touches. It's about 1.5 GB now, see [Memory](#memory).
- **You can't tell what's what in a process list.** Wine renames a process whenever a program names its main thread, so Tappo showed up as `main` and its WebView2 processes as `CrRendererMain`, `CrGpuMain` and friends. They keep their own names now: `WuwaTappo.exe`, `msedgewebview2.` (Linux cuts process names at 15 characters), and Tappo's Wine server runs as `tappo-wineserver`, the bridge as `tappo-bridge` and its helper as `tappo-input.exe`. The rest, `explorer.exe`, `services.exe` and so on, are Wine's own, running in Tappo's prefix.
- **The pet vanishes after the PC sleeps.** Your screens disconnect while it sleeps, and when they came back Wine matched each one up with another screen's free space, so Tappo moved the pet off the edge of every screen but your main one. KWin also hides every window while the screens are off, and Wine took that for the pet being minimised and fought it, which could leave the pet's window with nothing managing it at all. Either way only quitting Tappo brought it back. And a wake can renumber your screens, which Tappo's side of Wine never noticed, so switching screens afterwards sent the pet to one screen and the part you click to another. Three more Wine bugs, patched in the same copy of Wine.
- **No proper menu entry, and a desktop shortcut that breaks things.** Kuro's installer always puts a shortcut on your desktop, and Wine turns it into one that starts Tappo on your system's Wine, without any of these fixes. If that Wine is newer, it also updates the prefix, which undoes the crash fix. Tappo's install also told your desktop to open `.reg`, `.vbs` and a few other Windows file types in Tappo's prefix. Instead you get a menu entry that starts Tappo, catches the `wuwa-buddy://` link the browser login sends back, and can start it when you log in, plus a desktop shortcut that works if you want one.

## Requirements

- KDE Plasma 6 on Wayland, ideally (see above). Tested on Arch (Plasma 6.8) and Fedora (Plasma 6.7).
- Tested on an NVIDIA and AMD GPU. Intel should behave the same - the transparency in particular is unproven there.
- Arch, Fedora, or something that can run Arch's Wine package. The Wine patches only fit one exact build (see [Wine version](#wine-version)), so the installer downloads that build from the Arch Linux Archive instead of using yours.
- A few packages:
  - On Arch:
    ```
    sudo pacman -S --needed wine python-evdev base-devel curl zstd
    ```
  - On Fedora:
    ```
    sudo dnf install wine python3-evdev gcc gcc-c++ make curl zstd
    ```
  `wine` is only there for the libraries the downloaded Wine needs.
- Your user in the `input` group, so the bridge can read your keyboards and mice. That lets every program you run read your keyboards too, so think about it on a shared machine.

The installer checks the last two and offers to sort them out. It also downloads the two Windows installers Tappo needs, both from their official sources: Microsoft's WebView2 runtime, and the latest Tappo from the same place [Kuro's download page](https://download-wutheringwaves-wuwatappo.kurogames-global.com/download/en/) gets it.

## Setup

```
sh -c "$(curl -sSfL https://raw.githubusercontent.com/Alphaproject1998/wuwa-tappo-linux/main/get.sh)"
```

That puts this repo in `~/.local/share/wuwa-tappo-linux` (with git if you have it, as a download if not) and runs `install.sh` from there. Running the same line again updates it. [`get.sh`](get.sh) is short if you'd rather read it first. Options for the installer go after a `--`, like `sh -c "$(curl ...)" -- --autostart`, and `TAPPO_DIR=/some/folder` in front of the whole line puts the repo somewhere else.

Or clone it yourself, which is all `get.sh` does anyway:

```
git clone https://github.com/Alphaproject1998/wuwa-tappo-linux.git
cd wuwa-tappo-linux
./install.sh
```

It asks before anything that needs sudo. Kuro's installer opens partway through, so click through that, and if Wine offers to install Gecko while it sets up, let it. Kuro's installer may start Tappo when it finishes; the script closes that copy again, since the fixes aren't in yet. It also swaps the desktop shortcut Kuro's installer makes for one that works, if you want one. At the end it offers to start Tappo properly, and then you log in.

Steps that are already done get skipped, so if something goes wrong halfway, fix it and run it again.

`./install.sh --help` lists the options. The useful ones:

- `--prefix DIR` puts the Wine prefix somewhere other than `~/Games/wuwa-tappo`. Updates and `--uninstall` stick to that prefix without being told again.
- `--tappo FILE` and `--webview2 FILE` use installers you already have instead of downloading them. Any Tappo version will do, since it updates itself.
- `--autostart` starts Tappo when you log in. You can switch it off again on the Autostart page in System Settings.
- `--desktop-shortcut` and `--no-desktop-shortcut` answer the desktop shortcut question before it's asked.
- `--no-menu-entry` keeps Tappo out of your app menu. The entry is still there, hidden, because the browser login comes back through it. Updates keep it hidden, and `--menu-entry` puts it back.
- `--keep-installers` keeps the WebView2 and Tappo installers it downloads. Otherwise they're deleted at the end, since they're only needed to set up a new prefix.

Besides the prefix, it installs the `ole32` fix, the bridge as a user service, a menu entry and a logout script. The logout script quits Tappo properly when you log out, restart or shut down from Plasma, the way Windows would, instead of leaving it to get killed, so your pet's state gets saved. It's listed under Logout Scripts on the Autostart page in System Settings, and `runtime/tappo-quit` does the same thing by hand.

If you use the Rounded Corners KWin effect, it also offers to exclude Tappo from it, since the effect draws an outline round Tappo's panels. It only asks during a new setup, so if you change your mind later, `make corners-exclusion` does the same thing.

To keep Tappo off your taskbar, pager and Alt+Tab, switch off Show Taskbar Icon in its settings. You can switch it back on whenever you need it for OBS capture.

If you're using any force blur KWin effects, like `kwin-effects-force-blur`, make sure to add an exclusion for the class `wuwatappo.exe` otherwise a blur will cover your entire screen

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
- `make autostart`, `make desktop-shortcut` and `make corners-exclusion` are the optional extras. `MENU_ENTRY=no` on `make install` hides the menu entry.

### Updating

Quit Tappo from its tray icon first, then run the `get.sh` line again. In a clone of your own, that's the same as:

```
git pull
./install.sh
```

## The bridge

`bridge/tappo-bridge.py` runs as the `tappo-bridge` user service. It does nothing until Tappo is running, and stops again 15 seconds after it closes.

While Tappo runs, the bridge reads every keyboard and mouse, but what it passes on is only a stand-in: F13 for keys on the left half of the keyboard, F14 for the right half and for mouse clicks. Tappo only counts a key once it's let go, and ignores a key it already sees held, so a key pressed while another stand-in is down gets the next free one instead: the odd F-keys up to F23 on the left, the even ones up to F24 on the right. Tappo itself just alternates paws on every tap, so the split only matters if it ever starts going by which side of the keyboard you hit. It never sends what you actually typed, because Wine would deliver real keys to whichever Tappo window it last focused, even while you're typing somewhere else. Nobody wants half a Discord message turning up in Tappo's settings.

The stand-ins also mean Tappo can't keylog you. On Windows its keyboard hook sees every key you press in every app; here all it ever gets from the bridge is that some key went down on one side of the keyboard, so even if it wanted to record what you type, there's nothing to record. The only real keys it sees are the ones you type into its own windows.

The exception is Tappo's own shortcuts (F7, F8 and F9 out of the box, or whatever you've set them to in its settings). The bridge reads them from Tappo's `shortcuts.json`, picks up changes as you make them, and sends a shortcut through as the real key with its modifiers, so it works from any app like it does on Windows. All Tappo learns from that is that you pressed one of its shortcuts. Only its three shortcuts are read, and one with no modifiers has to be an F-key to work from other apps: a bare letter would tell Tappo every time you typed it, so that one gets a stand-in like any other key and only works while Tappo has the keyboard. A stand-in is never one of your shortcuts either, so if you bind one to F13 to F24, the bridge leaves that key out of the stand-ins.

It also watches the pointer through its own connection to Xwayland, so it can move the cursor Wine sees once yours has left every X window. The same connection tells it when a click or key went to one of Tappo's own windows, which Wine already passes to Tappo, so that one gets no stand-in and doesn't count twice. If one of Tappo's windows has the keyboard but an F-key never reaches it, because Plasma has it bound as a global shortcut, the bridge sends that F-key through for real, so the shortcut box in Tappo's settings records the key you pressed. Plasma still runs its own shortcut too, so pick a key nothing else uses.

Tappo's menu, from the tray or a right-click on the pet, closes when you click outside it, the same as on Windows. Wine only closes it for clicks it sees, which leaves out every Wayland window, so the bridge closes it for any click that doesn't land on one of Tappo's windows.

Tappo treats every update as one you have to install: from the moment it finds one until it's installed, it counts no taps, won't open its panel and cuts its tray menu down to Exit. Its update window (titled 强制更新) is an ordinary window, though, so it can open behind everything else or on another screen, and the pet just looks broken. Once that window has been open for a minute, the bridge sends a notification saying an update is waiting, with a button that brings the window up.

It keeps the pet on its screen when your screens change, after a sleep or a monitor unplugged and back. Monitors come back from a sleep one at a time and in any order, and Plasma moves windows around as each one does, the pet's among them, so it could wake up on another screen. Tappo itself also starts on your main screen every time, wherever the pet was when it quit. The bridge remembers which screen the pet is on by its connector (DP-1 and so on), and keeps that in `~/.local/state/tappo-bridge/screens.json` so it lasts through restarts and logins. After a sleep, or whenever Tappo starts, it waits for the screens to settle and then presses Tappo's own switch-screen shortcut until the pet is back. Moving the pet to another screen yourself just changes the one it remembers. It puts the pet back on the right screen, not always in the same spot on it.

It also stands in for fullscreen apps, for Tappo's "Enter Game Mode in Fullscreen". Tappo goes by the window Wine has in front, which is never a Linux app, so while a fullscreen X window has the keyboard, the bridge's helper puts a window of its own in front for Wine, sized to that screen. Wine never hands it to Xwayland, so it can't show up or take focus. Switch to something windowed and it shrinks, so Tappo leaves game mode again, and if you click into Tappo it steps aside.

Controllers don't go through the bridge at all. Tappo reads them itself through XInput, and Wine passes the pad's buttons on whichever window has focus, so controller presses count while you're playing, the same as on Windows.

Options (`python3 bridge/tappo-bridge.py --help` has the full list):

- `--mouse-hand left|right|off`: which stand-in a click sends, or ignore clicks.
- `--real-keys`: send your real keys instead of stand-ins. Tappo then sees everything you type, and so does whichever of its windows Wine last focused.
- `--no-shortcuts`: send stand-ins for Tappo's shortcuts too. They then only work while one of its windows has the keyboard.
- `--no-menu-close`: leave Tappo's menu open when you click outside it. A click on a Wayland window then never closes it.
- `--fullscreen-poll SECONDS`: how often to check whether a fullscreen app has the keyboard, for "Enter Game Mode in Fullscreen". 0.5 by default; 0 turns that off.
- `--ignore NAME`: skip input devices whose name contains NAME. Repeatable; the default skips YubiKeys.
- `--hardware-input`: inject through Wine's private hardware-input call, so Tappo can't tell the presses were injected.
- `--cursor-poll SECONDS`: how often to check whether the pointer has left every X window, 0.1 by default. 0 stops the bridge moving Wine's cursor at all.
- `--cursor-margin PIXELS`: how far from any X window that moved cursor lands, when there's room. 32 by default.
- `--cursor-recheck SECONDS`: how often to look for a window that has opened over the moved cursor, and move it again if one has. 1 by default, 0 never looks.
- `--direct-wait SECONDS`: how long to wait for Xwayland to report a press while one of Tappo's windows has the keyboard or the pointer, to tell whether Wine got it itself. 0.05 by default; 0 sends a stand-in for everything.
- `--update-notice critical|normal|off`: how to tell you Tappo has an update waiting. Critical, the default, stays on screen until you dismiss it; normal goes into the notification history; off sends nothing.
- `--update-notice-delay SECONDS`: how long the update window can be open before that notice, so an update you're already installing doesn't get one. 60 by default.
- `--screen-poll SECONDS`: how often to check which screen the pet is on. 1 by default; 0 leaves the pet wherever the screens put it.
- `--screen-settle SECONDS`: how long the screens have to stay put after a change before the pet is moved back. 5 by default.
- `--screen-switch-wait SECONDS`: how long to give each of Tappo's screen switches before looking where the pet went. 3 by default.
- `--screen-file PATH`: where to remember the pet's screen between restarts. `~/.local/state/tappo-bridge/screens.json` by default; an empty string remembers it only until Tappo quits.

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
- "Enter Game Mode in Fullscreen" only notices apps that run through Xwayland, which takes in games under Proton or Wine and many native ones. A fullscreen Wayland app, like a video in Firefox, still looks windowed to Tappo, because nothing outside KWin can see it.
- The pet's state can reset when Tappo is killed rather than quit. Quitting from the tray or logging out of Plasma keeps it, but a shutdown that skips Plasma, such as `systemctl poweroff` from a terminal, still kills it.
- Kuro's login page doesn't like ad blockers or any other filtering. If signing in fails, open the login link in a clean browser with no extensions. Thanks to timurinal for finding that one.

## Contributing

Help is very welcome, whether that's a bug report, a test on a setup I don't have or a PR. The things I can't check myself:

- Other desktops. Everything but the logout script should work outside Plasma, and the logout script needs an equivalent there.
- Intel graphics.
- Distros other than Arch and Fedora, as long as they can run Arch's Wine package.
- Newer Wine. Each new build means finding the patch sites again, and getting the fixes into Wine itself would make most of this repo unnecessary.

If something breaks, open an issue with what you ran and what happened. Tappo logs to `drive_c/users/<you>/AppData/Local/com.kuro.wuwatappo.global/logs` in the prefix, and `journalctl --user -u tappo-bridge` shows the bridge.

## Wine version

Everything is pinned to Arch's `wine 11.20-1`. The patches are binary patches written for that exact build, and `runtime/patch-runtime.py` and `ole32/patch-ole32.py` refuse to touch any file whose hash they don't recognise. Moving to a newer Wine means finding the same spots in the new binaries and updating the offsets, hashes and package pin. Each patch notes which Wine function it changes.

## Uninstalling

Quit Tappo first, then in the repo's folder (`~/.local/share/wuwa-tappo-linux` if you used `get.sh`):

```
./install.sh --uninstall
```

That removes the bridge, the menu and autostart entries, the desktop shortcut, the logout script and what Wine added to your menus and file types when Tappo was installed. It then asks whether to delete the prefix too, which deletes Tappo and everything it saved, so the default is no. `make uninstall` is the same without the question, leaving the prefix alone. After that, delete this folder, which takes the downloaded Wine with it.

## About AI

This was built with some help from an AI coding assistant. It did much of the digging through Wine, helped make the binary patches and hunted for bugs and other issues. I tested every change on my own desktop. Binary patches are hard to check by eye, so if anything looks wrong, please open an issue.

## Licence

GNU General Public License, version 2 or (at your option) any later version. See [LICENSE](LICENSE).

Wuwa Tappo and Wuthering Waves belong to Kuro Games.
