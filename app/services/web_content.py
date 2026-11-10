"""Extract public page text and heading paths without executing scripts or following assets."""
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..ingest import Block, Headings, paragraphs
from .web_fetch import FetchError, canonical


def extract(text, url, host):
    soup = BeautifulSoup(text, 'html.parser')
    if any('noindex' in tag.get('content', '').lower().replace(',', ' ').split() for tag in soup.find_all(
            'meta', attrs={'name': lambda name: name and name.lower() in ('robots', 'marketingaddaknowledgebot')})):
        return [], []
    links = []
    for tag in soup.find_all('a', href=True, limit=500):
        if 'nofollow' in tag.get('rel', []):
            continue
        try:
            links.append(canonical(urljoin(url, tag['href']), host))
        except (FetchError, ValueError):
            continue
    for tag in soup.select('script,style,nav,footer,aside,form,iframe,noscript,template,[hidden],[aria-hidden="true"]'):
        if tag.parent:
            tag.decompose()
    root = soup.find('main') or soup.find('article') or soup.body or soup
    blocks, headings = [], Headings()
    for tag in root.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'tr', 'pre', 'blockquote']):
        if tag.find_parent(['p', 'li', 'tr', 'pre', 'blockquote']):
            continue
        value = tag.get_text(' ', strip=True)
        if tag.name.startswith('h'):
            headings.push(int(tag.name[1]), value)
        elif value:
            blocks.append(Block(value, headings.path, row=tag.name == 'tr'))
    if not blocks:
        blocks = [Block(value, headings.path) for value in paragraphs(root.get_text('\n', strip=True))]
    if sum(len(block.text) for block in blocks) > 100000:
        raise FetchError('The website page contains too much text. Choose a smaller page.')
    return blocks, list(dict.fromkeys(links))
