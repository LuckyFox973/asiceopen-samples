"""Optical character recognition for scans and photographs.

Deliberately not a library binding.  Two binaries do the work — ``tesseract``
for the recognition and ``pdftoppm`` to turn a scanned PDF into images — and
both are driven through pipes, which buys three things at once.

Nothing touches the disk.  A photographed contract goes in on stdin and text
comes back on stdout, so OCR creates no second copy of an attachment that
would then have to be found and deleted when a client asks.

Hostile input runs somewhere it can be killed.  These are images from
strangers, fed to a large C++ image stack; a subprocess with a timeout and a
page budget can be stopped, and an in-process decoder cannot.

And the system stays honest about not having it.  Neither binary is a Python
dependency, so a machine without them runs everything else unchanged and says
plainly what is missing.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

from app.core.logging import get_logger

log = get_logger(__name__)

TESSERACT = "tesseract"
PDFTOPPM = "pdftoppm"

# Below this a page produced nothing worth storing: OCR on a blank scan
# returns a few stray marks, not text.
MIN_PAGE_CHARS = 8


@dataclass(frozen=True)
class OcrCapability:
    """What this machine can actually do, discovered rather than assumed."""

    tesseract: str | None = None
    rasteriser: str | None = None
    languages: tuple[str, ...] = ()

    @property
    def reads_images(self) -> bool:
        return self.tesseract is not None

    @property
    def reads_pdfs(self) -> bool:
        return self.reads_images and self.rasteriser is not None

    def missing(self) -> list[str]:
        gaps = []
        if self.tesseract is None:
            gaps.append("tesseract")
        if self.rasteriser is None:
            gaps.append("poppler (pdftoppm)")
        return gaps

    def unsupported(self, requested: str) -> list[str]:
        """Requested languages this installation has no data for."""
        if not self.languages:
            return []
        return [part for part in requested.split("+") if part and part not in self.languages]


@dataclass
class OcrResult:
    text: str = ""
    pages: int = 0
    engine: str = ""
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


@lru_cache(maxsize=1)
def capability() -> OcrCapability:
    """Probe the machine once. Call :func:`forget_capability` after installing."""
    tesseract = shutil.which(TESSERACT)
    rasteriser = shutil.which(PDFTOPPM)
    languages: tuple[str, ...] = ()

    if tesseract:
        try:
            listed = subprocess.run(
                [tesseract, "--list-langs"],
                capture_output=True,
                timeout=30,
                check=False,
            )
            languages = tuple(
                line.strip()
                for line in listed.stdout.decode("utf-8", "replace").splitlines()[1:]
                if line.strip()
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            log.warning("ocr.list_langs_failed", error=str(exc))

    return OcrCapability(tesseract=tesseract, rasteriser=rasteriser, languages=languages)


def forget_capability() -> None:
    """Drop the cached probe — used by tests, and after installing the tools."""
    capability.cache_clear()


def _run(argv: list[str], payload: bytes, timeout: int) -> tuple[int, bytes, bytes]:
    completed = subprocess.run(
        argv,
        input=payload,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def read_image(
    data: bytes,
    languages: str = "slk+eng",
    timeout: int = 120,
) -> OcrResult:
    """Text from one image, via a pipe in and a pipe out."""
    tools = capability()
    if not tools.reads_images:
        return OcrResult(error="tesseract is not installed")

    try:
        code, out, err = _run(
            [tools.tesseract, "stdin", "stdout", "-l", languages],
            data,
            timeout,
        )
    except subprocess.TimeoutExpired:
        return OcrResult(error=f"gave up after {timeout}s")
    except OSError as exc:  # pragma: no cover - the probe just said it exists
        return OcrResult(error=f"could not be run: {exc}")

    if code != 0:
        message = err.decode("utf-8", "replace").strip().splitlines()
        return OcrResult(error=message[-1] if message else f"exit status {code}")

    return OcrResult(text=out.decode("utf-8", "replace"), pages=1, engine="tesseract")


def rasterise_page(
    data: bytes,
    page: int,
    dpi: int = 300,
    timeout: int = 120,
) -> bytes | None:
    """One page of a PDF as a PNG, on stdout.

    Omitting the output root is what makes pdftoppm write the image to stdout
    instead of to numbered files beside the process.
    """
    tools = capability()
    if tools.rasteriser is None:
        return None
    try:
        code, out, _err = _run(
            [
                tools.rasteriser,
                "-png",
                "-r",
                str(dpi),
                "-f",
                str(page),
                "-l",
                str(page),
                "-singlefile",
                "-",
            ],
            data,
            timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return out if code == 0 and out else None


def read_pdf(
    data: bytes,
    languages: str = "slk+eng",
    dpi: int = 300,
    max_pages: int = 30,
    timeout: int = 120,
) -> OcrResult:
    """Text from a scanned PDF, one page at a time.

    Page by page on purpose: a 400-page scan rasterised in one go is gigabytes
    of bitmaps, and a per-page budget means a long document yields its first
    pages rather than nothing at all.
    """
    tools = capability()
    if not tools.reads_images:
        return OcrResult(error="tesseract is not installed")
    if not tools.reads_pdfs:
        return OcrResult(error="poppler (pdftoppm) is not installed, so PDFs cannot be imaged")

    total = _page_count(data)
    if total == 0:
        return OcrResult(error="the PDF has no pages to image")

    limit = min(total, max_pages)
    chunks: list[str] = []
    notes: list[str] = []
    read = 0

    for page in range(1, limit + 1):
        image = rasterise_page(data, page, dpi=dpi, timeout=timeout)
        if image is None:
            notes.append(f"page {page} could not be imaged")
            continue
        result = read_image(image, languages=languages, timeout=timeout)
        if result.error:
            notes.append(f"page {page}: {result.error}")
            continue
        read += 1
        text = result.text.strip()
        if len(text) >= MIN_PAGE_CHARS:
            chunks.append(f"[page {page}]\n{text}")

    if total > limit:
        # Never silently truncate: a reader must know the tail was not read.
        notes.append(f"only the first {limit} of {total} pages were read")

    return OcrResult(
        text="\n\n".join(chunks),
        pages=read,
        engine="tesseract+pdftoppm",
        notes=notes,
    )


def _page_count(data: bytes) -> int:
    import io

    from pypdf import PdfReader

    try:
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001 - a damaged PDF is a result, not a crash
        return 0
