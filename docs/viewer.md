# Viewer

!!! warning "Experimental"
    The viewer is experimental and can change without notice.

The [hosted viewer](viewer/) is a standalone page: no Python, no build step, nothing is uploaded to a server.
It opens blank with Light, Dark and OSM basemaps. Enter a GeoZarr store URL in the top bar, or pass it in the URL.
The page loads its JavaScript dependencies and basemap tiles from the internet.

**[Open the viewer](viewer/){ .md-button .md-button--primary }**

## Variable cards

The sidebar lists the variables in two sections. **Selected (n)** holds the variables currently shown, top-most layer
first; **Other (n)** holds the rest in dataset order and can be collapsed with its header (the choice is remembered in
the browser). Clicking a card moves it between the sections. At least one variable stays selected, and the Other
section is hidden when every variable is selected.

## URL parameters

| Parameter | Meaning |
|---|---|
| `store` | Store URL (`https://...`), a relative URL (`./data.zarr`), or a public S3 path `s3://bucket/key` |
| `endpoint` | S3 endpoint URL, used with `s3://` stores |

Example:

```text
viewer/?store=https://example.org/data.zarr
viewer/?store=s3://my-bucket/data.zarr&endpoint=https://s3.example.org
```

## Requirements for remote stores

Because the page runs in your browser, the store host must:

- allow **CORS** for the viewer's origin (`https://lukegre.github.io`) when the store is on another origin, and
- support **HTTP Range** requests (needed for sharded arrays).

Stores need GeoZarr v3 metadata with consolidated array metadata, or the variable-order attribute written by this
package.

!!! note "Buckets without CORS"
    Many public buckets do not set CORS rules, so they will not load in the hosted viewer. Use the local Python
    preview server below instead.

## Local preview server (no CORS needed)

The Python server serves the viewer and relays remote or local stores, so filesystem paths and buckets without CORS
work:

```bash
geozarr-pyramid preview out.zarr --serve --port 8000
geozarr-pyramid preview            # blank viewer; type a path or URL into the sidebar
```

## Docker and RenkuLab

The
[`Dockerfile`](https://github.com/lukegre/geozarr-pyramid-maker/blob/main/Dockerfile)
builds an image that starts the blank viewer (`geozarr-pyramid preview` with no store) on port 8888. Type a store
path or URL into the sidebar to open any pyramid; relative paths resolve from the working directory. CI publishes it
to Docker Hub as `lukegre/geozarr-viewer` on every push to `main`.

**RenkuLab session launcher**: add a custom environment with

| Field | Value |
|---|---|
| Container image | `lukegre/geozarr-viewer:latest` |
| Default URL | `/` |
| Port | `8888` |
| UID / GID | `1000` / `100` |
| Mount directory / Working directory | `/home/renku/work` |
| Command / Arguments | leave empty (the image's entrypoint is used) |

The server reads `RENKU_BASE_URL_PATH` (RenkuLab does not strip it) and `RENKU_WORKING_DIR`, so data connectors and
project files under the working directory open by relative path. Keep the Docker Hub repository public, or add
registry credentials in RenkuLab.

**Locally**: `docker compose up --build`, then open <http://127.0.0.1:8888/>. Stores in `./data` (or
`DATA_DIR=/path/to/stores`) are mounted as the working directory. Set `RENKU_BASE_URL_PATH` to try a URL prefix.
