# Ubuntu nvidea+cuda driver installation

We want to use the `ubuntu-drivers` to install the 'open' and 'server' drivers.
For example
```
sudo ubuntu-drivers install --gpgpu nvidia:590-server-open
sudo reboot
```

When you want to work with CUDA, only install the `cuda-toolkit` rather than `cuda` (this will break the driver stuff.)

https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/kernel-modules.html

https://ubuntu.com/server/docs/_sources/how-to/graphics/install-nvidia-drivers.md.txt

https://askubuntu.com/questions/1262401/what-is-the-nvidia-server-driver

```
sudo tee /etc/apt/preferences.d/99-block-cuda-driver-overrides >/dev/null <<'EOF'
Package: nvidia-* libnvidia-* linux-modules-nvidia-* xserver-xorg-video-nvidia-* cuda-drivers cuda-drivers-* nvidia-open nvidia-open-*
Pin: origin developer.download.nvidia.com
Pin-Priority: -1
EOF

sudo apt update
```