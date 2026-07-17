"""SoundSeek CLI: ingest and inspect 1001tracklists setlists."""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.table import Table

from . import store
from .models import Setlist

# Track/artist names carry non-Latin-1 characters (Ørjan, Beyoncé, →); the
# Windows console defaults to cp1252 and would crash on them.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

app = typer.Typer(help="SoundSeek v0 - 1001tracklists setlist ingestor", no_args_is_help=True)
console = Console()


def _load_or_exit(ref: str) -> Setlist:
    """Load a stored setlist by id or URL, or exit with a clear message."""
    try:
        setlist = store.load_by_url(ref) if ref.startswith("http") else store.load(ref)
    except FileNotFoundError:
        setlist = None
    if setlist is None:
        console.print(f"[red]No stored setlist for {ref} — ingest it first.[/red]")
        raise typer.Exit(1)
    return setlist


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


def _coverage_table(plan) -> Table:
    from collections import Counter

    table = Table(title=f"Export plan → {plan.target}")
    table.add_column("#", justify="right", style="dim")
    table.add_column("track / reason")
    table.add_column("status", style="magenta")
    for i, item in enumerate(plan.items, 1):
        table.add_row(str(i), item.label, "[green]add[/green]")
    for label, reason in plan.skipped:
        table.add_row("", f"[dim]{label}[/dim]", f"[yellow]skip: {reason}[/yellow]")
    reasons = Counter(reason for _, reason in plan.skipped)
    if reasons:
        table.caption = "skipped: " + ", ".join(f"{n} {r}" for r, n in reasons.most_common())
    return table


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
def push(
    ref: str = typer.Argument(None, help="Setlist id or source URL (omit with --all)"),
    all_: bool = typer.Option(False, "--all", help="Push every locally stored setlist"),
) -> None:
    """Upsert processed setlist(s) into the Neon cache table."""
    from . import db  # lazy: psycopg import

    if all_:
        setlists = store.list_all()
    elif ref:
        try:
            found = store.load_by_url(ref) if ref.startswith("http") else store.load(ref)
        except FileNotFoundError:
            found = None
        if found is None:
            console.print(f"[red]No stored setlist for {ref}[/red]")
            raise typer.Exit(1)
        setlists = [found]
    else:
        console.print("[red]Give a setlist id/URL or use --all.[/red]")
        raise typer.Exit(1)

    try:
        for s in setlists:
            db.push(s)
            console.print(f"[green]pushed[/green] {s.title or s.source_url} ({len(s.tracks)} tracks)")
    except db.DbError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command()
def remote() -> None:
    """List setlists stored in the Neon cache table."""
    from . import db

    try:
        rows = db.list_remote()
    except db.DbError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if not rows:
        console.print("Remote table is empty. Try: soundseek push --all")
        return
    table = Table()
    table.add_column("pushed", style="dim")
    table.add_column("title")
    table.add_column("tracks", justify="right")
    table.add_column("id", style="dim")
    for r in rows:
        table.add_row(r["pushed_at"][:19], r["title"] or "", str(r["tracks"]), r["id"])
    console.print(table)


@app.command()
def export(
    ref: str = typer.Argument(help="Setlist id or source URL"),
    target: str = typer.Option(..., "--target", help="spotify or youtube"),
    name: str = typer.Option(None, "--name", help="Playlist name (default: '<title> (via SoundSeek)')"),
    public: bool = typer.Option(False, "--public/--private", help="Playlist visibility"),
    skip_played_with: bool = typer.Option(False, "--skip-played-with", help="Exclude layered (w/) tracks"),
    no_expand_mashups: bool = typer.Option(False, "--no-expand-mashups", help="Don't split mashups into components"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be exported without creating anything"),
) -> None:
    """Export a resolved setlist to a Spotify or YouTube playlist."""
    if target not in ("spotify", "youtube"):
        console.print("[red]--target must be 'spotify' or 'youtube'.[/red]")
        raise typer.Exit(1)

    from .exporter.export import export_setlist  # lazy: httpx/oauth imports
    from .exporter.oauth import OAuthError

    setlist = _load_or_exit(ref)
    try:
        result = export_setlist(
            setlist, target, name=name, public=public,
            expand_mashups=not no_expand_mashups, skip_played_with=skip_played_with,
            dry_run=dry_run,
        )
    except OAuthError as e:
        console.print(f"[red]Auth not configured: {e}[/red]")
        raise typer.Exit(1)

    plan = result.plan
    console.print(_coverage_table(plan))
    verb = "would add" if result.dry_run else "added"
    console.print(
        f"\n[bold]{setlist.title}[/bold] → {target}\n"
        f"  [green]{plan.added} {verb}[/green] | {len(plan.skipped)} skipped"
        f" (of {plan.total_considered} considered)"
    )
    if result.dry_run:
        console.print("  [dim]dry run — nothing created. Drop --dry-run to export.[/dim]")
        return
    if plan.added == 0:
        console.print("  [yellow]Nothing resolved for this target — nothing to export.[/yellow]")
        return
    console.print(f"  [green]{result.added} added to the playlist[/green]")
    if result.failed:
        quota = sum(1 for _, r in result.failed if r == "quota_exceeded")
        other = len(result.failed) - quota
        note = []
        if quota:
            note.append(f"{quota} not added (daily quota reached — re-run to continue)")
        if other:
            note.append(f"{other} failed ({', '.join(sorted({r for _, r in result.failed if r != 'quota_exceeded'}))})")
        console.print(f"  [yellow]{'; '.join(note)}[/yellow]")
    if result.url:
        console.print(f"  [bold cyan]{result.url}[/bold cyan]")


@app.command()
def show(ref: str = typer.Argument(help="Setlist id or source URL")) -> None:
    """Pretty-print a stored setlist."""
    console.print(_track_table(_load_or_exit(ref)))


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


@app.command()
def reset(
    database: bool = typer.Option(False, "--database", help="Also empty the Neon cache table"),
    all_: bool = typer.Option(
        False, "--all", help="Also remove login state: Cloudflare browser profile + OAuth tokens"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Delete local processed data to start fresh.

    Removes setlists, cached HTML, the track registry, the URL index, and debug
    logs. Login state (Cloudflare browser profile, OAuth tokens) is kept unless
    --all. Use --database to also empty the Neon cache table.
    """
    import shutil

    from .config import settings

    targets = [
        ("setlists", settings.setlists_dir),
        ("cached HTML", settings.raw_html_dir),
        ("LLM input dumps", settings.llm_inputs_dir),
        ("resolution logs", settings.resolution_logs_dir),
        ("track registry", settings.tracks_path),
        ("URL index", settings.index_path),
    ]
    if all_:
        targets += [
            ("browser profile (Cloudflare clearance)", settings.browser_profile_dir),
            ("OAuth tokens", settings.auth_dir),
        ]

    present = [(label, p) for label, p in targets if p.exists()]

    console.print("[bold]This will delete:[/bold]")
    for label, p in present:
        console.print(f"  - {label}  [dim]{p}[/dim]")
    if database:
        console.print("  - [bold]all rows in the Neon cache table[/bold]")
    if not present and not database:
        console.print("  [dim](nothing found — already clean)[/dim]")
        return
    if not all_:
        console.print(
            "[dim]Keeping login state (browser profile, OAuth tokens); pass --all to remove those too.[/dim]"
        )

    if not yes and not typer.confirm("Proceed?"):
        console.print("[yellow]Aborted — nothing deleted.[/yellow]")
        raise typer.Exit(1)

    for label, p in present:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        console.print(f"[green]removed[/green] {label}")

    if database:
        from . import db

        try:
            removed = db.clear()
            console.print(f"[green]cleared[/green] Neon cache table ({removed} rows)")
        except db.DbError as e:
            console.print(f"[yellow]database not cleared: {e}[/yellow]")

    console.print("\n[bold green]Reset complete.[/bold green]")


if __name__ == "__main__":
    app()
