#!/usr/bin/env bash
# Build and roll out the AIO Tests MCP server behind Traefik.
#
#   deploy/traefik/deploy.sh             # deploy origin/main
#   deploy/traefik/deploy.sh <ref>       # deploy a branch, tag or commit
#   deploy/traefik/deploy.sh rollback    # put back the image that served before
#   deploy/traefik/deploy.sh status      # what is serving, and from which commit
#
# The image is built on the host rather than pulled, from a `git archive` of the
# ref, so the checkout itself stays on main. A rollout only counts once /healthz,
# through Traefik, reports the commit that was built; if it does not, the
# previous image is put back. Host settings come from the .env beside this file.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
STATE="$HERE/.env" # host settings, plus IMAGE_TAG and PREVIOUS_IMAGE_TAG
IMAGE=aio-tests-mcp-server
SERVICE=mcp-aio-tests

die() {
	echo "deploy.sh: $*" >&2
	exit 1
}

compose() {
	docker compose -f "$HERE/docker-compose.yml" "$@"
}

state_get() {
	if [[ -f $STATE ]]; then sed -n "s/^$1=//p" "$STATE" | tail -n 1; fi
}

# Rewrite only the two tag lines, keeping the host settings around them.
set_tags() {
	local rest=""
	if [[ -f $STATE ]]; then rest=$(grep -v -E '^(PREVIOUS_)?IMAGE_TAG=' "$STATE" || true); fi
	{
		if [[ -n $rest ]]; then printf '%s\n' "$rest"; fi
		printf 'IMAGE_TAG=%s\nPREVIOUS_IMAGE_TAG=%s\n' "$1" "$2"
	} >"$STATE.tmp"
	mv "$STATE.tmp" "$STATE"
}

load_settings() {
	[[ -f $STATE ]] || die "$STATE is missing: copy .env.example to .env and fill it in"
	MCP_HOST=$(state_get MCP_HOST)
	[[ -n $MCP_HOST ]] || die "set MCP_HOST in $STATE"
	PATH_PREFIX=$(state_get PATH_PREFIX)
	PATH_PREFIX=${PATH_PREFIX:-/aio-tests}
	TRAEFIK_URL=$(state_get TRAEFIK_URL)
	TRAEFIK_URL=${TRAEFIK_URL:-http://127.0.0.1}
}

healthz() {
	curl -fsS -m 5 -H "Host: $MCP_HOST" "$TRAEFIK_URL$PATH_PREFIX/healthz"
}

# The tag of the image the container runs now, empty if there is no container.
running_tag() {
	{ docker inspect -f '{{.Config.Image}}' "$SERVICE" 2>/dev/null || true; } | sed -n "s/^$IMAGE://p"
}

# The version uv-dynamic-versioning would give this commit (pep440, bump=true):
# v0.1.0 itself is 0.1.0, and 19 commits past it is 0.1.1.dev19+30246d1.
pep440_version() {
	local describe base distance commit major minor patch
	describe=$(git -C "$REPO" describe --tags --long --abbrev=7 --match 'v[0-9]*' "$1") ||
		die "no v* tag is reachable from $1"
	commit=${describe##*-g}
	describe=${describe%-g*}
	distance=${describe##*-}
	base=${describe%-*}
	base=${base#v}
	[[ $base =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || die "cannot derive a version from tag v$base"
	if ((distance == 0)); then
		echo "$base"
		return
	fi
	major=${BASH_REMATCH[1]} minor=${BASH_REMATCH[2]} patch=${BASH_REMATCH[3]}
	echo "$major.$minor.$((patch + 1)).dev$distance+$commit"
}

# Wait for /healthz to answer ok, and to report commit $1 when one is given.
wait_live() {
	local body
	for _ in $(seq 45); do
		if body=$(healthz 2>/dev/null) &&
			[[ $(jq -r .status <<<"$body") == ok ]] &&
			[[ -z $1 || $(jq -r '.commit // empty' <<<"$body") == "$1" ]]; then
			echo "$body"
			return 0
		fi
		sleep 2
	done
	return 1
}

deploy() {
	local ref=$1 sha short version previous ctx body
	git -C "$REPO" fetch --quiet --tags origin
	sha=$(git -C "$REPO" rev-parse --verify --quiet "origin/$ref^{commit}" ||
		git -C "$REPO" rev-parse --verify --quiet "$ref^{commit}") || die "unknown ref: $ref"
	short=$(git -C "$REPO" rev-parse --short=7 "$sha")
	version=$(pep440_version "$sha")
	previous=$(running_tag)

	if ! git -C "$REPO" diff --quiet HEAD origin/main -- deploy/traefik; then
		echo "warning: deploy/traefik differs from origin/main; run 'git pull --ff-only' first" >&2
	fi

	echo "building $IMAGE:$short ($version)"
	ctx=$(mktemp -d)
	trap "rm -rf '$ctx'" EXIT
	git -C "$REPO" archive "$sha" | tar -x -C "$ctx"
	docker build \
		--build-arg VERSION="$version" \
		--build-arg GIT_COMMIT="$sha" \
		-t "$IMAGE:$short" "$ctx"
	rm -rf "$ctx"

	set_tags "$short" "$previous"
	if compose up -d "$SERVICE" && body=$(wait_live "$sha"); then
		echo "live: $body"
		return
	fi

	echo "deploy.sh: $IMAGE:$short did not come up serving $sha" >&2
	compose logs --tail 40 "$SERVICE" >&2 || true
	if [[ -n $previous && $previous != "$short" ]]; then
		echo "putting $IMAGE:$previous back" >&2
		# The failed build is not worth rolling back to, so it is not recorded.
		set_tags "$previous" ""
		{ compose up -d "$SERVICE" && wait_live "" >/dev/null; } ||
			echo "deploy.sh: $IMAGE:$previous is not healthy either" >&2
	fi
	exit 1
}

# Swap to the previous image. Running it again swaps back.
rollback() {
	local current previous body
	current=$(state_get IMAGE_TAG)
	previous=$(state_get PREVIOUS_IMAGE_TAG)
	[[ -n $previous ]] || die "no previous image recorded in $STATE"
	docker image inspect "$IMAGE:$previous" >/dev/null 2>&1 || die "image $IMAGE:$previous no longer exists"

	set_tags "$previous" "$current"
	if compose up -d "$SERVICE" && body=$(wait_live ""); then
		echo "live: $body"
		return
	fi
	set_tags "$current" "$previous"
	die "$IMAGE:$previous did not come up healthy; run 'deploy.sh rollback' again to return to $IMAGE:$current"
}

status() {
	echo "checkout   $(git -C "$REPO" log -1 --format='%h %s')"
	echo "image      $(state_get IMAGE_TAG) (previous: $(state_get PREVIOUS_IMAGE_TAG))"
	echo "container  $(docker ps -a --filter "name=^$SERVICE\$" --format '{{.Image}}  {{.Status}}')"
	echo "healthz    $(healthz 2>&1)"
}

case ${1:-} in
-h | --help)
	sed -n '4,7s/^# \{0,1\}//p' "$0"
	exit
	;;
esac

load_settings
case ${1:-} in
rollback) rollback ;;
status) status ;;
*) deploy "${1:-main}" ;;
esac
