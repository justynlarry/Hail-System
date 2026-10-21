"""Works out which NOAA file covers a local day and downloads it.

The file is MESH_Max_1440min stamped at the local midnight that ends the day, converted
to UTC with the time zone given.  Zone is an argument, this module has no Local Time in it.
S3 resets connections now and then (three times in about 100 requests when checked), so a 
download retries and is written to a .part file and renamed, a half file is never left under
the real name.
"""


import http.client
import os
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime, time as clock, timedelta, timezone

BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
PRODUCT = "MESH_Max_1440min_00.50"

HTTP_ATTEMPTS = 6
HTTP_BACKOFF = 6
HTTP_TIMEOUT = 180


class NotAvailable(Exception):
    """NOAA has no file at the key (HTTP 404), so retrying will not help."""


class FetchError(Exception):
    """The download failed on every attempt."""


def day_stamp(day, tz):
    """UTC datetime of the file that covers local date 'day': the next local midnight."""
    end = datetime.combine(day + timedelta(days=1), clock(0), tzinfo=tz)
    return end.astimezone(timezone.utc)


def source_key(stamp):
    """Key inside the bucket for a UTC stamp."""
    text = stamp.strftime("%Y%m%d-%H%M%S")
    return f"CONUS/{PRODUCT}/{text[:8]}/MRMS_{PRODUCT}_{text}.grib2.gz"


def download(url, dest, *, attempts=HTTP_ATTEMPTS, backoff=HTTP_BACKOFF,
             timeout=HTTP_TIMEOUT, opener=urllib.request.urlopen, sleep=time.sleep):
    """Download url to dest and return the byte count.  NotAvailable on 404, FetchError otherwise."""
    part = dest + ".part"
    for attempt in range(1, attempts + 1):
        try:
            with opener(url, timeout=timeout) as response, open(part, "wb") as out:
                shutil.copyfileobj(response, out)
                wanted = response.headers.get("Content-Length")
            size = os.path.getsize(part)
            if wanted is not None and int(wanted) != size:
                raise OSError(f"got {size} bytes of {wanted}")
            os.replace(part, dest)
            return size
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotAvailable(url) from exc
            if exc.code < 500:
                raise FetchError(f"{url}: HTTP {exc.code}") from exc
            failure = exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            failure = exc
        if attempt == attempts:
            raise FetchError(f"{url}: gave up after {attempts} attempts: {failure}") from failure
        sleep(backoff * attempt)


def fetch_day(day, tz, directory, **kwargs):
    """Download the file for local date 'day' into directory.  Returns (path, stamp, key)."""
    stamp = day_stamp(day, tz)
    key = source_key(stamp)
    path = os.path.join(directory, os.path.basename(key))
    download(f"{BUCKET}/{key}", path, **kwargs)
    return path, stamp, key