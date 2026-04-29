import logging
import warnings

logger = logging.getLogger(__name__)


def setup_definitions() -> None:
    """Register COSMO/ICON GRIB2 shortName definitions with ecCodes.

    Must be called once at process startup before any cfgrib/xarray operations.
    COSMO definitions must appear before the vendor (ECMWF) definitions in the path
    so that ICON-specific shortNames (T_2M, U_10M, QV, PMSL …) are resolved correctly.
    """
    warnings.filterwarnings("ignore", message="ecCodes .* or higher is recommended", module="gribapi")
    import eccodes
    import eccodes_cosmo_resources

    vendor_path = eccodes.codes_definition_path()
    cosmo_path = eccodes_cosmo_resources.get_definitions_path()
    eccodes.codes_set_definitions_path(f"{cosmo_path}:{vendor_path}")
    logger.info("ecCodes definitions path: %s:%s", cosmo_path, vendor_path)
