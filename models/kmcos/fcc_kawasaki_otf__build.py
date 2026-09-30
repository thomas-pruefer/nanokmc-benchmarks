#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from models.kmcos.fcc_kawasaki_geometry import (
    FCC_BASIS2,
    decompose_doubled_coordinate,
    directed_fcc_bond_templates,
)

MODEL_NAME = "fcc_kawasaki_otf"

# Keep DLL-directory handles alive for the lifetime of the Python process.
_DLL_DIRECTORY_HANDLES = []


def _configure_windows_native_toolchain(msys2_ucrt_bin: Path | None) -> None:
    if os.name != "nt" or msys2_ucrt_bin is None:
        return

    dll_dir = msys2_ucrt_bin.resolve()
    if not dll_dir.exists():
        raise FileNotFoundError(f"MSYS2 UCRT64 bin directory not found: {dll_dir}")

    current_path = os.environ.get("PATH", "")
    path_entries = current_path.split(os.pathsep) if current_path else []
    if str(dll_dir).lower() not in {entry.lower() for entry in path_entries}:
        os.environ["PATH"] = str(dll_dir) + os.pathsep + current_path

    if hasattr(os, "add_dll_directory"):
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(dll_dir)))


def _coord(model, q2: tuple[int, int, int]):
    cell, basis = decompose_doubled_coordinate(q2)
    return model.lattice.generate_coord(
        f"s{basis}.({cell[0]}, {cell[1]}, {cell[2]}).fcc"
    )


def _check_windows_build_environment() -> None:
    if os.name != "nt":
        return
    if sys.version_info[:2] != (3, 10):
        raise RuntimeError(
            "The frozen kmcos 1.1.0 Windows build is run in the dedicated Python 3.10 environment. "
            f"Current interpreter is {sys.version.split()[0]}."
        )
    major_minor = tuple(int(x) for x in np.__version__.split(".")[:2])
    if major_minor < (1, 26):
        raise RuntimeError(
            "NumPy >= 1.26 is required for the Windows f2py wrapper used here. "
            f"Current NumPy is {np.__version__}. Use NumPy 1.26.4 in kmcos-py310."
        )


def _export_source_only(kmcos, xml_path: Path, export_dir: Path) -> Path:
    """Generate unmodified kmcos OTF Fortran source without invoking its legacy Windows linker path."""
    if export_dir.exists():
        shutil.rmtree(export_dir)
    # kmcos CLI appends /src to the requested export directory.
    kmcos.export(f"{xml_path} {export_dir} -b otf -s -o")
    src_dir = export_dir / "src"
    if not (src_dir / "base.f90").exists():
        raise RuntimeError(f"kmcos source export did not create expected files under {src_dir}")
    return src_dir


def _compile_windows_f2py(src_dir: Path, kmcos_root: Path) -> Path:
    """Compile generated kmcos source using the working MinGW/f2py route.

    This does not edit the kmcos checkout or generated Fortran solver. It only
    supplies a Windows build invocation compatible with NumPy 1.26.4 and the
    MSYS2 UCRT64 GCC/gfortran toolchain.
    """
    _check_windows_build_environment()
    sys.path.insert(0, str(kmcos_root))
    from kmcos.utils import evaluate_kind_values

    old_cwd = Path.cwd()
    try:
        os.chdir(src_dir)
        kind_f2py = src_dir / "kind_values_f2py.f90"
        helper_pyds = list(src_dir.glob("f2py_selected_kind*.pyd"))
        if kind_f2py.exists():
            kind_f2py.unlink()
        for p in helper_pyds:
            p.unlink()

        # kmcos resolves compiler-dependent selected_*_kind values through a
        # tiny helper module before the full model is compiled.
        evaluate_kind_values("kind_values.f90", "kind_values_f2py.f90")

        files = [
            "kind_values_f2py.f90",
            "base.f90",
            "lattice.f90",
            "proclist_constants.f90",
            "proclist_pars.f90",
        ]
        files.extend(sorted(p.name for p in src_dir.glob("nli_*.f90")))
        files.extend(sorted(p.name for p in src_dir.glob("run_proc_*.f90")))
        files.append("proclist.f90")

        for filename in files:
            if not (src_dir / filename).exists():
                raise FileNotFoundError(f"Required generated source file missing: {filename}")

        for p in src_dir.glob("kmc_model*.pyd"):
            p.unlink()

        # Do NOT add -fimplicit-none here. The generated kmcos modules contain
        # their own `implicit none`, while NumPy 1.26's temporary F2PY wrapper
        # has two legacy helper bindings that do not compile if that flag is
        # imposed globally. No kmcos Fortran source is changed.
        f90flags = (
            "-ffree-line-length-none -ffree-form -xf95-cpp-input "
            "-Wall -O3 -fmax-identifier-length=63"
        )
        command = [
            sys.executable,
            "-m",
            "numpy.f2py",
            "-c",
            "--fcompiler=gnu95",
            "--compiler=mingw32",
            f"--f90flags={f90flags}",
            "-m",
            "kmc_model",
            *files,
        ]
        print("Compiling generated kmcos OTF source with explicit MinGW/f2py wrapper...")
        subprocess.run(command, check=True)

        pyds = list(src_dir.glob("kmc_model*.pyd"))
        if len(pyds) != 1:
            raise RuntimeError(f"Expected exactly one kmc_model*.pyd, found: {pyds}")

        # Import validation catches missing runtime DLLs immediately.
        sys.path.insert(0, str(src_dir))
        sys.modules.pop("kmc_model", None)
        module = importlib.import_module("kmc_model")
        print(f"FULL KMCOS OTF MODEL IMPORT PASS: {module.__file__}")
        return pyds[0]
    finally:
        os.chdir(old_cwd)


def build_model(
    kmcos_root: Path,
    output_dir: Path,
    msys2_ucrt_bin: Path | None = Path("C:/msys64/ucrt64/bin"),
) -> Path:
    kmcos_root = kmcos_root.resolve()
    output_dir = output_dir.resolve()
    _configure_windows_native_toolchain(msys2_ucrt_bin)
    if not (kmcos_root / "kmcos" / "__init__.py").exists():
        raise FileNotFoundError(
            f"kmcos source tree not found at {kmcos_root}. Expected {kmcos_root / 'kmcos' / '__init__.py'}"
        )

    sys.path.insert(0, str(kmcos_root))
    import kmcos
    from kmcos.types import Action, Bystander, Condition, Process

    output_dir.mkdir(parents=True, exist_ok=True)
    xml_path = output_dir / f"{MODEL_NAME}.xml"
    ini_path = output_dir / f"{MODEL_NAME}.ini"
    export_dir = output_dir / f"{MODEL_NAME}_otf"

    for path in (xml_path, ini_path):
        if path.exists():
            path.unlink()

    old_cwd = Path.cwd()
    try:
        os.chdir(output_dir)
        model = kmcos.create_kmc_model(MODEL_NAME)
        model.set_meta(
            author="NanoKMC benchmark project",
            email="benchmark@example.invalid",
            model_dimension=3,
        )

        layer = model.add_layer(name="fcc")
        for basis, q2 in FCC_BASIS2.items():
            layer.add_site(
                name=f"s{basis}",
                pos=np.asarray(q2, dtype=float) / 2.0,
                default_species="B",
            )

        model.add_species(name="B", color="#0000ff", representation="")
        model.add_species(name="A", color="#ff0000", representation="")
        model.add_parameter(name="Ea", value=1.0, adjustable=True, min=0.0, max=10.0)
        model.add_parameter(name="kT", value=1.0, adjustable=True, min=1.0e-12, max=10.0)
        model.lattice.cell = np.eye(3)

        templates = directed_fcc_bond_templates()
        for template in templates:
            source = _coord(model, template.source_q2)
            target = _coord(model, template.target_q2)

            bystanders = [
                Bystander(coord=_coord(model, q2), allowed_species=["A"], flag="ini")
                for q2 in template.initial_unique_neighbors_q2
            ]
            bystanders.extend(
                Bystander(coord=_coord(model, q2), allowed_species=["A"], flag="fin")
                for q2 in template.final_unique_neighbors_q2
            )

            model.add_process(
                Process(
                    name=f"swap_{template.index:02d}_s{template.source_basis}_s{template.target_basis}",
                    condition_list=[
                        Condition(species="A", coord=source),
                        Condition(species="B", coord=target),
                    ],
                    action_list=[
                        Action(species="B", coord=source),
                        Action(species="A", coord=target),
                    ],
                    bystander_list=bystanders,
                    rate_constant="1.0",
                    otf_rate=(
                        "base_rate*min(1.0, "
                        "exp(-Ea*(nr_A_ini-nr_A_fin)/kT))"
                    ),
                )
            )

        model.backend = "otf"
        model.save_model()
        print(f"Generated {xml_path}")
        print("Model definition: 4 FCC basis sites, 48 directed A->B NN exchange templates")
        print("OTF environment: 7 initial-unique + 7 final-unique A-count bystanders per template")
    finally:
        os.chdir(old_cwd)

    print("Generating unmodified kmcos OTF Fortran source...")
    src_dir = _export_source_only(kmcos, xml_path, export_dir)
    if os.name == "nt":
        _compile_windows_f2py(src_dir, kmcos_root)
    else:
        # For final Linux/WSL publication builds we will establish a
        # compiler-normalized path separately; do not silently invent it here.
        raise RuntimeError("This helper currently implements the audited Windows build path only.")
    print(f"Compiled model source directory: {src_dir}")

    return export_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kmcos-root",
        default=str(REPO_ROOT / "sources" / "kmcos"),
        help="Path to the unmodified kmcos source checkout",
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "build" / "kmcos"),
        help="Benchmark-owned directory for generated/compiled kmcos model files",
    )
    parser.add_argument(
        "--msys2-ucrt-bin",
        default="C:/msys64/ucrt64/bin",
        help="MSYS2 UCRT64 bin directory containing gcc/gfortran and runtime DLLs",
    )
    args = parser.parse_args()
    build_model(
        Path(args.kmcos_root),
        Path(args.output_dir),
        msys2_ucrt_bin=Path(args.msys2_ucrt_bin) if args.msys2_ucrt_bin else None,
    )


if __name__ == "__main__":
    main()
