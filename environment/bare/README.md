# Bare controller environment

This directory defines the uv-managed Python environment for controller-manager and NDSP host processes.

For the overall environment model, see the root [README.md](../../README.md). Dependency ownership and known manual dependencies are tracked in [docs/environment_dependencies.md](../../docs/environment_dependencies.md).

This environment deliberately does not depend on ARTIQ and does not enter the Nix flake. It is for host-side controller packages and hardware-facing Python packages that are awkward to place in Nix.

Install/update it with:

```bash
cd ~/artiq-files/dnamic-lab/environment/bare
export UV_PROJECT_ENVIRONMENT=~/artiq-files/install/virtualenvs/controller_manager
uv sync
```

The lockfile for this environment is `uv.lock`. If dependencies change, update the lockfile rather than installing packages by hand into the venv.
