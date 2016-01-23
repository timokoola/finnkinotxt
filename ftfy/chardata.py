# -*- coding: utf-8 -*-
"""
This gives other modules access to the gritty details about characters and the
encodings that use them.
"""

from __future__ import unicode_literals
import re
import zlib
import unicodedata
from pkg_resources import resource_string
from ftfy.compatibility import unichr

# These are the five encodings we will try to fix in ftfy, in the
# order that they should be tried.
CHARMAP_ENCODINGS = [
    'latin-1',
    'sloppy-windows-1252',
    'macroman',
    'cp437',
    'sloppy-windows-1251',
]


def _build_regexes():
    """
    ENCODING_REGEXES contain reasonably fast ways to detect if we
    could represent a given string in a given encoding. The simplest one is
    the 'ascii' detector, which of course just determines if all characters
    are between U+0000 and U+007F.
    """
    # Define a regex that matches ASCII text.
    encoding_regexes = {'ascii': re.compile('^[\x00-\x7f]*$')}

    for encoding in CHARMAP_ENCODINGS:
        latin1table = ''.join(unichr(i) for i in range(128, 256))
        charlist = latin1table.encode('latin-1').decode(encoding)

        # Build a regex from the ASCII range, followed by the decodings of
        # bytes 0x80-0xff in this character set. (This uses the fact that all
        # regex special characters are ASCII, and therefore won't appear in the
        # string.)
        regex = '^[\x00-\x7f{0}]*$'.format(charlist)
        encoding_regexes[encoding] = re.compile(regex)
    return encoding_regexes
ENCODING_REGEXES = _build_regexes()


def _build_utf8_punct_regex():
    """
    Recognize UTF-8 mojibake that's so blatant that we can fix it even when the
    rest of the string doesn't decode as UTF-8 -- namely, UTF-8 sequences for
    the 'General Punctuation' characters U+2000 to U+2040, re-encoded in
    Windows-1252.

    These are recognizable by the distinctive 'â€' ('\xe2\x80') sequence they
    all begin with when decoded as Windows-1252.
    """
    # We're making a regex that has all the literal bytes from 0x80 to 0xbf in
    # a range. "Couldn't this have just said [\x80-\xbf]?", you might ask.
    # However, when we decode the regex as Windows-1252, the resulting
    # characters won't even be remotely contiguous.
    #
    # Unrelatedly, the expression that generates these bytes will be so much
    # prettier when we deprecate Python 2.
    continuation_char_list = ''.join(
        unichr(i) for i in range(0x80, 0xc0)
    ).encode('latin-1')
    obvious_utf8 = ('â€['
                    + continuation_char_list.decode('sloppy-windows-1252')
                    + ']')
    return re.compile(obvious_utf8)
PARTIAL_UTF8_PUNCT_RE = _build_utf8_punct_regex()


# Recognize UTF-8 sequences that would be valid if it weren't for a b'\xa0'
# that some Windows-1252 program converted to a plain space.
#
# The smaller values are included on a case-by-case basis, because we don't want
# to decode likely input sequences to unlikely characters. These are the ones
# that *do* form likely characters before 0xa0:
#
#   0xc2 -> U+A0 NO-BREAK SPACE
#   0xc3 -> U+E0 LATIN SMALL LETTER A WITH GRAVE
#   0xc5 -> U+160 LATIN CAPITAL LETTER S WITH CARON
#   0xce -> U+3A0 GREEK CAPITAL LETTER PI
#   0xd0 -> U+420 CYRILLIC CAPITAL LETTER ER
#
# These still need to come with a cost, so that they only get converted when
# there's evidence that it fixes other things. Any of these could represent
# characters that legitimately appear surrounded by spaces, particularly U+C5
# (Å), which is a word in multiple languages!
#
# We should consider checking for b'\x85' being converted to ... in the future.
# I've seen it once, but the text still wasn't recoverable.

ALTERED_UTF8_RE = re.compile(b'[\xc2\xc3\xc5\xce\xd0][ ]'
                             b'|[\xe0-\xef][ ][\x80-\xbf]'
                             b'|[\xe0-\xef][\x80-\xbf][ ]'
                             b'|[\xf0-\xf4][ ][\x80-\xbf][\x80-\xbf]'
                             b'|[\xf0-\xf4][\x80-\xbf][ ][\x80-\xbf]'
                             b'|[\xf0-\xf4][\x80-\xbf][\x80-\xbf][ ]')


# These regexes match various Unicode variations on single and double quotes.
SINGLE_QUOTE_RE = re.compile('[\u2018-\u201b]')
DOUBLE_QUOTE_RE = re.compile('[\u201c-\u201f]')


def possible_encoding(text, encoding):
    """
    Given text and a single-byte encoding, check whether that text could have
    been decoded from that single-byte encoding.

    In other words, check whether it can be encoded in that encoding, possibly
    sloppily.
    """
    return bool(ENCODING_REGEXES[encoding].match(text))


CHAR_CLASS_STRING = zlib.decompress(
    resource_string(__name__, 'char_classes.dat')
).decode('ascii')

def chars_to_classes(string):
    """
    Convert each Unicode character to a letter indicating which of many
    classes it's in.

    See build_data.py for where this data comes from and what it means.
    """
    return string.translate(CHAR_CLASS_STRING)


def _build_control_char_mapping():
    """
    Build a translate mapping that strips all C0 control characters,
    except those that represent whitespace.
    """
    control_chars = {}
    for i in range(32):
        control_chars[i] = None

    # Map whitespace control characters to themselves.
    for char in '\t\n\f\r':
        del control_chars[ord(char)]
    return control_chars
CONTROL_CHARS = _build_control_char_mapping()


# A translate mapping that breaks ligatures made of Latin letters. While
# ligatures may be important to the representation of other languages, in
# Latin letters they tend to represent a copy/paste error.
#
# Ligatures may also be separated by NFKC normalization, but that is sometimes
# more normalization than you want.
LIGATURES = {
    ord('Ĳ'): 'IJ',
    ord('ĳ'): 'ij',
    ord('ﬀ'): 'ff',
    ord('ﬁ'): 'fi',
    ord('ﬂ'): 'fl',
    ord('ﬃ'): 'ffi',
    ord('ﬄ'): 'ffl',
    ord('ﬅ'): 'ſt',
    ord('ﬆ'): 'st'
}


def _build_width_map():
    """
    Build a translate mapping that replaces halfwidth and fullwidth forms
    with their standard-width forms.
    """
    # Though it's not listed as a fullwidth character, we'll want to convert
    # U+3000 IDEOGRAPHIC SPACE to U+20 SPACE on the same principle, so start
    # with that in the dictionary.
    width_map = {0x3000: ' '}
    for i in range(0xff01, 0xfff0):
        char = unichr(i)
        alternate = unicodedata.normalize('NFKC', char)
        if alternate != char:
            width_map[i] = alternate
    return width_map
WIDTH_MAP = _build_width_map()
