"""
bsrn data retrieval module.

Handles FTP connections and automated downloads.
"""

import os
import re
import time
from ftplib import FTP
from bsrn.constants import BSRN_FTP_HOST

BSRN_FILENAME_PATTERN = re.compile(
    r"([a-zA-Z0-9]{3})(\d{2})(\d{2})(?:[._]([a-z0-9_-]+))?(?:\.dat\.gz|\.\d{3}|\.parquet)",
    re.IGNORECASE,
)


def _safe_ftp_quit(ftp):
    """Call ``quit`` on *ftp* if set; ignore errors (cleanup path)."""
    if ftp is None:
        return
    try:
        ftp.quit()
    except Exception:
        pass


def _ftp_relogin(ftp, host, username, password):
    """Reconnect an existing client after a transient failure."""
    try:
        ftp.connect(host)
        ftp.login(user=username, passwd=password)
        ftp.set_pasv(True)
    except Exception:
        pass


def _connect_ftp(host, username, password):
    """Return a logged-in passive-mode FTP client."""
    ftp = FTP(host)
    ftp.set_pasv(True)
    ftp.login(user=username, passwd=password)
    return ftp


def _monthly_dat_gz_filename(station, year, month):
    """Build ``stnMMYY.dat.gz`` (lowercase station, 2-digit month and year)."""
    year_str = str(year)[-2:]
    month_int = int(month)
    return f"{station.lower()}{month_int:02d}{year_str}.dat.gz"


def _filter_station_archive_files(files):
    """Keep only station-to-archive style names from an FTP ``nlst`` result."""
    return [
        f for f in files
        if f.lower().endswith(".dat.gz")
        or (len(f) > 4 and f[-4:].startswith(".") and f[-3:].isdigit())
    ]


def get_bsrn_file_inventory(stations, username, password, host=BSRN_FTP_HOST):
    """
    Connects to bsrn ftp and lists available station-to-archive files.

    Parameters
    ----------
    stations : list
        List of station abbreviations (e.g., ['PAY', 'NYA']).
    username : str
        BSRN FTP username.
    password : str
        BSRN FTP password.
    host : str, default BSRN_FTP_HOST
        FTP host address.

    Returns
    -------
    inventory : dict
        Mapping of station abbreviations to lists of filenames.

    References
    ----------
    .. [1] Driemel, A., et al. (2018). Baseline Surface Radiation Network (BSRN): 
       structure and data description (1992–2017). Earth System Science Data, 
       10(3), 1491-1501.
    """
    inventory = {}
    try:
        with FTP(host) as ftp:
            ftp.set_pasv(True)
            ftp.login(user=username, passwd=password)

            for i, stn in enumerate(stations):
                print(
                    f"[{i + 1}/{len(stations)}] Fetching inventory for station "
                    f"{stn.upper()}...",
                )
                stn_lower = stn.lower()
                success = False

                # Retry logic for the connection
                for attempt in range(2):
                    try:
                        ftp.cwd("/")
                        ftp.cwd(stn_lower)

                        files = ftp.nlst()
                        inventory[stn.upper()] = _filter_station_archive_files(files)
                        success = True
                        break
                    except Exception as e:
                        if attempt == 0:
                            _ftp_relogin(ftp, host, username, password)
                        else:
                            print(f"BSRN FTP: Failed to retrieve {stn} after retry: {e}")

                if not success:
                    inventory[stn.upper()] = []

    except Exception as e:
        print(f"BSRN FTP: Major connection error: {e}")
    return inventory


def download_bsrn_single(station, year, month, local_dir, username,
                         password, host=BSRN_FTP_HOST):
    """
    Download a single BSRN file by specifying station, year, and month.

    Parameters
    ----------
    station : str
        Station abbreviation (e.g., 'pay').
    year : int or str
        Four-digit year (e.g., 2024 or '2024').
    month : int or str
        Month number or string (1-12 or '01'-'12').
    local_dir : str
        The local directory to save the file.
    username : str
        BSRN FTP username.
    password : str
        BSRN FTP password.
    host : str, default BSRN_FTP_HOST
        FTP host address.

    Returns
    -------
    local_path : str or None
        The path to the downloaded file, or None if failed.
    """
    filename = _monthly_dat_gz_filename(station, year, month)
    return download_bsrn_files([filename], local_dir, username, password, host=host)[0]


def download_bsrn_stn(station, local_dir, username,
                      password, host=BSRN_FTP_HOST):
    """
    Download all available station-to-archive files for a specific station.

    Parameters
    ----------
    station : str
        Station abbreviation (e.g., 'pay').
    local_dir : str
        The local directory to save the files.
    username : str
        BSRN FTP username.
    password : str
        BSRN FTP password.
    host : str, default BSRN_FTP_HOST
        FTP host address.

    Returns
    -------
    downloaded_paths : list
        List of paths to the downloaded files.
    """
    inventory = get_bsrn_file_inventory([station], username, password, host=host)
    filenames = inventory.get(station.upper(), [])
    return download_bsrn_files(filenames, local_dir, username, password, host=host)


def download_bsrn_mon(stations, year, month, local_dir,
                      username, password, host=BSRN_FTP_HOST):
    """
    Download station-to-archive files for multiple stations for a specific month and year.

    Parameters
    ----------
    stations : list of str
        List of station abbreviations (e.g., ['pay', 'nya']).
    year : int or str
        Four-digit year (e.g., 2024 or '2024').
    month : int or str
        Month number or string (1-12 or '01'-'12').
    local_dir : str
        The local directory to save the files.
    username : str
        BSRN FTP username.
    password : str
        BSRN FTP password.
    host : str, default BSRN_FTP_HOST
        FTP host address.

    Returns
    -------
    downloaded_paths : list
        List of paths to the downloaded files.
    """
    filenames = [_monthly_dat_gz_filename(stn, year, month) for stn in stations]
    return download_bsrn_files(filenames, local_dir, username, password, host=host)


def download_bsrn_files(filenames, local_dir, username,
                        password, host=BSRN_FTP_HOST, retries=3):
    """
    Download many BSRN files efficiently using a single FTP connection with robust retries.

    Parameters
    ----------
    filenames : list of str
        List of filenames to download (e.g., ['pay0123.dat.gz']).
    local_dir : str
        The local directory to save the files.
    username : str
        BSRN FTP username.
    password : str
        BSRN FTP password.
    host : str, default BSRN_FTP_HOST
        FTP host address.
    retries : int, default 3
        Number of retry attempts for both connection and file transfer errors.

    Returns
    -------
    downloaded_paths : list
        List of paths to the downloaded files.
    """
    os.makedirs(local_dir, exist_ok=True)
    downloaded_paths = []

    ftp = None
    try:
        ftp = _connect_ftp(host, username, password)
        for filename in filenames:
            filename_lower = filename.lower()
            local_path = os.path.join(local_dir, filename_lower)
            station_code = filename_lower[:3]

            success = False
            for attempt in range(retries):
                try:
                    if ftp is None:
                        ftp = _connect_ftp(host, username, password)

                    ftp.cwd("/")
                    ftp.cwd(station_code)

                    with open(local_path, "wb") as f:
                        ftp.retrbinary(f"RETR {filename_lower}", f.write)
                    
                    downloaded_paths.append(local_path)
                    success = True
                    break
                except Exception as e:
                    print(f"BSRN FTP: Attempt {attempt + 1} failed for {filename}: {e}")
                    _safe_ftp_quit(ftp)
                    ftp = None
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff

            if not success:
                downloaded_paths.append(None)

    except Exception as e:
        print(f"BSRN FTP: Major connection error: {e}")
        while len(downloaded_paths) < len(filenames):
            downloaded_paths.append(None)
    finally:
        _safe_ftp_quit(ftp)

    return downloaded_paths


def parse_bsrn_filename(filename):
    """
    Extract station code, year, month, and optional suffix from a filename.

    Parameters
    ----------
    filename : str
        BSRN filename (e.g., 'pay0123.dat.gz') or parquet (e.g., 'ber0198_crs.parquet').

    Returns
    -------
    station : str or None
        Three-letter station code (uppercase).
    year : int or None
        Four-digit calendar year (e.g., 2023).
    month : int or None
        Month number in ``1`` … ``12``.
    suffix : str or None
        Optional filename suffix (e.g., ``nsrdb_aggregated``).
    """
    match = BSRN_FILENAME_PATTERN.match(filename)
    if not match:
        return None, None, None, None

    # Get components
    station = match.group(1).upper()
    month = int(match.group(2))
    yy = int(match.group(3))
    suffix = match.group(4)

    # 4-digit year conversion
    # BSRN convention: 00-79 -> 2000-2079, 80-99 -> 1980-1999
    year = 2000 + yy if yy < 80 else 1900 + yy

    return station, year, month, suffix


def months_from_ftp_filenames(filenames):
    """
    Extract a unique set of (year, month) tuples from a list of BSRN filenames.

    Parameters
    ----------
    filenames : list of str
        List of filenames from BSRN FTP.

    Returns
    -------
    months : list of tuple
        Sorted list of (year, month) tuples.
    """
    ym_set = set()
    for f in filenames:
        _, y, m, _ = parse_bsrn_filename(f)
        if y is not None:
            ym_set.add((y, m))
    return sorted(ym_set)
