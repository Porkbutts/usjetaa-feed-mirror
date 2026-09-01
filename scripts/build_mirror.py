#!/usr/bin/env python3
"""Mirror the USJETAA events RSS feed with its image URLs repaired.

Wild Apricot emits <img src> paths under /widget/resources/..., which 404.
The same file is served fine from /resources/... . Discord therefore cannot
render a thumbnail for any USJETAA article, and MonitoRSS's free tier has no
way to rewrite a placeholder.

This rebuilds the feed with:
  - image paths corrected (/widget/resources/ -> /resources/)
  - the base64 spacer GIFs Wild Apricot injects stripped out
  - an <enclosure> carrying the first real image, so consumers get a clean
    image field instead of having to dig one out of description HTML
"""

import html
import re
import sys
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
DATA_IMG = re.compile(r"<img[^>]+src=\"data:[^\"]*\"[^>]*>", re.I)
IMG_SRC = re.compile(r"<img[^>]+src=\"([^\"]+)\"", re.I)

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


def mime_for(url: str) -> str:
    ext = url.rsplit(".", 1)[-1].split("?")[0].lower()
    return MIMES.get(ext, "image/png")


def rewrite_item(item: str) -> str:
    match = DESCRIPTION.search(item)
    if not match:
        return item

    description = html.unescape(match.group(2))
    description = DATA_IMG.sub("", description)
    images = [u for u in IMG_SRC.findall(description) if not u.startswith("data:")]

    rebuilt = (
        item[: match.start(2)]
        + html.escape(description, quote=False)
        + item[match.end(2) :]
    )

    if images:
        enclosure = (
            f'<enclosure url="{html.escape(images[0], quote=True)}"'
            f' length="0" type="{mime_for(images[0])}"/>'
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
