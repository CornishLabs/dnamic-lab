# Environment dependency ownership

This file records where dependencies are meant to be owned. The aim is to avoid hidden state in long-lived virtual environments and to make it clear when a dependency should move into Nix or uv.

Install owner meanings:

- `nix`: provided by `environment/nix/flake.nix` and pinned by `environment/nix/flake.lock`.
- `artiq-venv-overlay`: installed into the nested `artiq-nix-dev` venv on top of the Nix ARTIQ environment.
- `uv-controller`: installed by `environment/bare/pyproject.toml` and pinned by `environment/bare/uv.lock`.
- `manual-system`: installed outside Python, usually from a vendor driver or system package.
- `undecided`: known or expected dependency whose ownership has not been settled yet.

| Dependency | Needed by | Install owner | Source / pin | Expected location | Verify | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| ARTIQ | master, dashboard, experiments | `nix` | `environment/nix/flake.lock` | Nix store via `nix develop ./environment/nix` | `python -c "import artiq"` | Core environment anchor. |
| DAX | ARTIQ-side tooling | `nix` | `artiq-extrapkg` input in `environment/nix/flake.lock` | Nix store | `python -c "import dax"` | Currently included explicitly in the flake. |
| `sipyco` | ARTIQ RPC and host controllers | `nix`, `uv-controller` | ARTIQ flake for Nix; uv git source for bare env | Nix store and controller venv | `python -c "import sipyco"` | Keep Nix and uv revisions aligned if protocol compatibility becomes sensitive. |
| `artiq-comtools` | controller manager / `artiq_ctlmgr` | `nix`, `uv-controller` | ARTIQ flake for Nix; uv git source for bare env | Nix store and controller venv | `python -c "import artiq_comtools"` | Present in both environments. |
| `ndscan` | dashboard plugin, dataset janitor | `artiq-venv-overlay` | local editable checkout for now | `~/artiq-files/install/ndscan` | `python -c "import ndscan"` | Installed manually into `artiq-nix-dev` for now; candidate for Nix packaging or a locked overlay. |
| `oitg` | `ndscan` / analysis helpers | `artiq-venv-overlay` | local editable checkout for now | `~/artiq-files/install/oitg` | `python -c "import oitg"` | Installed manually into `artiq-nix-dev` for now; candidate for Nix packaging or a locked overlay. |
| `nubopy` / `nubo` | `ndscan` Bayesian optimisation support | `artiq-venv-overlay` | transitive dependency of `ndscan` for now | nested `artiq-nix-dev` venv | `python -c "import nubo"` | Distribution name is `nubopy`; import package is `nubo`. Its heavy `torch`/`gpytorch` dependencies are Nix-owned. |
| controller manager | NDSP/controller processes | `uv-controller` | `environment/bare/pyproject.toml` and `uv.lock` | `~/artiq-files/install/virtualenvs/controller_manager` | `python -c "import sipyco, aiohttp"` | Bare uv environment, deliberately no ARTIQ dependency. |
| `dnamic-andor-host` | Andor camera controller | `uv-controller` | editable local path from `environment/bare/pyproject.toml` | `ndsps/andor-camera/host` | `python -c "import dnamic_andor_host"` | Python package only; vendor libraries are separate. |
| `spectrum-awg-host` | Spectrum AWG controller | `uv-controller` | editable local path from `environment/bare/pyproject.toml` | `ndsps/spectrum-awg/host` | `python -c "import spectrum_awg_host"` | Python package only; Spectrum driver is separate. |
| `AWGSegmentFactory` | Spectrum AWG sequence compilation | `uv-controller` | local editable path in `ndsps/spectrum-awg/host/pyproject.toml` | `~/code/AWGSegmentFactory` | `python -c "import awgsegmentfactory"` | Prefer a pinned git source or vendored path if this must be reproducible. |
| `spcm` Python package | Spectrum AWG Python API | `uv-controller` | transitive dependency of `AWGSegmentFactory[control-hardware]` | controller venv | `python -c "import spcm"` | Does not replace the Spectrum kernel/library driver install. |
| Spectrum kernel/library driver | Spectrum AWG hardware | `manual-system` | vendor source/repository | system install | `cat /proc/spcm4_cards` | See [AWG_install.md](AWG_install.md). |
| `pyAndorSDK2` | Andor camera Python API | `uv-controller` | local path in `ndsps/andor-camera/host/pyproject.toml` | `~/artiq-files/install/pyAndorSDK2` | `python -c "import pyAndorSDK2"` | Python wrapper only; Andor shared libraries are separate. |
| Andor SDK shared libraries | Andor camera hardware | `manual-system` | vendor SDK | usually `/usr/local/lib` | run `aqctl_andor_emccd` | Keep SDK version documented when installed. |
| NVIDIA/CUDA driver | GPU synthesis / CuPy / torch | `manual-system` | system driver install | system install | `nvidia-smi` | See [GPU_drivers.md](GPU_drivers.md). |
| `cupy-cuda13x` | AWG GPU synthesis | `uv-controller` | transitive dependency of `AWGSegmentFactory[cuda]` | controller venv | `python -c "import cupy"` | Requires compatible system NVIDIA driver. |
| `torch`, `gpytorch` | `nubopy`, GPU/analysis workflows | `nix` | `environment/nix/flake.lock`; CUDA binary cache configured in `environment/nix/flake.nix` | Nix store | `python -c "import torch, gpytorch; print(torch.__file__)"` | The printed Torch path should start with `/nix/store`. If it points into `artiq-nix-dev`, recreate the venv to remove the shadowing PyPI install. |

When adding a dependency, prefer updating the owning source of truth first:

- Nix/ARTIQ dependencies: `environment/nix/flake.nix`.
- Bare controller dependencies: `environment/bare/pyproject.toml`, then refresh `environment/bare/uv.lock`.
- Manual hardware dependencies: add a row here and link to the install notes.

The current manual `ndscan`/`oitg` overlay is intentionally temporary. Once the APIs and package choices are stable, replace the editable venv install with either Nix package definitions in `environment/nix/flake.nix` or a locked overlay with pinned refs.
