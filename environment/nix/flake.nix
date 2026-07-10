{
  nixConfig = {
    extra-trusted-public-keys = [
      "nixbld.m-labs.hk-1:5aSRVA5b320xbNvu30tqxVPXpld73bhtOeH6uAjRyHc="
      "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M="
    ];
    extra-substituters = [
      "https://nixbld.m-labs.hk"
      "https://cache.nixos-cuda.org"
    ];
  };

  # Switch this one line between local development and remote/pinned use.
  # inputs.artiqpkgs.url = "path:/home/lab/code/artiq";
  inputs.artiqpkgs.url = "git+https://git.m-labs.hk/M-Labs/artiq.git?ref=release-9";

  inputs.extrapkg.url = "git+https://git.m-labs.hk/M-Labs/artiq-extrapkg.git?ref=release-9";
  inputs.extrapkg.inputs.artiqpkgs.follows = "artiqpkgs";

  outputs = { self, extrapkg, artiqpkgs, ... }:
    let
      # PACKAGE SETS
      nixpkgsPkgs = import artiqpkgs.inputs.nixpkgs {
        system = "x86_64-linux";
        config = {
          allowUnfree = true;
          cudaSupport = true;
        };
      };                                            # Nixpkgs PackageSet (python3, gtkwave, tmux, ...)
      artiqPkgs = artiqpkgs.packages.x86_64-linux;  # Artiq PackageSet (artiq, migen, misoc, ...)
      extraPkgs = extrapkg.packages.x86_64-linux;   # Artiq Extra Packages PackageSet (dax, nix-servo, ...)

      # PACKAGES
      artiqPkg = artiqPkgs.artiq; # the Artiq Package (the same thing as  extraPkgs.artiq)
      #daxPkg = extraPkgs.dax;     # the DAX Package, for example

      python-env = nixpkgsPkgs.python3.withPackages(ps : [
            artiqPkg
            extraPkgs.dax

            # Explicit dependencies, however these are already pulled in artiqPkg
            ps.numpy       # Included in artiqPkg, but repeated for clarity
            ps.scipy       # Included in artiqPkg, but repeated for clarity
            ps.h5py        # HDF5 files in python
            ps.pyqt6       # GUIs
            ps.pyqtgraph   # Fast GUI Graphs
            ps.qasync      # Makes async and QT play nice

            # Useful python packages
            ps.pandas
            ps.matplotlib

            # Eventually want to package ndscan here too (see Oxford flake)
            # For now, manually install.

            # GPU acceleration. CUDA support is enabled in nixpkgsPkgs above;
            # the CUDA binary cache keeps this from rebuilding the CUDA stack.
            ps.torch       # For GPU acceleration
            ps.gpytorch    # GPU accelerated gaussian process regression
      ]);

      runtimeLibs = nixpkgsPkgs.lib.makeLibraryPath [
        # Needed by binary Python wheels installed into the nested venv, e.g. PyPI torch.
        nixpkgsPkgs.stdenv.cc.cc.lib
        nixpkgsPkgs.zlib
      ];

      artiq-lab-tmux = nixpkgsPkgs.writeShellApplication {
        name = "artiq-lab-tmux";
        runtimeInputs = [ nixpkgsPkgs.tmux nixpkgsPkgs.bash ];
        text = builtins.readFile ./src/artiq-lab-tmux.sh;
      };

      artiq-master-dev = nixpkgsPkgs.mkShell {
        name = "artiq-master-dev";
        buildInputs = [ 
          python-env
          artiq-lab-tmux
          nixpkgsPkgs.gtkwave
          nixpkgsPkgs.libcanberra-gtk3
        ];
      shellHook = ''
        if [ -z "$SCRATCH_DIR" ]; then
          echo "SCRATCH_DIR environment variable not set, defaulting to ~/artiq-files/install."
          export SCRATCH_DIR=$HOME/artiq-files/install
        fi

        export QT_PLUGIN_PATH=${nixpkgsPkgs.qt5.qtbase}/${nixpkgsPkgs.qt5.qtbase.dev.qtPluginPrefix}
        export QML2_IMPORT_PATH=${nixpkgsPkgs.qt5.qtbase}/${nixpkgsPkgs.qt5.qtbase.dev.qtQmlPrefix}
        # Keep matplotlib applets on Qt instead of auto-selecting a GTK backend.
        export MPLBACKEND=qtagg
        # GTK looks for modules under $GTK_PATH/modules.
        export GTK_PATH=${nixpkgsPkgs.libcanberra-gtk3}/lib/gtk-3.0''${GTK_PATH:+:$GTK_PATH}
        export LD_LIBRARY_PATH=${runtimeLibs}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}

        # Let CUDA-enabled Nix packages find the host NVIDIA driver without adding
        # the whole system library directory to LD_LIBRARY_PATH.
        nvidia_driver_lib_dir="''${TMPDIR:-/tmp}/artiq-nix-nvidia-libs-''${USER:-user}"
        mkdir -p "$nvidia_driver_lib_dir"
        for nvidia_lib in libcuda.so libcuda.so.1 libnvidia-ml.so libnvidia-ml.so.1; do
          for nvidia_host_lib in \
            /run/opengl-driver/lib/$nvidia_lib \
            /usr/lib/x86_64-linux-gnu/$nvidia_lib \
            /lib/x86_64-linux-gnu/$nvidia_lib; do
            if [ -e "$nvidia_host_lib" ]; then
              ln -sfn "$nvidia_host_lib" "$nvidia_driver_lib_dir/$nvidia_lib"
              break
            fi
          done
        done
        if [ -e "$nvidia_driver_lib_dir/libcuda.so.1" ]; then
          export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:$nvidia_driver_lib_dir"
        fi

        # Only do nested venv setup when interactive.
        # This keeps `nix develop --command ...` non-blocking.
        case "$-" in
          *i*)
            ${
              ./src/setup-artiq-master-dev.sh
            } ${python-env} ${python-env.sitePackages} || exit 1
            source $SCRATCH_DIR/virtualenvs/artiq-nix-dev/bin/activate || exit 1
            export PYTHONPATH="''${SCRATCH_DIR:-}''${PYTHONPATH:+:$PYTHONPATH}"
            ;;
          *)
            ;;
        esac
      '';
      };
  in {
    inherit artiq-master-dev;
    devShells.x86_64-linux.default = artiq-master-dev;
  };
}
