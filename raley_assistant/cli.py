"""CLI interface for Raley Grocery Assistant."""

import click
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .cookies import (
    import_and_save,
    load_saved_cookies,
    validate_cookies,
    COOKIES_FILE,
)
from .api import (
    create_client,
    check_session,
    search_products,
    get_offers,
    clip_offer,
    clip_all_offers,
    get_products_by_sku,
    add_to_cart,
    CartItem,
    get_user_profile,
    get_points,
    get_orders,
    get_previously_purchased,
)

console = Console()


def get_client():
    """Get authenticated client or exit with error."""
    if not COOKIES_FILE.exists():
        console.print("[red]No saved session. Run 'raley login' first.[/red]")
        raise SystemExit(1)

    return create_client(COOKIES_FILE)


@click.group()
def main():
    """Raley Grocery Assistant — your dependable shopping pal."""
    pass


@main.command()
@click.option(
    "--file",
    "-f",
    type=click.Path(exists=True),
    help="Import from DevTools JSON export (advanced)",
)
def login(file: str | None):
    """Login to your account.

    Opens a browser window for interactive login by default.
    Use --file to import cookies from a DevTools JSON export.
    """
    if file:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Importing cookies...", total=None)

            _, warnings = import_and_save(file)

            if warnings:
                for w in warnings:
                    console.print(f"[yellow]Warning: {w}[/yellow]")

            progress.update(task, description="Testing session...")
            client = create_client(COOKIES_FILE)
            valid = check_session(client)

        if valid:
            console.print("[green]Login successful! Session saved.[/green]")

            profile = get_user_profile(client)
            if profile:
                console.print(
                    f"[dim]Logged in as: {profile.get('email', 'Unknown')}[/dim]"
                )

            points = get_points(client)
            if points is not None:
                points_value = (
                    points.get("points", 0) if isinstance(points, dict) else points
                )
                console.print(f"[dim]Points: {points_value}[/dim]")
        else:
            console.print(
                "[yellow]Cookies imported but session may be expired. Try again.[/yellow]"
            )
    else:
        from .auth import interactive_login

        console.print("[cyan]Opening browser for login...[/cyan]")
        console.print("[dim]Complete the login in the browser window.[/dim]")

        success, message = interactive_login()

        if success:
            console.print(f"[green]{message}[/green]")

            try:
                if COOKIES_FILE.exists():
                    client = create_client(COOKIES_FILE)

                    profile = get_user_profile(client)
                    if profile:
                        console.print(
                            f"[dim]Logged in as: {profile.get('email', 'Unknown')}[/dim]"
                        )

                    points_data = get_points(client)
                    if points_data is not None:
                        points_value = (
                            points_data.get("points", 0)
                            if isinstance(points_data, dict)
                            else points_data
                        )
                        console.print(f"[dim]Points: {points_value}[/dim]")
            except Exception:
                pass
        else:
            console.print(f"[red]{message}[/red]")
            console.print(
                "[dim]Tip: Use --file to import cookies manually if browser login fails.[/dim]"
            )


@main.command()
def status():
    """Check session status and account info."""
    saved = load_saved_cookies()
    if not saved:
        console.print("[red]No saved session. Run 'raley login' first.[/red]")
        return

    cookies = saved.get("cookies", saved) if isinstance(saved, dict) else saved

    valid, missing = validate_cookies(cookies)
    if not valid:
        console.print(f"[yellow]Missing cookies: {', '.join(missing)}[/yellow]")

    client = get_client()
    session_valid = check_session(client)

    if session_valid:
        console.print("[green]Session is valid.[/green]")

        profile = get_user_profile(client)
        if profile:
            console.print(f"Email: {profile.get('email', 'Unknown')}")
            console.print(
                f"Name: {profile.get('firstName', '')} {profile.get('lastName', '')}"
            )

        points = get_points(client)
        if points is not None:
            points_value = (
                points.get("points", 0) if isinstance(points, dict) else points
            )
            console.print(f"Something Extra Points: {points_value}")
    else:
        console.print("[red]Session expired. Please re-login.[/red]")


@main.command()
@click.argument("query")
@click.option("--sale", is_flag=True, help="Show only sale items")
@click.option("--limit", "-n", default=10, help="Max results")
def search(query: str, sale: bool, limit: int):
    """Search for products with unit pricing."""
    client = get_client()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Searching '{query}'...", total=None)
        products = search_products(client, query, on_sale=sale, limit=limit)

    if not products:
        console.print("[yellow]No products found.[/yellow]")
        return

    table = Table(title=f"Results for '{query}'")
    table.add_column("SKU", style="dim", width=10)
    table.add_column("Name", max_width=35)
    table.add_column("Brand", style="cyan", max_width=12)
    table.add_column("Price", style="green", width=8)
    table.add_column("$/oz", style="yellow", width=8)
    table.add_column("Sale", style="magenta", width=6)

    for p in products:
        price = p.sale_price_cents or p.price_cents
        ppo = f"${p.price_per_oz:.2f}" if p.price_per_oz else "-"

        table.add_row(
            p.sku,
            p.name[:35],
            (p.brand or "")[:12],
            f"${price/100:.2f}",
            ppo,
            "SALE" if p.on_sale else "",
        )

    console.print(table)
    console.print(f"\n[dim]{len(products)} results[/dim]")


@main.command()
@click.option("--category", "-c", help="Filter by category")
@click.option("--unclipped", is_flag=True, help="Show only unclipped offers")
@click.option("--clipped", is_flag=True, help="Show only clipped offers")
def offers(category: str | None, unclipped: bool, clipped: bool):
    """List available offers/coupons."""
    client = get_client()

    clip_filter = None
    if unclipped:
        clip_filter = "Unclipped"
    elif clipped:
        clip_filter = "Clipped"

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Fetching offers...", total=None)
        all_offers = get_offers(client, category=category, clipped=clip_filter, rows=200)

    table = Table(title="Available Offers")
    table.add_column("ID", style="dim", width=8)
    table.add_column("Headline", style="cyan", max_width=30)
    table.add_column("Description", max_width=40)
    table.add_column("Category", style="yellow")
    table.add_column("Clipped", style="magenta")

    for o in all_offers:
        table.add_row(
            o.id,
            o.headline,
            o.description[:40] + "..." if len(o.description) > 40 else o.description,
            o.category[:15] if o.category else "-",
            "Yes" if o.is_clipped else "No",
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(all_offers)} offers[/dim]")

    unclipped_count = sum(1 for o in all_offers if not o.is_clipped)
    if unclipped_count > 0:
        console.print(
            f"[cyan]Unclipped: {unclipped_count} (run 'raley clip-all' to clip)[/cyan]"
        )


@main.command("clip")
@click.argument("offer_id")
def clip(offer_id: str):
    """Clip a specific offer by ID."""
    client = get_client()

    all_offers = get_offers(client, rows=500)
    offer = next((o for o in all_offers if o.id == offer_id), None)

    if not offer:
        console.print(f"[red]Offer {offer_id} not found[/red]")
        return

    success, error = clip_offer(client, offer)
    if success:
        console.print(f"[green]Clipped offer {offer_id}[/green]")
    else:
        console.print(f"[red]Failed to clip offer {offer_id}: {error}[/red]")


@main.command("clip-all")
def clip_all():
    """Clip all available unclipped offers."""
    client = get_client()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Clipping offers...", total=None)

        def update_progress(current: int, total: int, clipped_so_far: int) -> None:
            progress.update(
                task,
                description=f"Clipping offers... {current}/{total} ({clipped_so_far} clipped)",
            )

        clipped_count, failed_count, errors = clip_all_offers(
            client, on_progress=update_progress
        )

    console.print(f"[green]Clipped: {clipped_count}[/green]")
    if failed_count:
        console.print(f"[yellow]Failed: {failed_count}[/yellow]")
    if errors:
        for e in errors:
            console.print(f"[dim]{e}[/dim]")


@main.command()
def history():
    """Show previously purchased items."""
    client = get_client()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Fetching purchase history...", total=None)
        products = get_previously_purchased(client, limit=50)

    if not products:
        console.print("[yellow]No purchase history found.[/yellow]")
        return

    table = Table(title="Previously Purchased Items")
    table.add_column("SKU", style="dim", width=10)
    table.add_column("Name", max_width=40)
    table.add_column("Brand", style="cyan", max_width=15)
    table.add_column("Price", style="green")
    table.add_column("Sale", style="magenta")

    for p in products[:30]:
        price = p.sale_price_cents or p.price_cents

        table.add_row(
            p.sku,
            p.name[:40],
            (p.brand or "")[:15],
            f"${price/100:.2f}",
            "On Sale" if p.on_sale else "",
        )

    console.print(table)
    console.print(
        f"\n[dim]Showing {min(len(products), 30)} of {len(products)} items[/dim]"
    )


@main.command()
def orders():
    """Show order history."""
    client = get_client()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Fetching orders...", total=None)
        order_list = get_orders(client)

    if not order_list:
        console.print("[yellow]No orders found or unable to fetch.[/yellow]")
        return

    table = Table(title="Order History")
    table.add_column("Order ID", style="cyan", width=12)
    table.add_column("Date", style="yellow")
    table.add_column("Total", style="green")
    table.add_column("Status")

    for order in order_list[:20]:
        order_id = (
            order.get("orderId")
            or order.get("id")
            or order.get("orderNumber")
            or ""
        )
        if isinstance(order_id, str):
            order_id = order_id[-8:] if len(order_id) > 8 else order_id

        date_str = (
            order.get("createdDate") or order.get("date") or order.get("createdAt") or ""
        )
        if isinstance(date_str, str) and len(date_str) >= 10:
            date_str = date_str[:10]

        total = 0
        if "totalPrice" in order and isinstance(order["totalPrice"], dict):
            total = order["totalPrice"].get("centAmount", 0) / 100
        elif "total" in order:
            total_val = order["total"]
            if isinstance(total_val, (int, float)):
                total = total_val if total_val < 1000 else total_val / 100
        else:
            product_amt = order.get("productAmount", 0)
            tax_amt = order.get("productTaxAmount", 0)
            service_amt = order.get("serviceFeeAmount", 0)
            adjustment = order.get("adjustmentAmount", 0)
            tip_amt = order.get("tipAmount", 0)
            total = product_amt + tax_amt + service_amt + adjustment + tip_amt

        status = ""
        if "orderStatus" in order and isinstance(order["orderStatus"], dict):
            status = order["orderStatus"].get("value", "")
        elif "status" in order:
            status = order["status"]

        table.add_row(str(order_id), str(date_str), f"${total:.2f}", str(status))

    console.print(table)
    console.print(
        f"\n[dim]Showing {min(len(order_list), 20)} of {len(order_list)} orders[/dim]"
    )


@main.command()
@click.argument("skus", nargs=-1)
def products(skus: tuple[str, ...]):
    """Get product details by SKU."""
    if not skus:
        console.print("[yellow]Provide one or more SKUs[/yellow]")
        return

    client = get_client()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Fetching products...", total=None)
        items = get_products_by_sku(client, list(skus))

    table = Table(title="Products")
    table.add_column("SKU", style="cyan")
    table.add_column("Name", max_width=40)
    table.add_column("Price", style="green")

    for item in items:
        master = item.get("masterData", {}).get("current", {})
        name = master.get("name", "Unknown")
        sku = item.get("key", "")

        price = "N/A"
        variants = master.get("masterVariant", {})
        prices = variants.get("prices", [])
        if prices:
            p = prices[0].get("value", {})
            cents = p.get("centAmount", 0)
            price = f"${cents / 100:.2f}"

        table.add_row(sku, name, price)

    console.print(table)


@main.command()
@click.argument("sku")
@click.option("--qty", "-q", default=1, help="Quantity to add")
@click.option("--price", "-p", type=float, required=True, help="Price in dollars")
def add(sku: str, qty: int, price: float):
    """Add item to cart by SKU."""
    client = get_client()

    item = CartItem(sku=sku, quantity=qty, price_cents=round(price * 100))

    if add_to_cart(client, [item]):
        console.print(f"[green]Added {qty}x SKU {sku} (${price:.2f}) to cart[/green]")
    else:
        console.print("[red]Failed to add to cart[/red]")


@main.command()
def points():
    """Show Something Extra points balance."""
    client = get_client()

    data = get_points(client)
    if data:
        console.print(
            Panel(
                f"[bold green]{data.get('points', 0)}[/bold green] Something Extra Points"
            )
        )
    else:
        console.print("[yellow]Unable to fetch points[/yellow]")


if __name__ == "__main__":
    main()
