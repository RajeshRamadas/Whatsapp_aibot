"""Synthetic, offline scan fixtures shared by backend and browser checks."""
import io

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas


def scan():
    image = Image.new('RGB', (1200, 300), 'white')
    ImageDraw.Draw(image).text((40, 90), 'Refunds accepted within 30 days.', fill='black',
                               font=ImageFont.load_default(size=42))
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def mixed_pdf():
    output = io.BytesIO()
    canvas = Canvas(output)
    canvas.drawString(40, 750, 'Shipping is free.')
    canvas.showPage()
    canvas.drawImage(ImageReader(io.BytesIO(scan())), 40, 550, width=500, height=125)
    canvas.showPage()
    canvas.showPage()  # A blank page is recognized but never becomes an empty chunk.
    canvas.save()
    return output.getvalue()
