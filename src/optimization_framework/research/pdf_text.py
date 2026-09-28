"""Bounded PDF extraction subprocess. Never executes document actions or links."""
import io
import json
import resource
import sys


def main():
    # Limit even decompression/parser allocations, not only extracted text.
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    from pypdf import PdfReader
    raw = sys.stdin.buffer.read(24 * 1024**2 + 1)
    if len(raw) > 24 * 1024**2:
        raise ValueError("PDF exceeds extraction input limit")
    reader = PdfReader(io.BytesIO(raw), strict=False)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF cannot be extracted")
    pages, remaining = [], 600_000
    for number, page in enumerate(reader.pages):
        if number >= 100 or remaining <= 0:
            break
        text = page.extract_text() or ""
        pages.append({"page": number + 1, "text": text[:remaining]})
        remaining -= len(text)
    print(json.dumps({"pages": pages, "page_count": len(reader.pages),
                      "truncated": remaining < 0 or len(pages) < len(reader.pages)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # No document text, URLs or internal paths in error output.
        print(type(exc).__name__, file=sys.stderr)
        sys.exit(1)
