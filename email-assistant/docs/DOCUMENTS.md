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
