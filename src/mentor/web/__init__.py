from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from mentor.i18n import gettext_, ngettext_

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"

_env = Environment(
    loader=FileSystemLoader(WEB_DIR / "templates"),
    autoescape=select_autoescape(),
    extensions=["jinja2.ext.i18n"],
)
_env.install_gettext_callables(gettext_, ngettext_, newstyle=True)  # type: ignore[attr-defined]

templates = Jinja2Templates(env=_env)
