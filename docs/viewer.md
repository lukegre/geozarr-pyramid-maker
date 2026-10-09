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
the browser). Cards show the array names (or the long names, see Global settings below). Clicking an Other card adds it to Selected; clicking a selected card keeps
it selected. The eye icon shows or hides its map layer while keeping the card in Selected; the × icon removes it
to Other. Expand settings to see the long name when available (the array name when labels are by long name). Drag a card by its handle to reorder Selected or move
it between sections. The map follows the selected card order. All cards can be removed. Other remains visible as a
drop target even when it is empty or collapsed. Visibility is preserved in shared URLs with `hidden=a,b`.

In a card's settings the colour limits are one **Range** row (min and max). **Auto** sets them from the data of the
current frame (2nd to 98th percentile of the loaded tiles; symmetric around zero when both signs are present and
balanced, in which case a non-diverging colourmap is switched to RdBu reversed and a diverging one is kept) and
**Min/Max** sets them to the true minimum and maximum of the current frame (every loaded pixel, no percentile clipping;
the colourmap is left alone) and **Symmetric** mirrors the current limits. Auto and Min/Max use the tiles loaded
for the current dimension slice, so the result can change with the viewport; with nothing loaded they leave the range
alone.

**Log scale** (next to **Reversed**) spaces the colours logarithmically, for positive data spanning decades such as
chlorophyll. It needs a positive Min (the box is disabled otherwise and the map stays linear), the legend then shows
1-2-5 ticks on a log axis, Auto and Min/Max ignore values of zero or below, and Symmetric is disabled. It is kept in the
browser and in shared URLs as `log=1`.

With the page focused (or the timeline), the left and right arrow keys step the horizontal dimension and the up and
down arrow keys step the first vertical dimension (up goes to the previous index, down to the next one). On a focused
vertical slider, up and down step that slider and left and right step the timeline. Keys with a modifier are left to the
browser.

## Global settings

The block at the bottom of the sidebar starts with **Global settings**. **Label by** chooses how variables are named
everywhere (cards, the collapsed sidebar, the pointer readout and the info panel): **Array name** (default) or **Long
name**, which falls back to the array name for variables without a `long_name`. It switches in place and is remembered
in the browser, not in the URL. It is hidden while no store is loaded. Basemap, Dimensions, Colourmaps and Centre
longitude follow.

Click the **Global settings** heading (cog icon and chevron) to collapse the block and give the cards more room; it is
expanded by default, and the choice is remembered in the browser, not in the URL. Errors and notes stay visible when collapsed.

## Colourmaps

The colourmap list in a card's settings is grouped like the matplotlib reference (perceptually uniform, sequential,
diverging, cyclic, miscellaneous), plus the **cmocean** and **cmasher** collections (listed as `cmo.<name>` and
`cmr.<name>` in the URL), each split into Sequential, Diverging and Cyclic subheadings (for example
`cmocean: Diverging`). Three checkboxes in the sidebar footer under Dimensions, **matplotlib**, **cmocean** and
**cmasher**, decide which of these are listed: matplotlib is on by default, the other two are off. A card keeps its current
colourmap in the list even when its group is hidden, and styles from a shared URL render with any map regardless of
the checkboxes. At least one of the three always stays checked (the last one is disabled). The choice is remembered in the browser. With a colourmap menu focused, the left and right arrow keys step to the previous and next colourmap, handled explicitly by the viewer because browsers differ (hover the menu for a tip). Space toggles **Reversed** for that card (when the dropdown list is already open, Space selects the highlighted option, as in the browser).

## Plot defaults from attrs

A variable can set how it first appears through its attrs, which `to_pyramid` stores on every level:

```python
da.attrs.update(cmap="cmo.thermal", vmin=270, vmax=305)
da.geozarr.to_pyramid("sst.zarr")
```

`cmap` is any colourmap name from the list above (`viridis`, `cmo.thermal`, `cmr.ember`, ...); a matplotlib-style
`_r` suffix such as `viridis_r` selects the map with **Reversed** switched on. `vmin` and `vmax` replace the default
2-98 % percentile limits and can be set independently; if both are given and `vmax <= vmin`, both are ignored.
Unknown colourmap names fall back to viridis with a warning, and categorical variables ignore all three attrs.
Settings from the URL or stored in the browser take precedence.

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
