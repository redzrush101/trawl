from __future__ import annotations

import json as j
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import click

from .sources import ablibrary as ablib_src
from .sources import eshia as eshia_src


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


def _search_ablib(query, page, per_page, exclude, author, title, publisher, lang, cat, pdf, json_mode):
    json_dict = None
    raw_result = None
    try:
        with ablib_src.Client() as c:
            raw_result = c.search(
                query=query, author=author or "", title=title or "",
                publisher=publisher or "", languages=[lang] if lang else None,
                categories=[cat] if cat else None, has_pdf=pdf if pdf else None,
                page=page, per_page=per_page,
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


def _search_eshia(query, page, book_id, category, exclude, arabic, json_mode):
    json_dict = None
    raw_results = None
    raw_cp = 0
    raw_total = 0
    try:
        group_key = None
        if category:
            with eshia_src.Client(arabic=arabic) as c2:
                group_key = c2.resolve_group_key(category)
        with eshia_src.Client(arabic=arabic) as c:
            if book_id:
                raw_results, raw_cp, raw_total = c.search_in_book(book_id, query, page=page)
            else:
                raw_results, raw_cp, raw_total = c.search(query, page=page, group_key=group_key)
        if exclude:
            raw_results = [r for r in raw_results if exclude not in r.book_title and exclude not in r.snippet]
        if json_mode:
            json_dict = {
                "results": [r.__dict__ for r in raw_results],
                "total": raw_total,
                "page": raw_cp,
            }
        else:
            click.echo("## Source: eshia")
            eshia_src.format_search_results(raw_results, raw_cp, raw_total, query, json_mode=False)
            click.echo("")
    except Exception as e:
        if json_mode:
            json_dict = {"error": str(e)}
        else:
            click.echo(f"## Source: eshia (error: {e})", err=True)
    return json_dict, raw_results, raw_cp, raw_total


# ---------------------------------------------------------------------------
# Main CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option("--json", "json_mode", is_flag=True, help="Output JSON")
@click.option("--arabic", is_flag=True, help="Use Arabic mirror (eshia)")
@click.pass_context
def cli(ctx, json_mode, arabic):
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_mode
    ctx.obj["arabic"] = arabic


# ---------------------------------------------------------------------------
# Common commands
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("query")
@click.option("--source", default="both", type=click.Choice(["ablib", "eshia", "both"]), help="Source to search")
@click.option("--page", default=1, type=int, help="Page number")
@click.option("--per-page", default=50, type=int, help="Results per page (ablib)")
@click.option("--exclude", help="Exclude results containing this term (client-side filter)")
@click.option("--save", type=click.Path(dir_okay=False, writable=True), help="Save results to file")
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
    ctx, query, source, page, per_page, exclude, save,
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
            query, page, per_page, exclude, author, title, publisher, lang, cat, pdf, json_mode)
        if json_mode:
            json_data["sources"]["ablibrary"] = jd

    if source in ("both", "eshia"):
        jd, eshia_raw, eshia_cp, eshia_total = _search_eshia(query, page, book_id, category, exclude, arabic, json_mode)
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
            with ablib_src.Client() as c:
                b = c.details(book_id)
            ablib_src.format_book(b, json_mode=json_mode)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
    else:
        arabic = ctx.obj["arabic"]
        try:
            with eshia_src.Client(arabic=arabic) as c:
                d = c.book_detail(int(book_id))
            eshia_src.format_book_detail(d, json_mode=json_mode)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)


@cli.command()
@click.argument("book_id")
@click.argument("pages", default="1")
@click.option("--source", required=True, type=click.Choice(["ablib", "eshia"]), help="Source")
@click.option("--volume", default=1, type=int, help="Volume number (eshia)")
@click.option("--lines", "max_lines", type=int, help="Max lines of text per page")
@click.pass_context
def read(ctx, book_id, pages, source, volume, max_lines):
    """Read book pages."""
    json_mode = ctx.obj["json"]

    if source == "ablib":
        page_nums = _parse_range(pages)
        try:
            with ablib_src.Client() as c:
                pgs = c.contents(book_id, page_nums)
            for p in pgs:
                if max_lines and not json_mode:
                    lines = ablib_src.page_to_markdown(p).split('\n')
                    print('\n'.join(lines[:max_lines]))
                    if len(lines) > max_lines:
                        print(f"... ({len(lines) - max_lines} more lines)")
                else:
                    ablib_src.format_page(p, json_mode=json_mode)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
    else:
        arabic = ctx.obj["arabic"]
        page_nums = _parse_range(pages)
        try:
            with eshia_src.Client(arabic=arabic) as c:
                for pn in page_nums:
                    content = c.read_page(int(book_id), volume, pn)
                    if max_lines and not json_mode:
                        rendered = eshia_src._render_page(content) if content else "Page not found"
                        lines = rendered.split('\n')
                        print('\n'.join(lines[:max_lines]))
                        if len(lines) > max_lines:
                            print(f"... ({len(lines) - max_lines} more lines)")
                    else:
                        eshia_src.format_page_content(content, json_mode=json_mode)
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
            with ablib_src.Client() as c:
                items = c.table_of_contents(book_id)
            ablib_src.format_toc(items, book_id, json_mode=json_mode)
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
    else:
        arabic = ctx.obj["arabic"]
        try:
            with eshia_src.Client(arabic=arabic) as c:
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
        with ablib_src.Client() as c:
            r = c.search_in_book(book_id, query, page=page, per_page=per_page)
        ablib_src.format_search_result(r, query, json_mode=json_mode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


@ablib.command("search-text")
@click.argument("book_id")
@click.argument("query")
@click.option("--max-pages", default=500, type=int, help="Max pages to scan")
@click.pass_context
def ablib_search_text(ctx, book_id, query, max_pages):
    """Search text within book pages (client-side)."""
    json_mode = ctx.obj["json"]
    try:
        with ablib_src.Client() as c:
            matching = c.search_in_text(book_id, query, max_pages=max_pages)
        if json_mode:
            click.echo(j.dumps([p.__dict__ for p in matching], indent=2, default=str, ensure_ascii=False))
        else:
            click.echo(f"# Search in book {book_id}: \"{query}\"")
            click.echo("")
            click.echo(f"Found **{len(matching)}** matching pages")
            click.echo("")
            for p in matching:
                snippet = p.text[:200].replace(query, f"**{query}**")
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
    arabic = ctx.obj["arabic"]
    try:
        with eshia_src.Client(arabic=arabic) as c:
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
    arabic = ctx.obj["arabic"]
    try:
        with eshia_src.Client(arabic=arabic) as c:
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
    arabic = ctx.obj["arabic"]
    try:
        with eshia_src.Client(arabic=arabic) as c:
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
    arabic = ctx.obj["arabic"]
    try:
        with eshia_src.Client(arabic=arabic) as c:
            items = c.autocomplete(query)
        eshia_src.format_autocomplete(items, json_mode=json_mode)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


def _download_page_images(c, book_id, volume, page, out, json_mode):
    content = c.read_page(book_id, volume, page)
    if not content or not content.images:
        if json_mode:
            return {"page": page, "images": []}
        click.echo(f"  Page {page}: no images")
        return None

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


@eshia.command()
@click.argument("book_id", type=int)
@click.option("--volume", default=1, type=int, help="Volume number")
@click.option("--pages", default=None, help="Page range (e.g. 1-10, 1,3,5; default: all pages)")
@click.option("--output", default=".", type=click.Path(file_okay=False, dir_okay=True), help="Output directory")
@click.pass_context
def download(ctx, book_id, volume, pages, output):
    """Download book page images."""
    json_mode = ctx.obj["json"]
    arabic = ctx.obj["arabic"]

    if pages is None:
        with eshia_src.Client(arabic=arabic) as c2:
            detail = c2.book_detail(book_id)
        pages = f"1-{detail.total_pages}" if detail and detail.total_pages else "1-10"
    page_nums = _parse_range(pages)
    out = Path(output) / str(book_id)
    out.mkdir(parents=True, exist_ok=True)

    results = []
    try:
        with eshia_src.Client(arabic=arabic) as c:
            for p in page_nums:
                r = _download_page_images(c, book_id, volume, p, out, json_mode)
                if json_mode and r is not None:
                    results.append(r)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)

    if json_mode:
        click.echo(j.dumps(results, indent=2, default=str, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Narrator lookup
# ---------------------------------------------------------------------------

def _narrator_ablib(name, json_mode):
    result = None
    try:
        with ablib_src.Client() as c:
            r = c.search(author=name, per_page=10)
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


def _narrator_eshia(name, arabic, json_mode):
    result = None
    try:
        with eshia_src.Client(arabic=arabic) as c:
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
    except Exception as e:
        if json_mode:
            result = {"error": str(e)}
        else:
            click.echo(f"  (error: {e})")
    return result


@cli.command()
@click.argument("name")
@click.pass_context
def narrator(ctx, name):
    """Look up a hadith narrator across both sources.

    Searches for books authored by them (ablib) and mentions in
    hadith/rijal literature (eshia).
    """
    json_mode = ctx.obj["json"]
    arabic = ctx.obj["arabic"]

    if not json_mode:
        click.echo(f"# Narrator: {name}")
        click.echo("")
        click.echo("## Books authored (ablib)")
        click.echo("")

    ablib_data = _narrator_ablib(name, json_mode)

    if not json_mode:
        click.echo("")
        click.echo("## Mentions in hadith/rijal literature (eshia)")
        click.echo("")

    eshia_data = _narrator_eshia(name, arabic, json_mode)

    if json_mode:
        from .output import print_json
        print_json({"name": name, "sources": {"ablibrary": ablib_data, "eshia": eshia_data}})
