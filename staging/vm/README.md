# Staging in a VM

The host running CI/dev may not have Docker. `run.sh` brings up a throwaway
Ubuntu/KVM VM, installs Docker inside it, and runs the staging `docker compose`
stack + the pytest e2e suite **inside the VM**. The multi-host DICOM network
(pacs / proxy / worker) therefore lives entirely in the guest.

```bash
bash staging/vm/run.sh
```

- The repo is shared into the guest over **9p** (`mount_tag=repo` → `/repo`); no
  SSH or port forwarding is needed.
- The guest writes `staging/.data/vm-result.txt` (the pytest output + proxy logs)
  and `staging/.data/vm-done` (completion sentinel) back through the share. Both
  are gitignored.
- Requires `qemu-system-x86_64`, `/dev/kvm`, `qemu-img`, `cloud-localds`, and
  outbound internet (for the cloud image + `apt` + the `orthancteam/orthanc`
  image pulls). The ~600 MB cloud image is cached in `WORK` (default
  `/tmp/orthanc-proxy-vm`) across runs.

Override `WORK=<dir>` and `TIMEOUT=<seconds>` as needed.
