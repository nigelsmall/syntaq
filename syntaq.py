#!/usr/bin/env python
# -*- encoding: utf-8 -*-

# Copyright 2011-2016 Nigel Small
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import re
import string

from bottle import abort, get, response, run, static_file, template
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.javascript import JavascriptLexer
from pygments.formatters.html import HtmlFormatter
from pygments.util import ClassNotFound

__author__ = "Nigel Small <nigel@nigelsmall.name>"
__copyright__ = "2011-2016 Nigel Small"
__license__ = "Apache License, Version 2.0"
__version__ = "v2"


URI_PATTERN = re.compile(r"""(?i)\b((?:[a-z][\w-]+:(?:/{1,3}|[a-z0-9%])|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'".,<>?«»“”‘’]))""")


def auto_link(text):
    out = HTML()
    bits = URI_PATTERN.split(text)
    out.write_text(bits[0])
    p = 1
    while p < len(bits):
        url = bits[p]
        out.element("a", {"href": url}, text=url)
        p += 5
        out.write_text(bits[p])
        p += 1
    return out.html


def code_writer(out, source):
    return out.element("code", text=source)


def image_writer(out, source):
    src, alt = source.partition("|")[0::2]
    out.tag("img", {"src": src, "alt": alt or None})


def pre_writer(out, source):
    return out.write_text(source)


def script_writer(out, source):
    return out.element("script", raw=source)


class HTML(object):

    @staticmethod
    def entities(text):
        chars = list(text)
        for i, ch in enumerate(chars):
            if ch == "&":
                chars[i] = "&amp;"
            elif ch == "'":
                chars[i] = "&apos;"
            elif ch == "\"":
                chars[i] = "&quot;"
            elif ch == "<":
                chars[i] = "&lt;"
            elif ch == ">":
                chars[i] = "&gt;"
        return "".join(chars)

    def __init__(self, processor=None):
        self.tokens = []
        self.stack = []
        self.token_buffer = []
        self.processor = processor or HTML.entities

    @property
    def html(self):
        return "".join(self.tokens)

    def __repr__(self):
        return self.html

    def _flush(self):
        if self.token_buffer:
            buffer = "".join(self.token_buffer)
            self.tokens.append(self.processor(buffer))
            self.token_buffer = []

    def write_html(self, html):
        self._flush()
        self.tokens.append(html)

    def write_text(self, text, post_process=False):
        if post_process:
            self.token_buffer.extend(text)
        else:
            self._flush()
            self.tokens.extend(HTML.entities(text))

    def write_raw(self, text):
        self._flush()
        self.tokens.extend(text)

    def tag(self, tag, attributes=None):
        if attributes:
            self.write_html("<{0} {1}>".format(
                tag,
                " ".join(
                    '{0}="{1}"'.format(key, HTML.entities(str(value)))
                    for key, value in sorted(attributes.items())
                    if value is not None
                )
            ))
        else:
            self.write_html("<{0}>".format(tag))

    def start_tag(self, tag, attributes=None, void=False):
        self.tag(tag, attributes)
        if not void:
            self.stack.append(tag)

    def end_tag(self, tag=None):
        if not self.stack:
            raise ValueError("No tags to close")
        if not tag:
            tag = self.stack[-1]
        if tag not in self.stack:
            raise ValueError("End tag </{0}> should have corresponding "
                             "start tag <{0}>".format(tag))
        while True:
            t = self.stack.pop()
            self.write_html("</{0}>".format(t))
            if t == tag:
                break

    def element(self, tag, attributes=None, html=None, text=None, raw=None):
        if sum(map(lambda x: 1 if x else 0, (html, text, raw))) > 1:
            raise ValueError("Cannot specify multiple content types")
        self.start_tag(tag, attributes)
        if html:
            self.write_html(html)
        if text:
            self.write_text(text)
        if raw:
            self.write_raw(raw)
        self.end_tag()

    def close(self):
        self._flush()
        while self.stack:
            t = self.stack.pop()
            self.write_html("</{0}>".format(t))


class Partitioner(object):

    def __init__(self, escape, *markers):
        self.escape = escape
        if escape:
            self.markers = [self.escape]
        else:
            self.markers = []
        self.markers.extend(markers)
        self.marker_chars = list(set(marker[0] for marker in self.markers))

    def partition(self, source):
        self.tokens = []
        p, q = 0, 0
        while q < len(source):
            if source[q] in self.marker_chars:
                if self.escape and source[q] == self.escape:
                    start = q + len(self.escape)
                else:
                    start = q
                for seq in self.markers:
                    end = start + len(seq)
                    if source[start:end] == seq:
                        if q > p:
                            yield source[p:q]
                        yield source[q:end]
                        p, q = end, end
                        break
                else:
                    q += 1
            else:
                q += 1
        if q > p:
            yield source[p:q]


class Text(object):

    def __init__(self, source=None):
        partitioner = Partitioner("~",
            "http://", "https://", "ftp://", "mailto:", "<<", ">>",
            Quote.BLOCK_DELIMITER, Preformatted.BLOCK_START, Preformatted.BLOCK_END, "<--", "-->",
            "\\\\", "{{", "}}", Code.INLINE_DELIMITER, Quote.INLINE_DELIMITER,
            "**", "//", "^^", ",,", "[[", "]]", "|"
        )
        self.tokens = list(partitioner.partition(source))

    @property
    def html(self):
        out = HTML(processor=auto_link)
        tokens = self.tokens[:]
        while tokens:
            token = tokens.pop(0)
            if token[0] == "~":
                out.write_text(token[1:])
            elif token in SIMPLE_TOKENS:
                out.write_html(SIMPLE_TOKENS[token])
            elif token in TOGGLE_TOKENS:
                tag = TOGGLE_TOKENS[token]
                if tag in out.stack:
                    out.end_tag(tag)
                else:
                    out.start_tag(tag)
            elif token in BRACKET_TOKENS:
                end_token, writer = BRACKET_TOKENS[token]
                source = []
                while tokens:
                    token = tokens.pop(0)
                    if token == end_token:
                        break
                    elif token[0] == "~":
                        source.append(token[1:])
                    else:
                        source.append(token)
                writer(out, "".join(source))
            elif token == "[[":
                href = []
                while tokens:
                    token = tokens.pop(0)
                    if token in ("|", "]]"):
                        break
                    elif token[0] == "~":
                        href.append(token[1:])
                    else:
                        href.append(token)
                href = "".join(href)
                out.start_tag("a", {"href": href})
                if token != "|":
                    out.write_text(href)
                    out.end_tag("a")
            elif token == "]]":
                try:
                    out.end_tag("a")
                except ValueError:
                    out.write_text(token)
            else:
                out.write_text(token, post_process=True)
        out.close()
        return out.html


class Heading(object):

    @classmethod
    def check(cls, source):
        return source.startswith("=")

    def __init__(self, source):
        if not Heading.check(source):
            raise ValueError("Heading must start with '='")
        chars = list(source)
        self.level = 0
        while chars and chars[0] == "=":
            chars.pop(0)
            self.level += 1
        self.text = "".join(chars).strip().rstrip("=").rstrip()
        if self.level > 6:
            self.level = 6

    @property
    def html(self):
        out = HTML()
        tag = "h%d" % self.level
        out.start_tag(tag)
        out.write_text(self.text)
        out.element("a", {"href": "#"}, raw="&sect;")
        out.end_tag(tag)
        return out.html


class HorizontalRule(object):

    @classmethod
    def check(cls, source):
        return source.startswith("----")

    def __init__(self, source):
        if not HorizontalRule.check(source):
            raise ValueError("Horizontal rule must start with '----'")

    @property
    def html(self):
        out = HTML()
        out.tag("hr")
        return out.html


class ListItem(object):

    @classmethod
    def check(cls, source, content_type):
        if content_type is ListItem or not source.startswith("**"):
            return source and source[0] in "#*"
        else:
            return False

    def __init__(self, source):
        chars = list(source)
        signature = []
        while chars and chars[0] in "#*":
            signature.append(chars.pop(0))
        self.signature = tuple(signature)
        self.level = len(signature)
        self.item = Text("".join(chars).strip())

    def ordered(self, level):
        return self.signature[level] == "#"

    def list_tag(self, level):
        return "ol" if self.ordered(level) else "ul"

    def compatible(self, other):
        m = min(len(self.signature), len(other.signature))
        return self.signature[0:m] == other.signature[0:m]

    @property
    def html(self):
        out = HTML()
        out.element("li", html=self.item.html)
        return out.html


class Preformatted(object):

    BLOCK_START, BLOCK_END = "{{{", "}}}"

    def __init__(self, source):
        self.text = source

    @property
    def html(self):
        out = HTML()
        out.write_text(self.text)
        return out.html


class Code(object):

    INLINE_DELIMITER = "``"
    BLOCK_DELIMITER = "```"

    def __init__(self, source):
        self.line = source

    @property
    def html(self):
        out = HTML()
        out.start_tag("li")
        out.element("code", text=self.line)
        out.end_tag()
        return out.html


class Quote(object):

    INLINE_DELIMITER = '""'
    BLOCK_DELIMITER = '"""'

    def __init__(self, source):
        self.text = Text(source)

    @property
    def html(self):
        return self.text.html


class TableRow(object):

    def __init__(self, source):
        if not source.startswith("|"):
            raise ValueError("Table row must start with '|'")
        bracket_tokens = {
            Code.INLINE_DELIMITER : Code.INLINE_DELIMITER,
            "[[": "]]",
            "{{": "}}",
            Preformatted.BLOCK_START: Preformatted.BLOCK_END,
        }
        partitioner = Partitioner("~", "|", Code.INLINE_DELIMITER, "[[", "]]",
                                  "{{", "}}",
                                  Preformatted.BLOCK_START,
                                  Preformatted.BLOCK_END)
        source = source.rstrip()
        if source.endswith("|"):
            tokens = list(partitioner.partition(source[:-1]))
        else:
            tokens = list(partitioner.partition(source))
        self.cells = []
        while tokens:
            token = tokens.pop(0)
            if token == "|":
                self.cells.append([])
            elif token in bracket_tokens:
                end = bracket_tokens[token]
                self.cells[-1].append(token)
                while tokens:
                    token = tokens.pop(0)
                    self.cells[-1].append(token)
                    if token == end:
                        break
            else:
                self.cells[-1].append(token)
        self.cells = ["".join(cell) for cell in self.cells]

    @property
    def html(self):
        out = HTML()
        out.start_tag("tr")
        for cell in self.cells:
            stripped_cell = cell.strip()
            attributes = {}
            if stripped_cell.startswith("="):
                tag = "th"
                content = cell[1:]
            elif stripped_cell.startswith("`") and stripped_cell.endswith("`"):
                tag = "td"
                content = cell
                attributes["class"] = "code"
            else:
                tag = "td"
                content = cell
            align = None
            if content:
                left_padded = content[0] in string.whitespace
                right_padded = content[-1] in string.whitespace
                if left_padded and right_padded:
                    align = "center"
                elif right_padded:
                    align = "left"
                elif left_padded:
                    align = "right"
            if align:
                content = content.strip()
                attributes["style"] = "text-align:%s" % align
            out.element(tag, attributes, html=Text(content).html)
        out.end_tag("tr")
        return out.html


class Block(object):

    def __init__(self, content_type=None, metadata=None, lines=None):
        self.content_type = content_type
        self.metadata = metadata
        self.lines = []
        if lines:
            for line in lines:
                self.append(line)

    def __len__(self):
        return len(self.lines)

    def __nonzero__(self):
        return bool(self.lines)

    def append(self, line):
        if not self.content_type or isinstance(line, self.content_type):
            self.lines.append(line)
        else:
            raise ValueError("Cannot add {0} to block of {1}".format(line.__class__.__name__, self.content_type.__name__))


class Document(object):

    def __init__(self, source):
        self.blocks = []
        self.title = None
        title_level = 7
        block = Block()
        for line in source.splitlines(True):
            if block.content_type is Preformatted:
                if line.startswith(Preformatted.BLOCK_END):
                    self.append(block)
                    block = Block()
                else:
                    block.lines.append(Preformatted(line))
            elif block.content_type is Code:
                if line.startswith(Code.BLOCK_DELIMITER):
                    self.append(block)
                    block = Block()
                else:
                    block.lines.append(Code(line))
            elif block.content_type is Quote:
                if line.startswith(Quote.BLOCK_DELIMITER):
                    self.append(block)
                    block = Block()
                else:
                    block.lines.append(Quote(line))
            else:
                line = line.rstrip()
                stripped_line = line.lstrip()
                if Heading.check(line):
                    self.append(block)
                    block = Block()
                    source = Heading(line)
                    self.blocks.append(Block(Heading, lines=[source]))
                    if not self.title or source.level < title_level:
                        self.title, title_level = source.text, source.level
                elif line.startswith("----"):
                    self.append(block)
                    block = Block()
                    self.blocks.append(Block(HorizontalRule, lines=[HorizontalRule(line)]))
                elif ListItem.check(stripped_line, block.content_type):
                    source = ListItem(stripped_line)
                    if not (block and block.content_type is ListItem and block.lines[0].compatible(source)):
                        self.append(block)
                        block = Block(ListItem)
                    block.lines.append(source)
                elif line.startswith(Preformatted.BLOCK_START):
                    metadata = line.lstrip("{").strip()
                    self.append(block)
                    block = Block(Preformatted, metadata=metadata)
                elif line.startswith(Code.BLOCK_DELIMITER):
                    metadata = line.lstrip("`").strip()
                    self.append(block)
                    block = Block(Code, metadata=metadata)
                elif line.startswith(Quote.BLOCK_DELIMITER):
                    metadata = line.lstrip('"').strip()
                    self.append(block)
                    block = Block(Quote, metadata=metadata)
                elif line.startswith("|"):
                    if block.content_type is not TableRow:
                        self.append(block)
                        block = Block(TableRow)
                    block.lines.append(TableRow(line))
                else:
                    if block.content_type is not None:
                        self.append(block)
                        block = Block()
                    if line:
                        block.lines.append(line)
                    else:
                        if block:
                            self.blocks.append(block)
                            block = Block()
        self.append(block)

    def append(self, block):
        if block:
            self.blocks.append(block)

    @property
    def html(self):
        out = HTML()
        for block in self.blocks:
            if block.content_type is None:
                out.element("p", html=Text(" ".join(block.lines)).html)
            elif block.content_type in (Heading, HorizontalRule):
                for line in block.lines:
                    out.write_html(line.html)
            elif block.content_type is Preformatted:
                if block.metadata:
                    cls = block.metadata.split()[0]
                    out.start_tag("pre", {"class": cls})
                else:
                    out.start_tag("pre")
                for line in block.lines:
                    out.write_html(line.html)
                out.end_tag("pre")
            elif block.content_type is Code:
                source = "".join(line.line for line in block.lines)
                lang, _, metadata = block.metadata.partition(" ")
                try:
                    lexer = get_lexer_by_name(lang)
                except ClassNotFound:
                    try:
                        lexer = guess_lexer(source)
                    except ClassNotFound:
                        lexer = None
                if lexer is None:
                    out.start_tag("pre")
                    out.write_text(source)
                    out.end_tag("pre")
                else:
                    out.write_raw(highlight(source, JavascriptLexer(), HtmlFormatter()))
            elif block.content_type is Quote:
                out.start_tag("blockquote")
                for line in block.lines:
                    out.write_html(line.html)
                out.end_tag("blockquote")
            elif block.content_type is ListItem:
                level = 0
                for line in block.lines:
                    while level > line.level:
                        out.end_tag()
                        level -= 1
                    while level < line.level:
                        out.start_tag(line.list_tag(level))
                        level += 1
                    out.write_html(line.html)
                while level:
                    out.end_tag()
                    level -= 1
            elif block.content_type is TableRow:
                out.start_tag("table")
                for line in block.lines:
                    out.write_html(line.html)
                out.end_tag("table")
        return out.html


SIMPLE_TOKENS = {
    "\\\\": "<br>",
    "-->": "&rarr;",
    "<--": "&larr;",
}
TOGGLE_TOKENS = {
    "//": "em",
    Quote.INLINE_DELIMITER: "q",
    "**": "strong",
    ",,": "sub",
    "^^": "sup",
}
BRACKET_TOKENS = {
    "<<": (">>", script_writer),
    Code.INLINE_DELIMITER: (Code.INLINE_DELIMITER, code_writer),
    "{{": ("}}", image_writer),
    Preformatted.BLOCK_START: (Preformatted.BLOCK_END, pre_writer),
}


@get("/<name>")
def content(name):
    try:
        with open("content/%s.syntaq" % name) as f:
            source = f.read()
            document = Document(source)
            return template("templates/content.html", title=document.title, body=document.html)
    except FileNotFoundError:
        abort(404)


@get("/_style/pygments.css")
def pygments_style():
    response.content_type = "text/css"
    return HtmlFormatter().get_style_defs('.highlight')


@get("/_style/<name>.css")
def style(name):
    return static_file("%s.css" % name, "style")


if __name__ == "__main__":
    run(reloader=True)
