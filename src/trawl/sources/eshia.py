from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from ..output import print_json

BASE_URL = "https://lib.eshia.ir"
AR_BASE_URL = "https://ar.lib.eshia.ir"


@dataclass
class Book:
    id: int
    title: str
    author: str
    author_url: str
    volumes: int


@dataclass
class BookDetail:
    id: int
    title: str
    author: str
    author_url: str
    volume: int
    total_pages: int
    total_volumes: int
    publisher: str = ""
    publisher_location: str = ""
    year: str = ""
    language: str = ""
    subject: str = ""
    cover_url: str = ""


@dataclass
class Author:
    name: str
    url: str
    book_count: int


@dataclass
class TocEntry:
    title: str
    page: int | None
    depth: int = 0


@dataclass
class SearchResult:
    book_id: int
    book_title: str
    author: str
    volume: int
    page: int
    snippet: str
    url: str


@dataclass
class PageContent:
    book_id: int
    book_title: str
    author: str
    volume: int
    page: int
    total_pages: int
    html: str
    images: list[str] = field(default_factory=list)
    text: str = ""


class Client:
    def __init__(self, arabic: bool = False, timeout: float = 30.0):
        self.arabic = arabic
        self.base = AR_BASE_URL if arabic else BASE_URL
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            headers={
                "User-Agent": "trawl/0.1",
                "Accept": "text/html,application/xhtml+xml",
            },
        )

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _get(self, path: str, params: dict | None = None) -> str:
        import time
        for attempt in range(3):
            resp = self._http.get(self._url(path), params=params)
            if resp.status_code in (502, 503, 504) and attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
                continue
            resp.raise_for_status()
            return resp.text

    def _post_autocomplete(self, query: str) -> str:
        url = f"{BASE_URL}/ajax/search/1"
        resp = self._http.post(
            url,
            data={"query": query},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        resp.raise_for_status()
        return resp.text

    def search(self, query: str, page: int = 1, group_key: str | None = None) -> tuple[list[SearchResult], int, int]:
        params: dict[str, str] = {}
        if page > 1:
            params["page"] = str(page)
        if group_key:
            params["groupKey"] = group_key
        html = self._get(f"/search/{_quote(query)}", params=params)
        return _parse_search_results(html)

    def search_in_book(self, book_id: int, query: str, page: int = 1) -> tuple[list[SearchResult], int, int]:
        params = {"page": str(page)} if page > 1 else {}
        html = self._get(f"/search/{book_id}/{_quote(query)}", params=params)
        return _parse_search_results(html)

    def book_detail(self, book_id: int) -> BookDetail | None:
        html = self._get(f"/{book_id}")
        return _parse_book_detail(html, book_id)

    def read_page(self, book_id: int, volume: int, page: int) -> PageContent | None:
        html = self._get(f"/{book_id}/{volume}/{page}")
        return _parse_page_content(html, book_id)

    def table_of_contents(self, book_id: int, volume: int = 1) -> list[TocEntry]:
        detail = self.book_detail(book_id)
        last_page = detail.total_pages if detail and detail.total_pages > 0 else 1
        html = self._get(f"/{book_id}/{volume}/{last_page}")
        return _parse_toc(html)

    def category_books(self, category_path: str, page: int = 1) -> list[Book]:
        params = {"page": str(page)} if page > 1 else {}
        html = self._get(f"/{category_path.lstrip('/')}", params=params)
        return _parse_books_from_category(html)

    def categories(self) -> list[dict[str, str]]:
        html = self._get("/")
        return _parse_category_names(html)

    def authors(self, page: int = 1) -> list[Author]:
        params = {"page": str(page)} if page > 1 else {}
        html = self._get("/authors", params=params)
        return _parse_authors(html)

    def autocomplete(self, query: str) -> list[dict]:
        html = self._post_autocomplete(query)
        return _parse_autocomplete(html)

    def resolve_group_key(self, category_name: str) -> str | None:
        html = self._get("/advanced-search")
        soup = BeautifulSoup(html, "html.parser")
        select = soup.find("select", attrs={"name": "groupKey"})
        if select:
            for opt in select.find_all("option"):
                if category_name in opt.get_text(strip=True):
                    val = opt.get("value")
                    if val:
                        return val
        return None

    def fetch_page_raw(self, book_id: int, volume: int, page: int) -> str:
        return self._get(f"/{book_id}/{volume}/{page}")

    def download_image(self, img_url: str, save_path: Path):
        headers = {"Referer": self.base}
        resp = self._http.get(img_url, headers=headers)
        resp.raise_for_status()
        save_path.write_bytes(resp.content)

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _quote(query: str) -> str:
    return quote(query.replace(" ", "_"), safe="")


def _extract_book_id(href: str) -> int | None:
    m = re.search(r"/(\d+)(?:/|$)", href)
    if m:
        return int(m.group(1))
    return None


def _parse_books_from_category(html: str) -> list[Book]:
    soup = BeautifulSoup(html, "html.parser")
    books: list[Book] = []
    table = soup.find("table", id="BooksList") or soup.find("table", id="AuthorBooksList")
    if not table:
        return books
    tbody = table.find("tbody")
    if not tbody:
        return books
    for row in tbody.find_all("tr", class_="course"):
        cols = row.find_all("td")
        if len(cols) < 3:
            continue
        name_td = cols[1]
        author_td = cols[2]
        link = name_td.find("a")
        if not link:
            continue
        href = link.get("href", "")
        book_id = _extract_book_id(href)
        title = link.get_text(strip=True)
        author_link = author_td.find("a")
        author = author_link.get_text(strip=True) if author_link else ""
        author_url = author_link.get("href", "") if author_link else ""
        volumes = 1
        if len(cols) >= 4:
            vol_text = cols[3].get_text(strip=True)
            try:
                volumes = int(vol_text)
            except ValueError:
                pass
        if book_id:
            books.append(Book(id=book_id, title=title, author=author, author_url=author_url, volumes=volumes))
    return books


def _parse_book_detail(html: str, book_id: int) -> BookDetail | None:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.find("h1", class_="book_title_heading") or soup.find("h1", class_="") or soup.find("h1")
    title = ""
    if title_el:
        raw = title_el.get_text(" ", strip=True)
        raw = re.sub(r'\s*جلد\s*:\s*\d+\s*صفحه\s*:\s*\d+', '', raw)
        title = raw.strip()

    author_el = soup.find("h2", class_="book_author_heading")
    author = ""
    author_url = ""
    if author_el:
        a = author_el.find("a")
        if a:
            author = a.get_text(strip=True)
            author_url = a.get("href", "")
    else:
        author_p = soup.find("p", id="author")
        if author_p:
            author = author_p.get_text(strip=True)

    vol = 1
    total_pages = 0
    total_volumes = 1

    vol_select = soup.find("select", class_="VolumeSelector")
    if vol_select:
        options = vol_select.find_all("option")
        total_volumes = len(options)

    last_link = soup.find("a", title="نمایش صفحه‌آخر")
    if last_link:
        href = last_link.get("href", "")
        parts = href.rstrip("/").split("/")
        try:
            total_pages = int(parts[-1])
        except (ValueError, IndexError):
            pass

    content_div = soup.find("td", class_="book-page-show")
    metadata: dict[str, str] = {}
    if content_div:
        for p in content_div.find_all("p"):
            text = p.get_text(" ", strip=True)
            for key in ("ناشر:", "محل نشر:", "سال نشر:", "زبان:", "موضوع:"):
                if key in text:
                    val = text.split(key, 1)[-1].strip()
                    metadata[key] = val

    cover_img = soup.find("img", class_="libimages")
    cover_url = ""
    if cover_img:
        src = cover_img.get("src", "")
        if src:
            if src.startswith("//"):
                src = "https:" + src
            elif not src.startswith("http"):
                src = BASE_URL + "/" + src.lstrip("/")
            cover_url = src

    return BookDetail(
        id=book_id,
        title=title,
        author=author,
        author_url=author_url,
        volume=vol,
        total_pages=total_pages,
        total_volumes=total_volumes,
        publisher=metadata.get("ناشر:", ""),
        publisher_location=metadata.get("محل نشر:", ""),
        year=metadata.get("سال نشر:", ""),
        language=metadata.get("زبان:", ""),
        subject=metadata.get("موضوع:", ""),
        cover_url=cover_url,
    )


def _parse_toc(html: str) -> list[TocEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[TocEntry] = []
    table = soup.find("table", class_="fehresttable")
    if not table:
        return entries
    for row in table.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) < 1:
            continue
        title_div = tds[0].find("div", class_="fehrest1")
        if not title_div:
            continue
        link = title_div.find("a")
        if link:
            title = link.get_text(" ", strip=True)
            try:
                page = int(link.get("href", ""))
            except (ValueError, TypeError):
                page = None
        else:
            title = title_div.get_text(" ", strip=True)
            page = None
        entries.append(TocEntry(title=title, page=page))
    return entries


def _parse_search_results(html: str) -> tuple[list[SearchResult], int, int]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []

    result_table = soup.find("table", id="search-result")
    if not result_table:
        return results, 0, 0

    for row in result_table.find_all("tr"):
        data_td = row.find("td", class_="data")
        if not data_td:
            continue
        result_div = data_td.find("div", class_="result")
        preview_div = data_td.find("div", class_="preview")
        if not result_div:
            continue
        all_links = result_div.find_all("a")
        link = None
        for al in all_links:
            h = al.get("href", "").strip()
            if h and h != "#":
                link = al
                break
        if not link:
            continue
        href = link.get("href", "")
        text = link.get_text(" ", strip=True)
        snippet = preview_div.get_text(" ", strip=True) if preview_div else ""

        parts = href.split("/")
        bkid = 0
        volume = 0
        page = 0
        for p in parts:
            try:
                numeric = int(p)
                if not bkid:
                    bkid = numeric
                elif not volume:
                    volume = numeric
                elif not page:
                    page = numeric
            except ValueError:
                continue

        clean = re.sub(r'[،,]\s*نام\s+کتاب\s*:', '', text)
        clean = re.sub(r'نام\s+کتاب\s*:', '', clean)
        clean = re.sub(r'[،,]\s*جلد\s*:', '', clean)
        clean = re.sub(r'[،,]\s*صفحه\s*:', '', clean)
        clean = clean.strip().strip('،').strip(',').strip()
        author = ""
        m = re.search(r'\(([^)]+)\)\s*$', clean)
        if m:
            author = m.group(1)
            clean = clean[:m.start()].strip()

        results.append(SearchResult(
            book_id=bkid,
            book_title=clean,
            author=author,
            volume=volume,
            page=page,
            snippet=snippet,
            url=href,
        ))

    current_page = 1
    total_results = 0
    result_count_span = soup.find("span", class_="result_count")
    if result_count_span:
        try:
            total_results = int(result_count_span.get_text(strip=True).replace(",", ""))
        except ValueError:
            pass

    current_span = soup.find("span", class_="current-page")
    if current_span:
        try:
            current_page = int(current_span.get_text(strip=True))
        except ValueError:
            pass

    return results, current_page, total_results


def _parse_page_content(html: str, book_id: int) -> PageContent | None:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.find("h1", class_="book_title_heading")
    book_title = ""
    if title_el:
        raw = title_el.get_text(" ", strip=True)
        raw = re.sub(r'\s*جلد\s*:\s*\d+\s*صفحه\s*:\s*\d+', '', raw)
        book_title = raw.strip()

    author_el = soup.find("h2", class_="book_author_heading")
    author = ""
    if author_el:
        a = author_el.find("a")
        if a:
            author = a.get_text(strip=True)

    vol = 1
    page = 0
    total_pages = 0

    vol_select = soup.find("select", class_="VolumeSelector")
    if vol_select:
        selected = vol_select.find("option", selected=True)
        if selected:
            try:
                vol = int(selected.get("value"))
            except (ValueError, TypeError):
                pass

    page_input = soup.find("input", class_="PageSelector")
    if page_input:
        try:
            page = int(page_input.get("value", 0))
        except ValueError:
            pass

    last_link = soup.find("a", title="نمایش صفحه‌آخر")
    if last_link:
        href = last_link.get("href", "")
        parts = href.rstrip("/").split("/")
        try:
            total_pages = int(parts[-1])
        except (ValueError, IndexError):
            pass

    content_div = soup.find("td", class_="book-page-show")
    inner_html = ""
    images: list[str] = []
    text = ""
    if content_div:
        inner_html = str(content_div)
        for ui_class in ('tools', 'sticky-menue', 'trans1', 'toolbox', 'quick-tools'):
            for el in content_div.find_all(class_=ui_class):
                el.decompose()
        for img in content_div.find_all("img"):
            src = img.get("src", "")
            if src:
                if src.startswith("//"):
                    src = "https:" + src
                elif not src.startswith("http"):
                    src = BASE_URL + "/" + src.lstrip("/")
                images.append(src)
        text = content_div.get_text("\n", strip=True)

    return PageContent(
        book_id=book_id,
        book_title=book_title,
        author=author,
        volume=vol,
        page=page,
        total_pages=total_pages,
        html=inner_html,
        images=images,
        text=text,
    )


def _parse_authors(html: str) -> list[Author]:
    soup = BeautifulSoup(html, "html.parser")
    authors: list[Author] = []
    table = soup.find("table", id="AuthorsList")
    if not table:
        return authors
    tbody = table.find("tbody")
    if not tbody:
        return authors
    for row in tbody.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) < 3:
            continue
        name_td = tds[1]
        count_td = tds[2]
        link = name_td.find("a")
        name = link.get_text(strip=True) if link else name_td.get_text(strip=True)
        url = link.get("href", "") if link else ""
        count = 0
        try:
            count = int(count_td.get_text(strip=True))
        except ValueError:
            pass
        authors.append(Author(name=name, url=url, book_count=count))
    return authors


def _parse_autocomplete(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    for li in soup.find_all("li", class_="ui-menu-item"):
        title = li.get("title", "")
        text = li.get_text(" ", strip=True)
        items.append({"book_id": title, "text": text})
    return items


def _parse_category_names(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    categories: list[dict[str, str]] = []
    nav = soup.find("div", id="navigationBar")
    if not nav:
        return categories
    tab_panels = nav.find_all("div", class_="tab-panel")
    for panel in tab_panels[:1]:
        for li in panel.find_all("li"):
            a = li.find("a")
            if a:
                href = a.get("href", "")
                name = a.get_text(strip=True)
                if href and name and "javascript" not in href:
                    categories.append({"name": name, "url": href})
    return categories


def _clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text("\n", strip=True)


def _render_page(content: PageContent) -> str:
    lines = []
    lines.append(f"Book: {content.book_title}")
    lines.append(f"Author: {content.author}")
    lines.append(f"Volume: {content.volume} | Page: {content.page}/{content.total_pages}")
    if content.images:
        lines.append(f"Images: {len(content.images)}")
        for img in content.images:
            lines.append(f"  {img}")
    if content.text:
        text = content.text[:2000]
        lines.append("")
        lines.append(text)
    return "\n".join(lines)


def search_results_to_text(results: list[SearchResult], current_page: int, total: int, query: str = "") -> str:
    lines = []
    if query:
        lines.append(f"# Search Results: \"{query}\"")
    else:
        lines.append("# Search Results")
    lines.append("")
    lines.append(f"Found **{total}** results")
    lines.append("")
    for r in results:
        lines.append(f"## [{r.book_id}] {r.book_title}")
        if r.author:
            lines.append(f"- Author: {r.author}")
        if r.volume or r.page:
            lines.append(f"- Volume: {r.volume}, Page: {r.page}")
        if r.snippet:
            lines.append(f"  > {r.snippet[:200]}")
        lines.append("")
    if current_page > 1:
        lines.append(f"> Page {current_page}")
    return "\n".join(lines)


def format_search_results(
    results: list[SearchResult], current_page: int, total: int,
    query: str = "", json_mode: bool = False,
):
    if json_mode:
        print_json({
            "results": [r.__dict__ for r in results],
            "total": total,
            "page": current_page,
            "query": query,
        })
    else:
        print(search_results_to_text(results, current_page, total, query))


def format_book_detail(detail: BookDetail | None, json_mode: bool = False):
    if json_mode:
        if detail:
            print_json(detail.__dict__)
        else:
            print_json({"error": "Book not found"})
    else:
        if not detail:
            print("Book not found")
            return
        lines = []
        lines.append(f"# {detail.title}")
        lines.append("")
        if detail.author:
            lines.append(f"**Author:** {detail.author}")
        lines.append(f"**Volumes:** {detail.total_volumes}")
        lines.append(f"**Pages:** {detail.total_pages}")
        if detail.publisher:
            lines.append(f"**Publisher:** {detail.publisher}")
        if detail.publisher_location:
            lines.append(f"**Location:** {detail.publisher_location}")
        if detail.year:
            lines.append(f"**Year:** {detail.year}")
        if detail.language:
            lines.append(f"**Language:** {detail.language}")
        if detail.subject:
            lines.append(f"**Subject:** {detail.subject}")
        if detail.cover_url:
            lines.append(f"**Cover:** {detail.cover_url}")
        print("\n".join(lines))


def format_page_content(content: PageContent | None, json_mode: bool = False):
    if json_mode:
        if content:
            print_json(content.__dict__)
        else:
            print_json({"error": "Page not found"})
    else:
        if not content:
            print("Page not found")
            return
        print(_render_page(content))


def format_toc(entries: list[TocEntry], json_mode: bool = False):
    if json_mode:
        print_json([e.__dict__ for e in entries])
    else:
        lines = ["# Table of Contents", ""]
        for e in entries:
            page_str = f" (p. {e.page})" if e.page else ""
            indent = "  " * e.depth
            lines.append(f"{indent}- {e.title}{page_str}")
        print("\n".join(lines))


def format_categories(categories: list[dict[str, str]], json_mode: bool = False):
    if json_mode:
        print_json(categories)
    else:
        lines = ["# Categories", ""]
        for c in categories:
            lines.append(f"- {c['name']}")
        print("\n".join(lines))


def format_authors(authors: list[Author], json_mode: bool = False):
    if json_mode:
        print_json([a.__dict__ for a in authors])
    else:
        lines = ["# Authors", ""]
        for a in authors:
            lines.append(f"- {a.name} ({a.book_count} books)")
        print("\n".join(lines))


def format_autocomplete(items: list[dict], json_mode: bool = False):
    if json_mode:
        print_json(items)
    else:
        lines = ["# Autocomplete Suggestions", ""]
        for item in items:
            lines.append(f"- {item['text']}")
        print("\n".join(lines))
