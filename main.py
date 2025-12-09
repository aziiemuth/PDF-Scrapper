#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import re
import sys
import urllib.parse as up
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from requests.utils import requote_uri

DEFAULT_TIMEOUT = 25

HDRS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

PDF_PATTERNS = [
    re.compile(r"[?&#]file=([^&#]+)", re.I),
    re.compile(r"(?:defaultUrl|DEFAULT_URL)\s*[:=]\s*['\"]([^'\"]+\.pdf[^'\"]*)['\"]", re.I),
    re.compile(r"PDFViewerApplication\.open\(\s*['\"]([^'\"]+\.pdf[^'\"]*)['\"]\s*\)", re.I),
    re.compile(r"PDFViewerApplicationOptions\.set\(\s*['\"]defaultUrl['\"]\s*,\s*['\"]([^'\"]+\.pdf[^'\"]*)['\"]\s*\)", re.I),
]

def resolve_url(base_url: str, maybe_url: str) -> str:
    if not maybe_url:
        return ""
    decoded = up.unquote(maybe_url)
    m = re.search(r"[?&#]file=([^&#]+)", decoded, re.I)
    if m:
        decoded = up.unquote(m.group(1))
    if decoded.lower().startswith(("http://", "https://", "data:")):
        return decoded
    return up.urljoin(base_url, decoded)

def find_pdf_url_in_html(base_url: str, html: str) -> str:
    for rx in PDF_PATTERNS:
        m = rx.search(html)
        if m:
            return resolve_url(base_url, m.group(1))

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["a", "link", "source"]):
        href = tag.get("href") or tag.get("src")
        if href and ".pdf" in href.lower():
            return resolve_url(base_url, href)

    for meta in soup.find_all("meta"):
        content = meta.get("content")
        if content and ".pdf" in content.lower():
            return resolve_url(base_url, content)

    for tag in soup.find_all(True):
        for _, val in tag.attrs.items():
            if isinstance(val, str) and ".pdf" in val.lower():
                return resolve_url(base_url, val)

    return ""

def extract_pdf_url(session: requests.Session, url: str, referer: str = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    parsed = up.urlparse(url)
    if parsed.path.lower().endswith(".pdf"):
        return requote_uri(url)

    m = re.search(r"[?&#]file=([^&#]+)", url, re.I)
    if m:
        return requote_uri(resolve_url(url, m.group(1)))

    headers = HDRS.copy()
    if referer:
        headers["Referer"] = referer

    r = session.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()

    pdf_url = find_pdf_url_in_html(r.url, r.text)
    if not pdf_url:
        raise RuntimeError("Gagal menemukan URL PDF.")
    return requote_uri(pdf_url)

def pick_filename_from_response(resp: requests.Response, fallback_url: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', cd, re.I)
    if m:
        try:
            return up.unquote(m.group(1)).strip()
        except Exception:
            return m.group(1).strip()

    name = os.path.basename(up.urlparse(fallback_url).path) or "download.pdf"
    return name if name.lower().endswith(".pdf") else name + ".pdf"

def download_pdf(pdf_url: str, out_path: Path, session: requests.Session, referer: str = None, timeout: int = DEFAULT_TIMEOUT):
    headers = HDRS.copy()
    if referer:
        headers["Referer"] = referer

    with session.get(pdf_url, headers=headers, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        out_file = out_path / pick_filename_from_response(r, pdf_url) if out_path.is_dir() else out_path

        with open(out_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

    return str(out_file)

def run():
    ap = argparse.ArgumentParser(description="Download PDF dari halaman PDF.js.")
    ap.add_argument("-o", "--output", default=".", help="Folder atau file tujuan.")
    ap.add_argument("--referer", help="Tambah header Referer jika perlu.")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--insecure", action="store_true")
    args = ap.parse_args()

    url = input("Masukkan URL viewer PDF.js atau file PDF: ").strip()
    if not url:
        print("URL tidak boleh kosong.")
        return

    sess = requests.Session()
    sess.verify = not args.insecure

    print("[i] Mengambil URL PDF...")
    pdf_url = extract_pdf_url(sess, url, referer=args.referer, timeout=args.timeout)
    print(f"[i] PDF ditemukan: {pdf_url}")

    print("[i] Mengunduh...")
    saved = download_pdf(pdf_url, Path(args.output), sess, referer=args.referer, timeout=args.timeout)
    print(f"[✓] Tersimpan: {saved}")

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"[x] Error: {e}")
    input("Tekan Enter untuk keluar...")
