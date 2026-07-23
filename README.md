# dnamic-lab
Durham Neutral Atom and Molecule Improved Control. Primarily Artiq code for experimental control, and NDSPs.

The wiki/docs stores my questions, musings, and documentation for future people who go on this journey.

The intended structure and lifecycle of reusable experiment sequences is documented in
[repository/sequences/README.md](repository/sequences/README.md). Existing sequence
files span several generations of design, so consult that document before choosing a
pattern to copy.

## Python environments

There are two Python environments in use:

- `environment/nix` is the ARTIQ master/dashboard environment. It is entered with `nix develop ./environment/nix`, provides ARTIQ and related Nix-packaged tools, and creates a nested `artiq-nix-dev` venv for local Python overrides such as editable `ndscan` or `oitg` checkouts.
- `environment/bare` is a simpler uv-managed Python environment for controller/NDSP host processes. It does not depend on ARTIQ and does not enter the Nix flake; it is intended for host-side packages such as the controller manager and hardware controller packages. This is because getting hardware libraries to play well with Nix is sometimes faffy.

Dependency ownership and known non-Nix/manual dependencies are tracked in [docs/environment_dependencies.md](docs/environment_dependencies.md).

## How to use this repo

### Create this folder structure (first time only)
```bash
cd ~/
mkdir -p ~/artiq-files
cd artiq-files
mkdir install
git clone https://github.com/CornishLabs/dnamic-lab
```

### Now we make the editable install repos
```bash
cd install
mkdir virtualenvs
git clone https://github.com/tomhepz/ndscan
git clone https://github.com/OxfordIonTrapGroup/oitg
```

### Now we activate the nix develop environment.
```bash
cd ~/artiq-files/dnamic-lab
nix develop ./environment/nix --accept-flake-config
# This will create a virtualenv if it doesn't exist
# It will also add the `artiq-lab-tmux` command to your shell.
# At this point, follow the instructions printed to install
#   Python packages to the environment as editable installs.
```

### Install temporary ARTIQ Python overlays
For now, `ndscan` and `oitg` are installed manually into the nested `artiq-nix-dev` venv. The flake owns the heavy ARTIQ-side runtime dependencies, including `torch` and `gpytorch`; pip may still install smaller missing Python dependencies such as `nubopy`, `statsmodels`, or `patsy` into the venv.

Run this from inside `nix develop ./environment/nix`, after the shell has activated the venv.

```bash
python -m pip install --config-settings editable_mode=compat -e ~/artiq-files/install/oitg
python -m pip install --config-settings editable_mode=compat -e ~/artiq-files/install/ndscan

# Smoke tests:
python -c "import ndscan, oitg, nubo; import torch, gpytorch"
python -c "import torch; print(torch.__file__); print(torch.__version__, torch.version.cuda); print(torch.cuda.is_available())"
```

The `torch.__file__` path should point into `/nix/store`, not into `~/artiq-files/install/virtualenvs/artiq-nix-dev`. If it points into the venv, pip has installed a PyPI Torch that shadows the Nix-packaged CUDA Torch; recreate the venv and reinstall the editable packages.

Do not pass `--no-dependencies` for the normal setup. `ndscan` currently pulls in `nubopy`, whose import package is `nubo`, and `oitg` pulls in analysis dependencies that are not all Nix-owned yet. Use `--no-dependencies` only as a targeted debugging tool when you have checked that every dependency is already provided by the Nix shell.

Once `ndscan`, `oitg`, and the lab-facing interfaces settle, these should stop being hand-installed into the venv. The preferred stable state is either:

- package them in `environment/nix/flake.nix`, following the Oxford `nix-oitg` pattern, or
- move them into a small locked ARTIQ overlay environment with pinned git refs.

### What to do if the virtualenv is messed up
```bash
# If the virtualenv is messed up, you can delete it and remake it
# To delete it, FIRST deactivate the nix environment (ctrl-d if in it)
# ONLY THEN delete the venv folder
rm -rf ~/artiq-files/install/virtualenvs/artiq-nix-dev
# To remake the folder, re-run
cd ~/artiq-files/dnamic-lab
nix develop ./environment/nix --accept-flake-config
# At this point, a new venv will be made (with your permission)
# Re-run the temporary ARTIQ Python overlay install step above.
```

### Start Artiq
```bash
# To start all the artiq processes
artiq-lab-tmux

# OR (doesn't start ndscan janitor)
python -m artiq.frontend.artiq_session -m=-v -m=--git -m=--repository -m=. -m=--experiment-subdir -m=repository -c=-v -d=-p -d=ndscan.dashboard_plugin
```
