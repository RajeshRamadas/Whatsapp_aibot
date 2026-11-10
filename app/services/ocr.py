"""Local, bounded text recognition. No uploaded content leaves the worker for OCR."""
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import settings

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp'}
FORMATS = {'PNG', 'JPEG', 'TIFF', 'WEBP'}


class OCRError(ValueError):
    pass


def run(arguments):
    """Use a separate native process with CPU, address-space, thread and wall-time limits."""
    try:
        return subprocess.run(['prlimit', '--as=536870912', f'--cpu={settings.ocr_timeout_seconds}', '--',
            *map(str, arguments)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={**os.environ, 'OMP_THREAD_LIMIT': '1'}, timeout=settings.ocr_timeout_seconds, check=True).stdout
    except subprocess.TimeoutExpired:
        raise OCRError('Text recognition took too long. Split the file or upload a clearer scan.') from None
    except (OSError, subprocess.CalledProcessError):
        raise OCRError('Text recognition could not finish. Check the file '
                       'or ask an admin to check recognition setup.') from None


def frames(path):
    try:
        with Image.open(path) as image:
            if image.format not in FORMATS:
                raise OCRError('Unsupported image format for text recognition.')
            for frame in range(settings.ocr_max_pages + 1):
                try:
                    image.seek(frame)
                except EOFError:
                    return frame
            raise OCRError('Too many image pages for text recognition. Split the file.')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise OCRError('Text recognition needs a valid PNG, JPEG, TIFF or WebP image.') from None


class Reader:
    def __init__(self, on_page=None):
        self.pages = 0
        self.on_page = on_page
        self.configured = False

    def read(self, path, page, *, pdf=False, frame=0):
        if self.pages >= settings.ocr_max_pages:
            raise OCRError('Too many scanned pages for text recognition. Split the document.')
        self.pages += 1
        with tempfile.TemporaryDirectory(prefix='knowledge-ocr-') as directory:
            directory = Path(directory)
            if pdf:
                run(['pdftoppm', '-f', page, '-l', page, '-singlefile', '-scale-to', '2400', '-png', path,
                     directory / 'page'])
                path, frame = directory / 'page.png', 0
            try:
                with Image.open(path) as image:
                    image.seek(frame)
                    if image.format not in FORMATS:
                        raise OCRError('Unsupported image format for text recognition.')
                    if image.width * image.height > settings.ocr_max_pixels:
                        raise OCRError('Image is too large for text recognition. Reduce its resolution.')
                    image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                    rgba = ImageOps.exif_transpose(image).convert('RGBA')
                    canvas = Image.new('RGB', rgba.size, 'white')
                    canvas.paste(rgba, mask=rgba.getchannel('A'))
                    canvas.save(directory / 'input.png')
            except (UnidentifiedImageError, OSError, EOFError, Image.DecompressionBombError):
                raise OCRError('Text recognition could not read this image. Upload a valid, clearer scan.') from None
            if not self.configured:
                available = set(run(['tesseract', '--list-langs']).decode('utf-8').splitlines())
                if not set(settings.ocr_languages.split('+')) <= available:
                    raise OCRError('Text recognition needs language data. Ask an admin to check recognition setup.')
                self.configured = True
            text = run(['tesseract', directory / 'input.png', 'stdout', '-l', settings.ocr_languages,
                        '--psm', '3']).decode('utf-8').strip()
            if len(text) > 32000:
                raise OCRError('Too much text on one scanned page. Split the image into smaller pages.')
            if self.on_page:
                self.on_page(page)
            return text
