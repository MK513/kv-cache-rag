import hashlib
import json
from pathlib import Path
import pytest
from src.tools import web_search as web

HTML = '''<html><head><title>Deployment notes</title>
<meta property="og:site_name" content="Example Lab">
<meta property="article:published_time" content="2026-09-01"></head>
<body><nav>skip nav</nav><article><h1>Deployment</h1>
<p>Developer Kim said TurboQuant integration requires testing the serving kernels. This is a direct comment about deployment work and does not establish a production deployment.</p>
<p>ITME evaluation used a specific hardware setup. The organization has not provided a public operational adoption statement in these deployment notes.</p></article></body></html>'''


def store(tmp_path, run='run-one', **kwargs):
    assert hasattr(web, 'WebEvidenceStore'), 'body evidence store is missing'
    return web.WebEvidenceStore(run, root=tmp_path, **kwargs)


def transport(url, **kwargs):
    return web.FetchedPage(url, 200, {'content-type': 'text/html'}, HTML.encode())


def searcher(query, top_k):
    return [{'url': 'https://example.org/article', 'title': 'candidate',
             'content': 'A snippet is not verified evidence', 'published_date': '2026-01-01'}]


def test_search_only_creates_candidates_not_evidence(tmp_path):
    s = store(tmp_path, searcher=searcher)
    hits = s.search('adoption', 'TurboQuant', 5)
    assert hits[0]['status'] == 'candidate'
    assert s.manifest['sources'] == {}
    assert s.manifest['evidence'] == {}
    assert list((s.directory / 'search').glob('*.json'))


def test_fetch_extracts_body_provenance_hash_and_paragraph_evidence(tmp_path):
    s = store(tmp_path, transport=transport)
    result = s.fetch('https://example.org/article')
    assert result['status'] == 'ok'
    assert result['published_at'] == '2026-09-01'
    assert result['institution'] == 'Example Lab'
    assert 'skip nav' not in result['body']
    assert 'A snippet' not in result['body']
    assert hashlib.sha256(Path(result['snapshot_path']).read_bytes()).hexdigest() == result['sha256']
    for e in result['evidence']:
        assert e['quote'] in result['body']
        assert e['source_id'] == result['source']['source_id']
        assert e['run_id'] == 'run-one'
        assert e['allowed_uses'] == ['stakeholder']
    manifest = json.loads((s.directory / 'manifest.json').read_text())
    assert len(manifest['sources']) == 1
    assert len(manifest['evidence']) >= 2


@pytest.mark.parametrize('status, body', [(403, HTML), (200, '<html><script>render()</script></html>'),
                                        (200, '<html><body>Access denied</body></html>')])
def test_fetch_failure_never_produces_evidence(tmp_path, status, body):
    s = store(tmp_path, transport=lambda url, **k: web.FetchedPage(url, status, {'content-type': 'text/html'}, body.encode()))
    r = s.fetch('https://example.org/article')
    assert r['status'] == 'failed'
    assert 'evidence' not in r
    assert s.manifest['evidence'] == {}
    assert s.manifest['failures']


def test_per_run_budget_is_persisted_and_cache_is_not_cross_run(tmp_path):
    a = store(tmp_path, transport=transport, max_sources=1)
    first = a.fetch('https://example.org/a')
    assert a.fetch('https://example.org/b')['status'] == 'failed'
    reopened = store(tmp_path, transport=transport, max_sources=1)
    assert reopened.fetch('https://example.org/a')['source']['source_id'] == first['source']['source_id']
    assert reopened.fetch('https://example.org/b')['status'] == 'failed'
    b = store(tmp_path, run='run-two', transport=transport, max_sources=1)
    assert b.fetch('https://example.org/b')['status'] == 'ok'
    assert a.directory != b.directory


def test_body_limit_blocks_instead_of_silently_truncating(tmp_path):
    s = store(tmp_path, transport=transport, max_body_chars=150)
    assert s.fetch('https://example.org/article')['status'] == 'failed'
    assert s.manifest['evidence'] == {}


def test_bad_run_id_and_private_urls_blocked(tmp_path):
    with pytest.raises(ValueError):
        store(tmp_path, run='../outside')
    s = store(tmp_path, transport=transport)
    for url in ['file:///etc/passwd', 'http://127.0.0.1/a', 'http://user:pass@example.org/a']:
        assert s.fetch(url)['status'] == 'failed'
    assert s.manifest['sources'] == {}


def test_bound_tools_require_run_context_and_preserve_signature(tmp_path):
    s = store(tmp_path, transport=transport, searcher=searcher)
    search, fetch = web.bind_web_tools(s)
    assert set(fetch.args) == {'url'}
    assert set(search.args) == {'query', 'technology', 'top_k'}
    assert fetch.invoke({'url': 'https://example.org/article'})['status'] == 'ok'
    with pytest.raises(RuntimeError, match='run'):
        web.search_market_signals.invoke({'query': 'q'})


def test_changed_body_gets_new_id_in_different_run(tmp_path):
    a = store(tmp_path, transport=transport).fetch('https://example.org/a')
    b = store(tmp_path, run='two', transport=lambda url, **k: web.FetchedPage(url, 200, {'content-type': 'text/html'}, HTML.replace('Kim', 'Lee').encode())).fetch('https://example.org/a')
    assert a['source']['source_id'] != b['source']['source_id']


def test_snapshot_tampering_blocks_cached_evidence(tmp_path):
    s = store(tmp_path, transport=transport)
    r = s.fetch('https://example.org/a')
    Path(r['body_path']).write_text('tampered')
    result = s.fetch('https://example.org/a')
    assert result['status'] == 'failed'
    assert 'evidence' not in result


def test_redirected_source_uses_final_url(tmp_path):
    s = store(tmp_path, transport=lambda url, **k: web.FetchedPage('https://example.org/final', 200, {'content-type': 'text/html'}, HTML.encode()))
    r = s.fetch('https://example.org/original')
    assert r['source']['url'] == 'https://example.org/final'
    assert r['source']['requested_url'] == 'https://example.org/original'


def test_redirect_to_private_address_is_rejected(tmp_path):
    s = store(tmp_path, transport=lambda url, **k: web.FetchedPage('http://127.0.0.1/', 200, {'content-type': 'text/html'}, HTML.encode()))
    assert s.fetch('https://example.org/a')['status'] == 'failed'
    assert not s.manifest['evidence']


def test_missing_date_stays_unknown_not_retrieved_date(tmp_path):
    html = HTML.replace('<meta property="article:published_time" content="2026-09-01">', '')
    s = store(tmp_path, transport=lambda url, **k: web.FetchedPage(url, 200, {'content-type': 'text/html'}, html.encode()))
    r = s.fetch('https://example.org/a')
    assert r['published_at'] is None
    assert r['retrieved_at']


def test_failed_search_is_bounded_and_does_not_leak_provider_message(tmp_path):
    def unavailable(*args):
        raise RuntimeError('secret-api-key-123')
    s = store(tmp_path, searcher=unavailable, max_searches=1)
    with pytest.raises(RuntimeError, match='web search failed') as exc:
        s.search('q')
    assert 'secret-api-key' not in str(exc.value)
    with pytest.raises(ValueError, match='budget'):
        s.search('q2')
    assert s.manifest['usage']['search_calls'] == 1


def test_format_signals_blocks_candidates_even_with_long_snippet():
    with pytest.raises(ValueError):
        web.format_signals([{'status': 'candidate', 'snippet': HTML}])


def test_non_200_untrusted_long_error_page_is_rejected(tmp_path):
    s = store(tmp_path, transport=lambda url, **k: web.FetchedPage(url, 200, {'content-type': 'text/html'},
        ('<html><head><title>Just a moment...</title></head><body>' + 'Checking your browser. ' * 20 + '</body></html>').encode()))
    assert s.fetch('https://example.org/a')['status'] == 'failed'
