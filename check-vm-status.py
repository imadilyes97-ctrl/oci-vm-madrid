#!/usr/bin/env python3
"""
check-vm-status.py — Vérifie l'état actuel des VMs dans la tenancy Madrid.
Fait 1 seul appel API (list_instances + list_volumes) + commit le résultat JSON.
Sûr anti-suspension : ~3 appels API / run, max 1 run / 10 min.
"""
import os
import sys
import json
import time
import oci

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def log(msg):
    print(f"[check] {msg}", flush=True)

# --- Config depuis env (GitHub Actions secrets) ---
CONFIG = {
    "user": os.environ.get("OCI_USER"),
    "fingerprint": os.environ.get("OCI_FINGERPRINT"),
    "tenancy": os.environ.get("OCI_TENANCY"),
    "region": os.environ.get("OCI_REGION", "eu-madrid-1"),
    "key_content": os.environ.get("OCI_KEY") or os.environ.get("OCI_PRIVATE_KEY"),
}

missing = [k for k, v in CONFIG.items() if not v]
if missing:
    log(f"❌ Missing config: {missing}")
    sys.exit(1)

# Écrire la clé privée dans un fichier temporaire (OCI SDK exige un path)
key_path = "/tmp/oci_api_key.pem"
with open(key_path, "w") as f:
    f.write(CONFIG["key_content"])
os.chmod(key_path, 0o600)

oci_config = {
    "user": CONFIG["user"],
    "fingerprint": CONFIG["fingerprint"],
    "tenancy": CONFIG["tenancy"],
    "region": CONFIG["region"],
    "key_file": key_path,
}

# --- Init clients ---
identity = oci.identity.IdentityClient(oci_config)
compute = oci.core.ComputeClient(oci_config)
network = oci.core.VirtualNetworkClient(oci_config)
blockstorage = oci.core.BlockstorageClient(oci_config)
for c in [identity, compute, network, blockstorage]:
    c.base_client.session.timeout = (10, 60)

tenancy = oci_config["tenancy"]
report = {
    "checked_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    "region": oci_config["region"],
    "tenancy_ocid": tenancy,
    "instances": [],
    "volumes": [],
    "vcns": [],
    "summary": {},
    "errors": [],
}

# --- 1. User info (valide l'auth) ---
try:
    u = identity.get_user(CONFIG["user"]).data
    report["summary"]["user"] = u.name
    report["summary"]["user_email"] = u.email
    log(f"✅ Auth OK: {u.name} <{u.email}>")
except Exception as e:
    report["errors"].append(f"identity.get_user: {e}")
    log(f"❌ Auth failed: {e}")
    # Si l'auth échoue on continue quand même (les autres calls vont échouer pareil)

# --- 2. Instances ---
try:
    instances = compute.list_instances(tenancy).data
    log(f"Instances trouvées: {len(instances)}")
    for i in instances:
        inst_info = {
            "id": i.id,
            "display_name": i.display_name,
            "lifecycle_state": i.lifecycle_state,
            "shape": i.shape,
            "ocpus": getattr(i.shape_config, "ocpus", None) if i.shape_config else None,
            "memory_gb": getattr(i.shape_config, "memory_in_gbs", None) if i.shape_config else None,
            "availability_domain": i.availability_domain,
            "time_created": str(i.time_created) if i.time_created else None,
            "region": i.region,
        }
        # Récupérer l'IP publique si RUNNING
        if i.lifecycle_state == "RUNNING":
            try:
                vnic_atts = compute.list_vnic_attachments(tenancy, instance_id=i.id).data
                if vnic_atts:
                    vnic = network.get_vnic(vnic_atts[0].vnic_id).data
                    inst_info["public_ip"] = vnic.public_ip
                    inst_info["private_ip"] = vnic.private_ip
            except Exception as ve:
                inst_info["public_ip_error"] = str(ve)[:100]
        report["instances"].append(inst_info)
        log(f"  - {i.display_name:30s} | {i.lifecycle_state:12s} | {i.shape}")
        if "public_ip" in inst_info:
            log(f"      IP publique: {inst_info['public_ip']}")
except Exception as e:
    report["errors"].append(f"compute.list_instances: {e}")
    log(f"❌ list_instances: {e}")

# --- 3. Block volumes ---
try:
    vols = blockstorage.list_volumes(compartment_id=tenancy).data
    for v in vols:
        report["volumes"].append({
            "id": v.id,
            "display_name": v.display_name,
            "size_gb": v.size_in_gbs,
            "lifecycle_state": v.lifecycle_state,
        })
    log(f"Volumes: {len(vols)}")
except Exception as e:
    report["errors"].append(f"blockstorage.list_volumes: {e}")
    log(f"⚠️ list_volumes: {e}")

# --- 4. VCNs ---
try:
    vcns = network.list_vcns(tenancy).data
    for v in vcns:
        report["vcns"].append({
            "id": v.id,
            "display_name": v.display_name,
            "cidr": v.cidr_blocks,
            "lifecycle_state": v.lifecycle_state,
        })
except Exception as e:
    report["errors"].append(f"network.list_vcns: {e}")

# --- Summary ---
running = [i for i in report["instances"] if i["lifecycle_state"] == "RUNNING"]
report["summary"]["instance_count"] = len(report["instances"])
report["summary"]["running_count"] = len(running)
report["summary"]["vm_madrid_exists"] = any(
    i["display_name"] == "jarvis-madrid" and i["lifecycle_state"] in ("RUNNING", "STOPPED", "STARTING")
    for i in report["instances"]
)

# --- Écrire JSON ---
out_path = os.environ.get("STATUS_OUTPUT", "vm-status.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

log(f"\n{'='*50}")
log(f"📊 RÉSUMÉ")
log(f"{'='*50}")
log(f"User: {report['summary'].get('user')} <{report['summary'].get('user_email')}>")
log(f"Instances totales: {report['summary']['instance_count']}")
log(f"Instances RUNNING: {report['summary']['running_count']}")
log(f"VM 'jarvis-madrid' existe: {report['summary']['vm_madrid_exists']}")
if running:
    for i in running:
        log(f"  ▶ {i['display_name']} → IP {i.get('public_ip', '?')}")
log(f"Rapport: {out_path}")
log(f"{'='*50}")

# Exit code non-zéro si VM absente (pour signaler aux GitHub Actions)
sys.exit(0 if report["summary"]["vm_madrid_exists"] else 1)