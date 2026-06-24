from __future__ import annotations

import json as j
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://ablibrary.net"

# Cache instance
_cache = None


@dataclass
class Contributor:
    id: str
    name: str
    era: str | None = None
    role_slug: str | None = None
    role_title: str | None = None


@dataclass
class Book:
    id: str
    title: str
    volume_number: int = 0
    volume_label: str | None = None
    pages_count: int = 0
    languages: list = field(default_factory=list)
    categories: list = field(default_factory=list)
    contributors: list[Contributor] = field(default_factory=list)
    publisher: str | None = None
    source: str | None = None
    has_pdf: bool = False
    collections: list = field(default_factory=list)
    author: str = ""


@dataclass
class PageContent:
    page_number: int
    label: str
    text: str
    contents: list = field(default_factory=list)
    footnote: str = ""


@dataclass
class SearchResult:
    books: list[Book]
    total_results: int
    page: int
    per_page: int
    has_more: bool


@dataclass
class TocItem:
    title: str
    page_number: int
    level: int
    children: list = field(default_factory=list)


def _book_from_json(d: dict) -> Book:
    name = d.get("name") or d.get("title", "")
    author_str = (d.get("author") or "").strip()
    vol_str = (d.get("volume") or "").strip()
    page_count = d.get("page_count", 0)
    lang_str = (d.get("language") or "").strip()
    publisher_str = (d.get("publisher") or "").strip()
    tags_str = (d.get("tags") or "").strip()
    pdf_size = d.get("pdf_size", 0) or 0

    cats = [tags_str] if tags_str else []
    langs = [lang_str] if lang_str else []
    contribs = []
    if author_str:
        contribs.append(Contributor(
            id="",
            name=author_str,
            role_slug="author",
            role_title="Author",
        ))

    vol_num = 0
    vol_label = None
    if vol_str:
        vol_label = vol_str
        try:
            vol_num = int(vol_str)
        except ValueError:
            pass

    return Book(
        id=str(d["id"]),
        title=name,
        volume_number=vol_num,
        volume_label=vol_label,
        pages_count=page_count,
        languages=langs,
        categories=cats,
        contributors=contribs,
        publisher=publisher_str,
        source=publisher_str,
        has_pdf=pdf_size > 0,
        collections=[],
        author=author_str,
    )


def _page_from_json(d: dict) -> PageContent:
    return PageContent(
        page_number=d.get("page_number", 0),
        label=d.get("page_name", ""),
        text=d.get("content", "").replace("\r\n", "\n").strip(),
        contents=[d],
        footnote=d.get("footnote", "").replace("\r\n", "\n").strip(),
    )


class Client:
    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        verbose: bool = False,
        user_agent: str | None = None,
        proxy: str | None = None,
        cache: bool = True,
        cache_ttl: int = 3600,
    ):
        self.base_url = base_url.rstrip("/")
        self.verbose = verbose
        global _cache
        if cache and _cache is None:
            from ..cache import Cache
            _cache = Cache(enabled=True, ttl=cache_ttl)
        elif not cache:
            _cache = None
        self._cache = _cache

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if user_agent:
            headers["User-Agent"] = user_agent
        client_kwargs = {
            "timeout": httpx.Timeout(timeout),
            "headers": headers,
            "follow_redirects": True,
        }
        if proxy:
            client_kwargs["proxies"] = proxy
        self._http = httpx.Client(**client_kwargs)

    def _log(self, msg: str):
        if self.verbose:
            print(f"[ablib] {msg}")

    def _request(self, http_method: str, path: str, json_body: Any = None, params: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"

        if self._cache and http_method == "GET":
            cached = self._cache.get(url, params)
            if cached is not None:
                self._log(f"CACHE HIT {path}")
                return j.loads(cached.decode("utf-8"))

        self._log(f"{http_method} {path}")
        t0 = time.time()
        last_exc = None
        for attempt in range(3):
            try:
                if http_method == "GET":
                    resp = self._http.get(url, params=params)
                else:
                    resp = self._http.post(url, json=json_body or {})
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    delay = 0.5 * (2 ** attempt)
                    self._log(f"Retry {attempt + 1} after {delay}s (status {resp.status_code})")
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                elapsed = time.time() - t0
                self._log(f"  -> {resp.status_code} ({elapsed:.2f}s, {len(resp.content)} bytes)")
                data = resp.json()

                if self._cache and http_method == "GET":
                    self._cache.set(url, j.dumps(data, ensure_ascii=False).encode("utf-8"), params)

                return data
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    delay = 0.5 * (2 ** attempt)
                    self._log(f"Retry {attempt + 1} after {delay}s (status {e.response.status_code})")
                    time.sleep(delay)
                    continue
                raise
            except Exception as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise
        raise last_exc if last_exc else RuntimeError(f"Request failed: {path}")

    def _create_search_task(self, query: str, book_ids: list[int] | None = None) -> str:
        body = {
            "queries": [{
                "phrase": query,
                "criteria": {
                    "include_equivalents": True,
                    "include_similars": True,
                    "ignore_numbers_and_symbols": True,
                },
            }],
            "search_in_contents": True,
            "search_in_descriptions": True,
            "search_in_footnotes": False,
            "book_ids": book_ids or [],
        }
        resp = self._request("POST", "/search/", json_body=body)
        progress_url = resp.get("progress", {}).get("url", "")
        task_id = progress_url.rstrip("/").split("/")[-1]
        self._log(f"Search task: {task_id}")
        return task_id

    def _poll_task(self, task_id: str, timeout: float = 60.0) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            resp = self._request("GET", f"/tasks/search/{task_id}")
            if resp.get("finished"):
                self._log(f"Task finished: {resp.get('status')}, hits={resp.get('hits_count', 0)}")
                return resp
            time.sleep(0.5)
        raise TimeoutError(f"Search task {task_id} did not finish within {timeout}s")

    def _get_raw_search_results(self, task_id: str, offset: int = 0, limit: int = 50) -> dict:
        return self._request("GET", f"/search/{task_id}", params={"offset": offset, "limit": limit})

    def _ensure_search_task(self, query: str, book_ids: list[int] | None = None):
        task_id = self._create_search_task(query, book_ids=book_ids)
        self._poll_task(task_id)
        raw = self._get_raw_search_results(task_id)
        all_entries = raw.get("books", [])
        total_matching = raw.get("total_books_count", 0)
        return (task_id, all_entries, total_matching)

    def search(
        self,
        query: str = "",
        *,
        author: str = "",
        title: str = "",
        publisher: str = "",
        languages: list[str] | None = None,
        categories: list[str] | None = None,
        has_pdf: bool | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> SearchResult:
        _, all_entries, total_books = self._ensure_search_task(query)

        offset = (page - 1) * per_page
        page_entries = all_entries[offset:offset + per_page]

        books: list[Book] = []
        for entry in page_entries:
            try:
                book = self.details(str(entry["book_id"]))
                if author and author not in book.author:
                    continue
                if title and title not in book.title:
                    continue
                if publisher and publisher not in (book.publisher or ""):
                    continue
                if languages:
                    if not any(lang in (book.languages or []) for lang in languages):
                        continue
                if categories:
                    if not any(c in (book.categories or []) for c in categories):
                        continue
                if has_pdf is True and not book.has_pdf:
                    continue
                books.append(book)
            except Exception:
                continue

        return SearchResult(
            books=books,
            total_results=total_books,
            page=page,
            per_page=per_page,
            has_more=(offset + per_page) < total_books,
        )

    def details(self, book_id: str) -> Book:
        data = self._request("GET", f"/books/{book_id}")
        if isinstance(data, dict) and "data" in data:
            raw = data["data"][0] if data["data"] else {}
        elif isinstance(data, list) and data:
            raw = data[0]
        else:
            raw = {}
        return _book_from_json(raw)

    def contents(self, book_id: str, page_numbers: list[int] | None = None) -> list[PageContent]:
        if page_numbers and len(page_numbers) <= 10:
            pages = []
            for pn in page_numbers:
                try:
                    data = self._request(
                        "GET", f"/books/{book_id}/content/{pn}",
                        params={"fields": "content,footnote,page_name,page_number"},
                    )
                    if data:
                        pages.append(_page_from_json(data[0]))
                except Exception:
                    continue
            return sorted(pages, key=lambda x: x.page_number)

        data = self._request(
            "GET", f"/books/{book_id}/content",
            params={"fields": "content,footnote,page_name,page_number"},
        )
        pages = [_page_from_json(p) for p in (data or [])]
        if page_numbers:
            pages = [p for p in pages if p.page_number in page_numbers]
        return sorted(pages, key=lambda x: x.page_number)

    def table_of_contents(self, book_id: str) -> list[TocItem]:
        data = self._request("GET", f"/books/{book_id}/toc")
        items = []
        for item in data or []:
            items.append(TocItem(
                title=(item.get("title") or "").replace("\r\n", "\n").strip(),
                page_number=item.get("page_number", 0),
                level=0,
                children=[],
            ))
        return items

    def search_in_book(self, book_id: str, query: str, page: int = 1, per_page: int = 50) -> SearchResult:
        try:
            bid = int(book_id)
        except ValueError:
            raise ValueError(f"Invalid book_id: {book_id}")
        _, all_entries, total_books = self._ensure_search_task(query, book_ids=[bid])

        offset = (page - 1) * per_page
        page_entries = all_entries[offset:offset + per_page]

        books: list[Book] = []
        for entry in page_entries:
            try:
                book = self.details(str(entry["book_id"]))
                books.append(book)
            except Exception:
                continue

        return SearchResult(
            books=books,
            total_results=total_books,
            page=page,
            per_page=per_page,
            has_more=(offset + per_page) < total_books,
        )

    def search_in_text(
        self, book_id: str, query: str,
        max_pages: int = 500, regex: bool = False,
    ) -> list[PageContent]:
        if regex:
            try:
                compiled = re.compile(query)
            except re.error as e:
                raise ValueError(f"Invalid regex: {e}")
            def predicate(p):
                return bool(compiled.search(p.text))
        else:
            def predicate(p):
                return query in p.text

        all_pages = self.contents(book_id)
        matching = [p for p in all_pages if predicate(p)]
        if max_pages and len(matching) > max_pages:
            matching = matching[:max_pages]
        return matching

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def search_result_to_markdown(result: SearchResult, query: str = "") -> str:
    lines = []
    if query:
        lines.append(f"# Search Results: \"{query}\"")
    else:
        lines.append("# Search Results")
    lines.append("")
    lines.append(f"Found **{result.total_results}** matches")
    lines.append("")
    for book in result.books:
        lines.append(f"## [{book.id}] {book.title}")
        if book.volume_label:
            lines.append(f"- Volume: {book.volume_label}")
        if book.contributors:
            authors = [c.name for c in book.contributors if c.role_slug == "author"]
            if authors:
                lines.append(f"- By: {', '.join(authors)}")
        if book.categories:
            lines.append(f"- Categories: {' / '.join(book.categories)}")
        if book.languages:
            lines.append(f"- Language: {' / '.join(book.languages)}")
        if book.pages_count:
            lines.append(f"- Pages: {book.pages_count}")
        if book.has_pdf:
            lines.append("- Has PDF")
        lines.append("")
    if result.has_more:
        lines.append(f"> More results available (page {result.page})")
    return "\n".join(lines)


def book_to_markdown(book: Book) -> str:
    lines = []
    lines.append(f"# {book.title}")
    lines.append("")
    if book.volume_label:
        lines.append(f"**Volume:** {book.volume_label}")
    if book.contributors:
        authors = [c.name for c in book.contributors if c.role_slug == "author"]
        if authors:
            lines.append(f"**Authors:** {', '.join(authors)}")
    if book.categories:
        lines.append(f"**Categories:** {' / '.join(book.categories)}")
    if book.publisher:
        lines.append(f"**Publisher:** {book.publisher}")
    if book.languages:
        lines.append(f"**Languages:** {' / '.join(book.languages)}")
    if book.pages_count:
        lines.append(f"**Pages:** {book.pages_count}")
    if book.source:
        lines.append(f"**Source:** {book.source}")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def page_to_markdown(page: PageContent) -> str:
    lines = []
    lines.append(f"## Page {page.page_number}")
    if page.label and page.label != str(page.page_number):
        lines.append(f"*{page.label}*")
    lines.append("")
    if page.text.strip():
        lines.append(page.text.strip())
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def toc_to_markdown(items: list[TocItem], book_id: str) -> str:
    lines = []
    lines.append(f"# Table of Contents (Book {book_id})")
    lines.append("")
    for item in items:
        indent = "  " * item.level
        lines.append(f"{indent}- **Page {item.page_number}:** {item.title}")
    return "\n".join(lines)


def _format_output(obj, to_markdown, json_mode: bool = False):
    if json_mode:
        print(j.dumps(obj, indent=2, default=str, ensure_ascii=False))
    else:
        print(to_markdown())


def format_search_result(result: SearchResult, query: str = "", json_mode: bool = False):
    _format_output(
        {"books": [b.__dict__ for b in result.books],
         "total_results": result.total_results,
         "page": result.page,
         "has_more": result.has_more},
        lambda: search_result_to_markdown(result, query),
        json_mode,
    )


def format_book(book: Book, json_mode: bool = False):
    _format_output(book.__dict__, lambda: book_to_markdown(book), json_mode)


def format_page(page: PageContent, json_mode: bool = False):
    _format_output(page.__dict__, lambda: page_to_markdown(page), json_mode)


def format_toc(items: list[TocItem], book_id: str, json_mode: bool = False):
    _format_output([i.__dict__ for i in items], lambda: toc_to_markdown(items, book_id), json_mode)
