#!/bin/sh
set -eu

repo=Alphaproject1998/wuwa-tappo-linux
branch=${TAPPO_BRANCH:-main}
git_url=${TAPPO_GIT_URL:-https://github.com/$repo.git}
tarball_url=${TAPPO_TARBALL_URL:-https://github.com/$repo/archive/refs/heads/$branch.tar.gz}
use_git=${TAPPO_GIT:-auto}

say() {
	printf '\n\033[1m==> %s\033[0m\n' "$*"
}

die() {
	printf '\033[31merror:\033[0m %s\n' "$*" >&2
	exit 1
}

fetch() {
	if command -v curl > /dev/null; then
		curl -sSfL --retry 3 "$1"
	elif command -v wget > /dev/null; then
		wget -qO- "$1"
	else
		die "this needs curl or wget"
	fi
}

tappo_running() {
	pgrep -u "$(id -u)" -f '\\WuwaTappo\.exe( |$)' > /dev/null
}

wait_for_tappo_to_quit() {
	tappo_running || return 0
	say "Wuwa Tappo is running. Quit it from its tray icon to carry on."
	while tappo_running; do
		sleep 2
	done
}

# A copy cloned by hand gets updated where it is, since installing a second one would move the bridge and the menu entry over to it.
installed_checkout() {
	unit="$HOME/.config/systemd/user/tappo-bridge.service"
	[ -f "$unit" ] || return 1
	found=$(sed -n 's|^ExecStart=[^"]*"\(.*\)/bridge/tappo-bridge\.py".*|\1|p' "$unit")
	[ -n "$found" ] && [ -f "$found/install.sh" ] || return 1
	printf '%s\n' "$found"
}

download_into() {
	target=$1
	parent=$(dirname "$target")
	mkdir -p "$parent"
	fresh=$(mktemp -d "$parent/.wuwa-tappo-linux.XXXXXX")
	chmod "$(umask -S)" "$fresh"
	if ! fetch "$tarball_url" | tar -xzm --strip-components=1 -C "$fresh"; then
		rm -rf "$fresh"
		die "couldn't download wuwa-tappo-linux"
	fi
	if [ -d "$target" ]; then
		[ ! -d "$target/build" ] || mv "$target/build" "$fresh/build"
		mv "$target" "$fresh.old"
		mv "$fresh" "$target"
		rm -rf "$fresh.old"
	else
		mv "$fresh" "$target"
	fi
}

main() {
	if [ -n "${TAPPO_DIR:-}" ]; then
		checkout=$TAPPO_DIR
	elif ! checkout=$(installed_checkout); then
		checkout="${XDG_DATA_HOME:-$HOME/.local/share}/wuwa-tappo-linux"
	fi

	if [ -e "$checkout/.git" ]; then
		command -v git > /dev/null || die "$checkout is a git checkout, so updating it needs git"
		wait_for_tappo_to_quit
		say "Updating $checkout"
		git -C "$checkout" pull --ff-only || die "git couldn't update $checkout; sort that out there, then run ./install.sh"
	elif [ -f "$checkout/install.sh" ]; then
		wait_for_tappo_to_quit
		say "Updating $checkout"
		download_into "$checkout"
	elif [ -n "$(ls -A "$checkout" 2> /dev/null)" ]; then
		die "$checkout is already there and isn't wuwa-tappo-linux; set TAPPO_DIR to put it somewhere else"
	elif [ "$use_git" != no ] && command -v git > /dev/null; then
		say "Downloading wuwa-tappo-linux into $checkout"
		git clone --branch "$branch" "$git_url" "$checkout"
	else
		say "Downloading wuwa-tappo-linux into $checkout"
		download_into "$checkout"
	fi

	cd "$checkout"
	# Piped into sh, stdin is the script itself, and install.sh would take every default without asking.
	if [ ! -t 0 ] && (: < /dev/tty) 2> /dev/null; then
		exec bash ./install.sh "$@" < /dev/tty
	fi
	exec bash ./install.sh "$@"
}

main "$@"
