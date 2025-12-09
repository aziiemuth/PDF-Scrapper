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
    # PDF.js query param: viewer.html?file=<encoded url or relative>
    re.compile(r"[?&#]file=([^&#]+)", re.I),
    # PDF.js config di script: defaultUrl/DEFAULT_URL = '...'
    re.compile(r"(?:defaultUrl|DEFAULT_URL)\s*[:=]\s*['\"]([^'\"]+\.pdf[^'\"]*)['\"]", re.I),
    # Pemanggilan open('...pdf')
    re.compile(r"PDFViewerApplication\.open\(\s*['\"]([^'\"]+\.pdf[^'\"]*)['\"]\s*\)", re.I),
    # window.PDFViewerApplicationOptions.set('defaultUrl', '...')
    re.compile(r"PDFViewerApplicationOptions\.set\(\s*['\"]defaultUrl['\"]\s*,\s*['\"]([^'\"]+\.pdf[^'\"]*)['\"]\s*\)", re.I),
]

def resolve_url(base_url: str, maybe_url: str) -> str:
    if not maybe_url:
        return ""
    # decode percent-encoding lalu pastikan absolute
    decoded = up.unquote(maybe_url)
    # jika masih mengandung query file=... bertingkat, ambil inner-nya
    m = re.search(r"[?&#]file=([^&#]+)", decoded, re.I)
    if m:
        decoded = up.unquote(m.group(1))
    if decoded.lower().startswith(("http://", "https://", "data:")):
        return decoded
    return up.urljoin(base_url, decoded)

def find_pdf_url_in_html(base_url: str, html: str) -> str:
    # 1) Cari pola umum di seluruh source
    for rx in PDF_PATTERNS:
        m = rx.search(html)
        if m:
            return resolve_url(base_url, m.group(1))

    # 2) Parse DOM untuk atribut umum
    soup = BeautifulSoup(html, "html.parser")

    # <a href="...pdf"> / <link href="...pdf">
    for tag in soup.find_all(["a", "link", "source"]):
        href = tag.get("href") or tag.get("src")
        if href and ".pdf" in href.lower():
            return resolve_url(base_url, href)

    # <meta content="...pdf">
    for meta in soup.find_all("meta"):
        content = meta.get("content")
        if content and ".pdf" in content.lower():
            return resolve_url(base_url, content)

    # atribut custom: data-pdf, data-url, data-pdf-url, dll.
    for tag in soup.find_all(True):
        for attr, val in tag.attrs.items():
            if isinstance(val, str) and ".pdf" in val.lower():
                return resolve_url(base_url, val)

    return ""

def extract_pdf_url(session: requests.Session, url: str, referer: str = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    # Langsung jika sudah .pdf
    parsed = up.urlparse(url)
    if parsed.path.lower().endswith(".pdf"):
        return requote_uri(url)

    # Cek param file= di URL
    m = re.search(r"[?&#]file=([^&#]+)", url, re.I)
    if m:
        return requote_uri(resolve_url(url, m.group(1)))

    # Ambil HTML viewer
    hdrs = HDRS.copy()
    if referer:
        hdrs["Referer"] = referer
    r = session.get(url, headers=hdrs, timeout=timeout)
    r.raise_for_status()
    html = r.text

    # Coba dapatkan PDF dari source
    pdf_url = find_pdf_url_in_html(r.url, html)  # pakai r.url untuk follow redirect
    if not pdf_url:
        raise RuntimeError("Gagal menemukan URL PDF dari halaman viewer.")
    return requote_uri(pdf_url)

def pick_filename_from_response(resp: requests.Response, fallback_url: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', cd, re.I)
    if m:
        name = m.group(1)
        try:
            name = up.unquote(name)
        except Exception:
            pass
        return name.strip()
    # fallback dari URL
    path = up.urlparse(fallback_url).path
    name = os.path.basename(path) or "download.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name

def download_pdf(pdf_url: str, out_path: Path, session: requests.Session, referer: str = None, timeout: int = DEFAULT_TIMEOUT):
    hdrs = HDRS.copy()
    if referer:
        hdrs["Referer"] = referer
    with session.get(pdf_url, headers=hdrs, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        if out_path.is_dir():
            fname = pick_filename_from_response(r, pdf_url)
            out_file = out_path / fname
        else:
            out_file = out_path
        # Tulis stream
        with open(out_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)
    return str(out_file)

def run():
    ap = argparse.ArgumentParser(description="Download PDF dari halaman PDF.js (viewer.html/viewer.js).")
    # URL sekarang tidak dari argumen, tapi dari input()
    ap.add_argument("-o", "--output", help="Path file atau folder tujuan. Default: nama file dari server.", default=".")
    ap.add_argument("--referer", help="Header Referer jika situs membutuhkannya.")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Timeout detik (default: 25).")
    ap.add_argument("--insecure", action="store_true", help="Lewati verifikasi SSL (tidak disarankan).")
    args = ap.parse_args()

    # Minta URL dari user saat runtime
    url = input("Masukkan URL viewer PDF.js atau langsung file PDF: ").strip()
    if not url:
        print("[x] URL tidak boleh kosong.", file=sys.stderr)
        return

    sess = requests.Session()
    sess.verify = not args.insecure

    print("[i] Mengambil URL PDF asli...")
    pdf_url = extract_pdf_url(sess, url, referer=args.referer, timeout=args.timeout)
    print(f"[i] Ditemukan URL PDF: {pdf_url}")

    out_path = Path(args.output)
    print("[i] Mengunduh file PDF...")
    saved = download_pdf(pdf_url, out_path, session=sess, referer=args.referer, timeout=args.timeout)
    print(f"[✓] Tersimpan: {saved}")

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"[x] Gagal: {e}", file=sys.stderr)
    # supaya jendela CMD tidak langsung nutup kalau kamu double-click .py
    input("Tekan Enter untuk keluar...")
