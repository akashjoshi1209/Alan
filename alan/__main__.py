"""Main entry point for Alan - Personal AI Operating System."""

import asyncio
import sys
from typing import Optional

import typer
from rich.console import Console

from alan.cli.main import app as cli_app
from alan.core import AlanKernel, get_kernel
from alan.logging import get_logger, setup_logging

console = Console()
logger = get_logger(__name__)

app = typer.Typer(
    name="alan",
    help="Alan - Personal AI Operating System",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-v", help="Show version and exit"
    ),
) -> None:
    """Alan - Personal AI Operating System."""
    if version:
        from alan import __version__
        console.print(f"Alan v{__version__}")
        raise typer.Exit()


@app.command()
def kernel(
    config: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
    env: str = typer.Option(
        "development", "--env", "-e", help="Environment name"
    ),
) -> None:
    """Start the Alan kernel."""
    asyncio.run(_run_kernel(config, env))


async def _run_kernel(config_path: Optional[str], env: str) -> None:
    """Run the kernel asynchronously."""
    setup_logging()
    logger.info("Starting Alan kernel", env=env)

    kernel = AlanKernel(config_path=config_path, environment=env)

    try:
        await kernel.start()
        logger.info("Kernel started successfully")

        # Keep running until shutdown signal
        await kernel.wait_for_shutdown()

    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.exception("Kernel error", error=str(e))
        sys.exit(1)
    finally:
        await kernel.stop()
        logger.info("Kernel stopped")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of workers"),
) -> None:
    """Start the API server."""
    import uvicorn

    setup_logging()
    logger.info("Starting API server", host=host, port=port)

    uvicorn.run(
        "alan.api.main:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        workers=workers,
        log_config=None,  # We use our own logging
    )


# Mount CLI commands
app.add_typer(cli_app, name="cli")

if __name__ == "__main__":
    app()