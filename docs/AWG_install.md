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