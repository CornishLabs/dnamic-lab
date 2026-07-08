# Notes on installing AWG drivers.

First step is to install the linux kernel drivers. It is rare that the ones on the disk you were given will work, so you will need to build from source. This is as simple as emailing spectrum instruments, getting the kernel driver source, then running the script `make_spcm4_linux_kerneldrv.sh`. Note that according to their docs, this only installs the kernel driver, not the library.

However, on Ubuntu with secure boot, this has the issue of the kernel driver needing to be signed in order to be loaded.
You will see an error like:
`Loading of unsigned module is rejected`
when running
`sudo dmesg`.

To fix this: follow the following instructions,
(see https://askubuntu.com/questions/770205/how-to-sign-kernel-modules-with-sign-file) , or the following ChatGPT corroborated set of instructions:

---

That `dmesg` line almost always means **UEFI Secure Boot (and Ubuntu’s kernel signature enforcement / lockdown)** is enabled, so the kernel will **refuse to load any kernel module (`.ko`) that isn’t signed by a key it trusts**.


### 1) Confirm Secure Boot is the reason

```bash
mokutil --sb-state
sudo dmesg | grep -iE 'secure boot|lockdown|unsigned module|rejected'
```

If it says SecureBoot enabled, this is it.

### 2) Install tools + headers

```bash
sudo apt update
sudo apt install -y mokutil openssl linux-headers-$(uname -r)
```

### 3) Create a Machine Owner Key (MOK) pair

Pick a directory you’ll keep long-term (don’t lose the private key, or you can’t sign future rebuilds):

```bash
mkdir -p ~/module-signing && cd ~/module-signing
openssl req -new -x509 -newkey rsa:2048 \
  -keyout MOK.priv -outform DER -out MOK.der \
  -nodes -days 36500 -subj "/CN=spcm module signing/"
```

### 4) Enroll the public key into the firmware “MOK” list

```bash
sudo mokutil --import MOK.der
```

It will ask you to create a one-time password. **Reboot**. On the blue “MOK Manager” screen, choose:

**Enroll MOK → Continue → Yes → enter the password → reboot**

That enrollment step is the key Ubuntu uses for trusting your signatures. ([Ask Ubuntu][2])

### 5) Sign the Spectrum module you built

First find the exact installed module path:

```bash
modinfo -n spcm 2>/dev/null || true
modinfo -n spcm4 2>/dev/null || true
```

Then sign it (use whichever module you actually load):

```bash
SIGNFILE="/lib/modules/$(uname -r)/build/scripts/sign-file"

sudo $SIGNFILE sha256 ~/module-signing/MOK.priv ~/module-signing/MOK.der "$(modinfo -n spcm4)"
# or:
sudo $SIGNFILE sha256 ~/module-signing/MOK.priv ~/module-signing/MOK.der "$(modinfo -n spcm)"
sudo depmod -a
```

Now try loading again:

```bash
sudo modprobe spcm4   # or spcm
```

---

## Make it survive kernel updates

Every time you install a new kernel, the module will be rebuilt/installed for that kernel and **must be signed again** (unless the vendor provides a DKMS package that automatically integrates signing). So keep `~/module-signing/MOK.priv` safe.

# Testing it's working

We can then check that the kernel driver is loaded by:
```
lab@control-pc:~$ cat /proc/spcm4_cards

Spectrum spcm driver interface for M4i/M4x/M2p/M5i cards
----------------------------------------------------------
Driver version:          3.13 build 23699
Driver major number:     10

/dev/spcm0
     Card type:       M4i.66xx-x8 / M4x.66xx-x4
```

# Adding software from spectrum repos

```
cd ~/
wget http://spectrum-instrumentation.com/dl/repo-key.asc
gpg --dearmor -o repo-key.gpg repo-key.asc
sudo cp repo-key.gpg /etc/apt/spectrum-instrumentation.gpg
# This will litter your home folder ~/ with some gpg files,
# These can be deleted now.

sudo touch /etc/apt/sources.list.d/spectrum-instrumentation.list
sudo nano /etc/apt/sources.list.d/spectrum-instrumentation.list
```

add in the line
```
deb [signed-by=/etc/apt/spectrum-instrumentation.gpg] http://spectrum-instrumentation.com/dl/ ./
```

save and exit nano.

```
sudo apt update
sudo apt install sbench6 spcmcontrol libspcm-linux
```

Available packages appear to be:
```
libspcm-linux
sbench6
spcddscontrol
spcmcontrol
```

# Rebuild, sign, load, and check the Spectrum `spcm4` kernel driver

This assumes:

- You are running Ubuntu with Secure Boot enabled.
- You already have a MOK signing key at `~/module-signing/MOK.priv` and `~/module-signing/MOK.der`.
- You are inside the Spectrum kernel driver source directory, for example:
  `~/software-downloads/spcm4-3.13.23699`.

```bash
# Check which kernel you are currently running.
# The Spectrum kernel module must be built for this exact kernel version.
uname -r

# Install the tools and kernel headers needed to build and sign the driver.
# This is safe to rerun; apt will skip packages that are already installed.
sudo apt update
sudo apt install -y build-essential linux-headers-$(uname -r) mokutil openssl

# Move into the Spectrum kernel driver source directory.
# Change this path if your driver source is somewhere else.
cd ~/software-downloads/spcm4-3.13.23699

# Rebuild and install the Spectrum spcm4 kernel driver for the current kernel.
# This script compiles spcm4.ko, copies it into /lib/modules/$(uname -r),
# updates module dependencies, installs udev rules, and may try to load it.
# If Secure Boot is enabled, the final load step may fail until we sign it.
sudo ./make_spcm4_linux_kerneldrv.sh

# Confirm that the module now exists and print the installed module path.
# This should return a path to spcm4.ko under /lib/modules/$(uname -r)/...
modinfo -n spcm4

# Store the path to Ubuntu's kernel module signing helper.
SIGNFILE="/lib/modules/$(uname -r)/build/scripts/sign-file"

# Store the path to the installed Spectrum kernel module.
MODPATH="$(modinfo -n spcm4)"

# Sign the installed spcm4 kernel module using your enrolled MOK private key.
# This is required when Secure Boot rejects unsigned third-party modules.
sudo "$SIGNFILE" sha256 ~/module-signing/MOK.priv ~/module-signing/MOK.der "$MODPATH"

# Refresh module dependency information after signing.
sudo depmod -a

# Check whether the module now reports a signer.
# Expected output should include something like: spcm module signing
modinfo -F signer spcm4

# Check the signature key information embedded in the module.
modinfo -F sig_key spcm4

# Load the signed Spectrum kernel module.
sudo modprobe -v spcm4

# Show recent kernel messages, useful if modprobe fails.
# Look for messages about spcm4, unsigned modules, rejected keys, PCI, BARs, or resources.
sudo dmesg -T | grep -iE 'spcm|spectrum|unsigned|rejected|secure|lockdown|module|pci|bar|iommu|resource' | tail -100

# Confirm that the spcm4 module is loaded.
lsmod | grep spcm

# Check whether the Spectrum driver sees the card.
# A successful result should list /dev/spcm0 and the card type.
cat /proc/spcm4_cards

# Check that the device node was created.
ls -l /dev/spcm*
```

If `modprobe` still says `Key was rejected by service`, check whether the MOK key is enrolled:

```bash
# Check whether Secure Boot is enabled.
mokutil --sb-state

# Check whether your MOK public key is enrolled.
mokutil --test-key ~/module-signing/MOK.der
```

If the key is not enrolled:

```bash
# Import the public MOK key.
# You will be asked to create a one-time password.
sudo mokutil --import ~/module-signing/MOK.der

# Reboot afterwards.
# On the blue MOK Manager screen:
#   Enroll MOK → Continue → Yes → enter the password → reboot
sudo reboot
```

After rebooting, load and check again:

```bash
# Load the signed driver.
sudo modprobe -v spcm4

# Confirm that the driver sees the card.
cat /proc/spcm4_cards

# Confirm that /dev/spcm0 exists.
ls -l /dev/spcm*
```