"""Bounded public-web HTTP and deterministic body extraction (no search snippets)."""
from dataclasses import dataclass
from io import BytesIO
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit, urljoin
from bs4 import BeautifulSoup
import requests


@dataclass(frozen=True)
class FetchedPage:
    url: str
    status_code: int
    headers: dict
    content: bytes


def public_url(url: str, *, resolve: bool = False) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
        raise ValueError('only public HTTP(S) URLs without credentials are allowed')
    host = parts.hostname.lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('private host is forbidden')
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = []
    if resolve and not addresses:
        addresses = [ipaddress.ip_address(x[4][0]) for x in socket.getaddrinfo(host, parts.port or (443 if parts.scheme == 'https' else 80), type=socket.SOCK_STREAM)]
    if any(not addr.is_global for addr in addresses):
        raise ValueError('non-public IP address is forbidden')
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', parts.query, ''))


def fetch_page(url: str, *, max_bytes: int, timeout: float) -> FetchedPage:
    """Validate every redirect, stream bounded bytes, and never reuse user cookies."""
    with requests.Session() as session:
        session.trust_env = False
        session.headers['User-Agent'] = 'KVCacheResearch/1.0 (+public evidence collection)'
        for _ in range(6):
            url = public_url(url, resolve=True)
            with session.get(url, timeout=(timeout, timeout), stream=True, allow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get('Location')
                    if not location:
                        raise ValueError('redirect without Location')
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    return FetchedPage(url, response.status_code, dict(response.headers), b'')
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError('response byte budget exceeded')
                    chunks.append(chunk)
                return FetchedPage(url, response.status_code, dict(response.headers), b''.join(chunks))
        raise ValueError('redirect limit exceeded')


NON_CONTENT = (
    'there was an error while loading',
    'please reload this page',
    'enable javascript',
    'javascript is disabled',
    'we use cookies',
    'accept all cookies',
    'sign in to continue',
    'rate limit exceeded',
)


def extract_body(page: FetchedPage) -> dict:
    """Extract paragraphs plus source metadata. Missing dates remain unknown."""
    content_type = next((v for k, v in page.headers.items() if k.lower() == 'content-type'), '').lower()
    host = urlsplit(page.url).hostname
    if 'application/pdf' in content_type or page.content.startswith(b'%PDF'):
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(page.content))
        if len(reader.pages) > 200:
            raise ValueError('web PDF page limit exceeded')
        paragraphs = [(f'page:{i + 1}', ' '.join((p.extract_text() or '').split())) for i, p in enumerate(reader.pages)]
        paragraphs = [(loc, text) for loc, text in paragraphs if text]
        return {'title': str((reader.metadata or {}).get('/Title') or host), 'institution': host,
                'author': (reader.metadata or {}).get('/Author'), 'published_at': None,
                'paragraphs': paragraphs, 'media_type': 'application/pdf'}
    if not any(t in content_type for t in ('text/html', 'application/xhtml+xml', 'text/plain')):
        raise ValueError(f'unsupported content-type: {content_type}')
    soup = BeautifulSoup(page.content, 'lxml')

    def meta(*names):
        for name in names:
            tag = soup.find('meta', attrs={'property': name}) or soup.find('meta', attrs={'name': name})
            if tag and tag.get('content', '').strip():
                return tag['content'].strip()
        return None

    title = meta('og:title') or (soup.title.get_text(' ', strip=True) if soup.title else host)
    if any(marker in (title or '').lower() for marker in (
        'just a moment', 'access denied', 'security verification', 'attention required',
        'verify you are human', 'captcha', '403 forbidden', '404 not found')):
        raise ValueError('access challenge/error page is not source evidence')
    institution = meta('og:site_name') or host
    author = meta('author', 'article:author')
    published = meta('article:published_time', 'datePublished', 'date', 'pubdate')
    if not published:
        time = soup.find('time', attrs={'datetime': True})
        published = time.get('datetime') if time else None
    for tag in soup.select('script,style,noscript,nav,header,footer,aside,form,svg'):
        tag.decompose()
    container = soup.find('article') or soup.find('main') or soup.body or soup
    paragraphs = []
    for tag in container.find_all(['h1', 'h2', 'h3', 'p', 'li', 'blockquote', 'pre']):
        if tag.find_parent(['p', 'li', 'blockquote', 'pre']):
            continue
        text = ' '.join(tag.get_text(' ', strip=True).split())
        if not text:
            continue
        # 클라이언트 렌더 실패·쿠키 안내 같은 상용구는 인용할 원문이 아니다.
        # 남겨 두면 "There was an error while loading." 이 그대로 Evidence 가 된다.
        if any(marker in text.lower() for marker in NON_CONTENT):
            continue
        paragraphs.append((f'paragraph:{len(paragraphs) + 1}', text))
    if not paragraphs:
        text = ' '.join(container.get_text(' ', strip=True).split())
        paragraphs = [('paragraph:1', text)] if text else []
    return {'title': title, 'institution': institution, 'author': author,
            'published_at': published, 'paragraphs': paragraphs, 'media_type': content_type}
