# usjetaa-feed-mirror

Serves a repaired copy of the USJETAA events RSS feed at:

**https://adriant.io/usjetaa-feed-mirror/usjetaa.xml**

MonitoRSS points at this instead of Wild Apricot directly, so USJETAA
announcements in the JETAASC Discord render with their event graphic.

## Why this exists

Wild Apricot's RSS widget writes image paths as
`usjetaa.wildapricot.org/widget/resources/Pictures/...`, which return **404**.
The identical file serves fine from `/resources/Pictures/...`. Discord cannot
build a thumbnail from a URL it cannot fetch, and the event pages carry no
OpenGraph tags either, so posts arrive as bare text.

MonitoRSS can rewrite a placeholder with a regex, but Custom Placeholders and
External Properties are both paid tiers. Repairing the feed upstream gets the
same result on the free tier.

## What the build does

`scripts/build_mirror.py` fetches the upstream feed and:

1. rewrites `/widget/resources/` to `/resources/` so images resolve
2. adds an `<enclosure>` holding the first image that actually resolves, so
   consumers get a clean image field rather than having to dig one out of
   description HTML
3. normalizes the description HTML so it survives conversion to markdown

It refuses to write output if the upstream returned no items or if any broken
path survived, so a bad fetch leaves the last good mirror in place.

### Why the description is parsed, not pattern-matched

Wild Apricot's editor emits deeply nested cruft: images four wrappers deep
(`<p><span><font><img></font></span></p>`), line breaks inside emphasis
(`<em>featuring<br></em>`, which becomes `*featuring\n*` and renders as
literal asterisks because Discord will not pair a marker alone on a line), and
`<strong>` around nothing but an empty anchor.

Matching each shape with its own regex meant a new rule for every new shape,
and it still missed cases. Parsing to a tree allows the recursive question a
regex cannot ask -- *does anything under this node render as visible text?* --
so three invariants cover the whole class:

1. no images in the description; the chosen one is in the `<enclosure>`
2. emphasis never wraps its own edges, so breaks are hoisted outside the tag
3. any element with no visible text beneath it is dropped, recursively, except
   those meaningful while empty (`br`, `hr`, table cells)

Measured over the 14 live articles, against the previous regex approach:
orphaned emphasis markers 4 to **0**, blank-line runs 79 to **67**, and **no
words lost** (verified by diffing the word sequence of every article against
upstream).

## How it is triggered

Two independent sources, so neither is a single point of failure:

- **`schedule`** — nominally hourly, but GitHub drops most scheduled fires on
  this repo. Measured cadence was ~6h whether the cron said `*/30` or hourly,
  so tuning the expression does nothing.
- **`push` to `main`** — a daily cloud routine commits a `.keepalive`
  timestamp, which fires this workflow. That push also resets the 60-day timer
  after which GitHub auto-disables scheduled workflows in public repos.

The job pushes with `GITHUB_TOKEN`, and GitHub does not fire workflows for
pushes made with that token, so the `push` trigger cannot loop. Switching the
job to a PAT would break that guarantee.

A stale timestamp in `.keepalive` means the routine itself has stopped.

## Notes

- **The `sk=` token is not needed.** Wild Apricot appends one to feed URLs it
  generates, but this feed serves identical bytes with no token, a tampered
  token, or none at all. If USJETAA ever restricts the feed, that changes and
  the fetch would start failing.
- **A browser User-Agent is required.** Wild Apricot 403s default agents with
  "Your User Agent has been flagged for unauthorized access."
- **Articles sometimes lead with a broken image.** The PNWJETAA Transitions
  event opens with `JETAAFL Career Workshop.png`, which 404s at every path, and
  carries the real graphic (`2026 PNWJETAA- FINAL BLOG.png`) second. So the
  build HEAD-checks candidates and takes the first that serves an `image/*`
  content type rather than trusting document order.
- If a check cannot complete (Wild Apricot unreachable mid-build) the candidate
  is treated as usable rather than discarded, so a flaky network degrades to
  the old behaviour instead of stripping every image.

## Running it locally

```bash
python3 scripts/build_mirror.py docs/usjetaa.xml
```

No dependencies beyond the Python standard library, including the HTML
parsing, so there is no install step in CI.
