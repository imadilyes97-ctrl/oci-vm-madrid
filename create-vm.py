#!/usr/bin/env python3
"""
create-vm.py — VM Always Free Madrid (eu-madrid-1)
GitHub Actions retry script with anti-suspension protection

ANTI-SUSPENSION RULES (depuis l'ancien compte suspendu):
- Max 1 tentative par run GitHub Actions (pas de boucle interne)
- Backoff: schedule toutes les 5 min (pas 30s)
- Max 200 tentatives total (environ 16h)
- Stop automatique quand VM créée
- Notification ntfy à la création
- Log détaillé pour debugging
"""
import oci
import sys
import os
import json
import time
import urllib.request
import urllib.error

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ============================================================
# CONFIG
# ============================================================
TENANCY = os.environ.get('OCI_TENANCY', '')
USER_ID = os.environ.get('OCI_USER', '')
FINGERPRINT = os.environ.get('OCI_FINGERPRINT', '')
PRIVATE_KEY = os.environ.get('OCI_KEY', '')
REGION = os.environ.get('OCI_REGION', 'eu-madrid-1')
NTFY_TOPIC = os.environ.get('NTFY_TOPIC', '')
AD = 'VnBa:EU-MADRID-1-AD-1'
VM_NAME = 'jarvis-madrid'
SHAPE = 'VM.Standard.A1.Flex'
OCPU = 2
RAM_GB = 12
BOOT_GB = 50
BLOCK_GB = 200
SSH_PUB = os.environ.get('SSH_PUBLIC_KEY', 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJquOC1f4y2+wgxqnx52e/oBD7MnYMuNOuPDwdOT4Pjh jarvis-madrid')
RDP_USER = 'ilyes'
RDP_PASS = 'imadil123'
MAX_ATTEMPTS = 200
STATE_FILE = 'vm-state.json'

def log(msg):
    ts = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    print(f'[{ts}] {msg}', flush=True)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'attempts': 0, 'status': 'pending', 'last_error': '', 'vm_id': None, 'public_ip': None}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def notify(msg):
    """Envoie notification ntfy."""
    if not NTFY_TOPIC:
        log('  Pas de NTFY_TOPIC, skip notification')
        return
    try:
        url = f'https://ntfy.sh/{NTFY_TOPIC}'
        req = urllib.request.Request(url, data=msg.encode(), method='POST',
                                    headers={'Content-Type': 'text/plain'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                log(f'  ✔ ntfy OK (200)')
            else:
                log(f'  ⚠ ntfy status: {resp.status}')
    except Exception as e:
        log(f'  ⚠ ntfy error: {e}')

# ============================================================
# MAIN
# ============================================================
def main():
    state = load_state()

    # Vérifier max tentatives
    if state['attempts'] >= MAX_ATTEMPTS:
        log(f'MAX ATTEINT ({MAX_ATTEMPTS} tentatives). Arret.')
        notify(f'VM Madrid: ABANDON apres {MAX_ATTEMPTS} tentatives')
        sys.exit(0)

    # Si VM déjà créée, arrêter
    if state['status'] == 'created':
        log('VM deja creee! Rien a faire.')
        sys.exit(0)

    state['attempts'] += 1
    log(f'=== TENTATIVE {state["attempts"]}/{MAX_ATTEMPTS} ===')

    # Config OCI
    if not all([TENANCY, USER_ID, FINGERPRINT, PRIVATE_KEY]):
        log('ERREUR: OCI secrets manquants')
        sys.exit(1)

    # Écrire la clé privée temporairement
    key_path = '/tmp/oci_key.pem'
    with open(key_path, 'w') as f:
        f.write(PRIVATE_KEY)
    os.chmod(key_path, 0o600)

    # Config OCI
    oci_config = {
        'tenancy': TENANCY,
        'user': USER_ID,
        'fingerprint': FINGERPRINT,
        'key_file': key_path,
        'region': REGION,
    }

    compute = oci.core.ComputeClient(oci_config)
    network = oci.core.VirtualNetworkClient(oci_config)

    try:
        # 1. Vérifier/créer ressources réseau
        log('Verification reseau...')

        # VCN
        vcns = network.list_vcns(TENANCY)
        vcn = None
        for v in vcns.data:
            if 'jarvis' in v.display_name.lower():
                vcn = v
                break
        if not vcn:
            vcn = network.create_vcn(oci.core.models.CreateVcnDetails(
                compartment_id=TENANCY, cidr_blocks=['10.0.0.0/16'],
                display_name='jarvis-vcn', dns_label='jarvisvcn'
            )).data
            log(f'  VCN cree: {vcn.id[:30]}...')
            time.sleep(5)
        else:
            log(f'  VCN existant: {vcn.id[:30]}...')

        # IGW
        igws = network.list_internet_gateways(TENANCY, vcn_id=vcn.id)
        igw = igws.data[0] if igws.data else None
        if not igw:
            igw = network.create_internet_gateway(oci.core.models.CreateInternetGatewayDetails(
                compartment_id=TENANCY, vcn_id=vcn.id, is_enabled=True, display_name='jarvis-igw'
            )).data
            log(f'  IGW cree')
            time.sleep(3)

        # Route Table
        rts = network.list_route_tables(TENANCY, vcn_id=vcn.id)
        rt = rts.data[0] if rts.data else None
        if not rt:
            rt = network.create_route_table(oci.core.models.CreateRouteTableDetails(
                compartment_id=TENANCY, vcn_id=vcn.id, display_name='jarvis-rt',
                route_rules=[oci.core.models.RouteRule(
                    destination='0.0.0.0/0', destination_type='CIDR_BLOCK',
                    network_entity_id=igw.id
                )]
            )).data
            log(f'  RT creee')
            time.sleep(3)

        # Security List
        sls = network.list_security_lists(TENANCY, vcn_id=vcn.id)
        sl = sls.data[0] if sls.data else None
        if not sl:
            sl = network.create_security_list(oci.core.models.CreateSecurityListDetails(
                compartment_id=TENANCY, vcn_id=vcn.id, display_name='jarvis-sl',
                ingress_security_rules=[
                    oci.core.models.IngressSecurityRule(
                        protocol='6', source='0.0.0.0/0',
                        tcp_options=oci.core.models.TcpOptions(
                            destination_port_range=oci.core.models.PortRange(min=22, max=22))),
                    oci.core.models.IngressSecurityRule(
                        protocol='6', source='0.0.0.0/0',
                        tcp_options=oci.core.models.TcpOptions(
                            destination_port_range=oci.core.models.PortRange(min=3389, max=3389))),
                    oci.core.models.IngressSecurityRule(
                        protocol='6', source='0.0.0.0/0',
                        tcp_options=oci.core.models.TcpOptions(
                            destination_port_range=oci.core.models.PortRange(min=443, max=443))),
                    oci.core.models.IngressSecurityRule(
                        protocol='6', source='0.0.0.0/0',
                        tcp_options=oci.core.models.TcpOptions(
                            destination_port_range=oci.core.models.PortRange(min=80, max=80))),
                ],
                egress_security_rules=[
                    oci.core.models.EgressSecurityRule(protocol='all', destination='0.0.0.0/0')]
            )).data
            log(f'  SL creee')
            time.sleep(3)

        # Subnet
        subs = network.list_subnets(TENANCY, vcn_id=vcn.id)
        sub = subs.data[0] if subs.data else None
        if not sub:
            sub = network.create_subnet(oci.core.models.CreateSubnetDetails(
                compartment_id=TENANCY, vcn_id=vcn.id, cidr_block='10.0.0.0/24',
                display_name='jarvis-subnet', dns_label='jarvissub',
                route_table_id=rt.id, security_list_ids=[sl.id],
                availability_domain=AD
            )).data
            log(f'  Subnet cree')
            time.sleep(10)
        else:
            log(f'  Subnet existant')

        # 2. Image Ubuntu
        images = compute.list_images(
            TENANCY, operating_system='Canonical Ubuntu',
            operating_system_version='24.04',
            sort_by='TIMECREATED', sort_order='DESC'
        )
        img = None
        for i in images.data:
            if 'aarch64' in i.display_name.lower():
                img = i
                break
        if not img:
            img = images.data[0]
        log(f'  Image: {img.display_name}')

        # 3. Lancer la VM
        log('Lancement VM A1.Flex 2OCPU/12GB...')
        vm = compute.launch_instance(oci.core.models.LaunchInstanceDetails(
            compartment_id=TENANCY,
            availability_domain=AD,
            display_name=VM_NAME,
            shape=SHAPE,
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=OCPU, memory_in_gbs=RAM_GB
            ),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type='image', image_id=img.id,
                boot_volume_size_in_gbs=BOOT_GB
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=sub.id, assign_public_ip=True,
                display_name='jarvis-vnic'
            ),
            metadata={'ssh_authorized_keys': SSH_PUB},
            agent_config=oci.core.models.LaunchInstanceAgentConfigDetails(
                is_monitoring_disabled=False, is_management_disabled=False
            )
        )).data

        log(f'  VM lancee: {vm.id}')
        log(f'  State: {vm.lifecycle_state}')

        # 4. Attendre RUNNING
        log('Attente RUNNING (max 10 min)...')
        for i in range(60):
            try:
                s = compute.get_instance(vm.id).data.lifecycle_state
                log(f'  [{i+1}/60] {s}')
                if s == 'RUNNING':
                    log('  VM RUNNING!')
                    break
                if s in ('TERMINATED', 'FAULTY'):
                    log(f'  VM {s} - arret')
                    state['status'] = 'failed'
                    state['last_error'] = f'VM {s}'
                    save_state(state)
                    sys.exit(1)
            except Exception as e:
                log(f'  [{i+1}/60] check error: {str(e)[:80]}')
            time.sleep(10)

        # 5. IP publique
        atts = compute.list_vnic_instances(TENANCY, instance_id=vm.id)
        ip = 'EN ATTENTE'
        if atts.data:
            try:
                vnic = network.get_vnic(atts.data[0].vnic_id)
                ip = vnic.data.public_ip
            except:
                pass
        log(f'  IP: {ip}')

        # 6. Block Volume 200GB
        log('Block volume 200GB...')
        try:
            vol = compute.create_volume(oci.core.models.CreateVolumeDetails(
                compartment_id=TENANCY, availability_domain=AD,
                display_name='data-jarvis', size_in_gbs=BLOCK_GB
            )).data
            compute.attach_volume(oci.core.models.AttachParavirtualizedVolumeDetails(
                instance_id=vm.id, volume_id=vol.id,
                display_name='data-jarvis-attach'
            ))
            log(f'  Volume attache: {vol.id[:30]}...')
        except Exception as e:
            log(f'  Volume error (non-fatal): {e}')

        # 7. SUCCES!
        state['status'] = 'created'
        state['vm_id'] = vm.id
        state['public_ip'] = ip
        state['created_at'] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
        save_state(state)

        msg = f'''VM MADRID CREEE!

IP: {ip}
SSH: ssh -i key ubuntu@{ip}
RDP: {RDP_USER} / {RDP_PASS} port 3389
Shape: A1.Flex {OCPU}OCPU/{RAM_GB}GB
Volume: {BLOCK_GB}GB
Tentatives: {state["attempts"]}
'''
        log(msg)
        notify(msg)
        log('=== SUCCES ===')

    except oci.exceptions.OutofHostCapacityError:
        log('  Out of host capacity (normal Always Free)')
        state['last_error'] = 'Out of host capacity'
        save_state(state)
        # Pas d'erreur fatale - le schedule va retry

    except oci.exceptions.TooManyRequestsError:
        log('  Rate limited (429) - prochain run dans 5 min')
        state['last_error'] = 'Rate limited 429'
        save_state(state)

    except oci.exceptions.ConnectTimeout:
        log('  Timeout reseau - prochain run dans 5 min')
        state['last_error'] = 'ConnectTimeout'
        save_state(state)

    except Exception as e:
        err = str(e)[:200]
        log(f'  Erreur: {err}')
        state['last_error'] = err
        save_state(state)

    # Cleanup
    try:
        os.unlink(key_path)
    except:
        pass

if __name__ == '__main__':
    main()
