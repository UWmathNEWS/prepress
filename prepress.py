import argparse
import hashlib
import html
import os
import os.path
import re
import shutil
import string
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Tuple
from xml.etree import ElementTree
from xml.etree.ElementTree import Element, SubElement

import bs4
import colorama
import pylatex
from bs4 import BeautifulSoup, Tag
from PIL import Image

from plugins.preformatted import add_linenos, highlight_code, wrap_lines
from plugins.smart_quotes import get_double_quote, get_quote_direction, get_single_quote
from plugins.syntax_highlighting import (
    SyntaxHighlightType,
    get_syntax_highlight_tag_name,
)
from util import (
    DISCRETIONARY_LINE_BREAK,
    INLINE_TAGS,
    LINE_SEPARATOR,
    VERBATIM_TAGS,
    html_escape,
    is_large_text,
    is_link_component,
    is_link_text,
    keep_verbatim,
)

# The directory to store generated assets. Can be changed by command line argument.
ASSET_DIR = "assets"
# The suffix of the output files
# "main_articles_<suffix>"
# "secondary_articles_<suffix>"
# Can be changed by command line argument
OUTPUT_FILE_SUFFIX = "issue.xml"
# The current working directory
CURRENT_DIR: str
# 273 pt, at 300 DPI
DPI = 300
IMAGE_WIDTH_DEFAULT = 1138
USER_AGENT = "curl/7.61"  # 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/113.0'

# Name of category for approved articles
APPROVED_CATEGORY = "Editor okayed"

# Words longer than this many characters get discretionary line breaks inserted into them, so
# that InDesign can break them instead of letting them overflow. Headings are set larger, so 
# a word runs out of room there far sooner
LONG_WORD_MAX_LENGTH_HEADING = 12
LONG_WORD_MAX_LENGTH_BODY = 30
# The characters that may appear inside a URL
URL_CHARS = r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;%=]"
# Anything a URL may not end on, so that a URL closing a sentence does not swallow the period,
# nor the comma in a list of links
URL_TRAILING_CHARS_REGEX = re.compile(r"[^A-Za-z0-9\-_~/#\[\]@$&'()*+%=]+$")
# The host part of a URL or an email address
URL_HOST = r"[A-Za-z0-9\-._~%]+"
# The domain endings we recognise when a URL announces itself with neither a scheme nor a
# leading "www.". Add to this list as the paper starts linking to new places
URL_TLDS = (
    "com",
    "ca",
    "org",
    "net",
    "edu",
    "gov",
    "io",
    "ai",
    "co",
    "uk",
    "de",
    "jp",
    "me",
    "dev",
    "app",
    "info",
    "biz",
    "xyz",
    "tv",
    "fm",
    "ly",
    "gg",
    "sh",
    "to",
    "cc",
    "us",
    "eu",
    "au",
    "nz",
)
# A URL is recognisable in one of four ways: it carries a scheme, it starts with "www.", it is
# an email address, or it ends in a domain we know. That last one insists the domain is not
# followed by more letters, so a missing space after a full stop does not read as a link
URL_REGEX = re.compile(
    r"(?:https?://{chars}+"
    r"|www\.{chars}+"
    r"|{host}@{host}\.(?:{tlds})"
    r"|{host}\.(?:{tlds})(?![A-Za-z]){chars}*)".format(
        chars=URL_CHARS, host=URL_HOST, tlds="|".join(URL_TLDS)
    )
)

# Characters we refuse start a line with when breaking, so dont break before when they
# trail a long word
TRAILING_PUNCTUATION = string.punctuation + "‘’“”…–—‽"

XML_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "wp": "http://wordpress.org/export/1.2/",
}


class Article:

    def __init__(self):
        self.author = ""
        self.title = ""
        self.subtitle = ""
        self.id = ""
        # content and postscript is stored as a beautiful soup tree
        self.content: BeautifulSoup = None
        self.postscript: BeautifulSoup = None

    def is_secondary_article(self) -> bool:
        """Check, based on our naming conventions, if the article is a secondary."""
        return self.title.strip().startswith("^") or self.title.strip().endswith("^")

    def get_article_slug(self) -> str:
        # generate a slug by trimming the title, replacing non-ascii chars, and replacing spaces
        # plus article id to prevent article title collisions
        file_prefix = re.sub(
            r"\W",
            "",
            self.title[0:10]
            .encode("ascii", errors="ignore")
            .decode()
            .replace(" ", "_"),
        )
        return file_prefix + "_" + self.id

    def get_image_location(self, file: str, index: int) -> str:
        article_slug = self.get_article_slug()
        filename = f"{article_slug}_{index:03}_{file}"
        return os.path.join(ASSET_DIR, "img", filename)

    def get_pdf_location(self, file: str) -> str:
        article_slug = self.get_article_slug()
        filename = article_slug + "_" + file
        return os.path.join(ASSET_DIR, "pdf", filename)

    def get_length(self) -> int:
        if self.content is None:
            return 0

        return len(self.content.get_text())

    def to_xml_element(self) -> Element:
        article_tag = Element("article")

        title_tag = SubElement(article_tag, "title")
        title_tag.text = html_escape(self.title)

        if self.subtitle:
            subtitle_tag = SubElement(article_tag, "subtitle")
            subtitle_tag.text = html_escape(self.subtitle)

        if self.author:
            postscript_tag = self.content.find("footer")
            author_tag = self.content.new_tag("address")
            author_tag.string = self.author

            if postscript_tag is not None:
                postscript_tag.insert_before(author_tag)
                postscript_tag.insert_before("\n")
            else:
                self.content.append("\n")
                self.content.append(author_tag)

        content_tag = SubElement(article_tag, "content")
        content_tag.text = str(self.content)

        return article_tag


def is_for_issue(article_tag: Element, issue_num: str) -> bool:
    """Returns True if the article given by the <item> tag article_tag
    belongs to the issue given by issue_num, and it is editor okayed
    """
    has_correct_tag = False
    has_approval = False
    for category in article_tag.findall("category"):
        if category.get("domain") == "post_tag" and category.text == issue_num:
            has_correct_tag = True
        elif (
            category.get("domain") == "category" and category.text == APPROVED_CATEGORY
        ):
            has_approval = True
    return has_correct_tag and has_approval


def preprocess_html(html: str) -> str:
    """Used to process content strings before they are parsed as HTML"""
    subs = [
        (r"\[caption([^\]]*)\]", r"<caption\1>"),
        (r"\[/caption\]", "</caption>"),
        (r"\[emphasis 1\]", r"<em>"),
        (r"\[/emphasis 1\]", "</em>"),
        (r"\[emphasis 2\]", r"<em2>"),
        (r"\[/emphasis 2\]", "</em2>"),
        (r"\[emphasis 3\]", r"<em3>"),
        (r"\[/emphasis 3\]", "</em3>"),
        (r"\[emphasis 4\]", r"<em4>"),
        (r"\[/emphasis 4\]", "</em4>"),
        (r"\[em1\]", r"<em>"),
        (r"\[/em1\]", "</em>"),
        (r"\[em2\]", r"<em2>"),
        (r"\[/em2\]", "</em2>"),
        (r"\[em3\]", r"<em3>"),
        (r"\[/em3\]", "</em3>"),
        (r"\[em4\]", r"<em4>"),
        (r"\[/em4\]", "</em4>"),
        (r"\[stress 1\]", r"<strong>"),
        (r"\[/stress 1\]", "</strong>"),
        (r"\[stress 2\]", r"<strong2>"),
        (r"\[/stress 2\]", "</strong2>"),
        (r"\[str1\]", r"<strong>"),
        (r"\[/str1\]", "</strong>"),
        (r"\[str2\]", r"<strong2>"),
        (r"\[/str2\]", "</strong2>"),
        (r"\[article\]", r"<aref>"),
        (r"\[/article\]", "</aref>"),
        (r"\[aref\]", r"<aref>"),
        (r"\[/aref\]", "</aref>"),
        (r"\[math\]", r"<imath>"),
        (r"\[/math\]", "</imath>"),
    ]
    for pattern, sub in subs:
        html = re.sub(pattern, sub, html)
    return html


def filter_articles(tree: ElementTree, issue_num: str) -> List[Article]:
    """Given an ElementTree parsed from an XML dump, returns a list
    of Article instances containing all the articles tagged with issue_num.
    """
    root = tree.getroot()
    articles: List[Article] = []
    article_tags = root.findall(".//item")
    for article_tag in article_tags:
        if not is_for_issue(article_tag, issue_num):
            continue
        article = Article()
        # possible optimization, instead of calling find several times,
        # loop through tag children once and parse out data as we run into it
        article.title = article_tag.find("title").text or "[no title]"
        article.id = article_tag.find("wp:post_id", XML_NS).text
        # go through post meta tags
        post_meta_tags = article_tag.findall("wp:postmeta", XML_NS)
        for post_meta_tag in post_meta_tags:
            meta_key = post_meta_tag.find("wp:meta_key", XML_NS).text
            meta_value = post_meta_tag.find("wp:meta_value", XML_NS).text

            if meta_key == "mn_subtitle":
                article.subtitle = meta_value
            elif meta_key == "mn_author":
                article.author = meta_value
            elif meta_key == "mn_postscript":
                article.postscript = BeautifulSoup(meta_value, "html.parser")
        # we will post process this later
        article_text_content = article_tag.find("content:encoded", XML_NS).text
        if article_text_content is None:
            article_text_content = ""

        article_text_content = preprocess_html(article_text_content)

        article.content = BeautifulSoup(article_text_content, "html.parser")
        # TODO: instead of appending to content, process postscript separately
        if article.postscript is not None:
            postscript_wrap = article.content.new_tag("footer")
            postscript_wrap.append(article.postscript)
            article.content.append("\n")
            article.content.append(postscript_wrap)
        articles.append(article)
    return articles


def replace_text_with_tag(
    sub_text: str, repl_tag: Tag, text_tag: bs4.NavigableString, article: Article
) -> bs4.NavigableString:
    # if we can't find the parent, assume it's just the document
    parent: Tag
    if text_tag.parent == None or text_tag.parent.name == "[document]":
        parent = article.content
    else:
        parent = text_tag.parent
    tag_idx = parent.contents.index(text_tag)
    # replace the matched text with a tag
    begin, _, end = text_tag.partition(sub_text)
    # convert these strings to tags
    begin = bs4.NavigableString(begin)
    end = bs4.NavigableString(end)
    text_tag.replace_with(begin)
    parent.insert(tag_idx + 1, repl_tag)
    parent.insert(tag_idx + 2, end)
    return end


def convert_imgur_embeds(article: Article) -> Article:
    """Converts Imgur embeds of the form `[embed]https://imgur.com/...[/embed]` into image tags.
    It does so by scraping the Imgur embed page and retrieving the image URL of the first image it sees.
    As a result, we don't (yet) support multiple images.
    """
    imgur_url_regex = re.compile(
        r"""
    (?:https?:)?//
    (?:i\.)?                  # Don't care if the URL uses the i.imgur.com subdomain
    imgur.com/
    (?P<scheme>a/|gallery/)?  # Don't care about URL scheme
    (?P<hash>\w{5}(?:\w\w)*)  # Match the gallery hash, which will be an odd number of characters
    .?                        # Don't care about any extraneous characters
    (?P<ext>\.\w+)?           # Match any potential file extensions
    """,
        re.VERBOSE | re.ASCII,
    )
    imgur_regex = re.compile(
        rf"""\[embed\]{imgur_url_regex.pattern}\[/embed\]""", re.VERBOSE | re.ASCII
    )
    imgur_url_templ = "https://i.imgur.com/{hash}{ext}"

    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag):
            continue

        for match in imgur_regex.finditer(text_tag):
            img_url = imgur_url_templ.format(**match.groupdict())
            if match["ext"] is None:
                # No file extension, have to scrape
                try:
                    with urllib.request.urlopen(
                        "https://imgur.com/{scheme}{hash}/embed?pub=true".format(
                            **match.groupdict(default="")
                        )
                    ) as resp:
                        if resp.getcode() != 200:
                            raise ValueError("Gallery does not exist")
                        html_text = resp.read()
                        imgur_soup = BeautifulSoup(html_text, "html.parser")
                    img_el = imgur_soup.find(id="image")
                    if img_el is None:
                        raise ValueError(
                            "Could not find image source in returned webpage"
                        )
                except (urllib.error.HTTPError, ValueError) as e:
                    print(f"Error downloading Imgur gallery {match[0]}. Reason: {e}")
                    input("[Enter] to continue...")
                    continue
                # Filter url given in content
                img_el = img_el.find("img", class_="post")
                img_hash = imgur_url_regex.match(img_el["src"])
                img_url = imgur_url_templ.format(**img_hash.groupdict())
            # Replace embed code with an actual img tag
            img_tag = article.content.new_tag("img", src=img_url)
            text_tag = replace_text_with_tag(match[0], img_tag, text_tag, article)

    return article


def resize_image(image_path: str):
    """Resizes the image at image_path to a standard size so they don't import
    into InDesign at giant size.
    """
    image: Image.Image = Image.open(image_path)
    w = image.width
    h = image.height
    scale_factor = IMAGE_WIDTH_DEFAULT / w
    image.resize((int(w * scale_factor), int(h * scale_factor))).save(
        image_path, dpi=(DPI, DPI)
    )


def download_images(article: Article) -> Article:
    """Looks through the article content for image tags and downloads them locally and saves
    them as an asset. Then, it changes the link text to point to the local copy instead of
    the web copy.
    """
    img_tag: Tag
    for index, img_tag in enumerate(article.content.find_all("img")):
        # try block because sometimes images without sources get added (don't ask me why)
        try:
            url = img_tag.attrs["src"]
        except KeyError:
            continue
        filename = os.path.basename(urllib.parse.urlparse(url).path)
        local_path = article.get_image_location(filename, index)
        print(f"Downloading {local_path}\t{url}", flush=True)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request) as response:
                with open(local_path, "wb") as f:
                    f.write(response.read())
                # resize the image to a reasonable size
                if response.headers["Content-Type"] != "image/svg+xml":
                    resize_image(local_path)
            # InDesign recognizes <link href=""> tags for images
            img_tag.name = "link"
            img_tag.attrs["href"] = "file://" + local_path
        except urllib.error.HTTPError as e:
            print(f"Error downloading image {url}. Reason: {e}")
            input("[Enter] to continue...")
        except FileNotFoundError as e:
            print(f"Error downloading image {url}. Reason: {e}")
            input("[Enter] to continue...")
    return article


class Preview(pylatex.base_classes.Environment):
    packages = [pylatex.Package("preview", ["active", "tightpage", "pdftex"])]
    escape = False
    content_separator = "\n"


def compile_latex_str(latex: str, filename: str, display: bool = False):
    """Compiles the string latex into a PDF, and saves it to filename."""
    document = pylatex.Document()
    document.packages.append(pylatex.Package("amsmath"))
    document.packages.append(pylatex.Package("amssymb"))
    document.packages.append(pylatex.Package("amsfonts"))
    document.preamble.append(pylatex.Command("thispagestyle", "empty"))
    # People seem to think \Z, \R and \Q exist, even though they don't. Just add them in to avoid problems.
    document.preamble.append(pylatex.NoEscape(r"\newcommand{\Z}{\mathbb{Z}}"))
    document.preamble.append(pylatex.NoEscape(r"\newcommand{\R}{\mathbb{R}}"))
    document.preamble.append(pylatex.NoEscape(r"\newcommand{\Q}{\mathbb{Q}}"))
    with document.create(Preview()):
        document.append(
            pylatex.NoEscape(
                (r"\[" if display else r"\(") + latex + (r"\]" if display else r"\)")
            )
        )
    document.generate_pdf(filename, compiler="pdflatex")
    print(f"{filename}\t{latex}", flush=True)


def compile_latex(article: Article) -> Article:
    """Looks through the article content for embedded LaTeX and compiles it into
    PDFs, and adds the proper tags so they show up on import.
    """
    text_tag: bs4.NavigableString
    # matches LaTeX inside one or two dollar signs
    inline_regex = r"\\[([]([\s\S]+?)\\[)\]]"
    # Compiled regex
    p = re.compile(inline_regex)
    # Memo to store validity and compile status of latex
    latex_valid_memo: Dict[str, bool] = dict()
    latex_compiled_memo: Dict[str, bool] = dict()
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag):
            continue

        for match in p.finditer(text_tag):
            # if this is invalid latex, skip
            if latex_valid_memo.get(match[1], True) == False:
                continue

            latex = match[1]
            # just use the hash of the latex for a unique filename, this should probably never collide
            # NOTE: sha1 is used for speed; we do not use the built-in `hash` function as it is non-deterministic across runs.
            #       We do NOT need to care about security risks, since we are solely concerned with uniqueness.
            filename = article.get_pdf_location(
                hashlib.sha1(match[0].encode("utf-8")).hexdigest()
            )
            if match[0] not in latex_compiled_memo:
                try:
                    compile_latex_str(latex, filename, display=(match[0][1] == "["))
                    latex_valid_memo[latex] = True
                    latex_compiled_memo[match[0]] = True
                except subprocess.CalledProcessError:
                    latex_valid_memo[latex] = False
                    input("[Enter] to continue...")
                    continue
            link_tag = Tag(name="link", attrs={"href": "file://" + filename + ".pdf"})
            # set the current tag to the new end tag
            text_tag = replace_text_with_tag(
                match[0], link_tag, text_tag, article=article
            )
    return article


def replace_inline_code(article: Article) -> Article:
    """Replaces Markdown-style inline code with actual code tags"""
    text_tag: bs4.NavigableString
    p = re.compile(r"`([\s\S]+?)`")
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag):
            continue

        for match in p.finditer(text_tag):
            code = match[1]
            code_tag = Tag(name="code")
            code_tag.string = code
            text_tag = replace_text_with_tag(
                match[0], code_tag, text_tag, article=article
            )

    return article


def convert_manual_syntax_highlighting(article: Article) -> Article:
    """Manually highlighted code gets custom styling"""
    text_tag: bs4.NavigableString
    for verb_tag in article.content.find_all(VERBATIM_TAGS):
        # Highlight strong
        for strong_tag in verb_tag.find_all(["strong", "b"]):
            strong_tag.name = get_syntax_highlight_tag_name(SyntaxHighlightType.Bold)
        # Highlight italicized
        for em_tag in verb_tag.find_all(["em", "i"]):
            em_tag.name = get_syntax_highlight_tag_name(SyntaxHighlightType.Italic)
        # Highlight underlined
        for u_tag in verb_tag.find_all("u"):
            u_tag.name = get_syntax_highlight_tag_name(SyntaxHighlightType.Underline)

    return article


def format_code_blocks(article: Article) -> Article:
    """Format code blocks by:
    - Using Pygments to highlight code
    - Inserting line numbers
    - Wrapping code
    """
    pre_tag: bs4.NavigableString
    options_regex = re.compile(
        r"""
    :(\S+?):  # Match the option name
    [ \t]*    # Allow optional whitespace after option name
    ([^\n]*)  # Match the option value (optional)
    """,
        re.VERBOSE,
    )
    options_block_regex = re.compile(
        rf"""
    (?:                          # Look for an option
        \s*                      # Unlimited leading whitespace
        {options_regex.pattern}  # Match an option
        \n                       # Enforce newline after each option
    )+                           # Match multiple options
    [ \t]*\n+                    # Enforce at least two lines of separation between options block and code
    """,
        re.VERBOSE,
    )
    for pre_tag in article.content.find_all("pre"):
        # Parse options
        pre_contents = pre_tag.decode_contents()
        options_block = options_block_regex.match(pre_contents)
        options = {}
        if options_block:
            pre_contents = pre_contents[options_block.end() :]
            for option_match in options_regex.finditer(
                options_block[0]
            ):  # match and save options
                options[option_match[1]] = (
                    option_match[2] or True
                )  # if no value given, turn into boolean

        pre_contents = highlight_code(pre_contents, options)
        pre_contents = add_linenos(pre_contents, options)

        new_tag = wrap_lines(
            BeautifulSoup(f"<pre><code>{pre_contents}</code></pre>", "html.parser")
        )

        pre_tag.replace_with(new_tag)

    return article


def replace_ellipses(article: Article) -> Article:
    """Replaces "..." with one single ellipse character"""
    text_tag: bs4.NavigableString
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag):
            continue

        new_tag = text_tag.replace("...", "…")
        text_tag.replace_with(new_tag)
    return article


# need <link href="{href}">
def replace_links(article: Article) -> Article:
    """Replaces links in <link></link> tags.

    This is the one place that decides what counts as a link, so everything downstream can
    rely on links being tagged. break_long_words in particular leaves <link> text alone, so
    a URL this pass fails to recognise is a URL that may get broken across lines.
    """
    text_tag: bs4.NavigableString
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag):
            continue

        for match in URL_REGEX.finditer(text_tag):
            # the match runs on into the sentence's punctuation, which belongs to the sentence
            # rather than to the URL
            url = URL_TRAILING_CHARS_REGEX.sub("", match[0])

            if not url:
                continue

            link_tag = Tag(name="link", attrs={"href": url})
            link_tag.string = url
            text_tag = replace_text_with_tag(url, link_tag, text_tag, article=article)

    return article


def replace_dashes(article: Article) -> Article:
    """Replaces hyphens used as spacing, that is, when they are surrounded with spaces,
    with em dashes.
    Also replaces hyphens in numeric ranges with en dashes.
    """
    text_tag: bs4.NavigableString
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag) or is_link_component(text_tag):
            continue

        new_tag = (
            re.sub(r"(?<=\d) ?--? ?(?=\d)", "–", text_tag)
            .replace(" - ", "—")
            .replace(" --- ", "—")
            .replace("---", "—")
            .replace(" -- ", "—")
            .replace("--", "—")
            .replace(" — ", "—")
            .replace("—", " — ")
        )
        text_tag.replace_with(new_tag)
    return article


def replace_smart_quotes(s: str):
    # create an array so we can modify this string
    char_array = list(s)

    for idx, char in enumerate(char_array):
        before = None if idx == 0 else char_array[idx - 1]
        after = None if idx == len(char_array) - 1 else char_array[idx + 1]
        direction = get_quote_direction(before, after)
        if char == '"':
            char_array[idx] = get_double_quote(direction)
        if char == "'":
            char_array[idx] = get_single_quote(direction)

    return "".join(char_array)


def add_smart_quotes(article: Article) -> Article:
    """Replaces regular quotes with smart quotes. Works on double and single quotes."""
    text_tags: List[bs4.NavigableString] = list(article.content.find_all(string=True))
    # some hackery here: breaks between text tags might lead to invalid quotes
    # example: "|<em>text</em>|" will make the first quote a right quote, since
    # it's at the end of its text tag.
    # To avoid this, we glue the first character in the following tag
    # and the last character in the previous tag to the current tag.

    for idx, tag in enumerate(text_tags):
        if keep_verbatim(tag):
            continue

        before_tag = None if idx == 0 else text_tags[idx - 1]
        after_tag = None if idx == len(text_tags) - 1 else text_tags[idx + 1]

        glued_tag = tag
        if before_tag is not None:
            glued_tag = before_tag[-1] + glued_tag
        if after_tag is not None:
            glued_tag = glued_tag + after_tag[0]

        replaced = replace_smart_quotes(glued_tag)

        # and remove the characters we glued on
        if before_tag is not None:
            replaced = replaced[1:]
        if after_tag is not None:
            replaced = replaced[:-1]

        tag.replace_with(replaced)

    return article


def punctuation_in_quotes(article: Article) -> Article:
    """Ensures punctuation is inside quotation marks"""
    text_tag: bs4.NavigableString
    inline_regex = re.compile(r"([’”])([\.\!?;\:\,])")
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag):
            continue

        new_tag = text_tag
        for match in inline_regex.finditer(text_tag):
            new_tag = new_tag.replace(match[0], match[2] + match[1])

        text_tag.replace_with(new_tag)

    return article


def remove_extraneous_spaces(article: Article) -> Article:
    """Removes extraneous spaces after characters."""
    text_tag: bs4.NavigableString
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag):
            continue

        base_alphanumeric = "A-Za-z0-9"
        accent_characters = "À-ÖØ-öø-ÿ"
        punctuation = string.punctuation + "‽"

        single_spaced_chars = base_alphanumeric + accent_characters + punctuation

        # a number of articles come to us with punctuation followed by a double space, where the
        #  first space is an nbsp. maybe it is inserted by a particular text editor?
        #  if we remove them directly, we can stop worrying about nbsps from that point on
        #  (who would be unintentionally adding consecutive nbsps)
        nbsp_sp_pairs = r"(?<=[{}])(\u00A0 )+".format(single_spaced_chars)
        new_tag = re.sub(nbsp_sp_pairs, " ", text_tag)

        nbsp_sps_found = new_tag != text_tag

        # with the nbsp-sp pairs removed, we can remove all other n-tuple breaking spaces
        multi_sp = r"(?<=[{}]) +".format(single_spaced_chars)
        new_tag = re.sub(multi_sp, " ", new_tag)

        text_tag.replace_with(new_tag)

        if nbsp_sps_found or new_tag != text_tag:
            print(
                'Removed extraneous spaces in article "'
                + article.title
                + '"'
                + (". Some were nbsp-sp pairs." if nbsp_sps_found else "")
            )
    return article


def normalize_newlines(article: Article) -> Article:
    """Normalizes newlines to Unix-style LF"""
    text_tag: bs4.NavigableString
    for text_tag in article.content.find_all(string=True):
        new_tag = text_tag.replace("\r\n", "\n")
        text_tag.replace_with(new_tag)
    return article


def hairspace_fractions_out_of_10(article: Article) -> Article:
    """For all fractions out of 10, surround the slash with hairspaces to
    break fraction formatting, as this is likely a rating
    """
    text_tag: bs4.NavigableString
    inline_regex = re.compile(r"([0-9]+)/10")
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag):
            continue

        new_tag = text_tag
        for match in inline_regex.finditer(text_tag):
            # the below spaces are hair spaces
            new_tag = new_tag.replace(match[0], f"{match[1]} / 10")

        text_tag.replace_with(new_tag)

    return article


def long_word_break_offsets(
    text: str,
    max_length: int,
    article_title: str,
    may_break_word: Callable[[int, int], bool] = lambda start, end: True,
) -> List[int]:
    """Returns the offsets into text before which a discretionary line break belongs, so that
    no word is left longer than max_length characters without somewhere to break.

    Every character past the first max_length of a word gets an offset, so that the word may
    break anywhere from that point on. The end of the word gets none, as there is nothing to
    break off there, and neither does trailing punctuation, as that punctuation would then be
    stranded at the start of the next line.
    """
    word_regex = re.compile(
        r"[^\s{}]{{{},}}".format(DISCRETIONARY_LINE_BREAK, max_length + 1)
    )
    offsets: List[int] = []

    for match in word_regex.finditer(text):
        word = match[0]

        if not may_break_word(match.start(), match.end()):
            continue

        # the last character we are willing to break before is the one before any trailing
        # punctuation
        breakable_until = len(word.rstrip(TRAILING_PUNCTUATION))

        if breakable_until <= max_length:
            # the word is only long because of its punctuation, so there is nowhere safe to break
            continue

        print(
            colorama.Fore.YELLOW
            + f'Large word "{word}" detected in article "{article_title}". '
            + "Splitting with discretionary line breaks..."
            + colorama.Fore.RESET
        )
        offsets.extend(
            range(match.start() + max_length, match.start() + breakable_until)
        )

    return offsets


def insert_discretionary_breaks(text: str, offsets: List[int]) -> str:
    """Returns text with a discretionary line break inserted before each of the given offsets"""
    pieces = []
    previous = 0

    for offset in offsets:
        pieces.append(text[previous:offset])
        previous = offset
    pieces.append(text[previous:])

    return DISCRETIONARY_LINE_BREAK.join(pieces)


def tags_between(first: bs4.NavigableString, second: bs4.NavigableString) -> List[Tag]:
    """Returns the tags crossed on the way from one piece of text to the next: those we leave
    on the way out of first, those we enter on the way into second, and anything standing
    between them, such as a <br> or an image.
    """
    second_ancestors = {id(ancestor) for ancestor in second.parents}
    # the tags we leave are first's ancestors that second does not share
    crossed = [
        ancestor for ancestor in first.parents if id(ancestor) not in second_ancestors
    ]

    node = first.next_element
    while node is not None and node is not second:
        if isinstance(node, Tag):
            crossed.append(node)
        node = node.next_element

    return crossed


def continuous_text_runs(
    content: BeautifulSoup,
) -> List[List[Tuple[bs4.NavigableString, bool]]]:
    """Groups text into runs that read as continuous text, pairing each piece with whether we
    are allowed to rewrite it.

    A word can be split across inline tags, as in "<em>unimaginably</em>enormous", and that
    is one word as far as the reader and InDesign are concerned. Grouping the pieces lets us
    measure it as one word, rather than as two short ones that each look harmless.

    Link text stays in the run but is marked as not ours to rewrite, so that half a URL in an
    inline tag is still measured as part of the whole URL. Verbatim text ends a run outright,
    since it is set apart from the text around it.
    """
    runs: List[List[Tuple[bs4.NavigableString, bool]]] = []

    text_tag: bs4.NavigableString
    for text_tag in content.find_all(string=True):
        if keep_verbatim(text_tag):
            # verbatim text also breaks up the text around it
            runs.append([])
            continue

        piece = (text_tag, not is_link_text(text_tag))

        if (
            runs
            and runs[-1]
            and all(
                tag.name in INLINE_TAGS
                for tag in tags_between(runs[-1][-1][0], text_tag)
            )
        ):
            runs[-1].append(piece)
        else:
            runs.append([piece])

    return [run for run in runs if run]


def break_long_words(article: Article) -> Article:
    """Adds discretionary line breaks to words too long for the space they are set in.

    How long is too long depends on where the word ends up: headings and captions get far
    fewer characters per line than body text, so titles, subtitles, headings and captions are
    held to LONG_WORD_MAX_LENGTH_HEADING, while body text and the author's byline (which is
    set in an <address> tag at body size) are held to LONG_WORD_MAX_LENGTH_BODY.
    """
    title = article.title

    article.title = insert_discretionary_breaks(
        article.title,
        long_word_break_offsets(article.title, LONG_WORD_MAX_LENGTH_HEADING, title),
    )
    article.subtitle = insert_discretionary_breaks(
        article.subtitle,
        long_word_break_offsets(article.subtitle, LONG_WORD_MAX_LENGTH_HEADING, title),
    )
    article.author = insert_discretionary_breaks(
        article.author,
        long_word_break_offsets(article.author, LONG_WORD_MAX_LENGTH_BODY, title),
    )

    for run in continuous_text_runs(article.content):
        # a run never crosses out of a heading, so its first piece speaks for all of them
        max_length = (
            LONG_WORD_MAX_LENGTH_HEADING
            if is_large_text(run[0][0])
            else LONG_WORD_MAX_LENGTH_BODY
        )

        # where the link text sits within the run, as half-open character ranges
        link_spans = []
        position = 0
        for text_tag, breakable in run:
            if not breakable:
                link_spans.append((position, position + len(text_tag)))
            position += len(text_tag)

        def is_part_of_a_link(start: int, end: int, spans=tuple(link_spans)) -> bool:
            """Whether a word overlaps link text. replace_links can only match a URL inside a
            single piece of text, so a URL broken over an inline tag arrives here as a tagged
            half and a bare remainder. A word touching a link is part of that link, and a
            link must never be broken.
            """
            return any(
                start < span_end and span_start < end for span_start, span_end in spans
            )

        offsets = long_word_break_offsets(
            "".join(text_tag for text_tag, _ in run),
            max_length,
            title,
            may_break_word=lambda start, end: not is_part_of_a_link(start, end),
        )

        if not offsets:
            continue

        # spread the offsets back over the pieces of text they were measured across
        start = 0
        for text_tag, breakable in run:
            end = start + len(text_tag)
            within = [offset - start for offset in offsets if start <= offset < end]

            if breakable and within:
                text_tag.replace_with(insert_discretionary_breaks(text_tag, within))

            start = end

    return article


def replace_newlines(article: Article) -> Article:
    """Replaces newlines with the Unicode LINE SEPARATOR character (U+2028). This preserves
    them in InDesign, which will treat newlines as paragraph breaks otherwise.
    """
    text_tag: bs4.NavigableString
    for text_tag in article.content.find_all(string=True):
        if not keep_verbatim(text_tag):
            # Non-verbatim tags must be handled separately, and we must make sure it's not a
            # double line-break (i.e. paragraph break). We also don't replace it if it's
            # immediately before or after a tag
            # FIXME: this is actually a hacky solution that can fail for poetry where a line ends
            #        with a tag. Disambiguate between block and inline tags?
            prev_sibling = text_tag.find_previous_sibling()
            next_sibling = text_tag.find_next_sibling()
            # Split along single line breaks
            new_tag_builder = re.split("(?<!\n)\n(?!\n)", text_tag)
            # Keep single line breaks that appear next to another tag, by throwing them out and
            # manually placing a newline character
            prefix = ""
            suffix = ""
            if new_tag_builder[0] == "" and prev_sibling != None:
                new_tag_builder = new_tag_builder[1:]
                prefix = "\n"
            if new_tag_builder[-1] == "" and next_sibling != None:
                new_tag_builder = new_tag_builder[:-1]
                suffix = "\n"
            new_tag = bs4.NavigableString(
                prefix + LINE_SEPARATOR.join(new_tag_builder) + suffix
            )
            text_tag.replace_with(new_tag)
    return article


def add_footnotes(article: Article) -> Article:
    """Replaces footnotes in <sup></sup> tags, [\\d] format, or *, **, etc."""
    text_tag: bs4.NavigableString
    footnote_regex = re.compile(r"\[([0-9§]*)\]")
    footnote_counter = 1  # is the expected number of the next footnote
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag):
            continue

        for match in footnote_regex.finditer(text_tag):
            # Check match for provided numbering -- if it exists, then use it
            footnote_num = footnote_counter
            footnote_contents = match[1]
            if len(footnote_contents):
                if "§" in footnote_contents:
                    first_number = re.search(r"([0-9])", footnote_contents)
                    print(footnote_contents, first_number)
                    footnote_num = int(first_number[0])
                else:
                    footnote_num = int(footnote_contents)
            sup_tag = Tag(name="sup")
            sup_tag.string = str(footnote_contents)
            text_tag = replace_text_with_tag(
                match[0], sup_tag, text_tag, article=article
            )
            # Only auto-increment if blank or explicitly incremented
            if len(match[1]) == 0 or footnote_num == footnote_counter:
                footnote_counter += 1
    return article


def footnote_after_punctuation(article: Article) -> Article:
    """Replaces footnotes in <sup></sup> tags, [\\d] format, or *, **, etc."""
    text_tag: bs4.NavigableString
    inline_regex = re.compile(r"(\[[§\d]*\])([\.,!?;:])")
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag):
            continue

        new_tag = text_tag
        for match in inline_regex.finditer(text_tag):
            new_tag = new_tag.replace(match[0], match[2] + match[1])

        text_tag.replace_with(new_tag)

    return article


def process_captions(article: Article) -> Article:
    """Replaces Wordpress's weird square bracket caption tags (which have already
    been converted to HTML tags) with <figcaption>
    """
    for caption in article.content.find_all("caption"):
        # wordpress puts the image inside the caption tag
        # sometimes it's an <img> and sometimes it's <a><img></a>
        images = []
        non_images = []
        for child in caption.children:
            if isinstance(child, Tag) and child.name in ("a", "img"):
                images.append(child)
            else:
                non_images.append(child)

        # wordpress likes to add an extra space at the beginning of the caption
        if non_images and isinstance(non_images[0], str) and non_images[0][0] == " ":
            non_images[0] = non_images[0][1:]

        figcaption = Tag(name="figcaption")
        figcaption.extend(non_images)
        caption.replace_with(*images, figcaption)
    return article


def convert_emphasis_2(article: Article) -> Article:
    """Converts nested bold/italic tags into a single <em2> tag."""
    for b in article.content.find_all(["b", "strong"]):
        for i in b.find_all(["i", "em"]):
            i.name = "em2"

    for i in article.content.find_all(["i", "em"]):
        for b in i.find_all(["b", "strong"]):
            b.name = "em2"

    return article


def convert_profquotes(article: Article) -> Article:
    """Converts unordered lists in articles titled "profQUOTES" into <profquotes> elements."""
    if article.title == "profQUOTES":
        for ul in article.content.find_all("ul"):
            ul.name = "profquotes"

    return article


def fix_lists(article: Article) -> Article:
    """
    Converts HTML lists into a format friendlier for InDesign.

    InDesign doesn't understand the <li> element or nested lists. We replace li elements with
    newlines, which get treated as paragraph breaks, and replace each level of nesting with
    its own tag (ul2, ul3, etc.) For top-level elements, we also give the first item in each
    list its own tag (ul_first, ol_first) for special formatting.
    """
    for list_tag in article.content.find_all(["ul", "ol", "profquotes"]):
        new_children = []
        for child in list_tag.children:
            if isinstance(child, str):
                continue
            elif child.name == "li":
                new_children.extend(child.contents)
            new_children.append("\n")
        list_tag.clear()
        list_tag.extend(new_children[:-1])

    for ul in article.content.find_all("ul"):
        parents = [parent.name for parent in ul.parents]
        level = (
            parents.count("ul")
            + parents.count("ul2")
            + parents.count("ul3")
            + parents.count("ul4")
            + parents.count("ul5")
            + 1
        )
        if 1 < level <= 5:
            new_tag_name = f"ul{level}"
            ul.name = new_tag_name

        if level == 1:
            ul.contents[0].wrap(Tag(name="ul_first"))

    for ol in article.content.find_all("ol"):
        parents = [parent.name for parent in ol.parents]
        level = parents.count("ol") + parents.count("ol2") + parents.count("ol3") + 1
        if 1 < level <= 3:
            new_tag_name = f"ol{level}"
            ol.name = new_tag_name

        if level == 1:
            ol.contents[0].wrap(Tag(name="ol_first"))

    return article


"""POST_PROCESS is a list of functions that take Article instances and return Article instances.

For each article we parse, every function in this list will be applied to it in order, and the
result saved back to the article list.

Use this to make any changes to articles you need before export, as well as to generate assets.
"""
POST_PROCESS: List[Callable[[Article], Article]] = [
    process_captions,
    normalize_newlines,
    convert_imgur_embeds,
    download_images,
    compile_latex,
    replace_inline_code,
    convert_manual_syntax_highlighting,
    format_code_blocks,
    replace_newlines,
    replace_ellipses,
    replace_links,
    replace_dashes,
    add_smart_quotes,
    punctuation_in_quotes,
    remove_extraneous_spaces,
    footnote_after_punctuation,
    add_footnotes,
    convert_emphasis_2,
    convert_profquotes,
    hairspace_fractions_out_of_10,
    break_long_words,
    fix_lists,
]


def create_asset_dirs():
    if not os.path.isdir(os.path.join(ASSET_DIR, "img")):
        os.makedirs(os.path.join(ASSET_DIR, "img"))
    if not os.path.isdir(os.path.join(ASSET_DIR, "pdf")):
        os.makedirs(os.path.join(ASSET_DIR, "pdf"))


def catch_process_errors(
    article: Article,
    process: Callable[[Article], Article],
):
    try:
        return process(article)
    except Exception as e:
        print(
            colorama.Fore.RED
            + colorama.Style.BRIGHT
            + f'Article "{article.title}" by {article.author} failed to process step {process.__name__}\033[0m'
            + colorama.Fore.RESET
            + colorama.Style.RESET_ALL
        )

        raise e


def write_issue(root: Element, output_file_path: str, current_dir_path: str):
    # do some processing first
    # Remove extraneous lines
    transformed = "\n".join(
        [
            line
            for line in html.unescape(
                ElementTree.tostring(root, encoding="unicode")
            ).split("\n")
            if line.strip() != ""
        ]
    )
    # Separate articles cleanly
    transformed = "</article>\n<article>".join(
        [article for article in transformed.split("</article><article>")]
    )
    # Separate title, subtitle, and content cleanly
    transformed = "</title>\n<content>".join(
        [article for article in transformed.split("</title><content>")]
    )
    transformed = "</title>\n<subtitle>".join(
        [article for article in transformed.split("</title><subtitle>")]
    )
    transformed = "</subtitle>\n<content>".join(
        [article for article in transformed.split("</subtitle><content>")]
    )
    # Remove extraneous items from beginning and end of lists
    transformed = "<ul>".join([thing for thing in transformed.split("<ul>\n")])
    transformed = "</ul>".join([thing for thing in transformed.split("\n</ul>")])
    transformed = "<ol>".join([thing for thing in transformed.split("<ol>\n")])
    transformed = "</ol>".join([thing for thing in transformed.split("\n</ol>")])

    os.chdir(current_dir_path)
    with open(output_file_path, "w", encoding="utf-8") as output_file:
        output_file.write(transformed)


if __name__ == "__main__":
    colorama.init()
    parser = argparse.ArgumentParser(description="article export for mathNEWS")
    parser.add_argument("issue", help="the issue number to export for, e.g, v141i3")
    parser.add_argument("xml_dump", help="location of the XML dump to read from")
    parser.add_argument(
        "-o",
        "--xml_output",
        help="suffix of the output files",
        default="issue.xml",
    )
    parser.add_argument(
        "-a", "--assets", help="a folder to store asset files to", default="assets"
    )
    args = parser.parse_args()
    CURRENT_DIR = os.getcwd()
    if os.path.isabs(args.assets):
        ASSET_DIR = args.assets
    else:
        ASSET_DIR = os.path.join(CURRENT_DIR, args.assets)
    shutil.rmtree(ASSET_DIR, ignore_errors=True)
    create_asset_dirs()
    OUTPUT_FILE_SUFFIX = args.xml_output
    if not os.path.isfile(args.xml_dump):
        print(f"{args.xml_dump} does not exist.")
        exit(1)

    print("Parsing XML...", flush=True)
    tree = ElementTree.parse(args.xml_dump)

    print("Filtering articles...", flush=True)
    articles = filter_articles(tree, args.issue)
    articles.sort(key=lambda article: article.get_length())

    print("Post-processing articles...", flush=True)
    for process in POST_PROCESS:
        print(f"Preparing post-process pass: {process.__name__}", flush=True)
        articles = map(
            lambda article, process=process: catch_process_errors(article, process),
            articles,
        )

    print(f"Post-processing...", flush=True)
    main_articles_root = Element("issue")
    secondary_articles_root = Element("issue")
    for article in articles:
        if article.is_secondary_article():
            secondary_articles_root.append(article.to_xml_element())
        else:
            main_articles_root.append(article.to_xml_element())

    main_articles_output_file = "main_articles_" + OUTPUT_FILE_SUFFIX
    secondary_articles_output_file = "secondary_articles_" + OUTPUT_FILE_SUFFIX
    print(f"Writing main articles to {main_articles_output_file}...", flush=True)
    write_issue(main_articles_root, main_articles_output_file, CURRENT_DIR)
    print(
        f"Writing secondary articles to {secondary_articles_output_file}...", flush=True
    )
    write_issue(secondary_articles_root, secondary_articles_output_file, CURRENT_DIR)

    print("Issues written.")
