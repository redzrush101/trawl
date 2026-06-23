from __future__ import annotations

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

    if source in ("both", "ablib"):
        try:
            with ablib_src.Client() as c:
                r = c.search(
                    query=query,
                    author=author or "",
                    title=title or "",
                    publisher=publisher or "",
                    languages=[lang] if lang else None,
                    categories=[cat] if cat else None,
                    has_pdf=pdf if pdf else None,
                    page=page,
                    per_page=per_page,
                )
            if exclude:
                r.books = [b for b in r.books if exclude not in b.title]
            if json_mode:
                json_data["sources"]["ablibrary"] = {
                    "books": [b.__dict__ for b in r.books],
                    "total_results": r.total_results,
                    "page": r.page,
                    "has_more": r.has_more,
                }
            else:
                click.echo("## Source: ablibrary")
                ablib_src.format_search_result(r, query, json_mode=False)
                click.echo("")
        except Exception as e:
            if json_mode:
                json_data["sources"]["ablibrary"] = {"error": str(e)}
            else:
                click.echo(f"## Source: ablibrary (error: {e})", err=True)

    if source in ("both", "eshia"):
        try:
            group_key = None
            if category:
                c2 = eshia_src.Client(arabic=arabic)
                group_key = c2.resolve_group_key(category)
                c2.close()
            c = eshia_src.Client(arabic=arabic)
            if book_id:
                results, cp, total = c.search_in_book(book_id, query, page=page)
            else:
                results, cp, total = c.search(query, page=page, group_key=group_key)
            if exclude:
                results = [r for r in results if exclude not in r.book_title and exclude not in r.snippet]
            if json_mode:
                json_data["sources"]["eshia"] = {
                    "results": [r.__dict__ for r in results],
                    "total": total,
                    "page": cp,
                }
            else:
                click.echo("## Source: eshia")
                eshia_src.format_search_results(results, cp, total, query, json_mode=False)
                click.echo("")
            c.close()
        except Exception as e:
            if json_mode:
                json_data["sources"]["eshia"] = {"error": str(e)}
            else:
                click.echo(f"## Source: eshia (error: {e})", err=True)

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
        try:
            save_parts.append("## Source: ablibrary")
            save_parts.append("")
            save_parts.append(ablib_src.search_result_to_markdown(r, query))
        except Exception:
            pass
        try:
            save_parts.append("## Source: eshia")
            save_parts.append("")
            save_parts.append(eshia_src.search_results_to_text(results, cp, total, query))
        except Exception:
            pass
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
            c = eshia_src.Client(arabic=arabic)
            d = c.book_detail(int(book_id))
            eshia_src.format_book_detail(d, json_mode=json_mode)
            c.close()
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
            c = eshia_src.Client(arabic=arabic)
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
            c.close()
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
            c = eshia_src.Client(arabic=arabic)
            entries = c.table_of_contents(int(book_id), volume=volume)
            eshia_src.format_toc(entries, json_mode=json_mode)
            c.close()
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
            import json as j
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
        c = eshia_src.Client(arabic=arabic)
        cats = c.categories()
        eshia_src.format_categories(cats, json_mode=json_mode)
        c.close()
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
        c = eshia_src.Client(arabic=arabic)
        subcats = c.categories()
        books = c.category_books(name, page=page)
        if json_mode:
            import json as j
            click.echo(j.dumps({
                "category": name,
                "subcategories": subcats,
                "books": [b.__dict__ for b in books],
            }, indent=2, default=str, ensure_ascii=False))
        else:
            click.echo(f"# Category: {name}")
            related = [s for s in subcats if name in s["name"] or s["name"] in name]
            if related:
                click.echo("")
                click.echo("**Subcategories:**")
                for s in related:
                    click.echo(f"- {s['name']}")
            click.echo("")
            click.echo("**Books:**")
            for b in books:
                click.echo(f"- [{b.id}] {b.title} by {b.author} ({b.volumes} vol)")
        c.close()
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
        c = eshia_src.Client(arabic=arabic)
        auths = c.authors(page=page)
        eshia_src.format_authors(auths, json_mode=json_mode)
        c.close()
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
        c = eshia_src.Client(arabic=arabic)
        items = c.autocomplete(query)
        eshia_src.format_autocomplete(items, json_mode=json_mode)
        c.close()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


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
        c2 = eshia_src.Client(arabic=arabic)
        detail = c2.book_detail(book_id)
        c2.close()
        pages = f"1-{detail.total_pages}" if detail and detail.total_pages else "1-10"
    page_nums = _parse_range(pages)
    out = Path(output) / str(book_id)
    out.mkdir(parents=True, exist_ok=True)

    results = []
    try:
        c = eshia_src.Client(arabic=arabic)
        for p in page_nums:
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
            if json_mode:
                results.append(page_result)
        c.close()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)

    if json_mode:
        import json as j
        click.echo(j.dumps(results, indent=2, default=str, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Narrator lookup
# ---------------------------------------------------------------------------

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

    if json_mode:
        data = {"name": name, "sources": {}}
        ablib_data = None
        eshia_data = None

    if not json_mode:
        click.echo(f"# Narrator: {name}")
        click.echo("")
        click.echo("## Books authored (ablib)")
        click.echo("")

    try:
        with ablib_src.Client() as c:
            r = c.search(author=name, per_page=10)
        if json_mode:
            ablib_data = {"books": [b.__dict__ for b in r.books], "total": r.total_results}
        else:
            for b in r.books[:5]:
                click.echo(f"- [{b.id}] {b.title}" + (f" ({b.pages_count}p)" if b.pages_count else ""))
            if r.total_results > 5:
                click.echo(f"  ... and {r.total_results - 5} more")
            if not r.books:
                click.echo("  (none found)")
    except Exception as e:
        if json_mode:
            ablib_data = {"error": str(e)}
        else:
            click.echo(f"  (error: {e})")

    if not json_mode:
        click.echo("")
        click.echo("## Mentions in hadith/rijal literature (eshia)")
        click.echo("")

    try:
        c = eshia_src.Client(arabic=arabic)
        results, cp, total = c.search(name, page=1)
        c.close()
        if json_mode:
            eshia_data = {"results": [r.__dict__ for r in results[:10]], "total": total}
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
            eshia_data = {"error": str(e)}
        else:
            click.echo(f"  (error: {e})")

    if json_mode:
        data["sources"]["ablibrary"] = ablib_data
        data["sources"]["eshia"] = eshia_data
        from .output import print_json
        print_json(data)
