# usjetaa-feed-mirror

Serves a repaired copy of the USJETAA events RSS feed at:

**https://porkbutts.github.io/usjetaa-feed-mirror/usjetaa.xml**

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
2. strips the base64 spacer GIFs Wild Apricot injects, which otherwise render
   as literal `data:image/gif;base64,...` text in a Discord embed
3. adds an `<enclosure>` holding the first image that actually resolves, so
   consumers get a clean image field rather than having to dig one out of
   description HTML

It refuses to write output if the upstream returned no items or if any broken
path survived, so a bad fetch leaves the last good mirror in place.

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

No dependencies beyond the Python standard library.
