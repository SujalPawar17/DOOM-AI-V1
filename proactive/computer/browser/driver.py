"""In-process browser driver. No coordinates, no user JavaScript, no downloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Protocol, Tuple
from urllib.parse import urljoin, urlparse

from proactive.computer.browser.observe import bound_observation
from proactive.computer.browser.types import BrowserElement, BrowserObservation
from proactive.computer.browser.url import origin_of


def _el(**kwargs) -> BrowserElement:
    return BrowserElement(**kwargs)


_PAGES: Dict[str, Tuple[str, List[BrowserElement]]] = {
    "https://example.com/": (
        "Example Domain",
        [
            _el(role="link", name="More information...", element_id="more", href="https://www.iana.org/domains/example", handle="more"),
            _el(role="textbox", name="Note", element_id="note", test_id="note", editable=True, handle="note"),
        ],
    ),
    "https://www.iana.org/domains/example": (
        "IANA Example",
        [
            _el(role="link", name="Home", element_id="home", href="https://example.com/", handle="home"),
            _el(role="button", name="Safe demo", element_id="demo", handle="demo"),
        ],
    ),
}


class BrowserDriver(Protocol):
    def snapshot(self, session_id: str) -> BrowserObservation:
        ...

    def navigate(self, url: str) -> BrowserObservation:
        ...

    def activate(self, handle: str) -> BrowserObservation:
        ...

    def type_text(self, handle: str, text: str) -> BrowserObservation:
        ...

    def back(self) -> BrowserObservation:
        ...

    def forward(self) -> BrowserObservation:
        ...

    def refresh(self) -> BrowserObservation:
        ...

    def close(self) -> None:
        ...


@dataclass
class MemoryBrowserDriver:
    url: str = ""
    title: str = ""
    history: List[str] = field(default_factory=list)
    future: List[str] = field(default_factory=list)
    values: Dict[str, str] = field(default_factory=dict)
    closed: bool = False
    last_download: bool = False
    pages: Dict[str, Tuple[str, List[BrowserElement]]] = field(default_factory=lambda: dict(_PAGES))

    def close(self) -> None:
        self.closed = True

    def _norm(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path or "/"
        out = f"{parsed.scheme}://{parsed.netloc}{path}"
        if parsed.query:
            out += "?" + parsed.query
        return out

    def _page(self) -> Tuple[str, List[BrowserElement]]:
        key = self.url
        if key not in self.pages and key.endswith("/"):
            alt = key[:-1]
            if alt in self.pages:
                key = alt
        if key not in self.pages and not key.endswith("/"):
            if key + "/" in self.pages:
                key = key + "/"
        if key not in self.pages:
            return self.title or "", []
        title, els = self.pages[key]
        out = []
        for el in els:
            out.append(BrowserElement(
                role=el.role,
                name=el.name,
                element_id=el.element_id,
                test_id=el.test_id,
                input_type=el.input_type,
                disabled=el.disabled,
                is_password=el.is_password,
                href=el.href,
                value=self.values.get(el.handle, el.value),
                editable=el.editable,
                download=el.download,
                ancestry=el.ancestry,
                handle=el.handle,
            ))
        return title, out

    def snapshot(self, session_id: str) -> BrowserObservation:
        title, els = self._page()
        self.title = title
        obs = BrowserObservation(
            session_id=session_id,
            url=self.url,
            origin=origin_of(self.url),
            title_advisory=title[:80],
            elements=els,
            download_attempted=self.last_download,
        )
        self.last_download = False
        return bound_observation(obs)

    def navigate(self, url: str) -> BrowserObservation:
        if self.url:
            self.history.append(self.url)
        self.future.clear()
        self.url = self._norm(url)
        return self.snapshot("")

    def activate(self, handle: str) -> BrowserObservation:
        _title, els = self._page()
        target = next((e for e in els if e.handle == handle), None)
        if target is None:
            return self.snapshot("")
        if target.download:
            self.last_download = True
            return self.snapshot("")
        if target.href:
            return self.navigate(urljoin(self.url or "https://example.com/", target.href))
        return self.snapshot("")

    def type_text(self, handle: str, text: str) -> BrowserObservation:
        self.values[handle] = text
        return self.snapshot("")

    def back(self) -> BrowserObservation:
        if not self.history:
            return self.snapshot("")
        self.future.append(self.url)
        self.url = self.history.pop()
        return self.snapshot("")

    def forward(self) -> BrowserObservation:
        if not self.future:
            return self.snapshot("")
        self.history.append(self.url)
        self.url = self.future.pop()
        return self.snapshot("")

    def refresh(self) -> BrowserObservation:
        return self.snapshot("")
