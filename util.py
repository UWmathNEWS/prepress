import functools

from bs4 import Tag

# Unicode LINE SEPARATOR character
LINE_SEPARATOR = "\u2028"
# Unicode ZERO WIDTH SPACE character, which InDesign treats as a discretionary line break:
# a point where a word may be broken across lines, without adding a hyphen
DISCRETIONARY_LINE_BREAK = "\u200b"
# Tags within which we should not be replacing content
VERBATIM_TAGS = ("pre", "code")
# Tags whose contents are set at heading size, and so have less room for long words.
# <caption> becomes <figcaption> in the first post-process pass, so both names appear here
LARGE_TEXT_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "caption", "figcaption")
# Tags that do not interrupt the text around them, so a word may run through one of them
INLINE_TAGS = (
    "a",
    "aref",
    "b",
    "em",
    "em2",
    "em3",
    "em4",
    "i",
    "imath",
    "link",
    "s",
    "small",
    "span",
    "strong",
    "strong2",
    "sub",
    "sup",
    "u",
)
# Tag within which we should not perform text substitutions
LINK_TAG = "link"
# Tags whose text is a link: the <link> tags we generate, and the anchors Wordpress gives us
LINK_TEXT_TAGS = ("link", "a")


def keep_verbatim(tag: Tag) -> bool:
    return tag.name in VERBATIM_TAGS or any(
        filter(lambda t: t.name in VERBATIM_TAGS, tag.parents)
    )


def is_large_text(tag: Tag) -> bool:
    return tag.name in LARGE_TEXT_TAGS or any(
        filter(lambda t: t.name in LARGE_TEXT_TAGS, tag.parents)
    )


def is_link_text(tag: Tag) -> bool:
    return tag.name in LINK_TEXT_TAGS or any(
        filter(lambda t: t.name in LINK_TEXT_TAGS, tag.parents)
    )


def is_link_component(tag: Tag) -> bool:
    return tag.name == LINK_TAG or any(
        filter(lambda t: t.name == LINK_TAG, tag.parents)
    )


__html_escape_lut = str.maketrans(
    {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
)


@functools.lru_cache()
def html_escape(value):
    return value.translate(__html_escape_lut)
