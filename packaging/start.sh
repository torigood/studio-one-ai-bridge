#!/bin/sh
# Entry point of the Claude Desktop extension (.mcpb): start the MCP server with the Python
# bundled for this Mac (python/darwin-<arch>/), or the system python3 when the bundle has none
# or it does not start. Bundle layout: bin/start.sh, python/, server/run.py.
# Absolute tool paths: the host may start this with a minimal PATH.
DIR="$(cd "${0%/*}/.." && pwd)"
case "$(/usr/bin/uname -m)" in
	arm64) ARCH=arm64 ;;
	*) ARCH=x86_64 ;;
esac
PY="$DIR/python/darwin-$ARCH/bin/python3"
if [ -f "$PY" ]; then
	# zip extractors do not always keep the executable bit
	[ -x "$PY" ] || /bin/chmod +x "$PY" 2>/dev/null
	if "$PY" -c "" 2>/dev/null; then
		exec "$PY" "$DIR/server/run.py" "$@"
	fi
	echo "studio-one-ai-bridge: bundled Python at $PY did not start, trying system python3" >&2
fi
exec /usr/bin/python3 "$DIR/server/run.py" "$@"
