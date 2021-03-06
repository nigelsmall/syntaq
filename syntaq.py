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
from pygments.lexers import get_lexer_by_name
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


class Lexer(object):

    def __init__(self, escape, *markers):
        self.escape = escape
        self.markers = [self.escape]
        self.markers.extend(markers)
        self.marker_chars = set(marker[0] for marker in self.markers)

    def tokens(self, source):
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
        self.source = source
        partitioner = Lexer("~",
            "http://", "https://", "ftp://", "mailto:", "<<", ">>",
                            Quote.BLOCK_DELIMITER, "<--", "-->",
            "\\\\", "{{", "}}", Literal.INLINE_DELIMITER, Quote.INLINE_DELIMITER,
            "**", "//", "^^", "__", "[[", "]]", "|"
                            )
        self.tokens = list(partitioner.tokens(source))

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
                    if token[0] == "~":
                        source.append(token[1:])
                    elif token == end_token:
                        break
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
        self.text = Text("".join(chars).strip().rstrip("=").rstrip())
        if self.level > 6:
            self.level = 6

    @property
    def html(self):
        out = HTML()
        if self.level == 1:
            out.element("h1", html=self.text.html)
        else:
            heading_text = self.text
            heading_id = "".join(ch if ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" else "-"
                                 for ch in heading_text.source)
            heading_id = heading_id.strip("-").lower()
            while "--" in heading_id:
                heading_id = heading_id.replace("--", "-")
            tag = "h%d" % self.level
            out.start_tag(tag, {"id": heading_id})
            out.write_html(heading_text.html)
            out.element("a", {"href": "#%s" % heading_id}, raw="&sect;")
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


class Literal(object):

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
        assert source.startswith("|")
        bracket_tokens = {
            Literal.INLINE_DELIMITER: Literal.INLINE_DELIMITER,
            "[[": "]]",
            "{{": "}}",
        }
        lexer = Lexer("~", "|", Literal.INLINE_DELIMITER, "[[", "]]", "{{", "}}")
        source = source.rstrip()
        if source.endswith("|"):
            source = source[:-1]
        tokens = list(lexer.tokens(source))
        cells = []
        while tokens:
            token = tokens.pop(0)
            if token == "|":
                cells.append([])
            elif token in bracket_tokens:
                end = bracket_tokens[token]
                cells[-1].append(token)
                while tokens:
                    token = tokens.pop(0)
                    cells[-1].append(token)
                    if token == end:
                        break
            else:
                cells[-1].append(token)
        self.cells = ["".join(cell) for cell in cells]

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


class Parser(object):

    def __init__(self):
        self.blocks = []
        self.context = Block()
        self.title = None
        self.title_level = 7

    def parse(self, source):

        def append(block):
            if block:
                self.blocks.append(block)

        def parse_literal(line):
            if line.startswith(Literal.BLOCK_DELIMITER):
                append(self.context)
                self.context = Block()
            else:
                self.context.lines.append(Literal(line))

        def parse_quote(line):
            if line.startswith(Quote.BLOCK_DELIMITER):
                append(self.context)
                self.context = Block()
            else:
                self.context.lines.append(Quote(line))

        for line in source.splitlines(True):
            if self.context.content_type is Literal:
                parse_literal(line)
            elif self.context.content_type is Quote:
                parse_quote(line)
            else:
                line = line.rstrip()
                stripped_line = line.lstrip()
                if Heading.check(line):
                    append(self.context)
                    self.context = Block()
                    source = Heading(line)
                    append(Block(Heading, lines=[source]))
                    if not self.title or source.level < self.title_level:
                        self.title, self.title_level = source.text.html, source.level
                elif line.startswith("----"):
                    append(self.context)
                    self.context = Block()
                    append(Block(HorizontalRule, lines=[HorizontalRule(line)]))
                elif ListItem.check(stripped_line, self.context.content_type):
                    source = ListItem(stripped_line)
                    if not (self.context and self.context.content_type is ListItem and self.context.lines[0].compatible(source)):
                        append(self.context)
                        self.context = Block(ListItem)
                    self.context.lines.append(source)
                elif line.startswith(Literal.BLOCK_DELIMITER):
                    metadata = line.lstrip("`").strip()
                    append(self.context)
                    self.context = Block(Literal, metadata=metadata)
                elif line.startswith(Quote.BLOCK_DELIMITER):
                    metadata = line.lstrip('"').strip()
                    append(self.context)
                    self.context = Block(Quote, metadata=metadata)
                elif line.startswith("|"):
                    if self.context.content_type is not TableRow:
                        append(self.context)
                        self.context = Block(TableRow)
                    self.context.lines.append(TableRow(line))
                else:
                    if self.context.content_type is not None:
                        append(self.context)
                        self.context = Block()
                    if line:
                        self.context.lines.append(line)
                    else:
                        if self.context:
                            append(self.context)
                            self.context = Block()
        append(self.context)


class Document(object):

    def __init__(self):
        self.parser = Parser()
        self.blocks = []
        self.block = Block()

    def parse(self, source):
        self.parser.parse(source)

    @property
    def title(self):
        return self.parser.title

    @property
    def html(self):
        out = HTML()
        for block in self.parser.blocks:
            if block.content_type is None:
                out.element("p", html=Text(" ".join(block.lines)).html)
            elif block.content_type in (Heading, HorizontalRule):
                for line in block.lines:
                    out.write_html(line.html)
            elif block.content_type is Literal:
                source = "".join(line.line for line in block.lines)
                lang, _, metadata = block.metadata.partition(" ")
                try:
                    lexer = get_lexer_by_name(lang)
                except ClassNotFound:
                    lexer = None
                if lexer is None:
                    out.start_tag("pre")
                    out.write_text(source)
                    out.end_tag("pre")
                else:
                    out.write_raw(highlight(source, lexer, HtmlFormatter()))
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
    "__": "sub",
    "^^": "sup",
}
BRACKET_TOKENS = {
    "<<": (">>", script_writer),
    Literal.INLINE_DELIMITER: (Literal.INLINE_DELIMITER, code_writer),
    "{{": ("}}", image_writer),
}


@get("/<name>")
def content(name):
    try:
        with open("content/%s.syntaq" % name) as f:
            source = f.read()
            document = Document()
            document.parse(source)
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
