"""The route table. Every path is a literal; none carries a person.

Handlers are `def`, not `async def`, and that is load-bearing rather than
stylistic. `StdioWarden.call` runs `asyncio.run(...)`; Starlette hands a sync
endpoint to a worker thread, which has no running loop, so it works — on an
`async def` it raises `RuntimeError: asyncio.run() cannot be called from a
running event loop`, on every request rather than only under load. Every
`store.*` call is blocking sqlite for the same reason.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from ..spend.warden import Warden
from . import panels, render
from .scope import Household

LINKS = (
    ("/", "Overview"),
    ("/privacy", "The boundary"),
    ("/ledger", "Ledger"),
    ("/pilot", "Pilot counts"),
)


def _page(house: Household, here: str, title: str, body_html: str) -> HTMLResponse:
    return HTMLResponse(
        render.document(
            title=title,
            banner_html=panels.banner(house),
            nav_html=render.nav(here, LINKS),
            body_html=body_html,
        )
    )


def build_app(house: Household, *, warden_client: Warden | None = None) -> Starlette:
    """One app, bound to one household.

    `warden_client` is the seam `warden.audit_log(warden=…)` already exposes, so
    the suite drives the ledger without spawning a subprocess. It is None in
    production, which is the only configuration where one is ever launched.
    """

    def overview(request: Request) -> Response:
        body_html = (
            render.deck(panels.waiting_on_you(house), panels.registered_in_policy(house))
            + render.deck(panels.saving_for(house), panels.money_back(house))
            + render.deck(panels.decided(house))
            + render.deck(panels.the_boundary(house))
        )
        return _page(house, "/", f"{house.sponsor()['name']} — steward", body_html)

    def boundary(request: Request) -> Response:
        return _page(
            house,
            "/privacy",
            "The boundary — steward",
            render.deck(panels.the_boundary(house, full=True)),
        )

    def ledger(request: Request) -> Response:
        """The only route that can spawn a process."""
        return _page(
            house,
            "/ledger",
            "Ledger — steward",
            render.deck(panels.ledger(house, client=warden_client)),
        )

    def pilot(request: Request) -> Response:
        return _page(
            house, "/pilot", "Pilot counts — steward", render.deck(panels.pilot_panel(house))
        )

    def nowhere(request: Request, exc: Exception) -> Response:
        sponsor_html = render.text(house.sponsor()["name"])
        body_html = (
            '<div class="card nowhere"><h1>There is no page here.</h1>'
            f"<p>This process serves {sponsor_html}'s household and nothing else."
            " There are no addressable households — no URL on this server takes a"
            " person id, so there is none to edit. It was started with"
            f" <code>--person {render.text(house.sponsor_id)}</code>, and that is the"
            " only place the scope is set.</p>"
            '<p><a href="/">Back to the overview</a></p></div>'
        )
        return HTMLResponse(
            render.document(
                title="Nothing here — steward",
                banner_html="",
                nav_html=render.nav("", LINKS),
                body_html=body_html,
            ),
            status_code=404,
        )

    return Starlette(
        routes=[
            Route("/", overview),
            Route("/privacy", boundary),
            Route("/ledger", ledger),
            Route("/pilot", pilot),
        ],
        exception_handlers={404: nowhere},
    )
