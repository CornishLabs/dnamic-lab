# dnamic-lab
Durham Neutral Atom and Molecule Improved Control. Primarily Artiq code for experimental control, and NDSPs.

The wiki/docs stores my questions, musings, and documentation for future people who go on this journey.

## How to use this repo

Get to the point of having a `nix develop` shell setup.

```bash
cd ~/
mkdir -p ~/artiq-files
cd artiq-files
mkdir install
git clone https://github.com/CornishLabs/dnamic-lab

# Now we make the editable install repos
cd install
mkdir virtualenvs
git clone https://github.com/tomhepz/ndscan
git clone https://github.com/OxfordIonTrapGroup/oitg

# Now we activate the nix develop environment.
cd ~/artiq-files/dnamic-lab
nix develop ./environment/nix
# This will create a virtualenv
# It will also add the `artiq-lab-tmux` command to your shell.
# At this point, follow the instructions to install the above 
#   Python packages to the environemtn as editable installs.

# To start all the artiq processes
artiq-lab-tmux


# OR (doesn't start ndscan janitor)
python -m artiq.frontend.artiq_session -m=-v -m=--git -m=--repository -m=. -m=--experiment-subdir -m=repository -c=-v -d=-p -d=ndscan.dashboard_plugin
```
