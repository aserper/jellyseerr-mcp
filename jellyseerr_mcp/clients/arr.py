"""Explicit Radarr and Sonarr clients against their v3 APIs.

Mutation contracts verified against upstream sources (2026-10-04, Radarr and
Sonarr ``develop`` branches): AddMovieService.AddSkyhookData / AddSeriesService
fetch metadata server-side by TmdbId/TvdbId; Radarr ``PUT movie/editor`` and
Sonarr ``PUT episode/{id}`` / ``PUT episode/monitor`` are monitored-only
updates; ``POST release`` resolves the release from a server-side cache keyed
``{indexerId}_{guid}`` and does NOT re-check rejections, so clients must
requery results and refuse non-approved releases before grabbing.

Live probes (2026-10-05) show ``/api/v3/calendar`` accepts only ``start``/``end``
as bare dates or ISO timestamps and ignores a ``movieId``/``seriesId`` filter, so
per-title scoping is done by the caller; Sonarr's calendar returns the owning
``series`` sub-object only when ``includeSeries=true`` is sent. Sonarr's
``/api/v3/episode`` requires a ``seriesId`` (or episodeIds) and answers 400
without one, so whole-library next-up is only possible through the calendar.

Rules enforced here: every mutation calls require_write() before ANY outbound
request (including preflight reads); additions never auto-search; grabs
revalidate the inspected release (guid + indexerId) and never force rejected
ones; episode selections are owned by the stated series and bounded to 100.

Queue removal is deliberately narrow. ``DELETE /api/v3/queue/{id}`` defaults to
``removeFromClient=false`` and ``blocklist=false``, so the default call only
clears the *arr's own queue record and leaves the downloader's files on disk.
The client never overrides those defaults; the caller has to ask for either.
Blocklisting additionally POSTs the failed history entry to ``/api/v3/history/failed``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..http import ServiceClient, page, pagination, positive_id, select, window

MOVIE_FIELDS = ('id', 'title', 'tmdbId', 'year', 'monitored', 'status', 'path',
                'qualityProfileId', 'hasFile', 'sizeOnDisk')
SERIES_FIELDS = ('id', 'title', 'tvdbId', 'year', 'monitored', 'status', 'path',
                 'qualityProfileId')
EPISODE_FIELDS = ('id', 'seriesId', 'seasonNumber', 'episodeNumber', 'title',
                  'monitored', 'hasFile', 'airDateUtc')
RELEASE_FIELDS = ('guid', 'indexerId', 'indexer', 'title', 'size', 'seeders',
                  'leechers', 'protocol', 'approved', 'temporarilyRejected',
                  'rejected', 'rejections')
COMMAND_FIELDS = ('id', 'name', 'status', 'queued', 'started', 'ended',
                  'duration', 'message')
QUEUE_FIELDS = ('id', 'title', 'status', 'trackedDownloadStatus',
                'trackedDownloadState', 'size', 'sizeleft', 'timeleft',
                'estimatedCompletionTime', 'downloadClient', 'indexer',
                'protocol', 'outputPath', 'downloadId')
HISTORY_DATA_FIELDS = ('indexer', 'releaseGroup', 'downloadClient', 'reason')
PROFILE_FIELDS = ('id', 'name', 'upgradeAllowed')
CALENDAR_WINDOW_DAYS = 90
MOVIE_CALENDAR_FIELDS = ('id', 'title', 'tmdbId', 'year', 'monitored', 'hasFile',
                         'status', 'inCinemas', 'digitalRelease', 'physicalRelease',
                         'releaseDate')
SERIES_CALENDAR_FIELDS = ('id', 'seriesId', 'seasonNumber', 'episodeNumber', 'title',
                          'airDate', 'airDateUtc', 'hasFile', 'monitored', 'tvdbId')
MAX_SELECTIONS = 100


def _int_list(values: object, name: str, minimum: int = 1) -> list[int]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError(f'{name} must be a list of integers')
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f'{name} must contain integers >= {minimum}')
        result.append(value)
    return result


def _season_number(value: object, name: str = 'season_number') -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{name} must be a non-negative integer (0 = specials)')
    return value


def _instant(value: object) -> datetime | None:
    """Parse a Sonarr/Radarr air or release timestamp, or None when unusable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _bounded_text(value: object, limit: int = 512) -> str:
    """Bound free-text (health/rejection messages); IDs stay intact."""
    return str(value)[:limit] if value is not None else ''


def _native_page(response: object, items: list, page_number: int, limit: int) -> dict:
    total = response.get('totalRecords') if isinstance(response, dict) else None
    if not isinstance(total, int):
        total = len(items)
    offset = (page_number - 1) * limit
    return {'items': items, 'offset': offset, 'limit': limit, 'total': total,
            'has_more': offset + len(items) < total, 'pagination': 'native'}


class ArrClientBase(ServiceClient):
    """Mechanics shared by Radarr and Sonarr; service differences live in subclasses."""

    def __init__(self, config, transport=None):
        super().__init__(config, transport=transport)

    def _paging(self, page_number: object, limit: object) -> tuple[int, int]:
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise ValueError('page_number must be a positive integer')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('limit must be between 1 and 100')
        return page_number, limit

    def _list(self, method: str, path: str, **kwargs) -> list:
        result = self.request_json(method, path, **kwargs)
        if result is None:
            return []
        return result if isinstance(result, list) else [result]

    def _release_search(self, params: dict) -> list:
        """Fresh target search; uses the longer per-service search deadline.

        Indexer fan-out is routinely slower than metadata reads and waits on
        unresponsive indexers, so it must not share the short request timeout.
        """
        return self._list('GET', 'api/v3/release', params=params,
                          timeout=self.config.search_timeout)

    def _options(self) -> dict:
        return {
            'root_folders': [select(r, ('id', 'path', 'freeSpace', 'accessible'))
                             for r in self._list('GET', 'api/v3/rootfolder')],
            'quality_profiles': [select(p, PROFILE_FIELDS)
                                 for p in self._list('GET', 'api/v3/qualityprofile')],
            'remote_path_mappings': [select(m, ('id', 'host', 'remotePath', 'localPath'))
                                     for m in self._list('GET', 'api/v3/remotepathmapping')],
        }

    def _validate_profile_and_root(self, quality_profile_id: object, root_folder: object) -> int:
        profile_id = positive_id(quality_profile_id, 'quality_profile_id')
        if not isinstance(root_folder, str) or not root_folder.strip():
            raise ValueError('root_folder must be a non-empty string')
        profile_ids = {p.get('id') for p in self._list('GET', 'api/v3/qualityprofile')}
        if profile_id not in profile_ids:
            raise ValueError(f'quality_profile_id {profile_id} does not exist on {self.config.name}')
        roots = {str(r.get('path', '')).rstrip('/') for r in self._list('GET', 'api/v3/rootfolder')}
        if root_folder.rstrip('/') not in roots:
            raise ValueError(f'root_folder {root_folder!r} is not a configured root folder on {self.config.name}')
        return profile_id

    def _health(self) -> list[dict]:
        items = []
        for item in self._list('GET', 'api/v3/health'):
            projected = select(item, ('id', 'source', 'type', 'message'))
            if 'message' in projected:
                projected['message'] = _bounded_text(projected['message'])
            items.append(projected)
        return items

    def _command(self, command_id: object) -> dict:
        command_id = positive_id(command_id, 'command_id')
        return select(self.request_json('GET', f'api/v3/command/{command_id}') or {}, COMMAND_FIELDS)

    def _history_failed(self, item_id: int, history_id: int | None) -> dict | None:
        """Blocklist one entry, preferring the exact history record it came from.

        Without a history id the *arr marks the newest failure whose download id
        matches, which can be ambiguous if the same release failed more than once.
        The returned body is untrusted upstream data and is only checked, not echoed.
        """
        payload: dict = {'id': item_id}
        if history_id is not None:
            payload['historyId'] = positive_id(history_id, 'history_id')
        result = self.request_json('POST', 'api/v3/history/failed', json=payload, mutation=True)
        return result if isinstance(result, dict) else None

    def _raw_queue_record(self, item_id: object) -> dict:
        """Fetch the queue page holding one item to confirm it exists and read its history id.

        The *arr queue is a flat list addressed by download-client id, not an
        addressable resource, so ``GET /api/v3/queue/{id}`` does not exist; the
        page carrying the record is the only way to prove the id is real.
        """
        wanted = positive_id(item_id, 'item_id')
        response = self.request_json('GET', 'api/v3/queue', params={
            'page': 1, 'pageSize': 100, 'includeUnknownMovieItems': 'true',
            'includeUnknownSeriesItems': 'true'}) or {}
        for record in response.get('records') or []:
            if record.get('id') == wanted:
                return record
        raise ValueError(f'queue item {wanted} not found in the first 100 records; '
                         'it may have already imported or been removed')

    @staticmethod
    def _release_matches(releases: list, guid: str, indexer_id: int) -> dict | None:
        for release in releases:
            if release.get('guid') == guid and release.get('indexerId') == indexer_id:
                return release
        return None

    @staticmethod
    def _project_release(release: dict) -> dict:
        projected = select(release, RELEASE_FIELDS)
        if 'rejections' in projected:
            projected['rejections'] = [_bounded_text(r) for r in projected['rejections'] or []]
        return projected

    @staticmethod
    def _require_acceptable_release(match: dict) -> None:
        if (match.get('rejections') or match.get('rejected')
                or match.get('temporarilyRejected') or not match.get('approved')):
            # Never echo upstream rejection bodies into the error string.
            raise ValueError('release is rejected or not approved; run releases() '
                             'again and choose a result with approved=true and no rejections')


class RadarrClient(ArrClientBase):
    """Radarr v3 API client (/api/v3, X-Api-Key)."""

    @staticmethod
    def _movie(movie: dict) -> dict:
        return select(movie, MOVIE_FIELDS)

    def find(self, query: str, source: str = 'catalog', offset: int = 0, limit: int = 25) -> dict:
        """Search the managed library (catalog) or TMDB (lookup); locally paged."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError('query must be a non-empty string')
        if len(query) > 512:
            raise ValueError('query must be at most 512 characters')
        pagination(offset, limit)
        if source == 'catalog':
            needle = query.casefold()
            movies = [m for m in self._list('GET', 'api/v3/movie')
                      if needle in str(m.get('title', '')).casefold()]
        elif source == 'lookup':
            movies = self._list('GET', 'api/v3/movie/lookup', params={'term': query})
        else:
            raise ValueError("source must be 'catalog' or 'lookup'")
        return page([self._movie(m) for m in movies], offset, limit)

    def get(self, movie_id: int | str) -> dict:
        movie_id = positive_id(movie_id, 'movie_id')
        return self._movie(self.request_json('GET', f'api/v3/movie/{movie_id}') or {})

    def options(self) -> dict:
        """Root folders, quality profiles and remote path mappings (safe fields only)."""
        return self._options()

    def queue(self, page_number: int = 1, limit: int = 25) -> dict:
        page_number, limit = self._paging(page_number, limit)
        response = self.request_json('GET', 'api/v3/queue', params={
            'page': page_number, 'pageSize': limit,
            'includeUnknownMovieItems': 'true'}) or {}
        items = []
        for record in response.get('records') or []:
            item = select(record, QUEUE_FIELDS + ('movieId',))
            item['statusMessages'] = [select(m, ('title', 'messages'))
                                      for m in record.get('statusMessages') or []]
            items.append(item)
        return _native_page(response, items, page_number, limit)

    def history(self, page_number: int = 1, limit: int = 25) -> dict:
        page_number, limit = self._paging(page_number, limit)
        response = self.request_json('GET', 'api/v3/history', params={
            'page': page_number, 'pageSize': limit}) or {}
        items = []
        for record in response.get('records') or []:
            item = select(record, ('id', 'movieId', 'eventType', 'date', 'sourceTitle',
                                   'downloadId'))
            data = record.get('data') or {}
            item['data'] = {key: data[key] for key in HISTORY_DATA_FIELDS if key in data}
            items.append(item)
        return _native_page(response, items, page_number, limit)

    def health(self) -> list[dict]:
        return self._health()

    def calendar(self, start: str, end: str, offset: int = 0, limit: int = 25) -> dict:
        """Scheduled releases in a window, soonest first; locally paged.

        Radarr's calendar ignores a movieId filter, so per-movie filtering stays
        with the caller; the endpoint already accepts a yyyy-mm-dd window. Only
        future-dated or recently dated entries appear, so a window that excludes
        "today" can legitimately return nothing.
        """
        first, last = window(start, end, CALENDAR_WINDOW_DAYS)
        pagination(offset, limit)
        movies = self._list('GET', 'api/v3/calendar', params={'start': first, 'end': last})
        return page([select(m, MOVIE_CALENDAR_FIELDS) for m in movies], offset, limit)

    def releases(self, movie_id: int | str, offset: int = 0, limit: int = 25) -> dict:
        """Fresh interactive release search for one movie (quota-consuming upstream)."""
        movie_id = positive_id(movie_id, 'movie_id')
        pagination(offset, limit)
        # Radarr answers 500 for an unknown movieId, so check locally first.
        if self.request_json('GET', f'api/v3/movie/{movie_id}') is None:
            raise ValueError(f'radarr movie {movie_id} not found')
        releases = self._release_search({'movieId': movie_id})
        return page([self._project_release(r) for r in releases], offset, limit)

    def command(self, command_id: int | str) -> dict:
        return self._command(command_id)

    # --- mutations: require_write() precedes every outbound request ---

    def add(self, tmdb_id: int | str, quality_profile_id: int | str, root_folder: str,
            monitored: bool = True) -> dict:
        """Add a movie by TMDB ID; returns the existing resource instead of mutating.

        Radarr fetches metadata from TMDB server-side, so no lookup round-trip is
        needed. Never auto-searches (addOptions.searchForMovie=False).
        """
        self.require_write()
        tmdb_id = positive_id(tmdb_id, 'tmdb_id')
        existing = self._list('GET', 'api/v3/movie', params={'tmdbId': tmdb_id})
        if existing:
            return {'added': False, 'movie': self._movie(existing[0])}
        profile_id = self._validate_profile_and_root(quality_profile_id, root_folder)
        payload = {'tmdbId': tmdb_id, 'qualityProfileId': profile_id,
                   'rootFolderPath': root_folder, 'monitored': bool(monitored),
                   'addOptions': {'searchForMovie': False}}
        created = self.request_json('POST', 'api/v3/movie', json=payload, mutation=True)
        return {'added': True, 'movie': self._movie(created) if created else None}

    def monitor(self, movie_id: int | str, monitored: bool) -> dict:
        """Set monitored on one movie via the editor endpoint (only monitored changes)."""
        self.require_write()
        movie_id = positive_id(movie_id, 'movie_id')
        updated = self.request_json('PUT', 'api/v3/movie/editor',
                                    json={'movieIds': [movie_id], 'monitored': bool(monitored)},
                                    mutation=True)
        movies = [self._movie(m) for m in updated] if isinstance(updated, list) else []
        return {'monitored': bool(monitored), 'movies': movies}

    def search(self, movie_id: int | str) -> dict:
        """Explicit MoviesSearch command for one movie; returns the command ID."""
        self.require_write()
        movie_id = positive_id(movie_id, 'movie_id')
        if self.request_json('GET', f'api/v3/movie/{movie_id}') is None:
            raise ValueError(f'radarr movie {movie_id} not found')
        result = self.request_json('POST', 'api/v3/command',
                                   json={'name': 'MoviesSearch', 'movieIds': [movie_id]},
                                   mutation=True) or {}
        return {'commandId': result.get('id'), 'name': 'MoviesSearch',
                'status': result.get('status')}

    def grab(self, movie_id: int | str, guid: str, indexer_id: int | str) -> dict:
        """Grab an inspected release: requery results, refuse stale or rejected."""
        self.require_write()
        movie_id = positive_id(movie_id, 'movie_id')
        indexer_id = positive_id(indexer_id, 'indexer_id')
        if not isinstance(guid, str) or not guid.strip():
            raise ValueError('guid must be a non-empty string')
        if len(guid) > 2048:
            raise ValueError('guid must be at most 2048 characters')
        # Radarr answers 500 for an unknown movieId, so check locally first.
        if self.request_json('GET', f'api/v3/movie/{movie_id}') is None:
            raise ValueError(f'radarr movie {movie_id} not found')
        releases = self._release_search({'movieId': movie_id})
        match = self._release_matches(releases, guid, indexer_id)
        if match is None:
            raise ValueError('release is stale: not in current results for this movie; '
                             'run releases() again and pick a current guid/indexerId pair')
        self._require_acceptable_release(match)
        payload = {'guid': guid, 'indexerId': indexer_id, 'movieId': movie_id}
        self.request_json('POST', 'api/v3/release', json=payload, mutation=True)
        return {'grabbed': True, 'guid': guid, 'indexerId': indexer_id}

    def dequeue(self, item_id: int | str, remove_from_client: bool = False,
                blocklist: bool = False) -> dict:
        """Clear ONE queue record; by default the downloader's files stay on disk.

        `remove_from_client` additionally tells the downloader to delete its copy,
        and `blocklist` marks the release failed so it is not grabbed again. Both
        default to False and are never inferred from the item's state, because each
        destroys data or changes future searches. An item stuck at import (for
        example a downgrade the profile refuses) only needs the default call.
        """
        self.require_write()
        record = self._raw_queue_record(item_id)
        params: dict = {}
        if remove_from_client:
            params['removeFromClient'] = 'true'
        if blocklist:
            params['blocklist'] = 'true'
        self.request_json('DELETE', f'api/v3/queue/{record["id"]}',
                          params=params or None, mutation=True)
        if blocklist:
            self._history_failed(record['id'], record.get('historyId'))
        return {'removed': True, 'item_id': record['id'],
                'remove_from_client': bool(remove_from_client),
                'blocklisted': bool(blocklist), 'history_id': record.get('historyId')}


class SonarrClient(ArrClientBase):
    """Sonarr v3 API client (/api/v3, X-Api-Key)."""

    @staticmethod
    def _series(series: dict) -> dict:
        projected = select(series, SERIES_FIELDS)
        projected['seasons'] = [select(s, ('seasonNumber', 'monitored'))
                                for s in series.get('seasons') or []]
        return projected

    @staticmethod
    def _episode(episode: dict) -> dict:
        projected = select(episode, EPISODE_FIELDS)
        if isinstance(episode.get('series'), dict):
            projected['seriesTitle'] = episode['series'].get('title')
        return projected

    def find(self, query: str, source: str = 'catalog', offset: int = 0, limit: int = 25) -> dict:
        """Search the managed library (catalog) or TVDB (lookup); locally paged."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError('query must be a non-empty string')
        if len(query) > 512:
            raise ValueError('query must be at most 512 characters')
        pagination(offset, limit)
        if source == 'catalog':
            needle = query.casefold()
            series = [s for s in self._list('GET', 'api/v3/series')
                      if needle in str(s.get('title', '')).casefold()]
        elif source == 'lookup':
            series = self._list('GET', 'api/v3/series/lookup', params={'term': query})
        else:
            raise ValueError("source must be 'catalog' or 'lookup'")
        return page([self._series(s) for s in series], offset, limit)

    def get(self, series_id: int | str) -> dict:
        series_id = positive_id(series_id, 'series_id')
        return self._series(self.request_json('GET', f'api/v3/series/{series_id}') or {})

    def options(self) -> dict:
        return self._options()

    def episodes(self, series_id: int | str, offset: int = 0, limit: int = 25) -> dict:
        series_id = positive_id(series_id, 'series_id')
        episodes = self._list('GET', 'api/v3/episode', params={'seriesId': series_id})
        return page([self._episode(e) for e in episodes], offset, limit)

    def _sonarr_episode(self, record: dict) -> dict:
        """A calendar entry: episode fields plus the owning series' title."""
        projected = select(record, SERIES_CALENDAR_FIELDS)
        series = record.get('series')
        if isinstance(series, dict):
            projected['seriesTitle'] = series.get('title')
            projected['seriesStatus'] = series.get('status')
        return projected

    def calendar(self, start: str, end: str, offset: int = 0, limit: int = 25) -> dict:
        """Scheduled episodes in a window, soonest first; locally paged.

        The endpoint ignores a seriesId filter, so filtering by series stays with
        the caller; it may return unmonitored entries, which pass through as
        monitored=false instead of being hidden.
        """
        first, last = window(start, end, CALENDAR_WINDOW_DAYS)
        pagination(offset, limit)
        episodes = self._list('GET', 'api/v3/calendar', params={
            'start': first, 'end': last, 'includeSeries': 'true'})
        return page([self._sonarr_episode(e) for e in episodes], offset, limit)

    def next_up(self, series_id: int | str, since: str | None = None, offset: int = 0,
                limit: int = 25, include_unmonitored: bool = False) -> dict:
        """Episodes of one series airing on or after `since`, soonest first; local paging.

        Sonarr requires a seriesId on `/api/v3/episode`, so this is inherently
        per-series; use the calendar for a whole-library view. `since` is an ISO
        date or timestamp and defaults to the current UTC time at call time. The
        stored episode list reaches well past any calendar window, so this keeps
        its lookahead. Episodes with no parseable air date cannot be proven
        upcoming and are dropped.
        """
        series_id = positive_id(series_id, 'series_id')
        if since is None:
            cutoff = datetime.now(timezone.utc)
        else:
            try:
                cutoff = _instant(window(since, since)[0])
            except ValueError:
                raise ValueError('since must be an ISO date or timestamp') from None
            if cutoff is None:
                raise ValueError('since must be an ISO date or timestamp')
        pagination(offset, limit)
        # includeSeries would inflate the payload many times over; next_up never
        # projects a series title, so the plain episode read stays lean.
        upcoming = []
        for episode in self._list('GET', 'api/v3/episode',
                                  params={'seriesId': series_id}):
            if not include_unmonitored and not episode.get('monitored'):
                continue
            airs = _instant(episode.get('airDateUtc'))
            if airs is not None and airs >= cutoff:
                upcoming.append((airs, episode))
        upcoming.sort(key=lambda pair: pair[0])
        return page([self._episode(e) for _, e in upcoming], offset, limit)

    def missing(self, page_number: int = 1, limit: int = 25) -> dict:
        """Monitored episodes without files, newest-airing first (native paging)."""
        page_number, limit = self._paging(page_number, limit)
        response = self.request_json('GET', 'api/v3/wanted/missing', params={
            'page': page_number, 'pageSize': limit,
            'monitored': 'true', 'includeSeries': 'true'}) or {}
        items = [self._episode(r) for r in response.get('records') or []]
        return _native_page(response, items, page_number, limit)

    def queue(self, page_number: int = 1, limit: int = 25) -> dict:
        page_number, limit = self._paging(page_number, limit)
        response = self.request_json('GET', 'api/v3/queue', params={
            'page': page_number, 'pageSize': limit,
            'includeUnknownSeriesItems': 'true', 'includeSeries': 'true'}) or {}
        items = []
        for record in response.get('records') or []:
            item = select(record, QUEUE_FIELDS + ('seriesId', 'episodeId'))
            item['statusMessages'] = [select(m, ('title', 'messages'))
                                      for m in record.get('statusMessages') or []]
            items.append(item)
        return _native_page(response, items, page_number, limit)

    def history(self, page_number: int = 1, limit: int = 25) -> dict:
        page_number, limit = self._paging(page_number, limit)
        response = self.request_json('GET', 'api/v3/history', params={
            'page': page_number, 'pageSize': limit}) or {}
        items = []
        for record in response.get('records') or []:
            item = select(record, ('id', 'seriesId', 'episodeId', 'eventType',
                                   'date', 'sourceTitle', 'downloadId'))
            data = record.get('data') or {}
            item['data'] = {key: data[key] for key in HISTORY_DATA_FIELDS if key in data}
            items.append(item)
        return _native_page(response, items, page_number, limit)

    def health(self) -> list[dict]:
        return self._health()

    def _require_series(self, series_id: int) -> None:
        """Fail with a clear error before the indexer fan-out.

        Sonarr answers 500 for an unknown seriesId instead of 404, so an unknown
        series would otherwise surface as an opaque upstream error.
        """
        if not self.request_json('GET', f'api/v3/series/{series_id}'):
            raise ValueError(f'sonarr series {series_id} not found')

    def _require_series_episode(self, series_id: int, episode_id: int) -> None:
        found = self.request_json('GET', f'api/v3/episode/{episode_id}') or {}
        if not found or found.get('seriesId') != series_id:
            raise ValueError(f'episode {episode_id} is not owned by series {series_id}')

    def releases(self, series_id: int | str, episode_id: int | str | None = None,
                 season_number: int | str | None = None,
                 offset: int = 0, limit: int = 25) -> dict:
        """Fresh release search for one episode (episodeId) or season
        (seriesId+seasonNumber). The API does not support series-only searches."""
        series_id = positive_id(series_id, 'series_id')
        if episode_id is not None and season_number is not None:
            raise ValueError('provide either episode_id or season_number, not both')
        pagination(offset, limit)
        if episode_id is not None:
            episode_id = positive_id(episode_id, 'episode_id')
            self._require_series_episode(series_id, episode_id)
            params = {'episodeId': episode_id}
        elif season_number is not None:
            season = _season_number(season_number)  # validate before any network
            self._require_series(series_id)
            params = {'seriesId': series_id, 'seasonNumber': season}
        else:
            raise ValueError('episode_id or season_number is required; '
                             'the Sonarr API does not search releases for a series alone')
        releases = self._release_search(params)
        return page([self._project_release(r) for r in releases], offset, limit)

    def command(self, command_id: int | str) -> dict:
        return self._command(command_id)

    # --- mutations: require_write() precedes every outbound request ---

    def add(self, tvdb_id: int | str, quality_profile_id: int | str, root_folder: str,
            seasons: list[int], monitored: bool = True) -> dict:
        """Add a series by TVDB ID monitoring exactly the listed season numbers
        (all other seasons unmonitored; specials are season 0).

        Sonarr requires a non-empty title on POST, so a tvdb-prefixed lookup runs
        first and its season list defines the payload's seasons. Never auto-searches
        (addOptions.searchForMissingEpisodes=False).
        """
        self.require_write()
        tvdb_id = positive_id(tvdb_id, 'tvdb_id')
        wanted_list = _int_list(seasons, 'seasons', minimum=0)
        if len(wanted_list) > MAX_SELECTIONS:
            raise ValueError(f'seasons is bounded to {MAX_SELECTIONS} per request')
        wanted = set(wanted_list)
        existing = self._list('GET', 'api/v3/series', params={'tvdbId': tvdb_id})
        if existing:
            return {'added': False, 'series': self._series(existing[0])}
        lookup = self._list('GET', 'api/v3/series/lookup', params={'term': f'tvdb:{tvdb_id}'})
        match = next((s for s in lookup if s.get('tvdbId') == tvdb_id), None)
        if match is None:
            raise ValueError(f'no Sonarr lookup result for tvdbId {tvdb_id}')
        available = {s.get('seasonNumber') for s in match.get('seasons') or []}
        unknown = sorted(wanted - available)
        if unknown:
            raise ValueError(f'seasons not present in lookup for tvdbId {tvdb_id}: {unknown}')
        profile_id = self._validate_profile_and_root(quality_profile_id, root_folder)
        payload = {'title': match.get('title'), 'tvdbId': tvdb_id,
                   'qualityProfileId': profile_id, 'rootFolderPath': root_folder,
                   'monitored': bool(monitored),
                   'seasons': [{'seasonNumber': s.get('seasonNumber'),
                                'monitored': s.get('seasonNumber') in wanted}
                               for s in match.get('seasons') or []],
                   'addOptions': {'searchForMissingEpisodes': False}}
        created = self.request_json('POST', 'api/v3/series', json=payload, mutation=True)
        return {'added': True, 'series': self._series(created) if created else None}

    def monitor(self, series_id: int | str, seasons: list[int] | None = None,
                monitored: bool = True, episode_ids: list[int] | None = None) -> dict:
        """Change monitoring without touching anything unlisted.

        seasons and episode_ids are separate explicit operations and cannot be
        combined. seasons: monitor exactly these season numbers (others
        preserved). episode_ids: bulk episode monitoring, ownership-validated;
        [] is an explicit no-op. With both None, only the series-level flag
        changes.
        """
        self.require_write()
        series_id = positive_id(series_id, 'series_id')
        if seasons is not None and episode_ids is not None:
            raise ValueError('use separate monitor calls for seasons and episode_ids')
        if seasons is not None:
            seasons = _int_list(seasons, 'seasons', minimum=0)
        if episode_ids is not None:
            episode_ids = _int_list(episode_ids, 'episode_ids')
            if len(episode_ids) > MAX_SELECTIONS:
                raise ValueError(f'episode_ids is bounded to {MAX_SELECTIONS} per request')
        result: dict = {'monitored': bool(monitored), 'seasons': [], 'episodes': 0}
        if seasons is not None:
            result['seasons'] = self._set_season_monitoring(series_id, seasons, bool(monitored))
        if episode_ids:
            self._set_episode_monitoring(series_id, episode_ids, bool(monitored))
            result['episodes'] = len(episode_ids)
        if seasons is None and episode_ids is None:
            result['series'] = self._set_series_monitored(series_id, bool(monitored))
        return result

    def _set_series_monitored(self, series_id: int, monitored: bool) -> dict:
        series = self.request_json('GET', f'api/v3/series/{series_id}') or {}
        if not series:
            raise ValueError(f'sonarr series {series_id} not found')
        series['monitored'] = monitored
        self.request_json('PUT', f'api/v3/series/{series_id}', json=series, mutation=True)
        return self._series(series)

    def _set_season_monitoring(self, series_id: int, wanted: list[int], monitored: bool) -> list[dict]:
        series = self.request_json('GET', f'api/v3/series/{series_id}') or {}
        if not series:
            raise ValueError(f'sonarr series {series_id} not found')
        available = {s.get('seasonNumber') for s in series.get('seasons') or []}
        unknown = sorted(set(wanted) - available)
        if unknown:
            raise ValueError(f'seasons not present on series {series_id}: {unknown}')
        wanted_set = set(wanted)
        changed = []
        for season in series.get('seasons') or []:
            if season.get('seasonNumber') in wanted_set:
                season['monitored'] = monitored
                changed.append(select(season, ('seasonNumber', 'monitored')))
        if changed:
            self.request_json('PUT', f'api/v3/series/{series_id}', json=series, mutation=True)
        return changed

    def _set_episode_monitoring(self, series_id: int, episode_ids: list[int], monitored: bool) -> None:
        owned = {e.get('id') for e in self._list('GET', 'api/v3/episode',
                                                 params={'episodeIds': episode_ids})
                 if e.get('seriesId') == series_id}
        foreign = [episode_id for episode_id in episode_ids if episode_id not in owned]
        if foreign:
            raise ValueError(f'episode ids not owned by series {series_id} (or unknown): {foreign[:10]}')
        self.request_json('PUT', 'api/v3/episode/monitor',
                          json={'episodeIds': episode_ids, 'monitored': monitored},
                          mutation=True)

    def search(self, series_id: int | str, episode_ids: list[int]) -> dict:
        """Explicit EpisodeSearch command; episodes must belong to the series."""
        self.require_write()
        series_id = positive_id(series_id, 'series_id')
        episode_ids = _int_list(episode_ids, 'episode_ids')
        if not episode_ids:
            raise ValueError('episode_ids must not be empty')
        if len(episode_ids) > MAX_SELECTIONS:
            raise ValueError(f'episode_ids is bounded to {MAX_SELECTIONS} per search')
        owned = {e.get('id') for e in self._list('GET', 'api/v3/episode',
                                                 params={'episodeIds': episode_ids})
                 if e.get('seriesId') == series_id}
        foreign = [episode_id for episode_id in episode_ids if episode_id not in owned]
        if foreign:
            raise ValueError(f'episode ids not owned by series {series_id} (or unknown): {foreign[:10]}')
        result = self.request_json('POST', 'api/v3/command',
                                   json={'name': 'EpisodeSearch', 'episodeIds': episode_ids},
                                   mutation=True) or {}
        return {'commandId': result.get('id'), 'name': 'EpisodeSearch',
                'status': result.get('status')}

    def grab(self, series_id: int | str, guid: str, indexer_id: int | str,
             episode_id: int | str | None = None,
             season_number: int | str | None = None) -> dict:
        """Grab an inspected release: requery results, refuse stale or rejected."""
        self.require_write()
        series_id = positive_id(series_id, 'series_id')
        indexer_id = positive_id(indexer_id, 'indexer_id')
        if not isinstance(guid, str) or not guid.strip():
            raise ValueError('guid must be a non-empty string')
        if len(guid) > 2048:
            raise ValueError('guid must be at most 2048 characters')
        if episode_id is not None and season_number is not None:
            raise ValueError('provide either episode_id or season_number, not both')
        if episode_id is not None:
            episode_id = positive_id(episode_id, 'episode_id')
            self._require_series_episode(series_id, episode_id)
            params = {'episodeId': episode_id}
        elif season_number is not None:
            season = _season_number(season_number)  # validate before any network
            self._require_series(series_id)
            params = {'seriesId': series_id, 'seasonNumber': season}
        else:
            raise ValueError('episode_id or season_number is required to requery releases')
        releases = self._release_search(params)
        match = self._release_matches(releases, guid, indexer_id)
        if match is None:
            raise ValueError('release is stale: not in current results; '
                             'run releases() again and pick a current guid/indexerId pair')
        self._require_acceptable_release(match)
        payload = {'guid': guid, 'indexerId': indexer_id, 'seriesId': series_id}
        if episode_id is not None:
            payload['episodeId'] = episode_id
        self.request_json('POST', 'api/v3/release', json=payload, mutation=True)
        return {'grabbed': True, 'guid': guid, 'indexerId': indexer_id}

    def dequeue(self, item_id: int | str, remove_from_client: bool = False,
                blocklist: bool = False) -> dict:
        """Clear ONE queue record; by default the downloader's files stay on disk.

        `remove_from_client` additionally tells the downloader to delete its copy,
        and `blocklist` marks the release failed so it is not grabbed again. Both
        default to False and are never inferred from the item's state, because each
        destroys data or changes future searches. An episode stuck at import after
        an upgrade (a downgrade the profile refuses) only needs the default call.
        """
        self.require_write()
        record = self._raw_queue_record(item_id)
        params: dict = {}
        if remove_from_client:
            params['removeFromClient'] = 'true'
        if blocklist:
            params['blocklist'] = 'true'
        self.request_json('DELETE', f'api/v3/queue/{record["id"]}',
                          params=params or None, mutation=True)
        if blocklist:
            self._history_failed(record['id'], record.get('historyId'))
        return {'removed': True, 'item_id': record['id'],
                'remove_from_client': bool(remove_from_client),
                'blocklisted': bool(blocklist), 'history_id': record.get('historyId')}
