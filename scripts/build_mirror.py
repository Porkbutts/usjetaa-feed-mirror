#!/usr/bin/env python3
"""Mirror the USJETAA events RSS feed, repaired for Discord.

Wild Apricot emits <img src> paths under /widget/resources/..., which 404;
the same file serves fine from /resources/... . Its event pages also carry no
OpenGraph tags, so Discord can build nothing from the link either. MonitoRSS
could patch this with a regex, but that is a paid tier. Repairing the feed
upstream gets the same result on the free tier.

The rebuilt feed:
  - corrects image paths
  - carries an <enclosure> holding the first image that actually resolves
  - normalizes the description HTML so it survives conversion to markdown

Articles sometimes lead with a stale graphic that 404s (a leftover from
another chapter's event) and carry the real one second, so image candidates
are checked rather than assumed.

The description is normalized structurally rather than by pattern. Wild
Apricot's editor emits deeply nested cruft (<p><span><font><img></font>
</span></p>, <em>featuring<br></em>, <strong> around an empty anchor), and
matching each shape with its own regex meant a new rule for every new shape.
Parsing to a tree allows the recursive question a regex cannot ask -- "does
anything under this node render as visible text?" -- so three invariants
cover the whole class. See normalize().

Standard library only; no install step.
"""

import html
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser

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
IMG_SRC = re.compile(r"<img[^>]+src=\"([^\"]+)\"", re.I)
BLANK_RUN = re.compile(r"\n\s*\n+")

VOID = {"br", "hr", "img", "input", "meta", "link"}
EMPHASIS = {"strong", "em", "b", "i", "u"}
# Elements that still mean something while holding no text of their own.
STRUCTURAL = {"br", "hr", "ul", "ol", "table", "tr", "td", "th"}

MIMES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


# --------------------------------------------------------------------------
# Description HTML
# --------------------------------------------------------------------------


class Node:
    __slots__ = ("tag", "attrs", "text", "children")

    def __init__(self, tag=None, attrs=None, text=None):
        self.tag = tag
        self.attrs = attrs or []
        self.text = text
        self.children = []

    @property
    def is_text(self) -> bool:
        return self.text is not None

    def visible_text(self) -> str:
        if self.is_text:
            return self.text.replace("\xa0", " ")
        return "".join(child.visible_text() for child in self.children)

    def render(self) -> str:
        if self.is_text:
            return self.text
        if self.tag in VOID:
            return f"<{self.tag}>"
        attrs = "".join(
            f' {name}="{html.escape(value, quote=True)}"'
            for name, value in self.attrs
            if value is not None
        )
        inner = "".join(child.render() for child in self.children)
        return f"<{self.tag}{attrs}>{inner}</{self.tag}>"


class Builder(HTMLParser):
    """Parses a fragment into a Node tree, tolerating unclosed tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        # Close to the nearest matching ancestor; stray end tags are ignored.
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(Node(text=data))


def is_blank(node: Node) -> bool:
    """True when the node renders nothing a reader would see."""
    if node.is_text:
        return not node.text.replace("\xa0", " ").strip()
    if node.tag in STRUCTURAL:
        return False
    return not node.visible_text().strip()


def normalize(node: Node) -> None:
    """Apply three invariants bottom-up.

    1. No images in the description; the chosen one lives in the <enclosure>.
    2. Emphasis never wraps its own edges. <em>featuring<br></em> converts to
       "*featuring\\n*", and Discord will not pair a marker sitting alone on a
       line, so the break is hoisted out to give "*featuring*".
    3. Any element with no visible text anywhere beneath it is dropped. This is
       recursive, so arbitrarily nested wrappers collapse in a single pass.
    """
    kept = []
    for child in node.children:
        if child.is_text:
            kept.append(child)
            continue

        if child.tag == "img":
            continue

        normalize(child)

        if child.tag in EMPHASIS:
            while child.children and is_blank(child.children[0]):
                kept.append(child.children.pop(0))
            trailing = []
            while child.children and is_blank(child.children[-1]):
                trailing.insert(0, child.children.pop())
            if not is_blank(child):
                kept.append(child)
            kept.extend(trailing)
            continue

        if is_blank(child):
            continue

        kept.append(child)

    node.children = kept


def clean_description(markup: str) -> str:
    builder = Builder()
    builder.feed(markup)
    builder.close()
    normalize(builder.root)
    rendered = "".join(child.render() for child in builder.root.children)
    # Removing an element leaves the blank lines that surrounded it. They are
    # insignificant in HTML but survive into markdown as real blank lines.
    return BLANK_RUN.sub("\n", rendered).strip()


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

_resolves_cache: dict = {}


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def resolves(url: str):
    """True if the URL serves an image, False if not, None if undetermined.

    None matters: if Wild Apricot is unreachable mid-build we must not
    silently drop every image, so an undetermined candidate stays usable.
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


def pick_image(candidates):
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
    extension = url.rsplit(".", 1)[-1].split("?")[0].lower()
    return MIMES.get(extension, "image/png")


# --------------------------------------------------------------------------
# Feed
# --------------------------------------------------------------------------


def rewrite_item(item: str) -> str:
    match = DESCRIPTION.search(item)
    if not match:
        return item

    description = html.unescape(match.group(2))
    images = [u for u in IMG_SRC.findall(description) if not u.startswith("data:")]

    rebuilt = (
        item[: match.start(2)]
        + html.escape(clean_description(description), quote=False)
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
