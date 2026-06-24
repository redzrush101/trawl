from __future__ import annotations

import csv
import json as j
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import click

from .sources import ablibrary as ablib_src
from .sources import eshia as eshia_src

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


def _make_ablib_client(ctx):
    kwargs = {}
    if ctx.obj.get("timeout"):
        kwargs["timeout"] = ctx.obj["timeout"]
    if ctx.obj.get("proxy"):
        kwargs["proxy"] = ctx.obj["proxy"]
    if ctx.obj.get("verbose"):
        kwargs["verbose"] = ctx.obj["verbose"]
    if ctx.obj.get("user_agent"):
        kwargs["user_agent"] = ctx.obj["user_agent"]
    if ctx.obj.get("cache"):
        kwargs["cache"] = True
        kwargs["cache_ttl"] = ctx.obj.get("cache_ttl", 3600)
    return ablib_src.Client(**{k: v for k, v in kwargs.items() if v is not None})


def _make_eshia_client(ctx):
    kwargs = {"arabic": ctx.obj.get("arabic", False)}
    if ctx.obj.get("timeout"):
        kwargs["timeout"] = ctx.obj["timeout"]
    if ctx.obj.get("proxy"):
        kwargs["proxy"] = ctx.obj["proxy"]
    if ctx.obj.get("verbose"):
        kwargs["verbose"] = ctx.obj["verbose"]
    if ctx.obj.get("user_agent"):
        kwargs["user_agent"] = ctx.obj["user_agent"]
    if ctx.obj.get("cache"):
        kwargs["cache"] = True
        kwargs["cache_ttl"] = ctx.obj.get("cache_ttl", 3600)
    return eshia_src.Client(**kwargs)


def _progress(iterable, desc=None, total=None, **kwargs):
    if HAS_TQDM:
        return tqdm(iterable, desc=desc, total=total, **kwargs)
    return iterable


def _parse_range(text: str) -> list[int]:
    nums = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            nums.extend(range(int(a), int(b) + 1))
        else:
            nums.append(int(part))
    return nums


def _search_ablib(
    ctx, query, page, per_page, exclude,
    author, title, publisher, lang, cat, pdf, json_mode, fetch_all=False,
):
    json_dict = None
    raw_result = None
    filters = {
        "author": author or None,
        "title": title or None,
        "publisher": publisher or None,
        "languages": [lang] if lang else None,
        "categories": [cat] if cat else None,
        "has_pdf": pdf if pdf else None,
    }
    filters = {k: v for k, v in filters.items() if v is not None and v is not False}
    try:
        with _make_ablib_client(ctx) as c:
            if fetch_all:
                raw_result = c.search_all(query=query, per_page=per_page, **filters)
            else:
                raw_result = c.search(
                    query=query, page=page, per_page=per_page, **filters,
                )
        if exclude:
            raw_result.books = [b for b in raw_result.books if exclude not in b.title]
        if json_mode:
            json_dict = {
                "books": [b.__dict__ for b in raw_result.books],
                "total_results": raw_result.total_results,
                "page": raw_result.page,
                "has_more": raw_result.has_more,
            }
        else:
            click.echo("## Source: ablibrary")
            ablib_src.format_search_result(raw_result, query, json_mode=False)
            click.echo("")
    except Exception as e:
        if json_mode:
            json_dict = {"error": str(e)}
        else:
            click.echo(f"## Source: ablibrary (error: {e})", err=True)
    return json_dict, raw_result


def _search_eshia(ctx, query, page, book_id, category, exclude, arabic, json_mode, fetch_all=False):
    json_dict = None
    all_results = []
    raw_cp = 0
    raw_total = 0
    try:
        group_key = None
        if category:
            with _make_eshia_client(ctx) as c2:
                group_key = c2.resolve_group_key(category)
        with _make_eshia_client(ctx) as c:
            if book_id:
                raw_results, raw_cp, raw_total = c.search_in_book(book_id, query, page=page)
            else:
                raw_results, raw_cp, raw_total = c.search(query, page=page, group_key=group_key)
            all_results = list(raw_results)

            if fetch_all and raw_total > len(raw_results):
                max_p = (raw_total // 20) + 1
                for p in _progress(range(2, max_p + 1), desc="Fetching eshia pages"):
                    try:
                        if not book_id:
                            rr, _, _ = c.search(query, page=p, group_key=group_key)
                        else:
                            rr, _, _ = c.search_in_book(book_id, query, page=p)
                        all_results.extend(rr)
                    except Exception:
                        break
        if exclude:
            all_results = [r for r in all_results if exclude not in r.book_title and exclude not in r.snippet]
        if json_mode:
            json_dict = {
                "results": [r.__dict__ for r in all_results],
                "total": raw_total,
                "page": raw_cp,
            }
        else:
            click.echo("## Source: eshia")
            eshia_src.format_search_results(all_results, raw_cp, raw_total, query, json_mode=False)
            click.echo("")
    except Exception as e:
        if json_mode:
            json_dict = {"error": str(e)}
        else:
            click.echo(f"## Source: eshia (error: {e})", err=True)
    return json_dict, all_results, raw_cp, raw_total


# ---------------------------------------------------------------------------
# Main CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.option("--arabic", is_flag=True, help="Use Arabic mirror (eshia)")
@click.option("--verbose", is_flag=True, help="Show request URLs and timing")
@click.option("--user-agent", default=None, help="Custom User-Agent header")
@click.option("--timeout", default=30.0, type=float, help="Request timeout in seconds")
@click.option("--proxy", default=None, help="Proxy URL (e.g. socks5://localhost:9050)")
@click.option("--cache/--no-cache", default=True, help="Enable/disable SQLite cache")
@click.option("--cache-ttl", default=3600, type=int, help="Cache TTL in seconds")
@click.pass_context
def cli(ctx, json_mode, arabic, verbose, user_agent, timeout, proxy, cache, cache_ttl):
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_mode
    ctx.obj["arabic"] = arabic
    ctx.obj["verbose"] = verbose
    ctx.obj["user_agent"] = user_agent
    ctx.obj["timeout"] = timeout
    ctx.obj["proxy"] = proxy
    ctx.obj["cache"] = cache
    ctx.obj["cache_ttl"] = cache_ttl


# ---------------------------------------------------------------------------
# Common commands
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("query")
@click.option("--source", default="both", type=click.Choice(["ablib", "eshia", "both"]), help="Source to search")
@click.option("--page", default=1, type=int, help="Page number")
@click.option("--per-page", default=50, type=int, help="Results per page (ablib)")
@click.option("--fetch-all", is_flag=True, help="Fetch all paginated results")
@click.option("--exclude", help="Exclude results containing this term (client-side filter)")
@click.option("--save", type=click.Path(dir_okay=False, writable=True), help="Save results to file")
@click.option("--format", "save_format", default="md",
              type=click.Choice(["md", "jsonl", "csv"]),
              help="Save format (with --save)")
# ablib filters
@click.option("--author", help="Filter by author (ablib)")
@click.option("--title", help="Filter by title (ablib)")
@click.option("--publisher", help="Filter by publisher (ablib)")
@click.option("--lang", help="Filter by language (ablib: ar, fa, en)")
@click.option("--cat", help="Filter by category (ablib)")
@click.option("--pdf", is_flag=True, help="Only books with PDF (ablib)")
# eshia filters
@click.option("--book", "book_id", type=int, help="Search within a specific book ID (eshia)")
@click.option("--category", help="Search within a category name (eshia)")
@click.pass_context
def search(
    ctx, query, source, page, per_page, fetch_all, exclude, save, save_format,
    author, title, publisher, lang, cat, pdf, book_id, category,
):
    """Search books across digital libraries."""
    json_mode = ctx.obj["json"]
    arabic = ctx.obj["arabic"]
    json_data = {"query": query, "sources": {}}

    ablib_raw = None
    eshia_raw = None
    eshia_cp = 0
    eshia_total = 0

    if source in ("both", "ablib"):
        jd, ablib_raw = _search_ablib(
            ctx, query, page, per_page, exclude, author, title, publisher, lang, cat, pdf, json_mode, fetch_all)
        if json_mode:
            json_data["sources"]["ablibrary"] = jd

    if source in ("both", "eshia"):
        jd, eshia_raw, eshia_cp, eshia_total = _search_eshia(
            ctx, query, page, book_id, category, exclude, arabic, json_mode, fetch_all)
        if json_mode:
            json_data["sources"]["eshia"] = jd

    if json_mode:
        from .output import print_json
        if save:
            out = StringIO()
            with redirect_stdout(out):
                print_json(json_data)
            Path(save).write_text(out.getvalue())
            click.echo(out.getvalue(), nl=False)
        else:
            print_json(json_data)

    if save and not json_mode:
        if save_format == "jsonl":
            rows = []
            if ablib_raw:
                for b in ablib_raw.books:
                    rows.append(j.dumps({
                        "source": "ablibrary", "id": b.id,
                        "title": b.title, "volume": b.volume_label,
                    }, ensure_ascii=False))
            if eshia_raw:
                for r in eshia_raw:
                    rows.append(j.dumps({
                        "source": "eshia", "id": r.book_id,
                        "title": r.book_title, "volume": r.volume,
                        "page": r.page, "snippet": r.snippet,
                    }, ensure_ascii=False))
            Path(save).write_text("\n".join(rows))
        elif save_format == "csv":
            buf = StringIO()
            w = csv.writer(buf, quoting=csv.QUOTE_ALL)
            w.writerow(["source", "id", "title", "volume", "page"])
            if ablib_raw:
                for b in ablib_raw.books:
                    w.writerow(["ablibrary", b.id, b.title, b.volume_label or "", "0"])
            if eshia_raw:
                for r in eshia_raw:
                    w.writerow(["eshia", r.book_id, r.book_title, r.volume, r.page])
            Path(save).write_text(buf.getvalue())
        else:
            save_parts = []
            if ablib_raw:
                save_parts.append(ablib_src.search_result_to_markdown(ablib_raw, query))
            if eshia_raw:
                save_parts.append(eshia_src.search_results_to_text(eshia_raw, eshia_cp, eshia_total, query))
            Path(save).write_text("\n".join(save_parts))


@cli.command()
@click.argument("book_id")
@click.option("--source", required=True, type=click.Choice(["ablib", "eshia"]), help="Source")
@click.pass_context
def book(ctx, book_id, source):
    """Show book details."""
    json_mode = ctx.obj["json"]

    if source == "ablib":
        try:
            with _make_ablib_client(ctx) as c:
                b = c.details(book_id)
            ablib_src.format_book(b, json_mode=json_mode)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
    else:
        try:
            with _make_eshia_client(ctx) as c:
                d = c.book_detail(int(book_id))
            eshia_src.format_book_detail(d, json_mode=json_mode)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)


@cli.command()
@click.argument("book_id")
@click.argument("pages", default="1")
@click.option("--source", required=True, type=click.Choice(["ablib", "eshia"]), help="Source")
@click.option("--volume", default=1, type=int, help="Volume number (eshia)")
@click.option("--lines", "max_lines", type=int, help="Max lines of text per page (deprecated: use --max-tokens)")
@click.option("--max-tokens", type=int, help="Max tokens (words) of text per page")
@click.option("--parallel", is_flag=True, help="Fetch pages in parallel (eshia)")
@click.option("--workers", default=10, type=int, help="Parallel workers (eshia)")
@click.option("--images/--no-images", default=True, help="Extract image URLs from pages")
@click.option("--chains/--no-chains", default=True, help="Keep hadith chain text (eshia)")
@click.pass_context
def read(ctx, book_id, pages, source, volume, max_lines, max_tokens, parallel, workers, images, chains):
    """Read book pages."""
    json_mode = ctx.obj["json"]

    truncate_tokens = max_tokens or max_lines

    if source == "ablib":
        page_nums = _parse_range(pages)
        try:
            with _make_ablib_client(ctx) as c:
                pgs = c.contents(book_id, page_nums)
            for p in _progress(pgs, desc="Reading pages"):
                if truncate_tokens and not json_mode:
                    words = p.text.split()
                    truncated = " ".join(words[:truncate_tokens])
                    if len(words) > truncate_tokens:
                        truncated += "\n..."
                    click.echo(f"## Page {p.page_number}")
                    click.echo("")
                    click.echo(truncated)
                    click.echo("")
                    click.echo("---")
                else:
                    ablib_src.format_page(p, json_mode=json_mode)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
    else:
        page_nums = _parse_range(pages)
        try:
            if parallel and len(page_nums) > 1:
                def _fetch_one(pn):
                    with _make_eshia_client(ctx) as c2:
                        return c2.read_page(int(book_id), volume, pn, extract_images=images, keep_chains=chains)
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futures = {ex.submit(_fetch_one, pn): pn for pn in page_nums}
                    for f in _progress(as_completed(futures), desc="Fetching pages", total=len(page_nums)):
                        pn = futures[f]
                        try:
                            content = f.result()
                            eshia_src.format_page_content(content, json_mode=json_mode, max_tokens=truncate_tokens)
                        except Exception as e:
                            click.echo(f"Error on page {pn}: {e}", err=True)
            else:
                with _make_eshia_client(ctx) as c:
                    for pn in _progress(page_nums, desc="Reading pages"):
                        content = c.read_page(int(book_id), volume, pn, extract_images=images, keep_chains=chains)
                        eshia_src.format_page_content(content, json_mode=json_mode, max_tokens=truncate_tokens)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)


@cli.command()
@click.argument("book_id")
@click.option("--source", required=True, type=click.Choice(["ablib", "eshia"]), help="Source")
@click.option("--volume", default=1, type=int, help="Volume number (eshia)")
@click.pass_context
def toc(ctx, book_id, source, volume):
    """Show table of contents."""
    json_mode = ctx.obj["json"]

    if source == "ablib":
        try:
            with _make_ablib_client(ctx) as c:
                items = c.table_of_contents(book_id)
            ablib_src.format_toc(items, book_id, json_mode=json_mode)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
    else:
        try:
            with _make_eshia_client(ctx) as c:
                entries = c.table_of_contents(int(book_id), volume=volume)
            eshia_src.format_toc(entries, json_mode=json_mode)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)


# ---------------------------------------------------------------------------
# ablibrary-specific commands
# ---------------------------------------------------------------------------

@cli.group()
def ablib():
    """ABLibrary-specific commands."""


@ablib.command("search-in-book")
@click.argument("book_id")
@click.argument("query")
@click.option("--page", default=1, type=int)
@click.option("--per-page", default=50, type=int)
@click.pass_context
def ablib_search_in_book(ctx, book_id, query, page, per_page):
    """Search metadata within a specific book."""
    json_mode = ctx.obj["json"]
    try:
        with _make_ablib_client(ctx) as c:
            r = c.search_in_book(book_id, query, page=page, per_page=per_page)
        ablib_src.format_search_result(r, query, json_mode=json_mode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


@ablib.command("search-text")
@click.argument("book_id")
@click.argument("query")
@click.option("--max-pages", default=500, type=int, help="Max pages to scan")
@click.option("--regex", "-r", is_flag=True, help="Treat query as a regex pattern")
@click.option("--workers", default=5, type=int, help="Parallel page batch workers")
@click.pass_context
def ablib_search_text(ctx, book_id, query, max_pages, regex, workers):
    """Search text within book pages (client-side)."""
    json_mode = ctx.obj["json"]
    try:
        with _make_ablib_client(ctx) as c:
            if regex:
                matching = c.search_in_text_regex(book_id, query, max_pages=max_pages, workers=workers)
            else:
                matching = c.search_in_text(book_id, query, max_pages=max_pages, workers=workers)
        if json_mode:
            click.echo(j.dumps([p.__dict__ for p in matching], indent=2, default=str, ensure_ascii=False))
        else:
            pattern_label = f"regex: {query}" if regex else query
            click.echo(f"# Search in book {book_id}: \"{pattern_label}\"")
            click.echo("")
            click.echo(f"Found **{len(matching)}** matching pages")
            click.echo("")
            for p in matching:
                snippet = p.text[:250]
                if regex:
                    try:
                        snippet = re.sub(query, lambda m: f"**{m.group(0)}**", snippet, count=1)
                    except re.error:
                        pass
                else:
                    snippet = snippet.replace(query, f"**{query}**")
                click.echo(f"- Page {p.page_number}: {snippet}")
                click.echo("")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


# ---------------------------------------------------------------------------
# eshia-specific commands
# ---------------------------------------------------------------------------

@cli.group()
def eshia():
    """Eshia-specific commands."""


@eshia.command()
@click.pass_context
def categories(ctx):
    """List all top-level categories."""
    json_mode = ctx.obj["json"]
    try:
        with _make_eshia_client(ctx) as c:
            cats = c.categories()
        eshia_src.format_categories(cats, json_mode=json_mode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


@eshia.command()
@click.argument("name")
@click.option("--page", default=1, type=int)
@click.pass_context
def category(ctx, name, page):
    """List books in a category."""
    json_mode = ctx.obj["json"]
    try:
        with _make_eshia_client(ctx) as c:
            subcats = c.categories()
            books = c.category_books(name, page=page)
        eshia_src.format_category_books(name, subcats, books, json_mode=json_mode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


@eshia.command()
@click.option("--page", default=1, type=int)
@click.pass_context
def authors(ctx, page):
    """List all authors."""
    json_mode = ctx.obj["json"]
    try:
        with _make_eshia_client(ctx) as c:
            auths = c.authors(page=page)
        eshia_src.format_authors(auths, json_mode=json_mode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


@eshia.command()
@click.argument("query")
@click.pass_context
def autocomplete(ctx, query):
    """Get autocomplete suggestions."""
    json_mode = ctx.obj["json"]
    try:
        with _make_eshia_client(ctx) as c:
            items = c.autocomplete(query)
        eshia_src.format_autocomplete(items, json_mode=json_mode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


def _download_page_images_worker(cfg, book_id, volume, page, out, json_mode):
    with _make_eshia_client_from_dict(cfg) as c:
        content = c.read_page(book_id, volume, page)
        if not content or not content.images:
            return {"page": page, "images": []} if json_mode else None

        if json_mode:
            page_result = {"page": page, "images": []}
        else:
            click.echo(f"  Page {page}: {len(content.images)} image(s)")

        for i, img_url in enumerate(content.images):
            ext = Path(img_url).suffix or ".jpg"
            filename = f"{book_id}_{volume}_{page}_{i}{ext}"
            filepath = out / filename
            if filepath.exists():
                if json_mode:
                    page_result["images"].append({"url": img_url, "file": str(filepath), "status": "exists"})
                else:
                    click.echo(f"    {filename} exists, skipping")
                continue
            try:
                c.download_image(img_url, filepath)
                if json_mode:
                    page_result["images"].append({"url": img_url, "file": str(filepath), "status": "downloaded"})
                else:
                    click.echo(f"    Saved {filename}")
            except Exception as e:
                if json_mode:
                    page_result["images"].append({"url": img_url, "status": f"error: {e}"})
                else:
                    click.echo(f"    Error downloading {filename}: {e}", err=True)
        return page_result if json_mode else None


def _make_eshia_client_from_dict(cfg):
    return eshia_src.Client(**cfg)


@eshia.command()
@click.argument("book_id", type=int)
@click.option("--volume", default=1, type=int, help="Volume number")
@click.option("--pages", default=None, help="Page range (e.g. 1-10, 1,3,5; default: all pages)")
@click.option("--output", default=".", type=click.Path(file_okay=False, dir_okay=True), help="Output directory")
@click.option("--parallel", is_flag=True, help="Download pages in parallel")
@click.option("--workers", default=5, type=int, help="Parallel workers")
@click.pass_context
def download(ctx, book_id, volume, pages, output, parallel, workers):
    """Download book page images."""
    json_mode = ctx.obj["json"]

    if pages is None:
        with _make_eshia_client(ctx) as c2:
            detail = c2.book_detail(book_id)
        pages = f"1-{detail.total_pages}" if detail and detail.total_pages else "1-10"
    page_nums = _parse_range(pages)
    out = Path(output) / str(book_id)
    out.mkdir(parents=True, exist_ok=True)

    cfg = {
        "arabic": ctx.obj.get("arabic", False),
        "timeout": ctx.obj.get("timeout", 30.0),
        "verbose": ctx.obj.get("verbose", False),
        "user_agent": ctx.obj.get("user_agent"),
        "proxy": ctx.obj.get("proxy"),
    }
    results = []
    try:
        if parallel and len(page_nums) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {
                    ex.submit(_download_page_images_worker, cfg, book_id, volume, p, out, json_mode): p
                    for p in page_nums
                }
                for f in _progress(as_completed(futures), desc="Downloading", total=len(page_nums)):
                    try:
                        r = f.result()
                        if json_mode and r is not None:
                            results.append(r)
                    except Exception as e:
                        click.echo(f"Error: {e}", err=True)
        else:
            with _make_eshia_client(ctx) as c:
                for p in _progress(page_nums, desc="Downloading"):
                    content = c.read_page(book_id, volume, p)
                    if not content or not content.images:
                        if json_mode:
                            results.append({"page": p, "images": []})
                        else:
                            click.echo(f"  Page {p}: no images")
                        continue
                    if json_mode:
                        page_result = {"page": p, "images": []}
                    else:
                        click.echo(f"  Page {p}: {len(content.images)} image(s)")
                    for i, img_url in enumerate(content.images):
                        ext = Path(img_url).suffix or ".jpg"
                        filename = f"{book_id}_{volume}_{p}_{i}{ext}"
                        filepath = out / filename
                        if filepath.exists():
                            if json_mode:
                                page_result["images"].append({
                                    "url": img_url, "file": str(filepath), "status": "exists",
                                })
                            else:
                                click.echo(f"    {filename} exists, skipping")
                            continue
                        try:
                            c.download_image(img_url, filepath)
                            if json_mode:
                                page_result["images"].append({
                                    "url": img_url, "file": str(filepath), "status": "downloaded",
                                })
                            else:
                                click.echo(f"    Saved {filename}")
                        except Exception as e:
                            if json_mode:
                                page_result["images"].append({"url": img_url, "status": f"error: {e}"})
                            else:
                                click.echo(f"    Error downloading {filename}: {e}", err=True)
                    if json_mode:
                        results.append(page_result)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)

    if json_mode:
        click.echo(j.dumps(results, indent=2, default=str, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Narrator lookup
# ---------------------------------------------------------------------------

def _narrator_ablib(ctx, name, json_mode):
    result = None
    try:
        with _make_ablib_client(ctx) as c:
            r = c.search(query=name, per_page=10)
        if json_mode:
            result = {"books": [b.__dict__ for b in r.books], "total": r.total_results}
        else:
            for b in r.books[:5]:
                click.echo(f"- [{b.id}] {b.title}" + (f" ({b.pages_count}p)" if b.pages_count else ""))
            if r.total_results > 5:
                click.echo(f"  ... and {r.total_results - 5} more")
            if not r.books:
                click.echo("  (none found)")
    except Exception as e:
        if json_mode:
            result = {"error": str(e)}
        else:
            click.echo(f"  (error: {e})")
    return result


def _narrator_eshia(ctx, name, json_mode, find_hadiths=False):
    result = None
    try:
        with _make_eshia_client(ctx) as c:
            results, cp, total = c.search(name, page=1)
        if json_mode:
            result = {"results": [r.__dict__ for r in results[:10]], "total": total}
        else:
            for r in results[:5]:
                click.echo(f"- [{r.book_id}] {r.book_title} — vol.{r.volume} p.{r.page}")
                if r.snippet:
                    click.echo(f"  {r.snippet[:120]}")
            if total > 5:
                click.echo(f"  ... and {total - 5} more results")
            if not results:
                click.echo("  (none found)")
            if find_hadiths:
                click.echo("")
                click.echo(f"## Searching for hadiths narrated by {name}...")
                for r in results[:3]:
                    click.echo(f"  Checking book {r.book_id}...")
                    try:
                        with _make_eshia_client(ctx) as c2:
                            hr, _, _ = c2.search_in_book(r.book_id, name, page=1)
                        for h in hr[:3]:
                            click.echo(f"    - [{h.book_id}] vol.{h.volume} p.{h.page}: {h.snippet[:100]}")
                    except Exception:
                        pass
    except Exception as e:
        if json_mode:
            result = {"error": str(e)}
        else:
            click.echo(f"  (error: {e})")
    return result


@cli.group()
def cache():
    """Manage the SQLite cache."""


@cache.command()
def clear():
    """Clear all cached data."""
    try:
        from .cache import Cache
        c = Cache()
        c.clear()
        click.echo("Cache cleared.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


@cache.command()
@click.option("--stats", is_flag=True, help="Show cache statistics")
def info(stats):
    """Show cache info."""
    try:
        import sqlite3

        from .cache import CACHE_DB
        if not CACHE_DB.exists():
            click.echo("No cache database found.")
            return
        conn = sqlite3.connect(str(CACHE_DB))
        count = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        size = CACHE_DB.stat().st_size
        click.echo(f"Cache entries: {count}")
        click.echo(f"Cache size: {size / 1024:.1f} KB")
        click.echo(f"Database: {CACHE_DB}")
        if count and stats:
            click.echo("")
            for row in conn.execute("SELECT key, created_at, ttl FROM cache LIMIT 20"):
                import time
                created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row[1]))
                expires = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row[1] + row[2]))
                click.echo(f"  {row[0][:16]}... created={created} expires={expires}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


@cli.command()
@click.argument("name")
@click.option("--find-hadiths", is_flag=True, help="Search for hadiths narrated by this person")
@click.pass_context
def narrator(ctx, name, find_hadiths):
    """Look up a hadith narrator across both sources.

    Searches for books authored by them (ablib) and mentions in
    hadith/rijal literature (eshia).
    """
    json_mode = ctx.obj["json"]

    if not json_mode:
        click.echo(f"# Narrator: {name}")
        click.echo("")
        click.echo("## Books authored (ablib)")
        click.echo("")

    ablib_data = _narrator_ablib(ctx, name, json_mode)

    if not json_mode:
        click.echo("")
        click.echo("## Mentions in hadith/rijal literature (eshia)")
        click.echo("")

    eshia_data = _narrator_eshia(ctx, name, json_mode, find_hadiths=find_hadiths)

    if json_mode:
        from .output import print_json
        print_json({"name": name, "sources": {"ablibrary": ablib_data, "eshia": eshia_data}})
