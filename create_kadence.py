"""Kadence: Always Free A1 (ARM) VM - 2 OCPU / 12 GB RAM / 145 GB boot volume.

ONE attempt per run (a scheduler calls it every 10 min). Idempotent: if an instance named
Kadence already exists it just reports it. Reuses the control-hub VCN/subnet (creates if missing).

Exit codes / final stdout line (machine readable):
  KADENCE_RUNNING <ip>   -> VM exists and is RUNNING (success)
  KADENCE_PENDING <state>-> VM exists but is not RUNNING yet
  KADENCE_NO_CAPACITY    -> Oracle has no A1 capacity right now (retry later)
  KADENCE_BLOCKED <why>  -> quota / auth / other error that retrying will not fix

Set KADENCE_NO_JITTER=1 to skip the random wait (used by GitHub Actions, which has its own
scheduling jitter and bills for every minute spent waiting).
"""
import os, sys, time
import oci

NAME, SHAPE = "Kadence", "VM.Standard.A1.Flex"
OCPUS, MEM_GB, BOOT_GB = 2, 12, 145

cfg = oci.config.from_file()
t = cfg["tenancy"]
vn, cc = oci.core.VirtualNetworkClient(cfg), oci.core.ComputeClient(cfg)
idn, lim = oci.identity.IdentityClient(cfg), oci.limits.LimitsClient(cfg)


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def done(line, code=0):
    print(line, flush=True)
    sys.exit(code)


def public_ip(inst):
    att = cc.list_vnic_attachments(t, instance_id=inst.id).data
    return vn.get_vnic(att[0].vnic_id).data.public_ip if att else "?"


# 1. Already exists?
insts = oci.pagination.list_call_get_all_results(cc.list_instances, t).data
live = [i for i in insts if i.display_name == NAME and i.lifecycle_state not in ("TERMINATED", "TERMINATING")]
if live:
    i = live[0]
    if i.lifecycle_state == "RUNNING":
        log("Kadence is RUNNING", i.shape_config.ocpus, "OCPU", i.shape_config.memory_in_gbs, "GB")
        done(f"KADENCE_RUNNING {public_ip(i)}")
    done(f"KADENCE_PENDING {i.lifecycle_state}")

# 2. Quota pre-check (free tier: 4 OCPU / 24 GB A1 and 200 GB block storage total)
ad = idn.list_availability_domains(t).data[0].name
checks = [("standard-a1-core-count", OCPUS, True), ("standard-a1-memory-count", MEM_GB, True),
          ("total-free-storage-gb-regional", BOOT_GB, False)]
for name, need, scoped in checks:
    svc = "block-storage" if "storage" in name else "compute"
    try:
        kw = {"availability_domain": ad} if scoped else {}
        r = lim.get_resource_availability(svc, name, t, **kw).data
        log(f"{name}: available={r.available} need={need}")
        if r.available is not None and r.available < need:
            done(f"KADENCE_BLOCKED not enough {name}: available {r.available}, need {need}", 2)
    except oci.exceptions.ServiceError as e:
        log("limit check skipped:", name, e.status)

# 3. Network (reuse control-hub VCN/subnet)
vcns = [v for v in vn.list_vcns(t).data if v.display_name == "control-hub-vcn" and v.lifecycle_state == "AVAILABLE"]
if not vcns:
    done("KADENCE_BLOCKED control-hub-vcn missing - run create_control_hub.py network section first", 2)
subs = [s for s in vn.list_subnets(t, vcn_id=vcns[0].id).data
        if s.display_name == "control-hub-public" and s.lifecycle_state == "AVAILABLE"]
if not subs:
    done("KADENCE_BLOCKED control-hub-public subnet missing", 2)
subnet = subs[0]

# 4. Image: latest Ubuntu 24.04 aarch64 (full)
imgs = oci.pagination.list_call_get_all_results(
    cc.list_images, t, operating_system="Canonical Ubuntu", operating_system_version="24.04",
    shape=SHAPE, sort_by="TIMECREATED", sort_order="DESC").data
imgs = [i for i in imgs if "Minimal" not in i.display_name and "aarch64" in i.display_name]
image = imgs[0]
log("image:", image.display_name)

ssh_pub = open(os.path.expanduser("~/.ssh/control-hub.pub")).read().strip()
details = oci.core.models.LaunchInstanceDetails(
    compartment_id=t, availability_domain=ad, display_name=NAME, shape=SHAPE,
    shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=OCPUS, memory_in_gbs=MEM_GB),
    source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=image.id, boot_volume_size_in_gbs=BOOT_GB),
    create_vnic_details=oci.core.models.CreateVnicDetails(
        subnet_id=subnet.id, assign_public_ip=True, hostname_label="kadence", display_name="kadence-vnic"),
    metadata={"ssh_authorized_keys": ssh_pub},
    availability_config=oci.core.models.LaunchInstanceAvailabilityConfigDetails(recovery_action="RESTORE_INSTANCE"),
    instance_options=oci.core.models.InstanceOptions(are_legacy_imds_endpoints_disabled=True),
    freeform_tags={"purpose": "kadence", "tier": "always-free"})

# 5. Jitter (local scheduler only): attempts land 10-15 min apart instead of on an exact beat.
#    Skipped when KADENCE_NO_JITTER=1 (GitHub Actions).
if os.environ.get("KADENCE_NO_JITTER") != "1":
    import json, random
    STATE = os.path.expanduser("~/.oci/scripts/kadence_state.json")
    try:
        last = json.load(open(STATE)).get("last_attempt", 0)
    except Exception:
        last = 0
    target = max(time.time() + random.uniform(0, 300), last + 600)
    wait = max(0, min(target - time.time(), 420))
    log(f"jitter: waiting {wait:.0f}s before launch attempt")
    time.sleep(wait)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"last_attempt": time.time()}, open(STATE, "w"))

# 6. Single launch attempt
try:
    inst = cc.launch_instance(details).data
except oci.exceptions.ServiceError as e:
    msg = (e.message or "")
    log(f"launch failed: {e.status} {e.code} {msg[:160]}")
    if "capacity" in msg.lower() or e.status in (429, 500, 503):
        done("KADENCE_NO_CAPACITY")
    done(f"KADENCE_BLOCKED {e.status} {e.code}", 2)

log("launch accepted, waiting for RUNNING ...")
inst = oci.wait_until(cc, cc.get_instance(inst.id), "lifecycle_state", "RUNNING", max_wait_seconds=900).data
done(f"KADENCE_RUNNING {public_ip(inst)}")
