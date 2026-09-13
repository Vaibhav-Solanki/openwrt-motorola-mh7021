#!/bin/bash
# One-time setup on the release host (needs root). SITE is the nginx site file whose server block
# answers SERVER_NAME; the snippet is included there, so /neon-mesh/ joins that site:
#     sudo SITE=/etc/nginx/sites-available/mysite SERVER_NAME=example.org bash openwrt/nginx/install.sh
# Creates /srv/neon-releases (owned by the invoking user, so release.sh / ota-promote.sh publish without
# sudo), installs the neon-mesh.conf snippet and includes it in that server block. Idempotent;
# the site file is backed up before its only edit, and restored if `nginx -t` rejects the result.
set -e -o pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
D="$(cd "$(dirname "$0")" && pwd)"
OWNER=${SUDO_USER:?run with sudo from the account that runs release.sh}
FRONT=${SITE:?set SITE=/etc/nginx/sites-available/<your site>}
SERVER_NAME=${SERVER_NAME:?set SERVER_NAME to the server_name of that site}
SNIP=/etc/nginx/snippets/neon-mesh.conf

install -d -o "$OWNER" -g "$OWNER" -m 0755 /srv/neon-releases /srv/neon-releases/neon-mesh /srv/neon-releases/neon-mesh/ota
install -D -m 0644 "$D/neon-mesh.conf" "$SNIP"

BAK=""
if ! grep -q 'snippets/neon-mesh.conf' "$FRONT"; then
	grep -q "^[[:space:]]*server_name $SERVER_NAME;" "$FRONT" || { echo "!! no 'server_name $SERVER_NAME;' line in $FRONT -- add 'include $SNIP;' to that server block by hand" >&2; exit 1; }
	BAK=$FRONT.bak-neon-mesh-$(date +%Y%m%d-%H%M%S)
	cp -p "$FRONT" "$BAK"
	sed -i "0,/^\([[:space:]]*\)server_name $SERVER_NAME;/s//&\n\n\1# neon-mesh releases, apk feed, OTA manifests (openwrt\/nginx)\n\1include snippets\/neon-mesh.conf;/" "$FRONT"
fi

if nginx -t; then
	systemctl reload nginx
	echo "== nginx reloaded; /neon-mesh/ is served from /srv/neon-releases/neon-mesh (owner $OWNER)"
else
	[ -n "$BAK" ] && cp -p "$BAK" "$FRONT" && echo "!! nginx -t failed -- $FRONT restored from $BAK" >&2
	rm -f "$SNIP"
	exit 1
fi
