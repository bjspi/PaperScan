"""Editing session: the pages being prepared, split into one or more documents.

Usually a session is one document (five photos of a heating bill). The user can
start a new document at any page, so one import can become several PDFs; each
document is a run of neighbouring pages. Pages share one filter by default (all
treated the same); a page can opt out with its own filter. Each page keeps a
downscaled copy of its photo so previews render fast; the full photo is only
reloaded for the export.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import lru_cache
from itertools import groupby
from pathlib import Path

import numpy as np

from paperscan.capture.adb_import import RemotePhoto
from paperscan.capture.photo_files import load_photo
from paperscan.imaging.border_detection import detect_page
from paperscan.imaging.filters import FilterSettings
from paperscan.imaging.geometry import Quad, limit_long_edge
from paperscan.pipeline import PageEdit, ScanPage, open_page, render_page
from paperscan.settings import AppSettings

#: Long edge of the in-memory working copy used for editing and quick previews.
PREVIEW_EDGE = 1400
#: Full-resolution photos kept in memory for final-quality previews (about 36 MB each).
FULL_PHOTO_CACHE_SIZE = 3


@dataclass(frozen=True)
class SessionPage:
    """A page with its preview working copy.

    Attributes:
        page: Source photo and user decisions (full-resolution coordinates).
        preview_rgb: Downscaled photo for the editor and previews.
        own_filter: True if this page does not follow the shared filter.
        remote: Phone photo the page was imported from (for deleting it after export).
        document: Id of the document the page belongs to; neighbouring pages with the
            same id form one document.
    """

    page: ScanPage
    preview_rgb: np.ndarray
    own_filter: bool = False
    #: The photo on the phone this page was imported from, if any.
    remote: RemotePhoto | None = None
    document: int = 0

    @property
    def preview_scale(self) -> float:
        """Return preview pixels per source pixel."""
        return self.preview_rgb.shape[1] / self.page.width


def load_session_page(path: Path, settings: AppSettings, remote: RemotePhoto | None = None) -> SessionPage:
    """Load a photo, detect its outline and build the preview copy (runs in a worker)."""
    page = open_page(path, settings)
    preview = limit_long_edge(load_photo(path), PREVIEW_EDGE)
    return SessionPage(page=page, preview_rgb=preview, remote=remote)


@lru_cache(maxsize=FULL_PHOTO_CACHE_SIZE)
def _full_photo(path: Path) -> np.ndarray:
    """Load and keep the full-resolution photos of the most recently viewed pages."""
    photo = load_photo(path)
    photo.setflags(write=False)
    return photo


def full_photo(entry: SessionPage) -> np.ndarray:
    """Return the full-resolution photo of a page (cached for the most recent pages)."""
    return _full_photo(entry.page.source)


def render_final(entry: SessionPage, max_edge: int | None) -> np.ndarray:
    """Render the page exactly as the export will: from the full photo at the output size."""
    return render_page(_full_photo(entry.page.source), entry.page.edit, max_edge)


def render_preview(entry: SessionPage, max_edge: int | None = None) -> np.ndarray:
    """Render the page from the preview copy with the page's current decisions."""
    edit = entry.page.edit
    scaled = replace(edit, quad=edit.quad.scaled(entry.preview_scale))
    return render_page(entry.preview_rgb, scaled, max_edge)


class DocumentSession:
    """Ordered pages, their split into documents, and the shared filter."""

    def __init__(self, shared_filter: FilterSettings) -> None:
        """Start an empty session whose pages use ``shared_filter``."""
        self.shared_filter = shared_filter
        self._pages: list[SessionPage] = []
        self._last_document_id = 0

    @property
    def pages(self) -> list[SessionPage]:
        """Return the pages in document order (a copy)."""
        return list(self._pages)

    def __len__(self) -> int:
        """Return the number of pages."""
        return len(self._pages)

    def page(self, index: int) -> SessionPage:
        """Return the page at ``index``."""
        return self._pages[index]

    def phone_photos(self, indices: Iterable[int] | None = None) -> list[RemotePhoto]:
        """Return the distinct phone photos of the pages at ``indices`` (default: all pages)."""
        entries = self._pages if indices is None else [self._pages[index] for index in indices]
        photos: dict[str, RemotePhoto] = {}
        for entry in entries:
            if entry.remote is not None:
                photos.setdefault(entry.remote.path, entry.remote)
        return list(photos.values())

    def scan_pages(self) -> list[ScanPage]:
        """Return the pipeline pages in page order."""
        return [entry.page for entry in self._pages]

    # --- Documents ------------------------------------------------------------------------------

    def document_ranges(self) -> list[range]:
        """Return the page indices of each document, in page order."""
        ranges: list[range] = []
        start = 0
        for _document, entries in groupby(self._pages, key=lambda entry: entry.document):
            end = start + len(list(entries))
            ranges.append(range(start, end))
            start = end
        return ranges

    def document_pages(self) -> list[list[ScanPage]]:
        """Return the pipeline pages of each document."""
        return [[self._pages[index].page for index in pages] for pages in self.document_ranges()]

    def starts_document(self, index: int) -> bool:
        """Return whether the page at ``index`` begins a document other than the first."""
        return 0 < index < len(self._pages) and self._pages[index].document != self._pages[index - 1].document

    def split_before(self, index: int) -> None:
        """Start a new document at page ``index``; the following pages of its document move along."""
        if index <= 0 or self.starts_document(index):
            return
        old, new = self._pages[index].document, self._new_document_id()
        for position in range(index, len(self._pages)):
            if self._pages[position].document != old:
                break
            self._pages[position] = replace(self._pages[position], document=new)

    def join_with_previous(self, index: int) -> None:
        """Merge the document containing page ``index`` into the document before it."""
        pages = next(pages for pages in self.document_ranges() if index in pages)
        if pages.start == 0:
            return
        previous = self._pages[pages.start - 1].document
        for position in pages:
            self._pages[position] = replace(self._pages[position], document=previous)

    def split_at_pauses(self, gap: timedelta) -> int:
        """Start a new document wherever neighbouring photos were taken more than ``gap`` apart.

        Returns the number of documents afterwards.
        """
        for index in range(1, len(self._pages)):
            pause = abs(self._pages[index].page.captured_at - self._pages[index - 1].page.captured_at)
            if pause > gap:
                self.split_before(index)
        return len(self.document_ranges())

    # --- Pages ----------------------------------------------------------------------------------

    def add(self, entry: SessionPage) -> int:
        """Append a page to the last document (it adopts the shared filter) and return its index."""
        document = self._pages[-1].document if self._pages else self._last_document_id
        self._pages.append(replace(self._with_filter(entry, self.shared_filter), document=document))
        return len(self._pages) - 1

    def remove(self, index: int) -> None:
        """Remove the page at ``index`` (a document without pages disappears)."""
        del self._pages[index]

    def clear(self) -> None:
        """Remove all pages."""
        self._pages.clear()

    def move(self, source: int, target: int) -> None:
        """Move the page at ``source`` to position ``target``.

        The page joins the document of the page it was dropped on, so documents stay
        runs of neighbouring pages.
        """
        document = self._pages[target].document
        moved = self._pages.pop(source)
        self._pages.insert(target, replace(moved, document=document))

    def set_quad(self, index: int, quad: Quad) -> None:
        """Set the page outline (source pixel coordinates)."""
        self._update_edit(index, quad=quad.clamped(self._pages[index].page.width, self._pages[index].page.height))

    def reset_quad(self, index: int, settings: AppSettings) -> Quad:
        """Run the border detection again on the preview copy and return the new outline."""
        entry = self._pages[index]
        detection = detect_page(entry.preview_rgb, settings.model_path)
        quad = detection.quad.scaled(1 / entry.preview_scale)
        self.set_quad(index, quad)
        return quad

    def full_quad(self, index: int) -> Quad:
        """Use the whole photo as page and return that outline."""
        entry = self._pages[index]
        quad = Quad.full_image(entry.page.width, entry.page.height)
        self.set_quad(index, quad)
        return quad

    def rotate(self, index: int, clockwise: bool) -> None:
        """Turn the page by a quarter turn."""
        rotation = self._pages[index].page.edit.rotation
        self._update_edit(index, rotation=rotation.turned_clockwise() if clockwise else rotation.turned_counterclockwise())

    def set_filter(self, index: int | None, settings: FilterSettings, *, only_this_page: bool) -> None:
        """Change the filter of one page, or the shared filter of every page that follows it."""
        if only_this_page and index is not None:
            self._pages[index] = replace(self._with_filter(self._pages[index], settings), own_filter=True)
            return
        self.shared_filter = settings
        self._pages = [entry if entry.own_filter else self._with_filter(entry, settings) for entry in self._pages]

    def follow_shared_filter(self, index: int) -> None:
        """Make the page use the shared filter again."""
        self._pages[index] = replace(self._with_filter(self._pages[index], self.shared_filter), own_filter=False)

    def _new_document_id(self) -> int:
        """Return an id no page uses yet."""
        self._last_document_id += 1
        return self._last_document_id

    def _update_edit(self, index: int, **changes: object) -> None:
        """Replace fields of the page's edit."""
        entry = self._pages[index]
        edit: PageEdit = replace(entry.page.edit, **changes)  # type: ignore[arg-type]
        self._pages[index] = replace(entry, page=entry.page.with_edit(edit))

    @staticmethod
    def _with_filter(entry: SessionPage, settings: FilterSettings) -> SessionPage:
        """Return ``entry`` using ``settings`` as its filter."""
        edit = replace(entry.page.edit, filter=settings)
        return replace(entry, page=entry.page.with_edit(edit))
