"""ggmm: build and manage GeoGuessr maps from the command line."""

import json
import sys
from pathlib import Path
from typing import Annotated, Literal, cast

import httpx
import typer
from pydantic import ValidationError
from rich.console import Console
from rich.progress import MofNCompleteColumn, Progress

from . import geojson
from ._version import __version__
from .client import COVERAGE_DELAY_S, ENV_VAR, Client
from .errors import AuthError, GeoGuessrError
from .geo import CIRCLE_VERTICES, MIN_FIVE_K_M
from .models import MIN_LOCATIONS_TO_PUBLISH, Draft, Location, Mode, Region, map_url

app = typer.Typer(
    help="Build and manage GeoGuessr maps from GeoJSON (unofficial).",
    epilog=f"Auth: set {ENV_VAR} to your geoguessr.com _ncfa cookie, or put it in .env.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,  # a crash report would otherwise print the cookie
)

MapArg = Annotated[str, typer.Argument(metavar="MAP", help="map id or URL")]
Output = Annotated[Path | None, typer.Option("--output", "-o", help="file to write")]
RadiusM = Annotated[float, typer.Option(min=1, help="circle radius when points become regions")]
Vertices = Annotated[int, typer.Option(min=3, help="vertices per circle")]
Delay = Annotated[float, typer.Option(min=0, help="seconds between coverage requests")]
DEFAULT_RADIUS_M = 400


def client(ctx: typer.Context) -> Client:
    """The API client, created on first use so commands that stay offline don't need a cookie."""
    root = ctx.find_root()
    if root.obj is None:
        # with_resource closes it when the command ends
        root.obj = root.with_resource(Client.from_env(env_file=root.params["env_file"]))
    return cast(Client, root.obj)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _write_json(data: object, output: Path | None, pretty: bool = False) -> None:
    text = json.dumps(data, indent=1) if pretty else json.dumps(data, separators=(",", ":"))
    if output:
        output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def _covered(gg: Client, regions: list[Region], delay: float) -> list[Region]:
    columns = (*Progress.get_default_columns(), MofNCompleteColumn())
    with Progress(*columns, console=Console(stderr=True), transient=True) as bar:
        checks = gg.check_coverage(regions, delay=delay)
        counts = list(bar.track(checks, total=len(regions), description="Checking coverage"))
    kept = [region for region, count in zip(regions, counts, strict=True) if count > 0]
    _err(
        f"  {len(kept)}/{len(regions)} regions have Street View "
        f"({sum(counts):,} locations in total)"
    )
    return kept


def _load(path: Path) -> geojson.Shapes:
    shapes = geojson.load(path)
    for w in dict.fromkeys(shapes.warnings):  # dedupe, keep order
        _err(f"warning: {w}")
    return shapes


def _choose_mode(draft: Draft | None, requested: Mode | None, shapes: geojson.Shapes) -> Mode:
    """Existing maps keep their mode. New ones use --mode, or regions if the file has polygons."""
    if draft is None:
        if requested:
            return requested
        return "regions" if shapes.regions else "coordinates"
    if requested and requested != draft.mode:
        message = f"{draft.name!r} is a {draft.mode} map and its mode can't change; use --new"
        raise typer.BadParameter(message, param_hint="'--mode'")
    return draft.mode


def _shapes_as_regions(shapes: geojson.Shapes, radius_m: float, vertices: int) -> list[Region]:
    if shapes.locations:
        _err(f"{len(shapes.locations)} points -> circles of {radius_m:g} m")
    return shapes.to_regions(radius_m, vertices)


def _shapes_as_locations(shapes: geojson.Shapes) -> list[Location]:
    if shapes.regions:
        raise typer.BadParameter(
            "it has polygons, which only a regions map can hold", param_hint="'FILE'"
        )
    return shapes.locations


def _require_enough_to_publish(total: int) -> None:
    if total < MIN_LOCATIONS_TO_PUBLISH:
        need = f"GeoGuessr needs at least {MIN_LOCATIONS_TO_PUBLISH} locations to publish"
        raise typer.BadParameter(
            f"{need}, and the map would have {total}", param_hint="'--publish'"
        )


def _set_contents(
    draft: Draft, regions: list[Region], locations: list[Location], append: bool
) -> None:
    if append:
        draft.regions += regions
        draft.locations += locations
    else:
        draft.regions, draft.locations = regions, locations


def _show_version(value: bool) -> None:
    if value:
        print(f"ggmm {__version__}")
        raise typer.Exit()


@app.callback()
def _options(
    env_file: Annotated[
        str, typer.Option(help="read the cookie from this file if it isn't in the environment")
    ] = ".env",
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_show_version, is_eager=True, help="show the version and exit"
        ),
    ] = False,
) -> None:
    pass


@app.command()
def whoami(ctx: typer.Context) -> None:
    """Check that your cookie works."""
    me = client(ctx).me()
    print(f"Logged in as {me.nick} ({me.id})")


@app.command()
def maps(
    ctx: typer.Context,
    page: int = 0,
    count: int = 20,
    all_: Annotated[bool, typer.Option("--all", help="fetch every page")] = False,
) -> None:
    """List your maps."""
    gg = client(ctx)
    found = list(gg.iter_maps()) if all_ else gg.list_maps(page, count)
    for m in found:
        print(f"{m.id}  {'live ' if m.published else 'draft'}  {m.coordinate_count:>7}  {m.name}")
    if not found:
        print("No maps.")


@app.command()
def show(ctx: typer.Context, map_: MapArg) -> None:
    """Summarize a map's draft."""
    draft = client(ctx).get_draft(map_)
    five_k = draft.five_k_distance_m
    print(draft.name)
    print(f"  id       {draft.id}")
    print(f"  mode     {draft.mode}")
    print(f"  size     {draft.size} {draft.mode}")
    print(f"  version  {draft.version}")
    print(f"  scoring  {f'5K within {five_k:.0f} m' if five_k else 'automatic'}")
    print(f"  updated  {draft.updated or '-'}")
    print(f"  edit     {draft.edit_url}")


@app.command()
def new(
    ctx: typer.Context,
    name: str,
    mode: Annotated[Mode, typer.Option(help="regions (polygons) or coordinates (exact spots)")],
) -> None:
    """Create an empty map."""
    draft = client(ctx).create_draft(name, mode)
    print(f"Created {draft.mode} map {draft.id}: {draft.edit_url}")


@app.command()
def upload(
    ctx: typer.Context,
    file: Annotated[
        Path,
        typer.Argument(
            metavar="FILE", help=".geojson, or a GeoGuessr / map-making.app .json export"
        ),
    ],
    map_: Annotated[
        str,
        typer.Option(
            "--map",
            show_default=False,
            help="existing map id or URL (its locations are replaced unless --append)",
        ),
    ] = "",
    new: Annotated[
        str,
        typer.Option(metavar="NAME", show_default=False, help="create a new map with this name"),
    ] = "",
    mode: Annotated[
        Mode | None, typer.Option(help="for --new: default is regions if the file has polygons")
    ] = None,
    append: Annotated[
        bool, typer.Option("--append", help="add to the map's existing locations")
    ] = False,
    check_coverage: Annotated[
        bool, typer.Option("--check-coverage", help="drop regions with no Street View")
    ] = False,
    five_k_m: Annotated[
        float | None,
        typer.Option(min=MIN_FIVE_K_M, help="custom scoring: guesses this close score 5,000"),
    ] = None,
    publish: Annotated[bool, typer.Option("--publish", help="publish after saving")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="show what would happen without saving")
    ] = False,
    radius_m: RadiusM = DEFAULT_RADIUS_M,
    vertices: Vertices = CIRCLE_VERTICES,
    delay: Delay = COVERAGE_DELAY_S,
) -> None:
    """Upload GeoJSON / GeoGuessr JSON into a map."""
    if bool(map_) == bool(new):
        raise typer.BadParameter("give exactly one of them", param_hint="'--map' / '--new'")
    shapes = _load(file)
    draft = client(ctx).get_draft(map_) if map_ else None
    map_mode = _choose_mode(draft, mode, shapes)

    regions: list[Region] = []
    locations: list[Location] = []
    if map_mode == "regions":
        regions = _shapes_as_regions(shapes, radius_m, vertices)
        if check_coverage:
            regions = _covered(client(ctx), regions, delay)
    else:
        locations = _shapes_as_locations(shapes)
    count = len(regions) + len(locations)
    if not count:
        raise ValueError("nothing to upload")
    if publish:
        _require_enough_to_publish(count + (draft.size if draft and append else 0))

    if dry_run:
        target = repr(draft.name) if draft else f"new map {new!r}"
        print(f"Dry run: would {'append' if append else 'upload'} {count} {map_mode} to {target}")
        return

    gg = client(ctx)
    if draft is None:
        draft = gg.create_draft(new, map_mode)
        _err(f"Created {map_mode} map {draft.id}")
    _set_contents(draft, regions, locations, append)
    if five_k_m:
        draft.set_five_k_distance(five_k_m)
    gg.save_draft(draft)
    print(f"Saved {draft.size} {map_mode} to {draft.name!r}: {draft.edit_url}")

    if publish:
        gg.publish(draft)
        print(f"Published: {map_url(draft.id)}")


@app.command()
def coverage(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(metavar="FILE")],
    output: Output = None,
    radius_m: RadiusM = DEFAULT_RADIUS_M,
    vertices: Vertices = CIRCLE_VERTICES,
    delay: Delay = COVERAGE_DELAY_S,
) -> None:
    """Check which shapes in a file have Street View (no map changes)."""
    shapes = _load(file)
    kept = _covered(client(ctx), shapes.to_regions(radius_m, vertices), delay)
    if output:
        _write_json(geojson.to_geojson(kept), output)
        _err(f"Wrote {len(kept)} covered regions to {output}")


@app.command()
def export(
    ctx: typer.Context,
    map_: MapArg,
    output: Output = None,
    format_: Annotated[Literal["geojson", "geoguessr"], typer.Option("--format")] = "geojson",
    pretty: Annotated[bool, typer.Option("--pretty", help="indent the JSON")] = False,
) -> None:
    """Download a map as GeoJSON or GeoGuessr JSON (to stdout unless -o is given)."""
    draft = client(ctx).get_draft(map_)
    data = (
        geojson.draft_to_geojson(draft)
        if format_ == "geojson"
        else geojson.draft_to_geoguessr_json(draft)
    )
    _write_json(data, output, pretty)
    if output:
        _err(f"Wrote {draft.size} {draft.mode} from {draft.name!r} to {output}")


@app.command()
def publish(ctx: typer.Context, map_: MapArg) -> None:
    """Publish a map's saved draft."""
    gg = client(ctx)
    draft = gg.get_draft(map_)
    gg.publish(draft)
    print(f"Published {draft.name!r}: {map_url(draft.id)}")


@app.command()
def unpublish(ctx: typer.Context, map_: MapArg) -> None:
    """Take a map offline."""
    client(ctx).unpublish(map_)
    print("Unpublished.")


@app.command()
def delete(
    ctx: typer.Context,
    map_: MapArg,
    yes: Annotated[bool, typer.Option("--yes", help="skip the confirmation prompt")] = False,
) -> None:
    """Permanently delete a map."""
    gg = client(ctx)
    draft = gg.get_draft(map_)
    if not yes:
        answer = input(
            f"Permanently delete {draft.name!r} ({draft.size} {draft.mode})? "
            "Type its name to confirm: "
        )
        if answer.strip() != draft.name:
            print("Aborted.")
            raise typer.Exit(1)
    if gg.is_published(draft.id):
        gg.delete_map(draft.id)
    else:
        gg.delete_draft(draft.id)
    print(f"Deleted {draft.name!r}.")


def main(argv: list[str] | None = None) -> int:
    """Run ggmm and return its exit code.

    1 bad file or data, 2 bad usage (Typer's code), 3 GeoGuessr or network error, 4 not logged in.
    """
    try:
        app(argv, prog_name="ggmm")
    except SystemExit as e:  # typer finishes every run this way, with the exit code
        return e.code if isinstance(e.code, int) else 0
    except ValidationError as e:  # bad shapes in a file become warnings, so this is an API response
        _err(f"unexpected response from GeoGuessr: {e}")
        return 3
    except (
        OSError,
        ValueError,
    ) as e:  # unreadable files, bad JSON or shapes, nothing to upload or publish
        _err(f"error: {e}")
        return 1
    except AuthError as e:
        _err(str(e))
        return 4
    except GeoGuessrError as e:
        _err(str(e))
        return 3
    except httpx.HTTPError as e:
        _err(f"network error: {e}")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
