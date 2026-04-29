"""Diagnostic: fetch one step for meteoswiss-INT and show raw ensemble values."""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app/src")

from lsmfapi._eccodes import setup_definitions

setup_definitions()

from lsmfapi.collectors.icon_ch1_eps import (  # noqa: E402
    N_MEMBERS,
    COLLECTION,
    IconCh1EpsCollector,
    _extract_station,
    _horizon_str,
    _latest_ref_dt,
    _read_grib2_eccodes,
    _search_item_url,
)
from lsmfapi.config import get_config
import httpx
import numpy as np

TARGET_STATION_ID = "meteoswiss-INT"
TEST_VARS = ["U_10M", "T_2M", "TOT_PREC"]
TEST_HORIZONS = [0, 1, 6, 24, 33]


async def main() -> None:
    cfg = get_config()
    ref_dt = _latest_ref_dt()
    print(f"ref_dt : {ref_dt.isoformat()}")
    print(f"N_MEMBERS constant: {N_MEMBERS}")
    print()

    # Fetch station list
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        resp = await client.get(f"{cfg.lenticularis.base_url}/api/stations")
        resp.raise_for_status()
        stations = resp.json()

    station = next((s for s in stations if s["station_id"] == TARGET_STATION_ID), None)
    if not station:
        print(f"ERROR: {TARGET_STATION_ID} not found in /api/stations")
        print(f"  available sample: {[s['station_id'] for s in stations[:10]]}")
        return
    print(f"Station: {station}")
    print()

    # Build grid KD-tree
    col = IconCh1EpsCollector()
    with tempfile.TemporaryDirectory(prefix="diag_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        await col._ensure_grid(tmpdir)

        from lsmfapi.collectors.icon_ch1_eps import _GRID_TREE
        if _GRID_TREE is None:
            print("ERROR: Grid tree not built — _ensure_grid failed")
            return

        lat = float(station["latitude"])
        lon = float(station["longitude"])
        _, flat_idx_arr = _GRID_TREE.query([[lat, lon]])
        flat_idx = int(flat_idx_arr[0])
        print(f"Nearest grid point flat_idx: {flat_idx}")
        print()

        async with httpx.AsyncClient(timeout=300) as client:
            for var in TEST_VARS:
                print(f"=== {var} ===")
                for h in TEST_HORIZONS:
                    horizon_s = _horizon_str(h)
                    url = await _search_item_url(
                        client, cfg.meteoswiss.stac_base_url, COLLECTION, ref_dt, var, h
                    )
                    if url is None:
                        print(f"  h={h:3d} ({horizon_s}): STAC → no item found")
                        continue

                    dest = tmpdir / f"{var}_{h:03d}.grib2"
                    try:
                        await col.download(url, str(dest))
                        size_kb = dest.stat().st_size // 1024
                        arr, level_coords = _read_grib2_eccodes(dest)
                        if arr is None:
                            print(f"  h={h:3d}: download OK ({size_kb} KB) but eccodes returned None")
                            continue
                        print(
                            f"  h={h:3d}: OK  shape={arr.shape}  "
                            f"n_members_in_grib={arr.shape[0]}  "
                            f"match={arr.shape[0] == N_MEMBERS}"
                        )
                        vals = arr[:, flat_idx]
                        print(
                            f"         raw[{TARGET_STATION_ID}] = "
                            f"{np.round(vals, 3).tolist()}"
                        )
                        print(
                            f"         median={np.nanmedian(vals):.4f}  "
                            f"min={np.nanmin(vals):.4f}  "
                            f"max={np.nanmax(vals):.4f}"
                        )
                    except Exception as exc:
                        print(f"  h={h:3d}: ERROR {exc}")
                    finally:
                        dest.unlink(missing_ok=True)
                print()


asyncio.run(main())
