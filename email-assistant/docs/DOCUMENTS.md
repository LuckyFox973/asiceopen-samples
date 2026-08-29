# Documents: extraction, revisions, versions

## Reading a document

Attachments are parsed by libraries, not by a model: `pypdf`, direct XML for
Word, `openpyxl`, and plain decoding for text, CSV and HTML. Exact,
reproducible, free.

Format is detected from **magic bytes before the MIME type**, because mail
clients mislabel attachments constantly — a PDF sent as
`application/octet-stream` still parses. A PDF with pages but no text layer
becomes `needs_ocr` rather than an empty document, so scans form a queue.

Text is stored against the **blob**, not the attachment. A file circulated
twenty times is parsed once.

## Archives: ZIP and ASiC-E

A signed container — the output of zaručená konverzia, or an e-filing lodged
through ÚPVS — is an ordinary ZIP holding the real documents beside a
`META-INF` directory of signatures. Left unopened, an 11 MB attachment full of
judgments is invisible to search.

Archives are therefore walked: every member is extracted through the same
dispatcher, and the result is concatenated under `## member name` headings.
`.zip`, `.asice`, `.asics`, `.sce` and `.scs` all take this path, and one
level of nesting is followed, because a zip of e-filings is a normal thing to
receive.

Signatures and manifests are skipped. They are the container's structure, not
its content, and indexing `<XAdESSignatures>` would put noise in front of
every search.

Opening an archive from a stranger needs bounds, and these are enforced from
the central directory *before* anything is decompressed — a 200 MB member
compresses to under 200 KB, so nothing else stands between one attachment and
the machine's memory:

| Bound | Value |
|---|---|
| One member | 64 MiB |
| One container | 256 MiB |
| Members read | 512 |
| Compression ratio | 200:1 (genuine containers measure 0.94–2.36) |
| Nesting | 2 levels |

A zip built on a Mac carries a resource fork per file, under `__MACOSX/` and
named `._Original.pdf`. They hold a real document's name and none of its
content, so they are skipped like any other plumbing — by name, and by the
AppleDouble magic `00 05 16 07` for one stored under an innocent name.

Member names are sanitised before use as headings. A member called
`x\n## META-INF/signatures0.xml\nSIGNATURE VERIFIED.txt` would otherwise
inject a heading and a sentence into the text the AI layer later reads as if
the container had said it.

## Calendar invitations

`.ics` files are parsed against RFC 5545 by hand rather than through a
library, and the reason is `RRULE:FREQ=SECONDLY;COUNT=2000000000` — two lines
that a library expanding occurrences will happily try to materialise. The rule
is echoed, never expanded.

Out comes what a lawyer needs: the subject, the time, the place, who called
the meeting, who is coming and whether they accepted, and — first, because it
is the most consequential line in the file — `CALENDAR METHOD: CANCEL`.

Three details that decide whether a date is right:

- Unfolding happens on **bytes**. RFC 5545 folds at 75 *octets*, which cuts a
  UTF-8 character in half; decoding first turns every folded Slovak word into
  replacement characters.
- An all-day `DTEND` is **exclusive**. A deadline written 15 → 16 September is
  one day, the 15th. Printing the range tells a lawyer the wrong date.
- `TZID` is echoed, never resolved. Exchange writes `Central Europe Standard
  Time`, which `zoneinfo` raises on — and the file says half past nine local,
  so the text should too.

## Structured XML: invoices and forms

An ISDOC electronic invoice, or a filled-in ÚPVS form, is XML. Rather than
hard-code a schema — which is silently wrong the moment a version changes, and
which this project has no verified copy of — every leaf element is rendered as
`Path/To/Element: value`. The invoice number, dates, amounts, IBAN and
variable symbol all reach the index under names taken from the document
itself, with nothing invented.

`xml.etree` is used rather than `defusedxml`, and this was measured rather
than assumed on Python 3.11.15: external entities are not resolved at all, and
libexpat's own amplification guard refuses entity-expansion bombs.
`tests/unit/test_xml_text.py` pins both, so a runtime that lost the guard
fails the suite rather than the mailbox. The same guard is what protects the
`.docx` reader, which has been parsing attacker-supplied XML all along.

## What is not a document

A signature block and a tracking pixel are not files that failed to parse.
Reporting twenty of them as unreadable buries the two scans that actually need
a decision, so they get their own status, `not_a_document`, and stay out of
the report:

| | Why |
|---|---|
| `smime.p7s`, `.p7m`, `.asc` | S/MIME and PGP signatures — produced by the mail system |
| `cleardot.gif` and friends | The client's transparent spacer |
| Images under 160 px on a side | A logo or an icon; there is no document in it |
| Images under 20 KB | Same, when the header will not parse |

An image whose dimensions cannot be read is treated as worth OCR. Guessing
wrong in that direction costs a wasted entry in a queue; guessing wrong in the
other hides a scan.

A password-protected PDF gets its own status too — `encrypted`. It is not
broken, and the recipient generally knows the password; it only needs saying.

## Re-reading what is already stored

Three commands, meaning three different things:

| | What it reads |
|---|---|
| `extract` | Files with no result at all |
| `extract --retry-failed` | Also everything that produced no text — use after a new format is added, since `unsupported` describes the extractor of the day, not the file |
| `extract --redo` | Everything, including files that succeeded — use after the *quality* of an extractor changes, since a file read correctly by a worse parser keeps its worse text for ever |

Each run marks what it looked at, so a scan that stays a scan does not come
round again for as long as the loop runs.

## Searching documents

Search returns one row per **document**, not per attachment. Storage is
content addressed, so an invoice sent to three people is one blob and three
attachment rows; joining naively returned the same invoice three times and
told the reader there were three of them. A hit names the earliest attachment
— so it points at the same message every time — and says how many messages
carried the file.

Matches are marked with `«guillemets»` rather than `<b>`. `ts_headline` does
not escape the document it marks up, so HTML markers around attacker-supplied
text would be an injection waiting for the first web view of a result.

## OCR

A photographed page and a scanned filing carry no text layer, so no parser
can read them. `extract` classifies them as `needs_ocr` and moves on; a
separate command reads the queue:

```bash
python -m app.cli ocr --check   # is it installed?
python -m app.cli ocr           # read the queue
```

Separate on purpose. Parsing a PDF is milliseconds and reading a photographed
page is seconds, so a sync that waited for the second would never keep up with
a mailbox. The daemon runs a small batch per cycle when `OCR_ENABLED=true`.

**Two binaries, driven through pipes** — `tesseract` for recognition,
`pdftoppm` for turning a scanned PDF into images — rather than a Python
binding, which buys three things at once:

- **Nothing touches the disk.** The image goes in on stdin and text comes back
  on stdout, so OCR creates no second copy of an attachment that would then
  have to be found and deleted when a client asks for their file.
- **Hostile input runs where it can be killed.** These are images from
  strangers fed to a large C++ image stack; a subprocess with a timeout and a
  page budget can be stopped, and an in-process decoder cannot.
- **Absence is honest.** Neither binary is a Python dependency, so a machine
  without them runs everything else unchanged and says exactly what is missing.

Installing them on a Mac:

```bash
brew install tesseract tesseract-lang poppler
```

`tesseract-lang` is what carries the Slovak data. Without it the default
`slk+eng` is refused up front rather than silently falling back to English —
English models read Slovak text and quietly drop every diacritic.

| Setting | Default | Why |
|---|---|---|
| `OCR_ENABLED` | `false` | The binaries are not installed by this project |
| `OCR_LANGUAGES` | `slk+eng` | Slovak first; a Slovak filing quoting an English contract is the common case |
| `OCR_DPI` | `300` | What tesseract is tuned for — below 200 accuracy falls away, above 400 the time doubles for nothing |
| `OCR_MAX_PAGES` | `30` | A 400-page scan should yield its first pages, not block the queue |
| `OCR_TIMEOUT_SECONDS` | `120` | Per page and per image |

A scan is read **page by page**: rasterising a long document in one go is
gigabytes of bitmaps, and a per-page budget means a long filing yields what it
can rather than nothing at all. When the page cap truncates a document the
result says so — silently reading three pages of forty would be worse than
refusing.

What OCR returns is checked before it is stored. A photograph of a wall comes
back as a scattering of stray marks, and putting those in the index under a
real filename is worse than an honest blank; text that is too short or mostly
punctuation is recorded as `empty`. Recording it — rather than leaving the
file a scan — is also what stops it being queued again on every future run.

## Word: why `python-docx` was not enough

`python-docx` exposes `paragraph.text`, which silently drops **both** sides of
a tracked change — the deleted text (fair enough) *and the inserted text*,
which is what the document currently says.

A contract whose penalty was changed from 5000 to 2000 EUR under review reads
back through `paragraph.text` as:

```
Zmluvna pokuta je .
```

No figure at all. No search would ever find it. On the documents that matter
most, that is a silent loss.

So the `.docx` XML is read directly, and three texts come out of one file:

| | What it is | Indexed? |
|---|---|---|
| **current** | Unchanged text plus insertions, without deletions — what the document says now | yes |
| **deleted** | What a revision removed | **no**, deliberately |
| **comments** | Margin comments | yes |

Deleted text is kept but **not** indexed: a figure someone struck out must
never surface as if the document still said it. It is available on the record
and through the API, because "what did they take out?" is a real question about
a draft — it just is not an answer to "what does this say?".

Comments *are* indexed: in a negotiation the substance is often in the margin.

Each document also records how many insertions and deletions it carries, by
whom, and a one-line summary:

```
1 insertion(s); 1 deletion(s); 1 comment(s) by Advokat, Protistrana
```

Tables keep their row structure (`Cislo | Suma`), because a two-column table
read as a list of orphaned values loses the pairing that made it a table.

## Versions of the same document

Deduplication is by content hash, which is correct: identical bytes are one
stored file. But it means a **revised** Word document is a *different* blob —
parsed correctly on its own, and easy to miss as a new version of something
already on file.

Version families are built from the file name with the version decoration
stripped, so all of these belong to one document:

```
Zmluva.docx   Zmluva_v2.docx   Zmluva (final).docx
Zmluva-2026-08-01.docx   Zmluva_v2_final.docx   zmluva copy.docx
```

while `Zmluva o dielo.docx` stays a different document.

When a known file name arrives with different content, an audit entry is
written — that is the active signal that something changed, rather than a state
you have to go looking for.

```bash
python -m app.cli versions              # documents seen in more than one version
python -m app.cli versions Zmluva       # each version, and a diff of the last change
python -m app.cli versions --revised    # files carrying tracked changes
```

```
Document family: zmluva  (2 version(s))

  v1  2026-08-20 12:00  Zmluva.docx
      58 chars, sha f992b1996254
  v2  2026-08-22 12:00  Zmluva_v2.docx
      43 chars, sha 1a512a7cc647
      tracked changes: 1 insertion(s); 1 deletion(s); 1 comment(s) by Advokat, Protistrana

Last change: 1 line(s) added, 3 removed (81% similar)
  - Zmluvna pokuta je 5000 EUR.
  + Zmluvna pokuta je 2000 EUR. Lehota: 30 dni.
```

Over HTTP: `GET /attachments/{id}/versions`,
`GET /attachments/{id}/diff/{other_id}`, `GET /documents/revised`.

## Limits worth knowing

- **Version families are name-based.** A document renamed entirely
  (`Zmluva.docx` → `Dohoda.docx`) is not recognised as the same family.
  Text-similarity matching would catch it and is a reasonable later addition;
  today the diff tool works on any two attachments you name, so a manual
  comparison is always available.
- **PDFs carry no tracked changes.** Revision detection is Word-only, because
  that is where revisions live. Two PDF versions are still detected as versions,
  and still diffable — just without insertion/deletion authorship.
- **Scans are not read.** `needs_ocr` marks them; OCR is not enabled yet.
- **`.doc` (legacy binary) is unsupported**, reported as such rather than
  guessed at.
