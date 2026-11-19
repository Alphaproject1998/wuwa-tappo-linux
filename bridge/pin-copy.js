// tappo-bridge loads this into KWin to hear when a fullscreen window covers its copy of the pet, since Wayland windows and desktops never reach Xwayland.
function isCopy(window) {
	return window.windowRole === 'tappo-bridge-copy';
}

function isHere(window) {
	return window.onAllDesktops || window.desktops.includes(workspace.currentDesktop);
}

function overlaps(a, b) {
	return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

function isTransientOf(window, main) {
	for (let parent = window.transientFor; parent; parent = parent.transientFor) {
		if (parent === main) return true;
	}
	return false;
}

// KWin's Window::isActiveFullScreen, after the keep below check that comes first in its layer code.
function onTop(window, active) {
	if (!window.fullScreen || window.minimized || window.keepBelow || isCopy(window) || !active) return false;
	if (!isHere(window)) return false;
	return active === window || !overlaps(active.frameGeometry, window.output.geometry) || isTransientOf(active, window);
}

// The bridge reads keepBelow back as _NET_WM_STATE_BELOW.
// A copy on another desktop is never covered, so the pet stays hidden there.
function cover() {
	const active = workspace.activeWindow;
	const windows = workspace.windowList();
	const onTopNow = windows.filter(window => onTop(window, active));
	windows.filter(isCopy).forEach(copy => {
		const covered = isHere(copy) && onTopNow.some(window => overlaps(window.frameGeometry, copy.frameGeometry));
		if (copy.keepBelow === covered) return;
		copy.keepBelow = covered;
		copy.keepAbove = !covered;
	});
}

function watch(window) {
	// if (isCopy(window)) {
	// 	window.onAllDesktops = true;
	// }
	window.fullScreenChanged.connect(cover);
	window.frameGeometryChanged.connect(cover);
	window.minimizedChanged.connect(cover);
	window.desktopsChanged.connect(cover);
	window.keepBelowChanged.connect(cover);
}

workspace.windowAdded.connect(window => {
	watch(window);
	cover();
});
workspace.windowActivated.connect(cover);
workspace.windowRemoved.connect(cover);
workspace.currentDesktopChanged.connect(cover);
workspace.windowList().forEach(watch);
cover();
