"""Public Newznab search/get only; never the Hydra UI API or arbitrary URLs."""
from __future__ import annotations

import copy
import json
import re
from urllib.parse import unquote

from ..http import ServiceClient, ServiceError, pagination, parse_xml, positive_id, sanitize, validate_nzb

SEARCH_TYPES = {'search': 'search', 'tv-search': 'tvsearch', 'movie-search': 'movie',
                'audio-search': 'audio', 'book-search': 'book'}


def local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def children(node, name):
    return [c for c in node if local(c.tag) == name] if node is not None else []


def child(node, name):
    return next(iter(children(node, name)), None)


def attributes(node):
    if isinstance(node, dict):
        return node.get('@attributes', {})
    return node.attrib if node is not None else {}


def text(value, limit=300):
    return re.sub(r'\s+', ' ', re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or ''))).strip()[:limit]


def integer(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
        return number if float(value) == number else None
    except (ValueError, TypeError, OverflowError):
        return None


def ranged(value, label, minimum, maximum):
    result = integer(value)
    if result is None or not minimum <= result <= maximum:
        raise ValueError(f'{label} must be an integer between {minimum} and {maximum}')
    return result


def parse(data, headers, service):
    try:
        if data.lstrip().startswith((b'{', b'[')):
            result = json.loads(data)
            if isinstance(result, dict) and 'code' in result and 'channel' not in result:
                error = result.get('code')
            else:
                return result
        else:
            result = parse_xml(data)
            if local(result.tag) == 'error':
                error = result.get('code')
            else:
                return result
    except (ValueError, UnicodeError, RecursionError):
        raise ServiceError(f'{service}: invalid or unsafe Newznab response') from None
    # Never echo upstream descriptions or arbitrary error-code text.
    code = str(integer(error)) if integer(error) is not None else 'unknown'
    raise ServiceError(f'{service}: newznab error {code}')


class NZBHydraClient(ServiceClient):
    def __init__(self, config, transport=None):
        super().__init__(config, transport=transport)
        self._caps = None

    def _fetch(self, params, follow_allowed_redirects=False):
        return self.request_bytes('GET', 'api', params={'apikey': self.config.api_key, 'o': 'json', **params},
                                  follow_allowed_redirects=follow_allowed_redirects)

    def _safe(self, value):
        return sanitize(value, (self.config.api_key, self.config.password))

    def capabilities(self, refresh: bool = False) -> dict:
        if self._caps is not None and not refresh:
            return copy.deepcopy(self._caps)
        obj = parse(*self._fetch({'t': 'caps'}), self.config.name)
        if isinstance(obj, dict):
            server = attributes(obj.get('server'))
            limits = attributes(obj.get('limits'))
            retention = attributes(obj.get('retention'))
            searching = obj.get('searching', {})
            raw_categories = obj.get('categories', {}).get('category', [])
            if isinstance(raw_categories, dict):
                raw_categories = [raw_categories]
        else:
            if local(obj.tag) != 'caps':
                raise ServiceError('nzbhydra: unexpected caps response')
            server = attributes(child(obj, 'server'))
            limits = attributes(child(obj, 'limits'))
            retention = attributes(child(obj, 'retention'))
            searching = child(obj, 'searching')
            raw_categories = children(child(obj, 'categories'), 'category')
        categories = []
        for entry in raw_categories[:100]:
            attrs = attributes(entry)
            if integer(attrs.get('id')) is None:
                continue
            subs = entry.get('subcat', []) if isinstance(entry, dict) else children(entry, 'subcat')
            if isinstance(subs, dict):
                subs = [subs]
            categories.append({'id': integer(attrs.get('id')), 'name': text(attrs.get('name'), 100),
                'subcats': [{'id': integer(attributes(s).get('id')), 'name': text(attributes(s).get('name'), 100)}
                            for s in subs[:50] if integer(attributes(s).get('id')) is not None]})
        searches = {}
        for raw, normalized in SEARCH_TYPES.items():
            entry = searching.get(raw) if isinstance(searching, dict) else child(searching, raw)
            attrs = attributes(entry)
            searches[normalized] = {'available': str(attrs.get('available', 'no')).lower() == 'yes',
                'supported_params': [p.strip() for p in str(attrs.get('supportedParams', '')).split(',') if p.strip()]}
        self._caps = self._safe({'server': {k: text(server[k], 100) for k in ('title', 'version', 'appversion') if k in server},
            'limits': {'max': integer(limits.get('max')), 'default': integer(limits.get('default'))},
            'retention_days': integer(retention.get('days')), 'searching': searches, 'categories': categories})
        return copy.deepcopy(self._caps)

    def search(self, query: str = '', search_type: str = 'search', tmdb_id: int | None = None,
               tvdb_id: int | None = None, imdb_id: str | None = None,
               season: int | None = None, episode: int | None = None,
               categories: list[int] | None = None, min_age: int | None = None,
               max_age: int | None = None, min_size_mb: int | None = None,
               max_size_mb: int | None = None, indexers: list[str] | None = None,
               offset: int = 0, limit: int = 25) -> dict:
        pagination(offset, limit)
        if search_type not in {'search', 'movie', 'tvsearch'}:
            raise ValueError('unsupported search_type: use search, movie or tvsearch')
        clean_query = text(query, 513)
        if len(clean_query) > 512:
            raise ValueError('query must be at most 512 characters')
        params = {'t': search_type, 'offset': offset, 'limit': limit}
        if clean_query:
            params['q'] = clean_query
        for value, wire, required_type in ((tmdb_id, 'tmdbid', 'movie'), (tvdb_id, 'tvdbid', 'tvsearch')):
            if value is not None:
                if search_type != required_type:
                    raise ValueError(f'{wire} requires search_type={required_type}')
                params[wire] = positive_id(value, wire)
        if imdb_id is not None:
            if search_type == 'search' or not re.fullmatch(r'(tt)?\d{1,10}', str(imdb_id), re.IGNORECASE):
                raise ValueError('imdb_id requires movie/tvsearch and a valid IMDB ID')
            params['imdbid'] = imdb_id
        for value, wire, maximum in ((season, 'season', 1000), (episode, 'ep', 10000)):
            if value is not None:
                if search_type != 'tvsearch':
                    raise ValueError(f'{wire} requires tvsearch')
                params[wire] = ranged(value, wire, 0, maximum)
        if categories:
            if len(categories) > 25:
                raise ValueError('at most 25 categories can be selected')
            params['cat'] = ','.join(str(ranged(c, 'category', 1, 999999)) for c in dict.fromkeys(categories))
        for low, high, wire_low, wire_high, maximum in (
            (min_age, max_age, 'minage', 'maxage', 3650),
            (min_size_mb, max_size_mb, 'minsize', 'maxsize', 2000000)):
            if low is not None:
                params[wire_low] = ranged(low, wire_low, 0, maximum)
            if high is not None:
                params[wire_high] = ranged(high, wire_high, 0, maximum)
            if low is not None and high is not None and low > high:
                raise ValueError(f'{wire_low} must be <= {wire_high}')
        if indexers:
            if len(indexers) > 25 or any(not text(i, 100) or ',' in i for i in indexers):
                raise ValueError('select at most 25 nonempty indexer names without commas')
            params['indexers'] = ','.join(dict.fromkeys(text(i, 100) for i in indexers))
        # Explicit caps discovery enables validation without an extra fetch on every search.
        if self._caps:
            capability = self._caps['searching'].get(search_type, {})
            if capability.get('available') is False:
                raise ServiceError(f'nzbhydra: {search_type} search is not supported')
            supported = capability.get('supported_params', [])
            for key in ('tmdbid', 'tvdbid', 'imdbid', 'season', 'ep'):
                if supported and key in params and key not in supported:
                    raise ServiceError(f'nzbhydra: search does not advertise {key}')
        obj = parse(*self._fetch(params), self.config.name)
        if isinstance(obj, dict):
            channel = obj.get('channel')
            if not isinstance(channel, dict):
                raise ServiceError('nzbhydra: unexpected search response')
            paging = attributes(channel.get('response'))
            items = channel.get('item', [])
            if isinstance(items, dict):
                items = [items]
        else:
            if local(obj.tag) != 'rss':
                raise ServiceError('nzbhydra: unexpected search response')
            channel = child(obj, 'channel')
            paging = attributes(child(channel, 'response'))
            items = []
            for node in children(channel, 'item'):
                item = {local(c.tag): c.text for c in node}
                item['enclosure'] = {'@attributes': attributes(child(node, 'enclosure'))}
                item['attr'] = [{'@attributes': c.attrib} for c in children(node, 'attr')]
                items.append(item)
        results = []
        for item in items[:1000]:
            if not isinstance(item, dict):
                continue
            rid = text(item.get('guid') or item.get('id'), 64)
            if not rid:
                continue
            attrs = item.get('attr', [])
            if isinstance(attrs, dict):
                attrs = [attrs]
            attrs = {attributes(a).get('name'): attributes(a).get('value') for a in attrs}
            enclosure = attributes(item.get('enclosure'))
            result: dict = {'id': rid, 'title': text(item.get('title'))}
            if item.get('pubDate'):
                result['date'] = text(item['pubDate'], 64)
            size = integer(enclosure.get('length')) or integer(attrs.get('size')) or integer(item.get('size'))
            if size and size > 0:
                result['size_bytes'] = size
            if item.get('category'):
                result['category'] = text(item['category'], 100)
            if attrs.get('hydraIndexerName'):
                result['indexer'] = text(attrs['hydraIndexerName'], 100)
            for wire, field in (('grabs', 'grabs'), ('hydraIndexerScore', 'indexer_score'),
                                ('files', 'files'), ('seeders', 'seeders'), ('leechers', 'leechers')):
                number = integer(item.get(wire)) if wire in item else integer(attrs.get(wire))
                if number is not None and (number >= 0 or field == 'indexer_score'):
                    result[field] = number
            results.append(self._safe(result))
        return {'offset': integer(paging.get('offset')) if integer(paging.get('offset')) is not None else offset,
                'limit': limit, 'total': integer(paging.get('total')), 'results': results[:limit],
                'truncated': len(results) > limit}

    def get_nzb(self, result_id: str) -> tuple[bytes, str]:
        if isinstance(result_id, bool) or not re.fullmatch(r'[0-9]{1,19}', str(result_id).strip()):
            raise ValueError('result_id must be a positive numeric Hydra result ID')
        rid = str(result_id).strip()
        if not 1 <= int(rid) <= 2**63 - 1:
            raise ValueError('result_id is outside the valid Hydra result ID range')
        data, headers = self._fetch({'t': 'get', 'id': rid}, follow_allowed_redirects=True)
        if not data:
            raise ServiceError('nzbhydra: empty NZB response')
        # Parse error envelopes, but never return descriptions or authenticated links.
        obj = parse(data, headers, self.config.name)
        if isinstance(obj, dict):
            raise ServiceError('nzbhydra: response is not an NZB')
        validate_nzb(data)
        disposition = headers.get('content-disposition', '')
        match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)|filename\s*=\s*\"?([^\";]+)", disposition, re.IGNORECASE)
        filename = unquote(next((g for g in match.groups() if g), '')) if match else ''
        filename = text(filename.replace('\\', '/').rsplit('/', 1)[-1], 200)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._()+\-]{0,250}\.nzb', filename, re.IGNORECASE):
            filename = f'result-{rid}.nzb'
        return data, filename
