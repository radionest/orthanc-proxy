# Encrypted SSD storage (LUKS) for the proxy cache

The proxy keeps transit PHI on an SSD encrypted at rest. Both `StorageDirectory`
and `IndexDirectory` live on the same encrypted volume so they stay consistent
across reboots, and Orthanc starts only after the volume is unlocked and mounted.

## One-time setup

```bash
# 1. Create the LUKS container on the SSD partition (DESTROYS data on it)
cryptsetup luksFormat /dev/sdX1

# 2. Add a keyfile so the volume unlocks unattended at boot
dd if=/dev/urandom of=/etc/orthanc-proxy.key bs=4096 count=1
chmod 0400 /etc/orthanc-proxy.key
cryptsetup luksAddKey /dev/sdX1 /etc/orthanc-proxy.key

# 3. Open, format, mount
cryptsetup open --key-file /etc/orthanc-proxy.key /dev/sdX1 orthanc-proxy
mkfs.ext4 /dev/mapper/orthanc-proxy
mkdir -p /var/lib/orthanc-proxy
mount /dev/mapper/orthanc-proxy /var/lib/orthanc-proxy
mkdir -p /var/lib/orthanc-proxy/storage /var/lib/orthanc-proxy/db
chown -R orthanc:orthanc /var/lib/orthanc-proxy
```

## Auto-unlock at boot

`/etc/crypttab`:
```
orthanc-proxy  /dev/sdX1  /etc/orthanc-proxy.key  luks
```

`/etc/fstab`:
```
/dev/mapper/orthanc-proxy  /var/lib/orthanc-proxy  ext4  defaults  0  2
```

The `RequiresMountsFor=/var/lib/orthanc-proxy` line in `orthanc-proxy.service`
makes systemd wait for the mount (which waits for the crypttab unlock) before
starting Orthanc — so the index and storage are never accessed unencrypted or
out of sync.

> Use `/dev/disk/by-uuid/...` instead of `/dev/sdX1` in production.
