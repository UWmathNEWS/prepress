import argparse
import os
import os.path
from xml.etree import ElementTree
from xml.etree.ElementTree import Element, SubElement
from typing import Dict, List, Callable
import re
import urllib.request
import urllib.parse
import urllib.error
import html
import shutil
import hashlib
import string
import subprocess

import bs4
from bs4 import BeautifulSoup, Tag
import pylatex
from PIL import Image

from util import LINE_SEPARATOR, VERBATIM_TAGS, keep_verbatim, html_escape
from plugins.preformatted import highlight_code, add_linenos, wrap_lines
from plugins.smart_quotes import get_quote_direction, get_double_quote, get_single_quote
from plugins.syntax_highlighting import SyntaxHighlightType, get_syntax_highlight_tag_name

#The directory to store generated assets. Can be changed by command line argument.
ASSET_DIR = 'assets'
#The location of the output file. Can be changed by command line argument'
OUTPUT_FILE = 'issue.xml'
#The current working directory
CURRENT_DIR: str
#273 pt, at 300 DPI
DPI = 300
IMAGE_WIDTH_DEFAULT = 1138
USER_AGENT = "curl/7.61" # 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/113.0'

# Name of category for approved articles
APPROVED_CATEGORY = "Editor okayed"

XML_NS = {
    'content': 'http://purl.org/rss/1.0/modules/content/',
    'wp': 'http://wordpress.org/export/1.2/'
}

#this is illegal or whatever, but I am the law.
urllib.request.URLopener.version = USER_AGENT

class Article:

    def __init__(self):
        self.author = ''
        self.title = ''
        self.subtitle = ''
        self.id = ''
        # content and postscript is stored as a beautiful soup tree
        self.content: BeautifulSoup = None
        self.postscript: BeautifulSoup = None

    def get_article_slug(self) -> str:
        # generate a slug by trimming the title, replacing non-ascii chars, and replacing spaces
        # plus article id to prevent article title collisions
        file_prefix = re.sub(r"\W",  "", self.title[0:10].encode('ascii', errors='ignore').decode().replace(' ', '_'))
        return file_prefix + "_" + self.id

    def get_image_location(self, file: str, index: int) -> str:
        article_slug = self.get_article_slug()
        filename = f"{article_slug}_{index:03}_{file}"
        return os.path.join(ASSET_DIR, 'img', filename)

    def get_pdf_location(self, file: str) -> str:
        article_slug = self.get_article_slug()
        filename = article_slug + '_' + file
        return os.path.join(ASSET_DIR, 'pdf', filename)

    def to_xml_element(self) -> Element:
        article_tag = Element('article')

        title_tag = SubElement(article_tag, 'title')
        title_tag.text = html_escape(self.title)

        if self.subtitle:
            subtitle_tag = SubElement(article_tag, 'subtitle')
            subtitle_tag.text = html_escape(self.subtitle)

        if self.author:
            postscript_tag = self.content.find('footer')
            author_tag = self.content.new_tag('address')
            author_tag.string = self.author

            if postscript_tag is not None:
                postscript_tag.insert_before(author_tag)
                postscript_tag.insert_before('\n')
            else:
                self.content.append('\n')
                self.content.append(author_tag)

        content_tag = SubElement(article_tag, 'content')
        content_tag.text = str(self.content)

        return article_tag

def is_for_issue(article_tag: Element, issue_num: str) -> bool:
    """Returns True if the article given by the <item> tag article_tag
    belongs to the issue given by issue_num, and it is editor okayed
    """
    has_correct_tag = False
    has_approval = False
    for category in article_tag.findall('category'):
        if category.get('domain') == 'post_tag' and category.text == issue_num: 
            has_correct_tag = True
        elif category.get('domain') == 'category' and category.text == APPROVED_CATEGORY:
            has_approval = True
    return has_correct_tag and has_approval

def preprocess_html(html: str) -> str:
    """Used to process content strings before they are parsed as HTML"""
    html = re.sub(r"\[caption([^\]]*)\]", r"<caption\1>", html)
    html = re.sub(r"\[/caption\]", "</caption>", html)
    return html

def filter_articles(tree: ElementTree, issue_num: str) -> List[Article]:
    """Given an ElementTree parsed from an XML dump, returns a list
    of Article instances containing all the articles tagged with issue_num.
    """
    root = tree.getroot()
    articles: List[Article] = []
    article_tags = root.findall('.//item')
    for article_tag in article_tags:
        if not is_for_issue(article_tag, issue_num):
            continue
        article = Article()
        #possible optimization, instead of calling find several times,
        #loop through tag children once and parse out data as we run into it
        article.title = article_tag.find('title').text or '[no title]'
        article.id = article_tag.find('wp:post_id', XML_NS).text
        # go through post meta tags
        post_meta_tags = article_tag.findall('wp:postmeta', XML_NS)
        for post_meta_tag in post_meta_tags:
            meta_key = post_meta_tag.find('wp:meta_key', XML_NS).text
            meta_value = post_meta_tag.find('wp:meta_value', XML_NS).text

            if meta_key == 'mn_subtitle':
                article.subtitle = meta_value
            elif meta_key == 'mn_author':
                article.author = meta_value
            elif meta_key == 'mn_postscript':
                article.postscript = BeautifulSoup(meta_value, 'html.parser')
        #we will post process this later
        article_text_content = article_tag.find('content:encoded', XML_NS).text
        if article_text_content is None:
            article_text_content = ''

        article_text_content = preprocess_html(article_text_content)
            
        article.content = BeautifulSoup(article_text_content, 'html.parser')
        # TODO: instead of appending to content, process postscript separately
        if article.postscript is not None:
            postscript_wrap = article.content.new_tag('footer')
            postscript_wrap.append(article.postscript)
            article.content.append('\n')
            article.content.append(postscript_wrap)
        articles.append(article)
    return articles

def replace_text_with_tag(sub_text: str,
                          repl_tag: Tag,
                          text_tag: bs4.NavigableString,
                          article: Article) -> bs4.NavigableString:
    #if we can't find the parent, assume it's just the document
    parent: Tag
    if text_tag.parent == None or text_tag.parent.name == '[document]':
        parent = article.content
    else:
        parent = text_tag.parent
    tag_idx = parent.contents.index(text_tag)
    #replace the matched text with a tag
    begin, _, end = text_tag.partition(sub_text)
    #convert these strings to tags
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
    imgur_url_regex = re.compile(r'''
    (?:https?:)?//
    (?:i\.)?                  # Don't care if the URL uses the i.imgur.com subdomain
    imgur.com/
    (?P<scheme>a/|gallery/)?  # Don't care about URL scheme
    (?P<hash>\w{5}(?:\w\w)*)  # Match the gallery hash, which will be an odd number of characters
    .?                        # Don't care about any extraneous characters
    (?P<ext>\.\w+)?           # Match any potential file extensions
    ''', re.VERBOSE | re.ASCII)
    imgur_regex = re.compile(rf'''\[embed\]{imgur_url_regex.pattern}\[/embed\]''', re.VERBOSE | re.ASCII)
    imgur_url_templ = 'https://i.imgur.com/{hash}{ext}'

    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag): continue

        for match in imgur_regex.finditer(text_tag):
            img_url = imgur_url_templ.format(**match.groupdict())
            if match['ext'] is None:
                # No file extension, have to scrape
                try:
                    with urllib.request.urlopen('https://imgur.com/{scheme}{hash}/embed?pub=true'.format(**match.groupdict(default=''))) as resp:
                        if resp.getcode() != 200:
                            raise ValueError('Gallery does not exist')
                        html_text = resp.read()
                        imgur_soup = BeautifulSoup(html_text, 'html.parser')
                    img_el = imgur_soup.find(id='image')
                    if img_el is None:
                        raise ValueError('Could not find image source in returned webpage')
                except (urllib.error.HTTPError, ValueError) as e:
                    print(f'Error downloading Imgur gallery {match[0]}. Reason: {e}')
                    input('[Enter] to continue...')
                    continue
                # Filter url given in content
                img_el = img_el.find('img', class_='post')
                img_hash = imgur_url_regex.match(img_el['src'])
                img_url = imgur_url_templ.format(**img_hash.groupdict())
            # Replace embed code with an actual img tag
            img_tag = article.content.new_tag('img', src=img_url)
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
    image.resize((int(w * scale_factor), int(h * scale_factor))).save(image_path, dpi=(DPI, DPI))

def download_images(article: Article) -> Article:
    """Looks through the article content for image tags and downloads them locally and saves
    them as an asset. Then, it changes the link text to point to the local copy instead of
    the web copy.
    """
    img_tag: Tag
    for index, img_tag in enumerate(article.content.find_all('img')):
        # try block because sometimes images without sources get added (don't ask me why)
        try:
            url = img_tag.attrs['src']
        except KeyError:
            continue
        filename = os.path.basename(urllib.parse.urlparse(url).path)
        local_path = article.get_image_location(filename, index)
        print(f"Downloading {local_path}\t{url}", flush=True)
        try:
            _, headers = urllib.request.urlretrieve(url, local_path)
            #resize the image to a reasonable size
            if headers["Content-Type"] != "image/svg+xml":
                resize_image(local_path)
            #InDesign recognizes <link href=""> tags for images
            img_tag.name = 'link'
            img_tag.attrs['href'] = 'file://' + local_path
        except urllib.error.HTTPError as e:
            print(f'Error downloading image {url}. Reason: {e}')
            input("[Enter] to continue...")
        except FileNotFoundError as e:
            print(f'Error downloading image {url}. Reason: {e}')
            input("[Enter] to continue...")
    return article

class Preview(pylatex.base_classes.Environment):
    packages = [pylatex.Package('preview', ['active', 'tightpage', 'pdftex'])]
    escape = False
    content_separator = "\n"

def compile_latex_str(latex: str, filename: str, display: bool = False):
    """Compiles the string latex into a PDF, and saves it to filename.
    """
    document = pylatex.Document()
    document.packages.append(pylatex.Package('amsmath'))
    document.packages.append(pylatex.Package('amssymb'))
    document.packages.append(pylatex.Package('amsfonts'))
    document.preamble.append(pylatex.Command('thispagestyle', 'empty'))
    # People seem to think \Z, \R and \Q exist, even though they don't. Just add them in to avoid problems.
    document.preamble.append(pylatex.NoEscape(r'\newcommand{\Z}{\mathbb{Z}}'))
    document.preamble.append(pylatex.NoEscape(r'\newcommand{\R}{\mathbb{R}}'))
    document.preamble.append(pylatex.NoEscape(r'\newcommand{\Q}{\mathbb{Q}}'))
    with document.create(Preview()):
        document.append(pylatex.NoEscape((r'\[' if display else r'\(') + latex + (r'\]' if display else r'\)')))
    document.generate_pdf(filename, compiler='pdflatex')
    print(f"{filename}\t{latex}", flush=True)

def compile_latex(article: Article) -> Article:
    """Looks through the article content for embedded LaTeX and compiles it into
    PDFs, and adds the proper tags so they show up on import.
    """
    text_tag: bs4.NavigableString
    #matches LaTeX inside one or two dollar signs
    inline_regex = r'\\[([]([\s\S]+?)\\[)\]]'
    # Compiled regex
    p = re.compile(inline_regex)
    # Memo to store validity and compile status of latex
    latex_valid_memo: Dict[str, bool] = dict()
    latex_compiled_memo: Dict[str, bool] = dict()
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag): continue

        for match in p.finditer(text_tag):
            # if this is invalid latex, skip
            if latex_valid_memo.get(match[1], True) == False: continue

            latex = match[1]
            # just use the hash of the latex for a unique filename, this should probably never collide
            # NOTE: sha1 is used for speed; we do not use the built-in `hash` function as it is non-deterministic across runs.
            #       We do NOT need to care about security risks, since we are solely concerned with uniqueness.
            filename = article.get_pdf_location(hashlib.sha1(match[0].encode('utf-8')).hexdigest())
            if match[0] not in latex_compiled_memo:
                try:
                    compile_latex_str(latex, filename, display=(match[0][1] == '['))
                    latex_valid_memo[latex] = True
                    latex_compiled_memo[match[0]] = True
                except subprocess.CalledProcessError:
                    latex_valid_memo[latex] = False
                    input("[Enter] to continue...")
                    continue
            link_tag = Tag(name='link', attrs={'href': 'file://' + filename + '.pdf'})
            #set the current tag to the new end tag
            text_tag = replace_text_with_tag(match[0], link_tag, text_tag, article=article)
    return article

def replace_inline_code(article: Article) -> Article:
    """Replaces Markdown-style inline code with actual code tags
    """
    text_tag: bs4.NavigableString
    p = re.compile(r'`([\s\S]+?)`')
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag): continue

        for match in p.finditer(text_tag):
            code = match[1]
            code_tag = Tag(name='code')
            code_tag.string = code
            text_tag = replace_text_with_tag(match[0], code_tag, text_tag, article=article)

    return article

def convert_manual_syntax_highlighting(article: Article) -> Article:
    """Manually highlighted code gets custom styling
    """
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
    options_regex = re.compile(r'''
    :(\S+?):  # Match the option name
    [ \t]*    # Allow optional whitespace after option name
    ([^\n]*)  # Match the option value (optional)
    ''', re.VERBOSE)
    options_block_regex = re.compile(rf'''
    (?:                          # Look for an option
        \s*                      # Unlimited leading whitespace
        {options_regex.pattern}  # Match an option
        \n                       # Enforce newline after each option
    )+                           # Match multiple options
    [ \t]*\n+                    # Enforce at least two lines of separation between options block and code
    ''', re.VERBOSE)
    for pre_tag in article.content.find_all('pre'):
        # Parse options
        pre_contents = pre_tag.decode_contents()
        options_block = options_block_regex.match(pre_contents)
        options = {}
        if options_block:
            pre_contents = pre_contents[options_block.end():]
            for option_match in options_regex.finditer(options_block[0]):  # match and save options
                options[option_match[1]] = option_match[2] or True  # if no value given, turn into boolean

        pre_contents = highlight_code(pre_contents, options)
        pre_contents = add_linenos(pre_contents, options)

        new_tag = wrap_lines(BeautifulSoup(f'<pre><code>{pre_contents}</code></pre>', 'html.parser'))

        pre_tag.replace_with(new_tag)

    return article

def replace_ellipses(article: Article) -> Article:
    """Replaces "..." with one single ellipse character
    """
    text_tag: bs4.NavigableString
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag): continue

        new_tag = text_tag.replace('...', '…')
        text_tag.replace_with(new_tag)
    return article

def replace_dashes(article: Article) -> Article:
    """Replaces hyphens used as spacing, that is, when they are surrounded with spaces,
    with em dashes.
    Also replaces hyphens in numeric ranges with en dashes.
    """
    text_tag: bs4.NavigableString
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag): continue

        new_tag = re.sub(r'(?<=\d) ?--? ?(?=\d)', '–', text_tag) \
            .replace(' - ', '—') \
            .replace(' --- ', '—') \
            .replace('---', '—') \
            .replace(' -- ', '—') \
            .replace('--', '—') \
            .replace(' — ', '—') \
            .replace('—', ' — ')
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
        if char == '\'':
            char_array[idx] = get_single_quote(direction)

    return ''.join(char_array)

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

def remove_extraneous_spaces(article: Article) -> Article:
    """Removes extraneous spaces after characters.
    """
    text_tag: bs4.NavigableString
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag): continue

        base_alphanumeric = 'A-Za-z0-9'
        accent_characters = 'À-ÖØ-öø-ÿ'
        punctuation = string.punctuation + '‽'

        single_spaced_chars = base_alphanumeric + accent_characters + punctuation

        # a number of articles come to us with punctuation followed by a double space, where the 
        #  first space is an nbsp. maybe it is inserted by a particular text editor?
        #  if we remove them directly, we can stop worrying about nbsps from that point on
        #  (who would be unintentionally adding consecutive nbsps)
        nbsp_sp_pairs = r'(?<=[{}])(\u00A0 )+'.format(single_spaced_chars)
        new_tag = re.sub(nbsp_sp_pairs, ' ', text_tag)

        nbsp_sps_found = new_tag != text_tag 

        # with the nbsp-sp pairs removed, we can remove all other n-tuple breaking spaces
        multi_sp = r'(?<=[{}]) +'.format(single_spaced_chars)
        new_tag = re.sub(multi_sp, ' ', new_tag)

        text_tag.replace_with(new_tag)

        if(nbsp_sps_found or new_tag != text_tag):
            print("Removed extraneous spaces in article \"" + article.title + "\"" + (". Some were nbsp-sp pairs." if nbsp_sps_found else ""))
    return article

def normalize_newlines(article: Article) -> Article:
    """Normalizes newlines to Unix-style LF
    """
    text_tag: bs4.NavigableString
    for text_tag in article.content.find_all(string=True):
        new_tag = text_tag.replace('\r\n', '\n')
        text_tag.replace_with(new_tag)
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
            new_tag_builder = re.split('(?<!\n)\n(?!\n)', text_tag)
            # Keep single line breaks that appear next to another tag, by throwing them out and
            # manually placing a newline character
            prefix = ''
            suffix = ''
            if new_tag_builder[0] == '' and prev_sibling != None:
                new_tag_builder = new_tag_builder[1:]
                prefix = '\n'
            if new_tag_builder[-1] == '' and next_sibling != None:
                new_tag_builder = new_tag_builder[:-1]
                suffix = '\n'
            new_tag = bs4.NavigableString(prefix + LINE_SEPARATOR.join(new_tag_builder) + suffix)
            text_tag.replace_with(new_tag)
    return article

def add_footnotes(article: Article) -> Article:
    """Replaces footnotes in <sup></sup> tags, [\d] format, or *, **, etc."""
    text_tag: bs4.NavigableString
    inline_regex = re.compile(r'\[(\d*)\]')
    footnote_counter = 1  # is the expected number of the next footnote
    for text_tag in article.content.find_all(string=True):
        if keep_verbatim(text_tag): continue

        for match in inline_regex.finditer(text_tag):
            # Check match for provided numbering -- if it exists, then use it
            footnote_num = footnote_counter
            if len(match[1]):
                footnote_num = int(match[1])
            sup_tag = Tag(name='sup')
            sup_tag.string = str(footnote_num)
            text_tag = replace_text_with_tag(match[0], sup_tag, text_tag, article=article)
            # Only auto-increment if blank or explicitly incremented
            if len(match[1]) == 0 or footnote_num == footnote_counter:
                footnote_counter += 1
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
        if non_images and isinstance(non_images[0], str) and non_images[0][0] == ' ':
            non_images[0] = non_images[0][1:]

        figcaption = Tag(name="figcaption")
        figcaption.extend(non_images)
        caption.replace_with(*images, figcaption)
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
    replace_dashes,
    add_smart_quotes,
    remove_extraneous_spaces,
    add_footnotes,
]

def create_asset_dirs():
    if not os.path.isdir(os.path.join(ASSET_DIR, 'img')):
        os.makedirs(os.path.join(ASSET_DIR, 'img'))
    if not os.path.isdir(os.path.join(ASSET_DIR, 'pdf')):
        os.makedirs(os.path.join(ASSET_DIR, 'pdf'))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='article export for mathNEWS')
    parser.add_argument('issue', help='the issue number to export for, e.g, v141i3')
    parser.add_argument('xml_dump', help='location of the XML dump to read from')
    parser.add_argument('-o', '--xml_output',
        help='location of the file to output to',
        default='issue.xml')
    parser.add_argument('-a', '--assets',
        help='a folder to store asset files to',
        default='assets')
    args = parser.parse_args()
    CURRENT_DIR = os.getcwd()
    if os.path.isabs(args.assets):
        ASSET_DIR = args.assets
    else:
        ASSET_DIR = os.path.join(CURRENT_DIR, args.assets)
    shutil.rmtree(ASSET_DIR, ignore_errors=True)
    create_asset_dirs()
    OUTPUT_FILE = args.xml_output
    if not os.path.isfile(args.xml_dump):
        print(f'{args.xml_dump} does not exist.')
        exit(1)
    print('Parsing XML...', flush=True)
    tree = ElementTree.parse(args.xml_dump)
    print('Filtering articles...', flush=True)
    articles = filter_articles(tree, args.issue)
    print('Post-processing articles...', flush=True)
    for process in POST_PROCESS:
        print(f'Preparing post-process pass: {process.__name__}', flush=True)
        articles = map(process, articles)
    print(f'Post-processing...', flush=True)
    root = Element('issue')
    for article in articles:
        root.append(article.to_xml_element())
    print(f'Writing to {OUTPUT_FILE}...', flush=True)
    os.chdir(CURRENT_DIR)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as output_file:
        # do some processing first
        # Remove extraneous lines
        transformed = "\n".join([line for line in html.unescape(ElementTree.tostring(root, encoding='unicode')).split("\n") if line.strip() != ''])
        # Separate articles cleanly
        transformed = "</article>\n<article>".join([article for article in transformed.split("</article><article>")])
        # Separate title, subtitle, and content cleanly
        transformed = "</title>\n<content>".join([article for article in transformed.split("</title><content>")])
        transformed = "</title>\n<subtitle>".join([article for article in transformed.split("</title><subtitle>")])
        transformed = "</subtitle>\n<content>".join([article for article in transformed.split("</subtitle><content>")])
        # Remove extraneous items from beginning and end of lists
        transformed = "<ul>".join([thing for thing in transformed.split("<ul>\n")])
        transformed = "</ul>".join([thing for thing in transformed.split("\n</ul>")])
        transformed = "<ol>".join([thing for thing in transformed.split("<ol>\n")])
        transformed = "</ol>".join([thing for thing in transformed.split("\n</ol>")])
        output_file.write(transformed)
    print('Issue written.')
