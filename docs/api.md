# GeoGuessr Map Maker API

These private `https://www.geoguessr.com` calls need your `_ncfa` cookie, and the ones marked *(untested)* haven't been tried on the live site.

| Call | What it does |
|---|---|
| `GET /api/v3/profiles/me` | Get your profile, which also checks that your cookie works. |
| `GET /api/v4/user-maps/maps?page=0&count=20` | List your maps, including drafts. |
| `GET /api/v4/user-maps/maps/{id}` | Get a map, or a `404` if it has never been published. |
| `POST /api/v4/user-maps/drafts` | Create a map with a `name` and a `mode` of `regions` or `coordinates`. |
| `GET /api/v4/user-maps/drafts/{id}` | Get a map's draft, or a `400` if it doesn't exist. |
| `PUT /api/v4/user-maps/drafts/{id}` | Replace a map's draft, sending `version` as the current version + 1. |
| `PUT /api/v4/user-maps/drafts/{id}/publish/{version}` | Publish a draft at its current version. *(untested)* |
| `PUT /api/v4/user-maps/{id}/unpublish` | Take a map offline. *(untested)* |
| `DELETE /api/v4/user-maps/drafts/{id}` | Delete a map that has never been published. |
| `DELETE /api/v4/user-maps/{id}` | Delete a published map. *(untested)* |
| `POST /api/v4/user-maps/region-count` | Count the Street View spots inside a polygon. |

## Checking against the live site

The normal tests run against a fake server, so they can't tell you when the real API changes. This does, using your cookie:

```sh
uv run pytest -m live
```

It creates unpublished maps named "ggmm live test (safe to delete)" and deletes them. It never publishes, so the calls marked *(untested)* stay untested.
