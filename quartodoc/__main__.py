import click
import contextlib
import os
import time
import sphobjinv as soi
import yaml
import importlib
from pathlib import Path
from watchdog.observers import Observer
from functools import partial
from watchdog.events import PatternMatchingEventHandler
from quartodoc import Builder, convert_inventory
from pydantic import BaseModel

def get_package_path(package_name):
    """
    Get the path to a package installed in the current environment.
    """
    try:
        lib = importlib.import_module(package_name)
        return lib.__path__[0]
    except ModuleNotFoundError:
        raise ModuleNotFoundError(f"Package {package_name} not found.  Please install it in your environment.")


class FileInfo(BaseModel):
    size: int
    mtime: float
    name: str= ""

class QuartoDocFileChangeHandler(PatternMatchingEventHandler):
    """
    A handler for file changes.
    """

    # Ignore patterns for the file watcher that are not relevant to the docs
    py_ignore_patterns = [
        '*/__pycache__/*',  # These are the compiled python code files which are automatically generated by Python
        '*/.ipynb_checkpoints/*',  # This directory is created by Jupyter Notebook for auto-saving notebooks
        '*/.vscode/*',  # If you're using Visual Studio Code, it creates this directory to store settings specific to that project.
        '*/.idea/*',  # Similar to .vscode/, but for JetBrains IDEs like PyCharm.
        '*/.git/*',  # i This directory is created by Git. It's not relevant to the docs.
        '*/venv/*', '*/env/*',  '*/.env/*',  # Common names for directories containing a Python virtual environment.
        '*/.pytest_cache/*',  # This directory is created when you run Pytest.
        '*/.eggs/*', '*/dist/*', '*/build/*', '*/*.egg-info/*', # These are typically created when building Python packages with setuptools.
        '*.pyo',  # These are optimized .pyc files, created when Python is run with the -O flag.
        '*.pyd',  # This is the equivalent of a .pyc file, but for C extensions on Windows.
        '*/.mypy_cache/*', # This directory is created when you run mypy.
    ]

    def __init__(self, callback):
        super().__init__(ignore_patterns=self.py_ignore_patterns, ignore_directories=True)
        self.callback = callback
        self.old_file_info = FileInfo(size=-1, mtime=-1, name="")


    def get_file_info(self, path:str) -> FileInfo:
        """
        Get the file size and modification time.
        """
        return FileInfo(size=os.stat(path).st_size, 
                        mtime=os.stat(path).st_mtime, 
                        name=path)
    
    def is_diff(self, old:FileInfo, new:FileInfo) -> bool:
        """
        Check if a file has changed. Prevents duplicate events from being triggered.
        """
        same_nm = old.name == new.name
        diff_sz = old.size != new.size
        diff_tm = (new.mtime - old.mtime) > 0.25 # wait 1/4 second before triggering
        return not same_nm or (same_nm and (diff_sz or diff_tm))
    
    def callback_if_diff(self, event):
        """
        Call the callback if the file has changed.
        """
        new_file_info = self.get_file_info(event.src_path)
        if self.is_diff(self.old_file_info, new_file_info):
            self.callback()
            self.print_event(event)
        self.old_file_info = new_file_info

    @classmethod
    def print_event(cls, event):
        print(f'Rebuilding docs.  Detected: {event.event_type} path : {event.src_path}')

    def on_modified(self, event):
        self.callback_if_diff(event)

    def on_created(self, event):
        self.callback_if_diff(event)

def _enable_logs():
    import logging
    import sys

    root = logging.getLogger("quartodoc")
    root.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)


@contextlib.contextmanager
def chdir(new_dir):
    prev = os.getcwd()
    os.chdir(new_dir)
    try:
        yield new_dir
    finally:
        os.chdir(prev)


@click.group()
def cli():
    pass


@click.command()
@click.option("--config", default="_quarto.yml", help="Change the path to the configuration file.  The default is `./_quarto.yml`")
@click.option("--filter", nargs=1, default="*", help="Specify the filter to select specific files. The default is '*' which selects all files.")
@click.option("--dry-run", is_flag=True, default=False, help="If set, prevents new documents from being generated.")
@click.option("--watch", is_flag=True, default=False, help="If set, the command will keep running and watch for changes in the package directory.")
@click.option("--verbose", is_flag=True, default=False, help="Enable verbose logging.")
def build(config, filter, dry_run, watch, verbose):
    """
    Generate API docs based on the given configuration file  (`./_quarto.yml` by default).
    """
    if verbose:
        _enable_logs()

    builder = Builder.from_quarto_config(config)
    doc_build = partial(builder.build, filter=filter)

    if dry_run:
        pass
    else:
        with chdir(Path(config).parent):
            if watch:
                pkg_path = get_package_path(builder.package)
                print(f"Watching {pkg_path} for changes...")
                observer = Observer()
                observer._event_queue.maxsize = 1 # the default is 0 which is infinite, and there isn't a way to set this in the constructor
                event_handler = QuartoDocFileChangeHandler(callback=doc_build)
                observer.schedule(event_handler, pkg_path, recursive=True)
                observer.start()
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass
                finally:
                    observer.stop()
                    observer.join()
            else:   
                doc_build()

@click.command()
@click.argument("config", default="_quarto.yml")
@click.option("--dry-run", is_flag=True, default=False)
def interlinks(config, dry_run):
    cfg = yaml.safe_load(open(config))
    interlinks = cfg.get("interlinks", None)

    cache = cfg.get("cache", "_inv")

    p_root = Path(config).parent

    if interlinks is None:
        print("No interlinks field found in your quarto config. Quitting.")
        return

    for k, v in interlinks["sources"].items():

        # TODO: user shouldn't need to include their own docs in interlinks
        if v["url"] == "/":
            continue

        url = v["url"] + v.get("inv", "objects.inv")
        inv = soi.Inventory(url=url)

        p_dst = p_root / cache / f"{k}_objects.json"
        p_dst.parent.mkdir(exist_ok=True, parents=True)

        convert_inventory(inv, p_dst)


cli.add_command(build)
cli.add_command(interlinks)


if __name__ == "__main__":
    cli()
