# -*- coding: utf-8 -*-
# Part of Inphms, see License file for full copyright and licensing details.
import base64
import binascii
import io
from typing import Tuple, Union

from PIL import Image, ImageOps
# We can preload Ico too because it is considered safe
from PIL import IcoImagePlugin
try:
    from PIL.Image import Transpose, Palette, Resampling
except ImportError:
    Transpose = Palette = Resampling = Image

from random import randrange

from inphms.exceptions import UserError
from inphms.tools.misc import DotDict
# from inphms.tools.translate import LazyTranslate


__all__ = ["image_process"]
# _lt = LazyTranslate('base')

# Preload PIL with the minimal subset of image formats we need
Image.preinit()
Image._initialized = 2

# Maps only the 6 first bits of the base64 data, accurate enough
# for our purpose and faster than decoding the full blob first
FILETYPE_BASE64_MAGICWORD = {
    b'/': 'jpg',
    b'R': 'gif',
    b'i': 'png',
    b'P': 'svg+xml',
    b'U': 'webp',
}

EXIF_TAG_ORIENTATION = 0x112
# The target is to have 1st row/col to be top/left
# Note: rotate is counterclockwise
EXIF_TAG_ORIENTATION_TO_TRANSPOSE_METHODS = { # Initial side on 1st row/col:
    0: [],                                              # reserved
    1: [],                                              # top/left
    2: [Transpose.FLIP_LEFT_RIGHT],                     # top/right
    3: [Transpose.ROTATE_180],                          # bottom/right
    4: [Transpose.FLIP_TOP_BOTTOM],                     # bottom/left
    5: [Transpose.FLIP_LEFT_RIGHT, Transpose.ROTATE_90],# left/top
    6: [Transpose.ROTATE_270],                          # right/top
    7: [Transpose.FLIP_TOP_BOTTOM, Transpose.ROTATE_90],# right/bottom
    8: [Transpose.ROTATE_90],                           # left/bottom
}

# Arbitrary limit to fit most resolutions, including Samsung Galaxy A22 photo,
# 8K with a ratio up to 16:10, and almost all variants of 4320p
IMAGE_MAX_RESOLUTION = 50e6

class ImageProcess:
    
    def __init__(self, source, verify_resolution=True):
        """Initialize the ``source`` image for processing.

        :param bytes source: the original image binary

            No processing will be done if the `source` is falsy or if
            the image is SVG.
        :param verify_resolution: if True, make sure the original image size is not
            excessive before starting to process it. The max allowed resolution is
            defined by `IMAGE_MAX_RESOLUTION`.
        :type verify_resolution: bool
        :rtype: ImageProcess

        :raise: ValueError if `verify_resolution` is True and the image is too large
        :raise: UserError if the image can't be identified by PIL
        """
        self.source = source or False
        self.operationsCount = 0

        if not source or source[:1] == b'<' or (source[0:4] == b'RIFF' and source[8:15] == b'WEBPVP8'):
            # don't process empty source or SVG or WEBP
            self.image = False
        else:
            try:
                self.image = Image.open(io.BytesIO(source))
            except (OSError, binascii.Error):
                raise UserError(_lt("This file could not be decoded as an image file."))

            # Original format has to be saved before fixing the orientation or
            # doing any other operations because the information will be lost on
            # the resulting image.
            self.original_format = (self.image.format or '').upper()

            self.image = image_fix_orientation(self.image)

            w, h = self.image.size
            if verify_resolution and w * h > IMAGE_MAX_RESOLUTION:
                raise UserError(_lt("Too large image (above %sMpx), reduce the image size.", str(IMAGE_MAX_RESOLUTION / 1e6)))


def image_process(source, size=(0, 0), verify_resolution=False, quality=0, expand=False, crop=None, colorize=False, output_format='', padding=False):
    """Process the `source` image by executing the given operations and
    return the result image.
    """
    if not source or ((not size or (not size[0] and not size[1])) and not verify_resolution and not quality and not crop and not colorize and not output_format and not padding):
        # for performance: don't do anything if the image is falsy or if
        # no operations have been requested
        return source

    image = ImageProcess(source, verify_resolution)
    if size:
        if crop:
            center_x = 0.5
            center_y = 0.5
            if crop == 'top':
                center_y = 0
            elif crop == 'bottom':
                center_y = 1
            image.crop_resize(max_width=size[0], max_height=size[1], center_x=center_x, center_y=center_y)
        else:
            image.resize(max_width=size[0], max_height=size[1], expand=expand)
    if padding:
        image.add_padding(padding)
    if colorize:
        image.colorize(colorize if isinstance(colorize, tuple) else None)
    return image.image_quality(quality=quality, output_format=output_format)

# ----------------------------------------
# Misc image tools
# ---------------------------------------

def image_data_uri(base64_source: bytes) -> str:
    """This returns data URL scheme according RFC 2397
    (https://tools.ietf.org/html/rfc2397) for all kind of supported images
    (PNG, GIF, JPG and SVG), defaulting on PNG type if not mimetype detected.
    """
    return 'data:image/%s;base64,%s' % (
        FILETYPE_BASE64_MAGICWORD.get(base64_source[:1], 'png'),
        base64_source.decode(),
    )
