from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ..output import print_json

BASE_URL = "https://grpc.ablibrary.net"
SERVICE = "ablibrary.services.book_service.BookService"
LANGS = {"ar": "العربية", "fa": "فارسی", "en": "English"}


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


@dataclass
class PageContent:
    page_number: int
    label: str
    text: str
    contents: list = field(default_factory=list)


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


def _parse_pagination(data: dict, page: int, per_page: int) -> dict:
    pagination = data.get("pagination", {})
    total = pagination.get("totalItems", 0) or 0
    total_pages = pagination.get("totalPages", 1) or 1
    current = pagination.get("currentPage", page) or page
    return {
        "total_results": total,
        "page": current,
        "per_page": pagination.get("perPage", per_page),
        "has_more": current < total_pages,
    }


def _book_from_json(d: dict) -> Book:
    cats = [c["name"] for c in d.get("categories", []) if c.get("name")]
    langs = [LANGS.get(lang["id"], lang.get("name", lang["id"])) for lang in d.get("languages", [])]
    contribs = []
    for c in d.get("contributors", []):
        cont = c.get("contributor", {})
        role = c.get("role", {})
        contribs.append(Contributor(
            id=cont.get("id", ""),
            name=cont.get("name", ""),
            era=cont.get("era"),
            role_slug=role.get("slug"),
            role_title=role.get("title"),
        ))
    pub = d.get("publisher") or {}
    atts = d.get("attachments", [])
    has_pdf = any(a.get("context") == "BOOK_ATTACHMENT_CONTEXT_PDF" for a in atts)
    colls = d.get("collections") or []
    vol_label = d.get("volumeLabel") or (str(d["volumeNumber"]) if d.get("volumeNumber") else None)

    return Book(
        id=str(d["id"]),
        title=d.get("title", ""),
        volume_number=d.get("volumeNumber", 0),
        volume_label=vol_label,
        pages_count=d.get("pagesCount", 0),
        languages=langs,
        categories=cats,
        contributors=contribs,
        publisher=pub.get("name"),
        source=d.get("source"),
        has_pdf=has_pdf,
        collections=[c.get("name", "") for c in colls],
    )


class Client:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=httpx.Timeout(timeout))

    def _request(self, method: str, payload: dict) -> dict:
        url = f"{self.base_url}/{SERVICE}/{method}"
        resp = self._http.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
            },
        )
        resp.raise_for_status()
        return resp.json()

    def search(
        self,
        query: str = "",
        *,
        author: str = "",
        title: str = "",
        publisher: str = "",
        languages: list[str] | None = None,
        categories: list[str] | None = None,
        sources: list[str] | None = None,
        has_pdf: bool | None = None,
        page: int = 1,
        per_page: int = 50,
        sort_by: str = "SORT_CONTRIBUTOR_DIED_AT",
        sort_dir: str = "SORT_DIRECTION_ASCENDING",
    ) -> SearchResult:
        body: dict[str, Any] = {
            "page": page,
            "perPage": per_page,
            "sortBy": sort_by,
            "sortDir": sort_dir,
        }
        if query:
            body["query"] = query
        if author:
            body["contributors"] = [{"name": author}]
        if title:
            body["title"] = title
        if publisher:
            body["publishers"] = [{"name": publisher}]
        if languages:
            body["languages"] = languages
        if categories:
            body["categories"] = categories
        if sources:
            body["sources"] = sources
        if has_pdf is not None:
            body["attachments"] = [{"context": "BOOK_ATTACHMENT_CONTEXT_PDF"}]

        data = self._request("List", body)
        books = [_book_from_json(b) for b in data.get("books", [])]
        return SearchResult(books=books, **_parse_pagination(data, page, per_page))

    def details(self, book_id: str) -> Book:
        data = self._request("Details", {"id": book_id})
        return _book_from_json(data.get("book", {}))

    def contents(self, book_id: str, page_numbers: list[int]) -> list[PageContent]:
        data = self._request("Contents", {
            "bookId": book_id,
            "pageNumbers": page_numbers,
        })
        pages = []
        abx = data.get("abx") or {}
        for p in abx.get("pages", []):
            parts = []
            for c in p.get("contents", []):
                txt = c.get("text") or {}
                if txt.get("text"):
                    parts.append(txt["text"])
            pages.append(PageContent(
                page_number=p.get("number", 0),
                label=p.get("label", ""),
                text=" ".join(parts),
                contents=p.get("contents", []),
            ))
        return pages

    def table_of_contents(self, book_id: str) -> list[TocItem]:
        data = self._request("TableOfContents", {"bookId": book_id})
        items = []
        for item in data.get("items", []):
            items.append(TocItem(
                title=item.get("title", ""),
                page_number=item.get("pageNumber", 0),
                level=item.get("level", 0),
                children=[],
            ))
        return items

    def search_in_book(self, book_id: str, query: str, page: int = 1, per_page: int = 50) -> SearchResult:
        body = {
            "page": page,
            "perPage": per_page,
            "query": query,
            "bookIds": [book_id],
            "sortBy": "SORT_RELEVANCE",
            "sortDir": "SORT_DIRECTION_ASCENDING",
        }
        data = self._request("List", body)
        books = [_book_from_json(b) for b in data.get("books", [])]
        return SearchResult(books=books, **_parse_pagination(data, page, per_page))

    def search_in_text(self, book_id: str, query: str, max_pages: int = 500) -> list[PageContent]:
        matching = []
        batch_size = 50
        for start in range(1, max_pages + 1, batch_size):
            end = min(start + batch_size - 1, max_pages)
            pages = self.contents(book_id, list(range(start, end + 1)))
            for p in pages:
                if query in p.text:
                    matching.append(p)
            if len(pages) < batch_size:
                break
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


def format_search_result(result: SearchResult, query: str = "", json_mode: bool = False):
    if json_mode:
        print_json({
            "books": [b.__dict__ for b in result.books],
            "total_results": result.total_results,
            "page": result.page,
            "has_more": result.has_more,
        })
    else:
        print(search_result_to_markdown(result, query))


def format_book(book: Book, json_mode: bool = False):
    if json_mode:
        print_json(book.__dict__)
    else:
        print(book_to_markdown(book))


def format_page(page: PageContent, json_mode: bool = False):
    if json_mode:
        print_json(page.__dict__)
    else:
        print(page_to_markdown(page))


def format_toc(items: list[TocItem], book_id: str, json_mode: bool = False):
    if json_mode:
        print_json([i.__dict__ for i in items])
    else:
        print(toc_to_markdown(items, book_id))
