# ArrChestra

![ArrChestra retro-CRT banner](header.png)

An MCP server for **Seerr/Jellyseerr, Sonarr, Radarr, NZBGet, SABnzbd and NZBHydra2**. Connect it to an MCP client to request media, manage monitoring, inspect failed acquisitions and control downloads.

Configure only the services you use. ArrChestra starts read-only, checks permissions before making changes, and leaves scheduling to your existing tools.

## What you can do

| Service | Tools cover |
| --- | --- |
| Seerr / Jellyseerr | Media search, requests and request status |
| Radarr | Movie lookup and additions, monitoring, release searches, queue/history and import diagnostics |
| Sonarr | Series and episode lookup, season monitoring, missing episodes, searches and queue/history |
| NZBGet / SABnzbd | Download status, queue/history, categories, pause/resume and targeted retries |
| NZBHydra2 | Release searches and submission of a selected result to either downloader |

For example: "Monitor seasons 1 and 2 of this show", "Why hasn't this movie imported?", or "Pause this download, not the whole queue."

There are no delete, purge, restart, configuration-write or script-control tools.

## Quick start

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/). From a checkout:

```bash
cp .env.example .env
# Uncomment and fill in the services you want to connect.
chmod 600 .env
uv run arrchestra-mcp
```

A minimal `.env` for Sonarr:

```dotenv
SONARR_URL=https://sonarr.example.com
SONARR_API_KEY=replace-me
MCP_READ_ONLY=true
```

The server uses **stdio**. Your MCP client launches the process and owns stdin/stdout; logs go to stderr. If you use an existing Python environment, `pip install .` installs the same `arrchestra-mcp` command.

Both module entrypoints work:

```bash
python -m arrchestra_mcp
python -m jellyseerr_mcp  # compatibility with earlier installations
```

## Configuration

Environment variables override `.env` in the working directory. Restart after changing settings. There is no YAML loader.

| Service | Required settings |
| --- | --- |
| Seerr / Jellyseerr | `JELLYSEERR_URL`, `JELLYSEERR_API_KEY` |
| Radarr | `RADARR_URL`, `RADARR_API_KEY` |
| Sonarr | `SONARR_URL`, `SONARR_API_KEY` |
| NZBGet | `NZBGET_URL`, `NZBGET_USERNAME`, `NZBGET_PASSWORD` |
| SABnzbd | `SABNZBD_URL`, `SABNZBD_API_KEY` |
| NZBHydra2 | `NZBHYDRA_URL`, `NZBHYDRA_API_KEY` |

Unset services have no tools. Partial configuration produces a startup error listing the missing settings. At least one service must be configured; Seerr is optional. One instance of each service is supported, and both downloaders can coexist.

Supply base URLs without API suffixes, credentials or query strings. Reverse-proxy prefixes work, for example `https://media.example.com/sonarr`. TLS verification stays on. Prefer HTTPS, especially for NZBGet's Basic authentication. SABnzbd needs its full API key, not its add-only NZB key.

| Setting | Default | Purpose |
| --- | --- | --- |
| `MCP_REQUEST_TIMEOUT` | `15` | Per-request timeout in seconds, positive and at most 300 |
| `<SERVICE>_TIMEOUT` | Shared timeout | Override for one service, including `JELLYSEERR_TIMEOUT` |
| `MCP_SEARCH_TIMEOUT` | `120` | Timeout for release/target search, which waits on every enabled indexer |
| `<SERVICE>_SEARCH_TIMEOUT` | Search timeout | Override for Radarr or Sonarr; `0` uses the ordinary request timeout |
| `LOG_LEVEL` | `INFO` | Diagnostic log level |
| `MCP_READ_ONLY` | `true` | Block mutations |
| `<SERVICE>_ALLOW_WRITES` | `false` | Permit that service's operations when read-only mode is off |
| `MCP_ALLOW_RAW_READ` | `false` | Optional legacy `GET status` tool only |
| `NZBHYDRA_ALLOWED_REDIRECT_ORIGINS` | Empty | Trusted indexer origins for NZB retrieval redirects |

Keep credentials in the process environment or a protected `.env` file, never in prompts or Git. The example file contains placeholders; environment files and local agent state are ignored by Git and excluded from the Docker context.

### Allowing changes

The global switch alone grants no writes. Opt into each service separately:

```dotenv
MCP_READ_ONLY=false
SONARR_ALLOW_WRITES=true
RADARR_ALLOW_WRITES=true
NZBGET_ALLOW_WRITES=true
```

Seerr and SABnzbd use `JELLYSEERR_ALLOW_WRITES` and `SABNZBD_ALLOW_WRITES`. Hydra submission checks the selected downloader's permission before fetching the NZB.

A timeout doesn't prove a mutation failed. ArrChestra doesn't retry automatically; inspect queue, history or command status before trying again. Accepted submissions retain their job ID if a follow-up status check fails.

## Connecting an MCP client

For clients that use `mcpServers`:

```json
{
  "mcpServers": {
    "arrchestra": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/arrchestra", "arrchestra-mcp"],
      "env": {
        "SONARR_URL": "https://sonarr.example.com",
        "SONARR_API_KEY": "replace-me",
        "MCP_READ_ONLY": "true"
      }
    }
  }
}
```

### Hermes

[Hermes uses `mcp_servers` in `~/.hermes/config.yaml`](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference):

```yaml
mcp_servers:
  arrchestra:
    command: uv
    args: [run, --directory, /path/to/arrchestra, arrchestra-mcp]
    supports_parallel_tool_calls: true
```

Keep the service settings in ArrChestra's `.env`. Hermes also supports per-server `tools.include` and `tools.exclude` filters.

### OpenClaw

[OpenClaw's MCP registry](https://docs.openclaw.ai/cli/mcp/registry) uses `mcp.servers`. Set the same stdio command and arguments in that entry. `openclaw mcp probe` checks a saved server's connection and tool discovery. Consult the documentation for your installed version: runtime adapters differ, and this registry is separate from mcporter.

These examples follow the hosts' documented configuration. Automated tests exercise MCP directly, not full Hermes or OpenClaw sessions.

## Using the tools

`get_services` reports configured services and effective write permissions. MCP `tools/list` supplies the argument schemas. Configuring all six services provides 41 tools, plus optional legacy status inspection.

- **Use the right IDs.** Movie additions take TMDB IDs; series additions take TVDB IDs. Library operations use native Sonarr/Radarr IDs. Seerr request IDs, Hydra result IDs and downloader IDs are separate.
- **Choose profiles and roots explicitly.** Discover them through `get_options`. Adding media never starts a search automatically. Direct Sonarr/Radarr additions bypass Seerr's approval workflow; use `request_media` when that workflow is intended.
- **Keep monitoring scoped.** Sonarr changes only the selected seasons or episodes. Use separate calls for those scopes. Episode operations check series ownership; searches accept at most 100 episode IDs.
- **Inspect before grabbing.** Release searches consume indexer quota. Grabs check the current GUID/indexer pair and refuse stale or rejected releases. Sonarr release searches need an episode or season scope. Search commands return a command ID, not a completed acquisition.
- **Track by native download ID.** Sonarr/Radarr queue and history include `downloadId`. NZBGet uses positive integer strings; SABnzbd uses opaque strings. Don't match jobs by title alone.
- **Make queue-wide actions explicit.** Pause/resume takes one `job_id` or `whole_queue=true`. Missing IDs never imply a global action. Retry targets one eligible failed history item; NZBGet redownloads it and may replace failed partial output.

Most lists default to 25 results, with a maximum requested page size of 100. Queue/history in Sonarr/Radarr use `page_number`; local lists, downloaders and Hydra use `offset`. Whole-list APIs report local paging rather than pretending the backend paginated.

### NZBHydra downloads

Fetch capabilities before using ID, category, age, size or indexer filters. To download a result, supply its Hydra ID, a configured category and `backend="nzbget"` or `backend="sabnzbd"`. ArrChestra retrieves the NZB and uploads its bytes without returning authenticated links.

**This path bypasses Sonarr/Radarr release selection. It doesn't guarantee library import or fulfill a Seerr request.** Downloader categories keep their existing post-processing settings.

If Hydra redirects to an indexer, allow only that trusted origin through `NZBHYDRA_ALLOWED_REDIRECT_ORIGINS`. Cross-origin requests don't carry Hydra credentials or cookies; HTTPS cannot downgrade to HTTP. Same-origin redirects must stay within the configured service path.

<details>
<summary>Full tool list</summary>

| Group | Tools |
| --- | --- |
| Common | `ping`, `get_services` |
| Seerr | `search_media`, `request_media`, `get_request` |
| Radarr | `radarr_find_movies`, `radarr_get_movie`, `radarr_get_options`, `radarr_get_queue`, `radarr_get_history`, `radarr_get_health`, `radarr_get_releases`, `radarr_get_command`, `radarr_add_movie`, `radarr_set_monitored`, `radarr_search_movie`, `radarr_grab_release` |
| Sonarr | `sonarr_find_series`, `sonarr_get_series`, `sonarr_get_episodes`, `sonarr_get_missing`, `sonarr_get_options`, `sonarr_get_queue`, `sonarr_get_history`, `sonarr_get_health`, `sonarr_get_releases`, `sonarr_get_command`, `sonarr_add_series`, `sonarr_set_monitoring`, `sonarr_search_episodes`, `sonarr_grab_release` |
| Downloaders | `downloads_get_status`, `downloads_get_queue`, `downloads_get_history`, `downloads_get_categories`, `downloads_pause`, `downloads_resume`, `downloads_retry` |
| NZBHydra | `nzbhydra_get_capabilities`, `nzbhydra_search`, `nzbhydra_download_result` |

The Hydra submission tool appears only when a downloader is configured. Downloader calls always require an explicit backend.

</details>

## Docker

```bash
docker build -t arrchestra-mcp .
docker run --rm -i --env-file .env arrchestra-mcp
```

The image runs as a non-root user. Use `-i` without a pseudo-TTY when an MCP client owns the streams. Runtime credentials are passed when the container starts; the build doesn't need access to any media service.

GitHub Actions builds the image for pull requests without publishing it. Pushes to `main`, release tags matching `v*.*.*`, and manual workflow runs publish to GHCR. The image path comes from `github.repository`; it follows the repository name. Registry authentication uses GitHub's job token, not a checked-in credential.

## Limits and compatibility

- **Stdio only.** SSE and HTTP are disabled until server authentication is implemented. Don't expose the process through an unauthenticated network bridge.
- Responses are limited to 8 MiB. NZBs/XML must be UTF-8 and stay within 4 MiB, 50,000 elements and depth 32. DTD/entities and compressed responses are refused. Large replies fail with a limit error.
- HTTP runs in worker threads. Same-service writes are serialized; unrelated reads can continue. Separate processes and external clients can still race, so there is no exactly-once guarantee.
- Treat release titles and backend messages as data, not instructions to the agent.

Existing Jellyseerr installations keep their module invocation and tool names. Requests now need write opt-in; `ping` identifies `arrchestra-mcp`. `raw_request` is off unless explicitly configured, and then permits only `GET status` without parameters or a body. Seerr TV requests still default to season 1 unless seasons are specified. The unused YAML example and unauthenticated SSE startup have been removed.

## Development

```bash
uv run --extra test pytest -q
```

Tests mock upstream APIs with `httpx.MockTransport` and use MCP sessions to check tool discovery, permissions, native IDs, both Hydra handoffs, redirects, response limits, uncertain mutations, concurrency and real subprocess stdio.

Contract coverage targets Sonarr/Radarr `/api/v3`, NZBGet v18+, SABnzbd 5.x and NZBHydra's public Newznab API. Mocked contracts don't certify every server release. Use disposable media and queues for live mutation tests.

[Implementation notes](IMPLEMENTATION_PLAN.md) cover the code structure and follow-up work. Changes to API operations should include a mock contract test.
