#!/bin/sh
# One-line installer for people who do not use Claude Desktop (for example ChatGPT users).
#
#   curl -fsSL https://raw.githubusercontent.com/torigood/studio-one-ai-bridge/main/install/install.sh | sh
#
# It downloads the latest release (.mcpb, which carries its own Python), unpacks it into
# ~/Library/Application Support/studio-one-ai-bridge/app, adds the `studio-one-bridge` command,
# installs the Studio One side (setup --apply) and, if you want, turns on sharing with the
# ChatGPT chat app (share --enable) and copies the connector URL to the clipboard.
#
# Options (after `sh -s --` when piped):
#   --mcpb FILE      install this .mcpb instead of downloading the latest release
#   --no-setup       skip the Studio One side
#   --no-share       skip ChatGPT sharing
#   --ngrok          use a fixed ngrok address (asks for the authtoken and domain)
#   --cloudflare     use a Cloudflare quick tunnel (no account, the address changes on every start)
#   --uninstall      stop sharing, remove the Studio One side, the app and the command
# Environment: S1BRIDGE_INSTALL_DIR overrides the app folder, S1BRIDGE_BIN_DIR the command folder.
set -eu

REPO="torigood/studio-one-ai-bridge"
APP_DIR="${S1BRIDGE_INSTALL_DIR:-$HOME/Library/Application Support/studio-one-ai-bridge/app}"
BIN_DIR="${S1BRIDGE_BIN_DIR:-$HOME/.local/bin}"
CMD="$BIN_DIR/studio-one-bridge"

MCPB="" DO_SETUP=1 DO_SHARE=1 PROVIDER="" UNINSTALL=0
while [ $# -gt 0 ]; do
	case "$1" in
		--mcpb) MCPB="$2"; shift ;;
		--no-setup) DO_SETUP=0 ;;
		--no-share) DO_SHARE=0 ;;
		--ngrok) PROVIDER=ngrok ;;
		--cloudflare) PROVIDER=cloudflare ;;
		--uninstall) UNINSTALL=1 ;;
		-h|--help) sed -n '2,20p' "$0" 2>/dev/null || true; exit 0 ;;
		*) echo "unknown option: $1" >&2; exit 2 ;;
	esac
	shift
done

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }

# Questions come from the terminal, also when this script itself arrives on stdin (curl | sh).
TTY=""
if [ -r /dev/tty ] && (: </dev/tty) 2>/dev/null; then TTY=/dev/tty; fi
ask() {  # ask "question" default -> answer on stdout
	if [ -z "$TTY" ]; then printf '%s' "$2"; return; fi
	printf '%s ' "$1" >/dev/tty
	read -r a </dev/tty || a=""
	printf '%s' "${a:-$2}"
}

studio_one_running() { /usr/bin/pgrep -x studioapp >/dev/null 2>&1; }
wait_for_studio_one_quit() {
	while studio_one_running; do
		[ -n "$TTY" ] || die "Studio One is running. Quit it and run this again."
		ask "Studio One is running. Quit it (it rewrites its settings when it quits), then press Enter." "" >/dev/null
	done
}

[ "$(uname -s)" = Darwin ] || die "this installer is for macOS only (Windows is not supported yet)"

if [ "$UNINSTALL" = 1 ]; then
	[ -x "$CMD" ] || die "not installed ($CMD not found)"
	say "Stopping ChatGPT sharing"
	"$CMD" share --disable || true
	wait_for_studio_one_quit
	say "Removing the Studio One side"
	"$CMD" uninstall --apply || true
	rm -rf "$APP_DIR" "$CMD"
	say "Removed. Your songs and the data folder (~/Library/Application Support/studio-one-ai-bridge) were kept."
	exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1. get the .mcpb
if [ -z "$MCPB" ]; then
	say "Finding the latest release"
	curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" -o "$TMP/release.json" \
		|| die "could not reach GitHub (is the repository public?)"
	URL="$(sed -n 's/.*"browser_download_url": *"\([^"]*\.mcpb\)".*/\1/p' "$TMP/release.json" | head -n 1)"
	[ -n "$URL" ] || die "the latest release has no .mcpb file"
	# "digest" sits just before "browser_download_url" in each asset of the API answer
	SHA="$(tr ',' '\n' <"$TMP/release.json" | grep -B 40 "\"browser_download_url\": *\"$URL\"" \
		| sed -n 's/.*"digest": *"sha256:\([0-9a-f]*\)".*/\1/p' | tail -n 1)"
	say "Downloading ${URL##*/}"
	curl -fL --progress-bar "$URL" -o "$TMP/bridge.mcpb" || die "download failed"
	MCPB="$TMP/bridge.mcpb"
	if [ -n "$SHA" ]; then
		[ "$(shasum -a 256 "$MCPB" | cut -d ' ' -f 1)" = "$SHA" ] || die "the download failed its SHA-256 check; nothing was installed"
		echo "SHA-256 checked."
	fi
fi
[ -f "$MCPB" ] || die "no such file: $MCPB"

# 2. unpack next to the old copy, then swap (a running background share restarts itself on the new code)
say "Installing into $APP_DIR"
mkdir -p "$(dirname "$APP_DIR")"
rm -rf "$APP_DIR.new"
/usr/bin/ditto -x -k "$MCPB" "$APP_DIR.new" || die "could not unpack $MCPB"
[ -f "$APP_DIR.new/bin/start.sh" ] || die "$MCPB does not look like a Studio One AI Bridge release"
grep -q '"share"' "$APP_DIR.new/server/s1bridge/__main__.py" || DO_SHARE_SUPPORTED=0
chmod +x "$APP_DIR.new/bin/start.sh" "$APP_DIR.new"/python/darwin-*/bin/python3 2>/dev/null || true
rm -rf "$APP_DIR.old"
[ -d "$APP_DIR" ] && mv "$APP_DIR" "$APP_DIR.old"
mv "$APP_DIR.new" "$APP_DIR"
rm -rf "$APP_DIR.old"

mkdir -p "$BIN_DIR"
cat >"$CMD" <<EOF
#!/bin/sh
# Studio One AI Bridge command (made by install.sh): studio-one-bridge doctor | share --status | ...
exec /bin/sh "$APP_DIR/bin/start.sh" "\$@"
EOF
chmod +x "$CMD"
"$CMD" doctor --no-ping >/dev/null 2>&1 || true  # first start: the bundled Python works
case ":$PATH:" in
	*":$BIN_DIR:"*) ;;
	*)
		if ! grep -qs "$BIN_DIR" "$HOME/.zprofile"; then
			printf '\n# Studio One AI Bridge\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >>"$HOME/.zprofile"
			echo "Added $BIN_DIR to PATH in ~/.zprofile (new terminal windows find studio-one-bridge)."
		fi ;;
esac

# 3. the Studio One side
if [ "$DO_SETUP" = 1 ]; then
	wait_for_studio_one_quit
	say "Installing the Studio One side (extension, control surface, device entry)"
	"$CMD" setup --apply || die "setup failed; run: studio-one-bridge doctor"
fi

# 4. sharing with the ChatGPT chat app
if [ "$DO_SHARE" = 1 ] && [ "${DO_SHARE_SUPPORTED:-1}" = 0 ]; then
	echo "This release has no ChatGPT sharing yet; skipping it."
	DO_SHARE=0
fi
if [ "$DO_SHARE" = 1 ] && [ -z "$PROVIDER" ]; then
	echo
	echo "ChatGPT needs a public address that reaches this Mac. Choose:"
	echo "  1) ngrok, fixed address (free account at ngrok.com; set up the ChatGPT app once)  [recommended]"
	echo "  2) Cloudflare, no account (the address changes after every restart or login)"
	echo "  3) skip (you use Claude Desktop or Codex)"
	case "$(ask "1, 2 or 3 [1]:" 1)" in
		2) PROVIDER=cloudflare ;;
		3) DO_SHARE=0 ;;
		*) PROVIDER=ngrok ;;
	esac
fi
if [ "$DO_SHARE" = 1 ]; then
	if [ "$PROVIDER" = ngrok ]; then
		if [ "$(uname -m)" != arm64 ] && ! command -v ngrok >/dev/null 2>&1 && [ ! -x /usr/local/bin/ngrok ]; then
			die "on an Intel Mac install ngrok first (brew install ngrok), then run this again"
		fi
		[ -n "$TTY" ] || die "ngrok setup asks questions; run this in a terminal, or use --cloudflare"
		if ! /usr/bin/security find-generic-password -s studio-one-ai-bridge -a ngrok >/dev/null 2>&1; then
			say "ngrok authtoken"
			echo "Sign up or log in at ngrok.com; the page with Your Authtoken opens now."
			/usr/bin/open "https://dashboard.ngrok.com/get-started/your-authtoken" 2>/dev/null || true
			ask "Copy the authtoken, then press Enter (a window asks you to paste it)." "" >/dev/null
			"$CMD" share --set-ngrok-token || die "no authtoken saved"
		fi
		say "ngrok domain"
		echo "Your free fixed domain is on the Domains page (opens now), like name-words.ngrok-free.app."
		/usr/bin/open "https://dashboard.ngrok.com/domains" 2>/dev/null || true
		DOMAIN="$(ask "Paste the domain and press Enter:" "")"
		[ -n "$DOMAIN" ] || die "no domain given"
		SHARE_ARGS="--ngrok-domain $DOMAIN"
	else
		SHARE_ARGS="--provider cloudflare"
	fi
	say "Turning on sharing (now and at every login)"
	# settings and --enable in one call: settings alone would start sharing in this terminal
	# shellcheck disable=SC2086
	"$CMD" share $SHARE_ARGS --enable || die "sharing did not start; log: ~/Library/Application Support/studio-one-ai-bridge/share.log"
	URL="$("$CMD" share --status | sed -n 's/^connector URL: *//p')"
	if [ -n "$URL" ] && [ "$URL" != "-" ]; then
		printf '%s' "$URL" | /usr/bin/pbcopy && echo "The connector URL is on the clipboard. Keep it private."
	fi
fi

say "Done"
echo "Next:"
if [ "$DO_SHARE" = 1 ]; then
	echo "  - In ChatGPT add the connector with the URL above (docs/chatgpt-guide.md, step 4)."
fi
echo "  - Start Studio One, open a song, and ask the AI: \"check the Studio One connection\" (daw_ping)."
echo "  - Problems: studio-one-bridge doctor"
