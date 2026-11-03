# Deploying behind Traefik

A compose project and a deploy script for running the server on a host that
already runs Traefik with its Docker provider. The server is routed by hostname
and path prefix, so it can share one hostname with other MCP servers. The MCP
endpoint is `http://$MCP_HOST$PATH_PREFIX/mcp`.

Nothing host-specific is tracked. Hostnames, paths and credentials live in
`deploy/traefik/.env`, which is gitignored; keep them out of commits and PRs.

## Install (once per host)

```bash
git clone https://github.com/AmirhosseinHanifehzadeh/aio-tests-mcp.git
cd aio-tests-mcp
cp deploy/traefik/.env.example deploy/traefik/.env   # then fill it in
```

The env file named by `JIRA_ENV_FILE` holds `JIRA_URL` and the Jira credentials;
see the repository's `.env.example` for the variables. The script needs `git`,
`docker` with compose, `curl` and `jq`.

## Deploy

```bash
git pull --ff-only
deploy/traefik/deploy.sh             # origin/main; or pass a branch, tag or commit
```

The script builds the image from a `git archive` of the ref, so the checkout
stays on `main` whatever you deploy. It bakes the commit and the version
`uv-dynamic-versioning` would compute into the image, then recreates the container.
It reports success only once `/healthz`, fetched through Traefik, shows the
commit it just built. If that never happens, it prints the container's logs and
puts the previous image back.

```bash
deploy/traefik/deploy.sh status      # checkout, image tags, container, /healthz
deploy/traefik/deploy.sh rollback    # swap to the previous image; run again to swap back
```

The image is only ever built on the host, so `pull_policy: never` makes a missing
tag fail fast instead of trying a registry.

## Changing the configuration

`READ_ONLY_MODE`, `ENABLED_TOOLS` and `AIO_API_TOKEN` can be set in
`deploy/traefik/.env`. They win over the Jira env file. To apply a change
without a rebuild:

```bash
docker compose -f deploy/traefik/docker-compose.yml up -d
```

## Taking over a hand-written setup

The compose project is named `aio-tests-mcp` and the container `mcp-aio-tests`.
If an existing setup used the same names, the first `deploy.sh` run recreates
that container in place. Once it reports `live`, remove the old compose file.
