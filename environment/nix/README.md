# Nix ARTIQ environment

This directory defines the Nix development shell for the ARTIQ master/dashboard environment.

For normal setup and usage, see the root [README.md](../../README.md). Dependency ownership and known manual dependencies are tracked in [docs/environment_dependencies.md](../../docs/environment_dependencies.md).

The flake currently provides:

- ARTIQ and related M-Labs packages from the pinned flake inputs
- selected lab Python/scientific packages used from the ARTIQ side
- CUDA-enabled `torch` and `gpytorch`, fetched from the CUDA binary cache when available
- `artiq-lab-tmux`
- `gtkwave`
- an interactive nested `artiq-nix-dev` venv bridge for local Python overrides

For now, `ndscan` and `oitg` are not treated as stable Nix packages in this repository. They are installed manually into the nested venv from editable checkouts under `~/artiq-files/install`, as described in the root README. Their missing Python-only dependencies may also be installed into the venv, but `torch` and `gpytorch` should continue to resolve from `/nix/store`.

Check this with:

```bash
python -c "import torch; print(torch.__file__)"
```

The path should start with `/nix/store`, not `~/artiq-files/install/virtualenvs/artiq-nix-dev`.

When those packages and their lab-facing interfaces settle, move this out of manual venv state. The preferred stable state is to package them in this flake, following the OxfordIonTrapGroup/nix-oitg pattern, or to use a small locked ARTIQ overlay with pinned git refs.

This setup was originally derived from OxfordIonTrapGroup/nix-oitg, but the root README is now the setup guide for this repository.

For debugging, the tmux launcher roughly starts:

```bash
python -m artiq.frontend.artiq_master
python -m artiq_comtools.artiq_ctlmgr
ndscan_dataset_janitor
python -m artiq.frontend.artiq_dashboard -p ndscan.dashboard_plugin
```
