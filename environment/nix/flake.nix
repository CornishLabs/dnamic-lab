{
  nixConfig = {
    extra-trusted-public-keys = "nixbld.m-labs.hk-1:5aSRVA5b320xbNvu30tqxVPXpld73bhtOeH6uAjRyHc=";
    extra-substituters = "https://nixbld.m-labs.hk";
  };

  # Switch this one line between local development and remote/pinned use.
  inputs.artiqpkgs.url = "path:/home/lab/code/artiq";
  # inputs.artiqpkgs.url = "git+https://git.m-labs.hk/M-Labs/artiq.git"; #?ref=release-8";

  inputs.extrapkg.url = "git+https://git.m-labs.hk/M-Labs/artiq-extrapkg.git"; #?ref=release-8";
  inputs.extrapkg.inputs.artiqpkgs.follows = "artiqpkgs";

  outputs = { self, extrapkg, artiqpkgs, ... }:
    let
      # PACKAGE SETS
      nixpkgsPkgs = extrapkg.pkgs;                  # Nixpkgs PackageSet (python3, gtkwave, tmux, ...)
      artiqPkgs = artiqpkgs.packages.x86_64-linux;  # Artiq PackageSet (artiq, migen, misoc, ...)
      extraPkgs = extrapkg.packages.x86_64-linux;   # Artiq Extra Packages PackageSet (dax, nix-servo, ...)

      # PACKAGES
      artiqPkg = artiqPkgs.artiq; # the Artiq Package (the same thing as  extraPkgs.artiq)
      daxPkg = extraPkgs.dax;     # the DAX Package

      python-env = nixpkgsPkgs.python3.withPackages(ps : [
            artiqPkg
            extraPkgs.dax
            ps.pandas
            ps.matplotlib
      ]);

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
        ];
      shellHook = ''
        if [ -z "$SCRATCH_DIR" ]; then
          echo "SCRATCH_DIR environment variable not set, defaulting to ~/artiq-files/install."
          export SCRATCH_DIR=$HOME/artiq-files/install
        fi

        export QT_PLUGIN_PATH=${nixpkgsPkgs.qt5.qtbase}/${nixpkgsPkgs.qt5.qtbase.dev.qtPluginPrefix}
        export QML2_IMPORT_PATH=${nixpkgsPkgs.qt5.qtbase}/${nixpkgsPkgs.qt5.qtbase.dev.qtQmlPrefix}

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
