"""Adapter selection for the eleven manuscript solver paths."""
from adapters.nanokmc_adapter import NanoKMCAdapter
from adapters.spparks_adapter import SPPARKSAdapter
from adapters.kmcos_adapter import KmcosOTFAdapter
from adapters.kmc_lattice_adapter import KMCLatticeAdapter

NANOKMC_MODES = {
    "nanokmc_bit_encoded": "bit_encoded",
    "nanokmc_partial_filter_optimized": "partial_filter_optimized",
    "nanokmc_active_filtered_generic": "active_filtered_generic",
    "nanokmc_active_filtered_binary_nn": "active_filtered_binary_nn",
    "nanokmc_rate_category_optimized": "rate_category_optimized",
    "nanokmc_exact_class_optimized": "exact_class_optimized",
}
SPPARKS_MODES = {
    "spparks_diffusion_sweep_random": "diffusion_sweep_random",
    "spparks_diffusion_linear": "diffusion_linear",
    "spparks_diffusion_tree": "diffusion_tree",
}
PATH_IDS = tuple(NANOKMC_MODES) + tuple(SPPARKS_MODES) + ("kmcos_otf", "kmc_lattice_selective")


def get_adapter(code: str, paths: dict):
    if code in NANOKMC_MODES:
        return NanoKMCAdapter(paths, mode=NANOKMC_MODES[code])
    if code in SPPARKS_MODES:
        return SPPARKSAdapter(paths, mode=SPPARKS_MODES[code])
    if code == "kmcos_otf":
        return KmcosOTFAdapter(paths)
    if code == "kmc_lattice_selective":
        return KMCLatticeAdapter(paths, mode="selective")
    raise KeyError(f"Not one of the eleven manuscript paths: {code}")
