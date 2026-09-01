#!/usr/bin/env python3
"""Mirror the USJETAA events RSS feed with its image URLs repaired.

Wild Apricot emits <img src> paths under /widget/resources/..., which 404.
The same file is served fine from /resources/... . Discord therefore cannot
render a thumbnail for any USJETAA article, and MonitoRSS's free tier has no
way to rewrite a placeholder.

This rebuilds the feed with:
  - image paths corrected (/widget/resources/ -> /resources/)
  - images removed from the description, along with the wrapper elements
    left empty behind them
  - an <enclosure> carrying the first image that actually resolves, so
    consumers get a clean image field instead of having to dig one out of
    description HTML

Articles sometimes lead with a stale graphic that 404s (a leftover from
another chapter's event, say) and carry the real one second, so candidates
are checked rather than assumed.
"""

import html
import re
import sys
import urllib.error
import urllib.request

FEED = "https://usjetaa.wildapricot.org/widget/Events/RSS"

# Wild Apricot 403s urllib's default agent ("Your User Agent has been flagged").
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

BROKEN_PREFIX = "wildapricot.org/widget/resources/"
FIXED_PREFIX = "wildapricot.org/resources/"

ITEM = re.compile(r"<item>.*?</item>", re.S)
DESCRIPTION = re.compile(r"(<description>)(.*?)(</description>)", re.S)
IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
IMG_SRC = re.compile(r"<img[^>]+src=\"([^\"]+)\"", re.I)

# A wrapper holding only whitespace once its image is gone. Wild Apricot nests
# these several deep (<p><span><font><img></font></span></p>), so this is
# applied repeatedly until the text stops changing.
EMPTY_EL = re.compile(
    r"<(p|div|span|font|strong|em|b|i|u)\b[^>]*>(?:\s|&nbsp;|\u00a0)*</\1>", re.I
)
BLANK_RUN = re.compile(r"\n\s*\n+")

MIMES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


_resolves_cache: dict[str, bool | None] = {}


def resolves(url: str) -> bool | None:
    """True if the URL serves an image, False if not, None if undetermined.

    None matters: if Wild Apricot is unreachable mid-build we must not
    silently drop every image, so an undetermined candidate is still usable.
    """
    if url in _resolves_cache:
        return _resolves_cache[url]

    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            ok = response.headers.get("Content-Type", "").startswith("image/")
    except urllib.error.HTTPError:
        ok = False
    except Exception:
        ok = None

    _resolves_cache[url] = ok
    return ok


def pick_image(candidates: list[str]) -> str | None:
    """First candidate that resolves; else first undetermined; else none."""
    checked = [(url, resolves(url)) for url in candidates]
    for url, ok in checked:
        if ok is True:
            return url
    for url, ok in checked:
        if ok is None:
            return url
    return None


def mime_for(url: str) -> str:
    ext = url.rsplit(".", 1)[-1].split("?")[0].lower()
    return MIMES.get(ext, "image/png")


def strip_images(markup: str) -> str:
    """Drop every <img> and any wrapper it leaves empty.

    Removing the tag alone leaves <p><span><font></font></span></p>, which
    renders as a blank line in a Discord embed, and an emptied <strong> shows
    up as a stray "**".
    """
    markup = IMG_TAG.sub("", markup)
    while True:
        collapsed = EMPTY_EL.sub("", markup)
        if collapsed == markup:
            break
        markup = collapsed

    # Removing an element leaves the blank lines that surrounded it. They are
    # insignificant in HTML but survive the conversion to markdown as real
    # blank lines, so collapse runs down to one; the block tags still supply
    # the paragraph breaks.
    return BLANK_RUN.sub("\n", markup).strip()


def rewrite_item(item: str) -> str:
    match = DESCRIPTION.search(item)
    if not match:
        return item

    description = html.unescape(match.group(2))
    images = [u for u in IMG_SRC.findall(description) if not u.startswith("data:")]
    description = strip_images(description)

    rebuilt = (
        item[: match.start(2)]
        + html.escape(description, quote=False)
        + item[match.end(2) :]
    )

    chosen = pick_image(images)
    if chosen:
        enclosure = (
            f'<enclosure url="{html.escape(chosen, quote=True)}"'
            f' length="0" type="{mime_for(chosen)}"/>'
        )
        rebuilt = rebuilt.replace("</item>", enclosure + "</item>", 1)

    return rebuilt


def build(feed_xml: str) -> str:
    feed_xml = feed_xml.replace(BROKEN_PREFIX, FIXED_PREFIX)
    return ITEM.sub(lambda m: rewrite_item(m.group(0)), feed_xml)


def main() -> int:
    destination = sys.argv[1] if len(sys.argv) > 1 else "docs/usjetaa.xml"
    mirrored = build(fetch(FEED))

    if "<item>" not in mirrored:
        print("Refusing to write: upstream returned no items", file=sys.stderr)
        return 1
    if BROKEN_PREFIX in mirrored:
        print("Refusing to write: broken image paths survived", file=sys.stderr)
        return 1

    with open(destination, "w", encoding="utf-8") as handle:
        handle.write(mirrored)

    print(f"Wrote {destination} ({mirrored.count('<item>')} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
