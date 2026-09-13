#!/bin/sh
set -eu
exec /usr/bin/kiwix-serve --address 127.0.0.1 --port 8766 --nosearchbar /opt/meshtastic-ai-bridge/zim/*.zim