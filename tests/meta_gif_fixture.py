"""Synthetic two-frame GIF data for Meta media tests."""
from io import BytesIO

from PIL import Image


def gif_bytes(size=(8, 4)):
    first = Image.new('RGB', size, 'navy')
    second = Image.new('RGB', size, 'orange')
    output = BytesIO()
    first.save(output, format='GIF', save_all=True, append_images=[second], duration=120, loop=0)
    return output.getvalue()
