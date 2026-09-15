"""Official EIA release archives and explicitly non-tradable futures ranks."""
from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
from lxml import html

ARCHIVE_URL = "https://www.eia.gov/petroleum/supply/weekly/archive/"
FIRST_RELEASE = "2011-08-03"
FUTURES_END = "2024-04-05"
US_SCOPE_BREAK_WEEK = "2016-10-07"
US_SCOPE_BREAK_RELEASE = "2016-10-13"
US_SCOPE_CHANGE_URL = "https://www.eia.gov/todayinEnergy/detail.php?id=28292"
INVENTORY_COLUMNS = ["report_id", "week_ending", "release_date", "knowledge_cutoff", "cushing_mbbl", "us_mbbl", "source_url", "sha256", "timestamp_quality"]
SAMPLES = {
    "2011_08_03": ("2011-07-29", "2011-08-03", 35.953),
    "2020_04_22": ("2020-04-17", "2020-04-22", 59.741),
    "2022_06_17_data": ("2022-06-17", "2022-06-29", 22.043),
    "2022_06_29": ("2022-06-24", "2022-06-29", 21.261),
    "2025_12_29": ("2025-12-19", "2025-12-29", 21.569),
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def knowledge_cutoff(release_date):
    """Conservative information boundary, including daylight-saving transitions."""
    local = datetime.fromisoformat(release_date).replace(hour=23, minute=59, second=59, tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(timezone.utc).isoformat()


def _cached(path: Path, url: str, refresh=False, validator=None):
    """Read immutable checked bytes, or download and validate before replacing."""
    manifest_path = path.with_suffix(path.suffix + ".source.json")
    if path.exists() and not refresh:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if manifest_path.exists():
            meta = json.loads(manifest_path.read_text())
            if digest != meta.get("sha256") or meta.get("url") != url:
                raise ValueError(f"Cached source SHA256/URL mismatch: {path}")
        else:
            meta = {"url": url, "sha256": digest, "downloaded_at_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "timestamp_method": "initial-download file mtime"}
            if validator:
                validator(data)
            _json(manifest_path, meta)
        if validator:
            validator(data)
        return data, meta
    errors = []
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (academic EIA archive research)"}), timeout=35) as response:
                data = response.read()
                final_url = response.geturl()
            if validator:
                validator(data)
            meta = {"url": url, "resolved_url": final_url, "sha256": hashlib.sha256(data).hexdigest(), "downloaded_at_utc": _now(), "bytes": len(data), "prior_attempt_errors": errors}
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".download")
            temporary.write_bytes(data)
            temporary.replace(path)
            _json(manifest_path, meta)
            return data, meta
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Failed {url} after 3 attempts: {errors}")


def parse_archive(data: bytes, cutoff: str):
    doc = html.fromstring(data)
    month = None
    records = []
    months = {name: i for i, name in enumerate(calendar.month_name) if name}
    # The official index has orphan <td> cells without an opening <tr> for
    # 2016-11-30 / 2017-11-29. Walk cells in document order so those real
    # publications are retained rather than silently losing two weeks.
    for cell in doc.xpath('//table[contains(@class,"contable")]//td'):
        text = cell.text_content().strip()
        if text in months:
            month = months[text]
        for a in cell.xpath('.//a[@href]'):
            href = a.get("href")
            match = re.search(r"/archive/(\d{4})/([^/]+)/wpsr_", href)
            if not match:
                continue
            next_cell = cell.getnext()
            if month is None or next_cell is None or next_cell.tag != "td":
                raise ValueError("Archive date context is missing")
            year, report_id = int(match.group(1)), match.group(2)
            release = datetime(year, month, int(a.text_content().strip())).date()
            if not FIRST_RELEASE <= release.isoformat() <= cutoff:
                continue
            end = next_cell.text_content().strip()
            if "/" in end:
                data_month, data_day = map(int, end.split("/"))
            else:
                data_month, data_day = month, int(end)
            data_year = year - int(data_month > month)
            week = datetime(data_year, data_month, data_day).date()
            if week.weekday() != 4 or week > release:
                raise ValueError(f"Invalid archive week/release pair: {report_id}: {week}/{release}")
            report_url = urljoin(ARCHIVE_URL, href)
            records.append({"report_id": report_id, "week_ending": week.isoformat(), "release_date": release.isoformat(), "report_url": report_url, "source_url": urljoin(report_url, "csv/table9.csv")})
    if not records:
        raise ValueError("No official archive entries in requested date range")
    if len({r["report_id"] for r in records}) != len(records):
        raise ValueError("Duplicate report ids in archive")
    return sorted(records, key=lambda r: (r["release_date"], r["week_ending"]))


def parse_inventory_csv(data: bytes, expected_week: str):
    rows = list(csv.reader(io.StringIO(data.decode("cp1252"))))
    if not rows or rows[0][:2] != ["STUB_1", "STUB_2"]:
        raise ValueError("Unexpected table9 CSV header")
    observed = pd.to_datetime(rows[0][2], format="%m/%d/%y").date().isoformat()
    if observed != expected_week:
        raise ValueError(f"Archive/header week mismatch: {expected_week} vs {observed}")
    values = {}
    for row in rows[1:]:
        if len(row) < 3 or row[0].strip() != "Stocks (Million Barrels)":
            continue
        label = row[1].strip()
        if label in {"Cushing, Oklahoma", "Commercial", "Crude Oil (including SPR)", "SPR"}:
            if label in values:
                raise ValueError(f"Duplicate inventory row {label}")
            number = float(row[2].replace(",", ""))
            if not math.isfinite(number) or number <= 0:
                raise ValueError(f"Missing/invalid inventory {label}: {row[2]}")
            values[label] = number
    required = {"Cushing, Oklahoma", "Commercial", "Crude Oil (including SPR)", "SPR"}
    if not required <= values.keys():
        raise ValueError(f"Missing required stock rows: {required - values.keys()}")
    # Some 2020 original CSV totals are rounded to .01 million while the two
    # components retain .001; their combined rounding envelope is .006 million.
    if abs(values["Crude Oil (including SPR)"] - values["SPR"] - values["Commercial"]) > .006000001:
        raise ValueError("National commercial + SPR does not reconcile to crude total")
    if values["Cushing, Oklahoma"] > values["Commercial"]:
        raise ValueError("Cushing exceeds national ex-SPR crude stocks")
    return values["Cushing, Oklahoma"], values["Commercial"]


def parse_inventory_pdf(data: bytes, expected_week: str):
    """Same-release Table 4 fallback; retain the PDF's lower .1m precision."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) != 1:
        raise ValueError("Expected a one-page original Table 4 PDF")
    lines = [re.sub(r"\s+", " ", line).strip() for line in reader.pages[0].extract_text(extraction_mode="layout").splitlines()]
    if not any("Table 4." in line and "Stocks of Crude Oil" in line for line in lines) or "(Million Barrels)" not in lines:
        raise ValueError("PDF identity/units mismatch")
    dated_lines = [line for line in lines if re.search(r"\d{1,2}/\d{1,2}/\d{2}", line)]
    if not dated_lines:
        raise ValueError("No dated PDF column headers")
    date = re.search(r"\d{1,2}/\d{1,2}/\d{2}", dated_lines[0]).group()
    if datetime.strptime(date, "%m/%d/%y").date().isoformat() != expected_week:
        raise ValueError("PDF current-week header does not match archive observation")
    values = {}
    for key, prefix in [("cushing", r"Cushing\d*"), ("us", r"Commercial \(Excluding SPR\)\d*"), ("total", "Crude Oil"), ("spr", r"SPR\d*")]:
        matches = [re.match(prefix + r"\s*\.+\s*([\d,]+\.\d+)", line) for line in lines]
        matches = [m for m in matches if m]
        if len(matches) != 1:
            raise ValueError(f"PDF inventory label is missing or ambiguous: {key}")
        values[key] = float(matches[0].group(1).replace(",", ""))
    if not 0 < values["cushing"] < values["us"] or abs(values["total"] - values["spr"] - values["us"]) > .150001:
        raise ValueError("PDF stock magnitudes or .1m rounding reconciliation failed")
    return values["cushing"], values["us"]


def parse_latest_inventory(data: bytes, series: str):
    """Parse each explicit weekly end-date/value pair, not column positions as dates."""
    doc = html.fromstring(data)
    title = " ".join(doc.xpath("//title/text()"))
    expected_title = "Cushing, OK" if series == "cushing_mbbl" else "U.S."
    expected_scope = "excluding SPR and including Lease Stock of Crude Oil" if series == "us_including_lease_mbbl" else "excluding SPR of Crude Oil"
    if expected_title not in title or expected_scope not in title or "Thousand Barrels" not in title:
        raise ValueError("Latest history series identity/units mismatch")
    records = []
    for month_cell in doc.xpath('//td[@class="B6"]'):
        month_label = month_cell.text_content().strip()
        match = re.fullmatch(r"(\d{4})-([A-Za-z]{3})", month_label)
        if not match:
            raise ValueError(f"Invalid latest series month: {month_label}")
        year, month = match.groups()
        month_number = datetime.strptime(month, "%b").month
        cell = month_cell.getnext()
        while cell is not None:
            value_cell = cell.getnext()
            if cell.get("class") != "B5" or value_cell is None or value_cell.get("class") != "B3":
                raise ValueError("Unexpected latest series date/value mapping")
            date, value = cell.text_content().strip(), value_cell.text_content().strip().replace(",", "")
            if date:
                observed = datetime.strptime(f"{year}/{date}", "%Y/%m/%d").date()
                if observed.month != month_number or observed.weekday() != 4:
                    raise ValueError("Latest inventory week is not a Friday in its labeled month")
                if value not in {"", "-", "--", "NA", "W"}:
                    number = float(value) / 1000
                    if not math.isfinite(number) or number <= 0:
                        raise ValueError("Invalid latest inventory numeric observation")
                    records.append({"week_ending": observed.isoformat(), series: number})
            elif value:
                raise ValueError("Latest inventory value has no observation date")
            cell = value_cell.getnext()
    frame = pd.DataFrame(records)
    if not len(frame) or frame.week_ending.duplicated().any():
        raise ValueError("Empty/duplicated latest inventory series")
    return frame


def crosscheck_latest_inventory(root: Path, original: pd.DataFrame, refresh=False):
    """Diagnostic only: a latest-history discrepancy never overwrites release data."""
    sources, failures, frames = [], [], []
    for column, series in [("cushing_mbbl", "W_EPC0_SAX_YCUOK_MBBL"), ("us_mbbl", "WCESTUS1"), ("us_including_lease_mbbl", "W_EPC0_SAX_NUS_MBBL")]:
        url = f"https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s={series}&f=W"
        path = root / "data/raw/inventory_latest" / (series + ".html")
        try:
            data, meta = _cached(path, url, refresh, lambda b: parse_latest_inventory(b, column))
            frames.append(parse_latest_inventory(data, column))
            sources.append({**meta, "series": series, "column": column, "raw_file": str(path.relative_to(root))})
        except Exception as exc:
            failures.append({"series": series, "url": url, "error": f"{type(exc).__name__}: {exc}"})
    audit = {"status": "complete" if not failures else "partial", "vintage": "latest history as downloaded, diagnostic only", "sources": sources, "failures": failures, "archived_values_overwritten": False}
    if len(frames) != 3:
        return audit
    latest = frames[0].merge(frames[1], on="week_ending", how="outer", validate="one_to_one")
    latest = latest.merge(frames[2], on="week_ending", how="outer", validate="one_to_one")
    comparison = original[["report_id", "week_ending", "cushing_mbbl", "us_mbbl", "source_url"]].merge(latest, on="week_ending", how="left", suffixes=("_archive", "_latest"), validate="one_to_one")
    comparison["archive_precision_mbbl"] = comparison.source_url.map(lambda s: .1 if s.endswith(".pdf") else .001)
    comparison["us_same_scope_latest_mbbl"] = comparison.us_mbbl_latest.where(comparison.week_ending >= US_SCOPE_BREAK_WEEK, comparison.us_including_lease_mbbl)
    comparison["us_same_scope_difference_mbbl"] = (comparison.us_same_scope_latest_mbbl - comparison.us_mbbl_archive).round(6)
    differences = []
    counts = {}
    for column in ["cushing_mbbl", "us_mbbl"]:
        delta = comparison[column + "_latest"] - comparison[column + "_archive"]
        comparison[column + "_difference"] = delta.round(6)
        mask = delta.abs() > 1e-8
        counts[column] = int(mask.sum())
        for row in comparison.loc[mask].to_dict("records"):
            classification = "vintage_or_source_difference_requires_review"
            if row["archive_precision_mbbl"] == .1 and abs(row[column + "_difference"]) <= .050001:
                classification = "within_printed_pdf_rounding"
            elif column == "us_mbbl" and row["week_ending"] < US_SCOPE_BREAK_WEEK and abs(row["us_same_scope_difference_mbbl"]) < 1e-8:
                classification = "lease_scope_change_confirmed_against_including_lease_series"
            differences.append({"report_id": row["report_id"], "week_ending": row["week_ending"], "series": column, "archived_value": row[column + "_archive"], "latest_value": row[column + "_latest"], "difference_mbbl": row[column + "_difference"], "classification": classification})
    path = root / "data/processed/inventory_latest_crosscheck.csv"
    comparison.to_csv(path, index=False)
    scope_path = root / "data/raw/inventory_latest/lease_scope_change_2016.html"
    try:
        _, scope_meta = _cached(scope_path, US_SCOPE_CHANGE_URL, refresh)
        scope_source = {**scope_meta, "raw_file": str(scope_path.relative_to(root))}
    except Exception as exc:
        scope_source = {"url": US_SCOPE_CHANGE_URL, "error": f"{type(exc).__name__}: {exc}"}
    audit.update(compared_rows=len(comparison), missing_latest_by_column=comparison[["cushing_mbbl_latest", "us_mbbl_latest", "us_same_scope_latest_mbbl"]].isna().sum().to_dict(), differing_values_by_series=counts, differences=differences, difference_classification_counts={kind: sum(d["classification"] == kind for d in differences) for kind in sorted({d["classification"] for d in differences})}, processed_file=str(path.relative_to(root)), processed_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), national_scope_change={"first_observation_excluding_lease": US_SCOPE_BREAK_WEEK, "first_release_excluding_lease": US_SCOPE_BREAK_RELEASE, "methodology_source": scope_source, "explanation": "Original national commercial inventories include lease stocks before this break. EIA later backcasted WCESTUS1 excluding lease stocks; historical including-lease series confirms the earlier archived values. Do not train a continuous national inventory feature across this unharmonized break."}, limitations=["Latest history is not a point-in-time input. Differences may reflect revisions, archived-source defects, or printed precision; no discrepancy is silently corrected."])
    return audit


def inventory_supporting_sources(root: Path, frame: pd.DataFrame, refresh=False):
    """Retain the same-release tables used to audit two mixed-precision totals."""
    sources = []
    for report_id in ["2020_03_18", "2020_04_15"]:
        match = frame.loc[frame.report_id.eq(report_id)]
        if match.empty:
            continue
        record = match.iloc[0]
        path = root / "data/raw/inventory" / (report_id + "_table4.csv")
        url = urljoin(record.source_url, "table4.csv")
        def validate(data):
            rows = list(csv.reader(io.StringIO(data.decode("cp1252"))))
            if not rows or rows[0][0] != "STUB_1":
                raise ValueError("Unexpected supporting Table 4 header")
            week = pd.to_datetime(rows[0][1], format="%m/%d/%y").date().isoformat()
            if week != record.week_ending:
                raise ValueError("Supporting Table 4 observation week differs")
            values = {r[0].strip(): float(r[1].replace(",", "")) for r in rows[1:] if len(r)>1 and r[0] in {"Cushing", "Commercial (Excluding SPR)"}}
            if abs(values.get("Cushing", float("inf"))-record.cushing_mbbl)>1e-9 or abs(values.get("Commercial (Excluding SPR)", float("inf"))-record.us_mbbl)>1e-9:
                raise ValueError("Supporting Table 4 stocks differ from publication inputs")
        data, meta = _cached(path, url, refresh, validate)
        validate(data)
        sources.append({**meta, "raw_file": str(path.relative_to(root)), "report_id": report_id,
                        "purpose": "Same-release crude-total rounding reconciliation; stocks match Table 9", "passed": True})
    return sources


def fetch_inventory(root: Path, cutoff: str, refresh=False):
    root = Path(root)
    index_path = root / "data/raw/eia_archive_index.html"
    index, index_meta = _cached(index_path, ARCHIVE_URL, refresh, lambda b: parse_archive(b, cutoff))
    expected = parse_archive(index, cutoff)
    output, failures, source_hashes, fallbacks = [], [], [], []

    def fetch(record):
        path = root / "data/raw/inventory" / (record["report_id"] + "_table9.csv")
        data, meta = _cached(path, record["source_url"], refresh)
        source_url = record["source_url"]
        fallback = None
        precision = .001
        try:
            cushing, national = parse_inventory_csv(data, record["week_ending"])
        except ValueError as exc:
            rejected = {**meta, "raw_file": str(path.relative_to(root)), "validation_error": str(exc)}
            source_url = urljoin(record["report_url"], "pdf/table4.pdf")
            path = root / "data/raw/inventory" / (record["report_id"] + "_table4.pdf")
            data, meta = _cached(path, source_url, refresh, lambda b: parse_inventory_pdf(b, record["week_ending"]))
            cushing, national = parse_inventory_pdf(data, record["week_ending"])
            precision = .1
            fallback = {"report_id": record["report_id"], "rejected_source": rejected, "replacement_source_url": source_url, "reason": "Same-release dated original Table 4 PDF; lower 0.1 million barrel precision retained."}
        row = {k: record[k] for k in ("report_id", "week_ending", "release_date", "source_url")}
        row["source_url"] = source_url
        row.update(knowledge_cutoff=knowledge_cutoff(record["release_date"]), cushing_mbbl=cushing, us_mbbl=national, sha256=meta["sha256"], timestamp_quality="release_date_only_ny_end_of_day")
        return row, {**meta, "report_id": record["report_id"], "raw_file": str(path.relative_to(root)), "value_precision_mbbl": precision}, fallback

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(fetch, record): record for record in expected}
        for n, future in enumerate(as_completed(futures), 1):
            try:
                row, meta, fallback = future.result()
                output.append(row)
                source_hashes.append(meta)
                if fallback:
                    fallbacks.append(fallback)
            except Exception as exc:
                failures.append({**futures[future], "error": f"{type(exc).__name__}: {exc}"})
            if n % 50 == 0 or n == len(expected):
                print(f"EIA inventory: {n}/{len(expected)} checked; {len(failures)} failures", flush=True)
    frame = pd.DataFrame(output, columns=INVENTORY_COLUMNS).sort_values(["release_date", "week_ending", "report_id"])
    discarded = frame.loc[frame.duplicated("week_ending", keep="first")].to_dict("records")
    frame = frame.drop_duplicates("week_ending", keep="first").reset_index(drop=True)
    present = set(frame.week_ending)
    expected_weeks = sorted({r["week_ending"] for r in expected})
    calendar_weeks = [d.date().isoformat() for d in pd.date_range(expected_weeks[0], expected_weeks[-1], freq="W-FRI")]
    samples = []
    for report_id, (week, release, value) in SAMPLES.items():
        if release > cutoff:
            continue
        match = frame.loc[frame.report_id == report_id]
        passed = len(match) == 1 and match.iloc[0].week_ending == week and match.iloc[0].release_date == release and abs(match.iloc[0].cushing_mbbl - value) < 1e-9
        samples.append({"report_id": report_id, "week_ending": week, "release_date": release, "expected_cushing_mbbl": value, "passed": bool(passed)})
    audit = {
        "status": "complete" if not failures and not set(calendar_weeks) - present and all(s["passed"] for s in samples) else "partial",
        "generated_at": _now(), "cutoff": cutoff, "units": "million barrels", "rows": len(frame),
        "start": frame.week_ending.min() if len(frame) else None, "end": frame.week_ending.max() if len(frame) else None,
        "expected_reports": len(expected), "expected_week_count": len(expected_weeks), "expected_weeks": expected_weeks,
        "failures": failures, "missing_expected_weeks": sorted(set(expected_weeks) - present),
        "calendar_weeks_absent_from_index": sorted(set(calendar_weeks) - set(expected_weeks)),
        "discarded_later_reports": discarded, "fixed_source_samples": samples,
        "multiple_reports_same_release": frame.loc[frame.release_date.duplicated(False), ["report_id", "release_date", "week_ending"]].to_dict("records"),
        "archive_index": index_meta, "source_hashes": sorted(source_hashes, key=lambda r: r["report_id"]),
        "source_fallbacks": fallbacks,
        "limitations": [
            "Official archived reports reconstructed by release date; archive index begins August 3, 2011. Not a minute-level immutable-vintage service.",
            "Knowledge cutoff is release-date 23:59:59 America/New_York, converted to UTC, not a claim of original publication time.",
            "Two observation weeks may be published on one day; both reports retain that same knowledge cutoff.",
            "Cushing and U.S. commercial crude exclude SPR; total stock scope can include pipeline fill/in-transit barrels and is not tank working-capacity utilization.",
            "No missing report or numeric value is interpolated, synthesized, or replaced with latest-vintage history.",
            "Same-release PDF fallback retains its printed 0.1 million barrel precision; source_fallbacks identifies exceptions. CSV crude-total reconciliation allows 0.006 million for mixed source rounding.",
            "National commercial stocks include lease stocks through observation week 2016-09-30, then exclude them from week 2016-10-07 (released 2016-10-13). Cushing is unaffected; national features require a scope-break gate.",
        ],
    }
    processed = root / "data/processed"
    processed.mkdir(parents=True, exist_ok=True)
    frame.to_csv(processed / "inventory.csv", index=False)
    audit["processed_sha256"] = hashlib.sha256((processed / "inventory.csv").read_bytes()).hexdigest()
    audit["latest_vintage_crosscheck"] = crosscheck_latest_inventory(root, frame, refresh)
    audit["supporting_sources"] = inventory_supporting_sources(root, frame, refresh)
    _json(processed / "inventory_audit.json", audit)
    _json(root / "data/raw/inventory_download_audit.json", {"generated_at": _now(), "expected": expected, "failures": failures, "source_fallbacks": fallbacks})
    return frame, audit


def parse_public_futures(data: bytes, rank: str):
    page = data.decode("utf-8", errors="replace")
    if f"Crude Oil Future Contract {rank[-1]}" not in page or "Dollars per Barrel" not in page:
        raise ValueError("Public futures source identity/units mismatch")
    tables = pd.read_html(io.StringIO(page))
    matches = [t for t in tables if list(t.columns) == ["Week Of", "Mon", "Tue", "Wed", "Thu", "Fri"]]
    if len(matches) != 1:
        raise ValueError("Expected one Mon-Fri futures table")
    records = []
    for _, row in matches[0].iterrows():
        label = str(row["Week Of"]).strip()
        if label in {"nan", "Week Of"}:
            continue
        match = re.fullmatch(r"(\d{4})\s+([A-Za-z]{3})-\s*(\d{1,2})\s+to\s+([A-Za-z]{3})-\s*(\d{1,2})", label)
        if not match:
            raise ValueError(f"Invalid futures week label: {label}")
        year, month, day, end_month, end_day = match.groups()
        monday = pd.to_datetime(f"{year}-{month}-{day}", format="%Y-%b-%d")
        friday = monday + pd.Timedelta(days=4)
        if monday.weekday() != 0 or friday.strftime("%b") != end_month or friday.day != int(end_day):
            raise ValueError(f"Futures weekdays do not match row boundaries: {label}")
        for offset, dayname in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
            value = pd.to_numeric(row[dayname], errors="coerce")
            if pd.notna(value):
                if not math.isfinite(value):
                    raise ValueError("Nonfinite futures price")
                records.append({"trade_date": (monday + pd.Timedelta(days=offset)).date().isoformat(), rank: float(value)})
    frame = pd.DataFrame(records)
    if frame.trade_date.duplicated().any() or frame.trade_date.max() > FUTURES_END:
        raise ValueError("Unexpected duplicate or post-discontinuation public futures observations")
    return frame


def fetch_public_futures(root: Path, cutoff: str, refresh=False):
    root = Path(root)
    frames, sources, failures = [], [], []
    for i in range(1, 5):
        rank = f"F{i}"
        url = f"https://www.eia.gov/dnav/pet/hist/RCLC{i}D.htm"
        path = root / "data/raw/public_futures" / f"RCLC{i}D.html"
        try:
            data, meta = _cached(path, url, refresh, lambda b: parse_public_futures(b, rank))
            frame = parse_public_futures(data, rank)
            frame = frame.loc[frame.trade_date <= min(cutoff, FUTURES_END)]
            frames.append(frame)
            sources.append({**meta, "rank": rank, "raw_file": str(path.relative_to(root)), "rows": len(frame)})
        except Exception as exc:
            failures.append({"rank": rank, "url": url, "error": f"{type(exc).__name__}: {exc}"})
    result = pd.DataFrame({"trade_date": pd.Series(dtype=str)})
    for frame in frames:
        result = result.merge(frame, on="trade_date", how="outer", validate="one_to_one")
    for rank in ["F1", "F2", "F3", "F4"]:
        if rank not in result:
            result[rank] = float("nan")
    result = result.sort_values("trade_date").reset_index(drop=True)
    audit = {"status": "complete" if not failures else "partial", "generated_at": _now(), "rows": len(result), "start": result.trade_date.min() if len(result) else None, "end": result.trade_date.max() if len(result) else None, "units": "USD per barrel", "cutoff": cutoff, "source_discontinued_after": FUTURES_END, "actual_contract_ids": False, "use": "descriptive_only", "sources": sources, "failures": failures, "missing_by_column": result.isna().sum().to_dict(), "limitations": ["Public EIA delivery-order ranks, not actual single-contract holdings or a tradable rolled portfolio.", "Futures series stop after April 5, 2024. No extrapolation, interpolation, synthetic identifiers, or executable-fill claims.", "Negative and zero prices are valid; do not apply positive-price or logarithmic-return validation.", "Trade date is an observation label, not provider publication time. Use conservative publication/availability assumptions for descriptive joins."]}
    (root / "data/processed").mkdir(parents=True, exist_ok=True)
    result.to_csv(root / "data/processed/public_futures.csv", index=False)
    audit["processed_sha256"] = hashlib.sha256((root / "data/processed/public_futures.csv").read_bytes()).hexdigest()
    _json(root / "data/processed/public_futures_audit.json", audit)
    return result, audit
