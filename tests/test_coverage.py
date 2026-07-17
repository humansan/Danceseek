"""build_coverage: per-status counts from the run summary, per-platform counts
scanned from the persisted resolutions (including mashup components)."""

from soundseek.models import (
    LastfmMatch,
    MashupComponent,
    ParserInfo,
    PlatformMatch,
    Resolution,
    Setlist,
    SetlistTrack,
)
from soundseek.resolver.resolve import ResolveSummary, build_coverage


def _spotify():
    return PlatformMatch(id="s", title="t", artists=["A"], url="u")


def _youtube(vid="y"):
    return PlatformMatch(id=vid, title="t", artists=["A"], url="u")


def test_build_coverage_counts_status_and_platforms():
    summary = ResolveSummary(
        resolved=2, partial=1, no_match=1, unreleased=1, skipped=4, registry_hits=3
    )
    full = Resolution(
        status="resolved", spotify=_spotify(), youtube=_youtube(), lastfm=LastfmMatch(artist="A", track="t")
    )
    yt_only = Resolution(status="partial", youtube=_youtube("y2"))
    setlist = Setlist(
        source_url="u",
        parser=ParserInfo(model="test"),
        tracks=[
            SetlistTrack(position=1, raw_text="x", resolution=full),
            SetlistTrack(position=2, raw_text="x", resolution=yt_only),
            SetlistTrack(position=3, raw_text="x", resolution=None),
        ],
    )

    cov = build_coverage(setlist, summary)

    assert cov["total"] == 5  # resolved+partial+no_match+unreleased (skipped excluded)
    assert cov["resolved"] == 2
    assert cov["partial"] == 1
    assert cov["no_match"] == 1
    assert cov["unreleased"] == 1
    assert cov["skipped"] == 4
    assert cov["registry_hits"] == 3
    assert cov["spotify"] == 1
    assert cov["youtube"] == 2  # full + yt_only
    assert cov["lastfm"] == 1


def test_build_coverage_counts_mashup_components():
    summary = ResolveSummary(resolved=2)
    component = MashupComponent(
        artists=["B"], title="part", resolution=Resolution(status="resolved", spotify=_spotify())
    )
    setlist = Setlist(
        source_url="u",
        parser=ParserInfo(model="test"),
        tracks=[
            SetlistTrack(
                position=1,
                raw_text="x",
                resolution=Resolution(status="resolved", spotify=_spotify()),
                mashup_components=[component],
            )
        ],
    )

    cov = build_coverage(setlist, summary)
    assert cov["spotify"] == 2  # top-level track + its component
