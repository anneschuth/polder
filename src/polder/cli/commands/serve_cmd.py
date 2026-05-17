"""`polder serve` command.

`polder serve` (bare, of `polder serve site`) brengt de lokale Astro-site
op: het (her)bouwt de organogram-JSON als die ontbreekt of stale is,
kopieert die naar `web/public/organogram/data/`, opent de browser en start
de Astro dev-server.

`polder serve db` start datasette op de gebouwde SQLite-database (de oude
`polder serve`-functionaliteit).
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="serve",
    no_args_is_help=False,
    add_completion=False,
    help="Start de lokale site (`serve` / `serve site`) of datasette (`serve db`).",
)


def _newest_mtime(path: Path) -> float:
    """Hoogste mtime van alle files onder `path`, of 0.0 als er niks is."""
    newest = 0.0
    for f in path.rglob("*"):
        if f.is_file():
            mt = f.stat().st_mtime
            if mt > newest:
                newest = mt
    return newest


def _resolve_root() -> Path:
    """Polder-root: cwd als die `data/` heeft, anders nette fout."""
    cwd = Path.cwd()
    if (cwd / "data").exists():
        return cwd
    raise typer.BadParameter(f"Geen data/ in {cwd}. Draai `polder serve` vanuit de polder-root.")


def _run_site(
    data_dir: Path,
    dist_dir: Path,
    port: int,
    host: str,
    force: bool,
    open_browser: bool,
    install: bool,
) -> None:
    """Bouw + kopieer organogram-data indien nodig en start de Astro dev-server."""
    root = _resolve_root()
    web_dir = root / "web"
    if not (web_dir / "package.json").exists():
        typer.echo(
            f"web/package.json niet gevonden onder {web_dir}. Is dit een complete polder-checkout?",
            err=True,
        )
        raise typer.Exit(code=2)

    target = web_dir / "public" / "organogram" / "data"
    viz_out = dist_dir / "site"
    viz_data = viz_out / "data"

    # Stale-check: rebuild als doel ontbreekt, --force, of data/ nieuwer is.
    needs_build = (
        force
        or not target.exists()
        or not (target / "index.json").exists()
        or _newest_mtime(data_dir) > _newest_mtime(target)
    )

    if needs_build:
        typer.echo("+ build viz (organogram-data herbouwen)", err=True)
        from polder.build.to_viz import build_viz

        build_viz(data_dir, viz_out)

        typer.echo(f"+ copy {viz_data} -> {target}", err=True)
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(viz_data, target)
    else:
        typer.echo("organogram-data is up-to-date, skip build", err=True)

    if install and not (web_dir / "node_modules").exists():
        typer.echo("+ npm install", err=True)
        proc = subprocess.run(["npm", "install"], cwd=web_dir, check=False)
        if proc.returncode != 0:
            raise typer.Exit(code=proc.returncode)

    url = f"http://{host}:{port}/polder/organogram/"
    if open_browser:
        typer.echo(f"browser opent op {url}", err=True)
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()

    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--host", host]
    typer.echo(f"+ {' '.join(cmd)} (cwd={web_dir})", err=True)
    proc = subprocess.run(cmd, cwd=web_dir, check=False)
    raise typer.Exit(code=proc.returncode)


@app.callback(invoke_without_command=True)
def _serve_root(
    ctx: typer.Context,
    data_dir: Annotated[Path, typer.Option("--data-dir", help="Source-of-truth data/-pad.")] = Path(
        "data"
    ),
    dist_dir: Annotated[
        Path, typer.Option("--dist-dir", help="Output dist/-pad voor build viz.")
    ] = Path("dist"),
    port: Annotated[int, typer.Option(help="Poort voor de Astro dev-server.")] = 4321,
    host: Annotated[str, typer.Option(help="Host-binding.")] = "localhost",
    force: Annotated[
        bool, typer.Option("--force", help="Forceer rebuild + copy, ook als niet stale.")
    ] = False,
    no_open: Annotated[bool, typer.Option("--no-open", help="Open de browser niet.")] = False,
    no_install: Annotated[
        bool, typer.Option("--no-install", help="Sla `npm install` over.")
    ] = False,
) -> None:
    """Start de lokale site. Zonder subcommando draait dit de site-flow."""
    if ctx.invoked_subcommand is not None:
        return
    _run_site(
        data_dir=data_dir,
        dist_dir=dist_dir,
        port=port,
        host=host,
        force=force,
        open_browser=not no_open,
        install=not no_install,
    )


@app.command("site")
def serve_site(
    data_dir: Annotated[Path, typer.Option("--data-dir", help="Source-of-truth data/-pad.")] = Path(
        "data"
    ),
    dist_dir: Annotated[
        Path, typer.Option("--dist-dir", help="Output dist/-pad voor build viz.")
    ] = Path("dist"),
    port: Annotated[int, typer.Option(help="Poort voor de Astro dev-server.")] = 4321,
    host: Annotated[str, typer.Option(help="Host-binding.")] = "localhost",
    force: Annotated[
        bool, typer.Option("--force", help="Forceer rebuild + copy, ook als niet stale.")
    ] = False,
    no_open: Annotated[bool, typer.Option("--no-open", help="Open de browser niet.")] = False,
    no_install: Annotated[
        bool, typer.Option("--no-install", help="Sla `npm install` over.")
    ] = False,
) -> None:
    """Bouw organogram-data indien nodig en start de Astro dev-server."""
    _run_site(
        data_dir=data_dir,
        dist_dir=dist_dir,
        port=port,
        host=host,
        force=force,
        open_browser=not no_open,
        install=not no_install,
    )


@app.command("db")
def serve_db(
    db: Annotated[Path, typer.Option(help="Pad naar de polder.db SQLite-database.")] = Path(
        "dist/polder.db"
    ),
    metadata: Annotated[Path, typer.Option(help="Pad naar metadata.json.")] = Path("metadata.json"),
    port: Annotated[int, typer.Option(help="Poort om op te luisteren.")] = 8001,
    host: Annotated[str, typer.Option(help="Host-binding.")] = "127.0.0.1",
) -> None:
    """Start datasette op `dist/polder.db` met de Polder-metadata."""
    if not db.exists():
        typer.echo(
            f"database niet gevonden: {db}. Run `polder build sqlite` eerst.",
            err=True,
        )
        raise typer.Exit(code=2)

    cmd = [
        "uv",
        "run",
        "datasette",
        str(db),
        "--port",
        str(port),
        "--host",
        host,
    ]
    if metadata.exists():
        cmd += ["-m", str(metadata)]

    typer.echo(f"+ {' '.join(cmd)}", err=True)
    proc = subprocess.run(cmd, check=False)
    raise typer.Exit(code=proc.returncode)
