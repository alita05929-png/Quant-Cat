"""Mirror https://firstbroccoli.com public pages and same-origin assets."""
import os
import re
import ssl
import time
from collections import deque
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

BASE = "https://firstbroccoli.com"
HOST = "firstbroccoli.com"
OUT = Path(r"E:\pack\solana\8\firstbroccoli.com")
SEEDS = [
    "https://firstbroccoli.com/",
    "https://firstbroccoli.com/academy/",
    "https://firstbroccoli.com/holding/",
    "https://firstbroccoli.com/ecosystem/",
]
SKIP_PREFIXES = (
    "/wp-admin/",
    "/wp-json/",
    "/xmlrpc.php",
    "/feed/",
    "/comments/",
    "/wp-sitemap",
)
ASSET_EXT = {
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".webm", ".mp3",
    ".json", ".map", ".avif",
}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
CTX = ssl.create_default_context()

ATTR_RE = re.compile(
    r"""(?:href|src|poster|data-src|data-lazy-src|data-background)\s*=\s*(['"])(.*?)\1""",
    re.I,
)
SRCSET_RE = re.compile(r"""srcset\s*=\s*(['"])(.*?)\1""", re.I)
CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.I)
IMPORT_RE = re.compile(r"""@import\s+(?:url\()?['"]([^'"]+)['"]""", re.I)


def normalize(url: str, base: str) -> str | None:
    url = url.strip()
    if not url or url.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    abs_url = urljoin(base, url)
    parts = urlparse(abs_url)
    if parts.scheme not in ("http", "https"):
        return None
    if parts.netloc.lower() not in (HOST, "www." + HOST):
        return None
    path = unquote(parts.path or "/")
    if any(path.startswith(p) or path == p.rstrip("/") for p in SKIP_PREFIXES):
        return None
    # drop fragment; keep query only for fetching, but we key files without query
    return urlunparse(("https", HOST, path, "", parts.query, ""))


def file_key(url: str) -> str:
    parts = urlparse(url)
    path = unquote(parts.path or "/")
    if path.endswith("/") or path == "":
        path = path + "index.html"
    elif "." not in Path(path).name:
        path = path.rstrip("/") + "/index.html"
    return path.lstrip("/")


def local_path(url: str, content_type: str | None = None) -> Path:
    key = file_key(url)
    ext = Path(key).suffix.lower()
    if content_type and "text/html" in content_type and ext not in (".html", ".htm"):
        key = key.rstrip("/") + "/index.html" if not key.endswith("index.html") else key
    return OUT / key.replace("/", os.sep)


def fetch(url: str):
    req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    last_err = None
    for attempt in range(3):
        try:
            with urlopen(req, context=CTX, timeout=60) as resp:
                data = resp.read()
                final = resp.geturl()
                ctype = resp.headers.get("Content-Type", "")
                return data, final, ctype
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            last_err = e
            time.sleep(1 + attempt)
    raise last_err


def extract_urls(text: str, base: str, is_css: bool) -> list[str]:
    found = []
    if not is_css:
        for m in ATTR_RE.finditer(text):
            n = normalize(m.group(2), base)
            if n:
                found.append(n)
        for m in SRCSET_RE.finditer(text):
            for part in m.group(2).split(","):
                bit = part.strip().split(" ")[0]
                n = normalize(bit, base)
                if n:
                    found.append(n)
    for m in CSS_URL_RE.finditer(text):
        n = normalize(m.group(2), base)
        if n:
            found.append(n)
    for m in IMPORT_RE.finditer(text):
        n = normalize(m.group(1), base)
        if n:
            found.append(n)
    return found


def is_page_url(url: str) -> bool:
    path = urlparse(url).path
    if path.startswith(("/wp-content/", "/wp-includes/", "/wp-admin/")):
        return False
    name = Path(path).name
    if not name:
        return True
    ext = Path(name).suffix.lower()
    if ext in ASSET_EXT:
        return False
    return ext in ("", ".html", ".htm", ".php")


def rewrite(text: str, page_url: str, saved: dict[str, Path]) -> str:
    here = local_path(page_url)

    def repl_url(raw: str) -> str:
        n = normalize(raw, page_url)
        if not n:
            return raw
        key = urlunparse((*urlparse(n)[:4], "", ""))  # strip query for lookup variants
        target = saved.get(n) or saved.get(key)
        if target is None:
            # match by path ignoring query
            path = urlparse(n).path
            for k, v in saved.items():
                if urlparse(k).path == path:
                    target = v
                    break
        if target is None:
            return raw
        rel = os.path.relpath(target, start=here.parent).replace("\\", "/")
        return rel

    def attr_sub(m):
        quote, val = m.group(1), m.group(2)
        return m.group(0).replace(val, repl_url(val), 1)

    def srcset_sub(m):
        quote, val = m.group(1), m.group(2)
        bits = []
        for part in val.split(","):
            pieces = part.strip().split()
            if not pieces:
                continue
            pieces[0] = repl_url(pieces[0])
            bits.append(" ".join(pieces))
        return f"srcset={quote}{', '.join(bits)}{quote}"

    def css_sub(m):
        inner = m.group(2).strip()
        return f"url({repl_url(inner)})"

    if Path(file_key(page_url)).suffix.lower() == ".css" or page_url.endswith(".css"):
        text = CSS_URL_RE.sub(css_sub, text)
        text = IMPORT_RE.sub(lambda m: m.group(0).replace(m.group(1), repl_url(m.group(1)), 1), text)
        return text

    text = SRCSET_RE.sub(srcset_sub, text)
    text = ATTR_RE.sub(attr_sub, text)
    text = CSS_URL_RE.sub(css_sub, text)
    return text


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    queue = deque()
    seen = set()
    saved: dict[str, Path] = {}
    bodies: dict[str, tuple[bytes, str]] = {}
    failed = []

    for s in SEEDS:
        queue.append(s)

    while queue:
        url = queue.popleft()
        bare = urlunparse((*urlparse(url)[:4], "", ""))
        if url in seen or bare in seen:
            continue
        seen.add(url)
        seen.add(bare)
        try:
            data, final, ctype = fetch(url)
        except Exception as e:
            failed.append((url, str(e)))
            print(f"FAIL {url} {e}")
            continue
        final_n = normalize(final, url) or url
        path = local_path(final_n, ctype)
        path.parent.mkdir(parents=True, exist_ok=True)
        # store raw first; rewrite text later
        is_text = any(t in (ctype or "") for t in ("text/", "javascript", "json", "xml", "svg"))
        if is_text:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("utf-8", "replace")
            bodies[final_n] = (text, ctype or "")
            for link in extract_urls(text, final_n, "css" in (ctype or "") or final_n.endswith(".css")):
                if link not in seen:
                    if is_page_url(link) or Path(urlparse(link).path).suffix.lower() in ASSET_EXT or "css" in link or "js" in link:
                        queue.append(link)
                    else:
                        # unknown same-origin file referenced as asset
                        queue.append(link)
        else:
            path.write_bytes(data)
        saved[final_n] = path
        saved[url] = path
        saved[bare] = path
        print(f"GET {len(data):8d} {final_n}")

    # second pass: rewrite text files
    for url, (text, ctype) in bodies.items():
        path = saved[url]
        rewritten = rewrite(text, url, saved)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rewritten, encoding="utf-8")

    print("---")
    print(f"saved {len(bodies) + sum(1 for p in OUT.rglob('*') if p.is_file())} file records, files on disk next")
    files = [p for p in OUT.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"files={len(files)} bytes={total}")
    if failed:
        print("FAILED:")
        for u, e in failed:
            print(f"  {u} :: {e}")


if __name__ == "__main__":
    main()
