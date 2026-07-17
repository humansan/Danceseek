"""SoundSeek CLI: ingest and inspect 1001tracklists setlists."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from . import store
from .models import Setlist

app = typer.Typer(help="SoundSeek v0 - 1001tracklists setlist ingestor", no_args_is_help=True)
console = Console()


def _summarize(setlist: Setlist) -> None:
    console.print(f"\n[bold]{setlist.title or setlist.source_url}[/bold]")
    if setlist.event or setlist.date_recorded:
        console.print(f"  {setlist.event or ''}  {setlist.date_recorded or ''}".rstrip())
    if setlist.genres:
        console.print(f"  genres: {', '.join(setlist.genres)}")
    if setlist.media_url:
        console.print(f"  media: {setlist.media_url} ({setlist.media_kind})")

    n_id = sum(t.is_id for t in setlist.tracks)
    n_w = sum(t.played_with is not None for t in setlist.tracks)
    n_mash = sum(bool(t.mashup_components) for t in setlist.tracks)
    console.print(
        f"  [green]{len(setlist.tracks)} tracks[/green]"
        f" | {n_id} unreleased (ID) | {n_w} layered (w/) | {n_mash} mashups"
    )
    console.print(f"  id: {setlist.id}")


def _track_table(setlist: Setlist) -> Table:
    table = Table(title=setlist.title or setlist.source_url)
    table.add_column("#", justify="right", style="dim")  # 1001TL's number ("w/" rows blank)
    table.add_column("row", justify="right", style="dim")  # our unique position
    table.add_column("cue", style="dim")
    table.add_column("artists")
    table.add_column("title")
    table.add_column("remix", style="cyan")
    table.add_column("flags", style="magenta")
    table.add_column("res", style="green")  # resolution: S/Y/L per platform
    for t in setlist.tracks:
        flags = []
        if t.is_id:
            flags.append("ID")
        if t.played_with is not None:
            flags.append(f"w/ #{t.played_with}" if t.played_with >= 0 else "w/")
        if t.mashup_components:
            flags.append("mashup")
        table.add_row(
            str(t.source_track_number) if t.source_track_number is not None else "w/",
            str(t.position),
            t.cue_time or "",
            ", ".join(t.artists),
            t.title or ("?" if t.is_id else t.raw_text),
            t.remix or "",
            " ".join(flags),
            _res_cell(t.resolution),
        )
    return table


def _res_cell(res) -> str:
    if res is None:
        return ""
    if res.status == "unreleased":
        return "unrel"
    if res.status == "no_match":
        return "—"
    platforms = "".join(
        letter for letter, match in (("S", res.spotify), ("Y", res.youtube), ("L", res.lastfm)) if match
    )
    return f"{platforms} {res.confidence:.2f}".strip()


@app.command()
def ingest(
    url: str = typer.Argument(help="1001tracklists tracklist URL"),
    force: bool = typer.Option(False, "--force", help="Re-fetch and re-parse even if already ingested"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Debug: extract only, skip LLM normalization, don't persist"),
) -> None:
    """Scrape, parse, and store a 1001tracklists setlist."""
    from .pipeline import ingest as run_ingest  # lazy: playwright/langchain imports

    already = store.lookup(url) if not force else None
    setlist = run_ingest(url, force=force, skip_llm=no_llm)

    if no_llm:
        console.print("[yellow]--no-llm: raw extraction only, not persisted[/yellow]")
        console.print(_track_table(setlist))
        return

    if already:
        console.print("[yellow]Already ingested (use --force to re-ingest):[/yellow]")
    _summarize(setlist)
    console.print(f"  file: {store.setlist_path(setlist.id)}")


@app.command()
def resolve(
    ref: str = typer.Argument(help="Setlist id or source URL"),
    force: bool = typer.Option(False, "--force", help="Re-resolve tracks that already have a resolution"),
    limit: int = typer.Option(None, "--limit", help="Debug: resolve only the first N unresolved tracks"),
) -> None:
    """Resolve a stored setlist's tracks to Spotify / YouTube / Last.fm."""
    from .resolver.resolve import resolve_setlist  # lazy: heavy imports

    try:
        setlist = store.load_by_url(ref) if ref.startswith("http") else store.load(ref)
    except FileNotFoundError:
        setlist = None
    if setlist is None:
        console.print(f"[red]No stored setlist for {ref} — ingest it first.[/red]")
        raise typer.Exit(1)

    summary = resolve_setlist(setlist, force=force, limit=limit)

    for warning in summary.warnings:
        console.print(f"[yellow]warning: {warning}[/yellow]")
    console.print(
        f"\n[bold]{setlist.title}[/bold]\n"
        f"  [green]{summary.resolved} resolved[/green]"
        f" | {summary.partial} partial"
        f" | {summary.no_match} no match"
        f" | {summary.unreleased} unreleased"
        f" | {summary.skipped} skipped (already done)"
    )
    console.print(
        f"  registry cache hits: {summary.registry_hits} | LLM batches: {summary.llm_batches}"
    )
    console.print(f"  file: {store.setlist_path(setlist.id)}")


@app.command()
def show(ref: str = typer.Argument(help="Setlist id or source URL")) -> None:
    """Pretty-print a stored setlist."""
    try:
        setlist = store.load_by_url(ref) if ref.startswith("http") else store.load(ref)
    except FileNotFoundError:
        setlist = None
    if setlist is None:
        console.print(f"[red]No stored setlist for {ref}[/red]")
        raise typer.Exit(1)
    console.print(_track_table(setlist))


@app.command(name="list")
def list_cmd() -> None:
    """List all ingested setlists."""
    setlists = store.list_all()
    if not setlists:
        console.print("No setlists ingested yet. Try: soundseek ingest <url>")
        return
    table = Table()
    table.add_column("scraped", style="dim")
    table.add_column("title")
    table.add_column("tracks", justify="right")
    table.add_column("id", style="dim")
    for s in setlists:
        table.add_row(s.scraped_at[:10], s.title or s.source_url, str(len(s.tracks)), s.id)
    console.print(table)


if __name__ == "__main__":
    app()
