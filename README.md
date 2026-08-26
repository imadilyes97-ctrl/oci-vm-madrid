# OCI VM Madrid — Auto-Retry

Crée automatiquement une VM Always Free Oracle Cloud à Madrid (eu-madrid-1).

## Fonctionnement

- **GitHub Actions** lance le script toutes les **5 minutes**
- Le script essaie de créer la VM **A1.Flex 2OCPU/12GB**
- Si "Out of host capacity" → attend le prochain run
- Quand la VM est créée → **notification ntfy** + arrêt automatique

## Sécurité Anti-Suspension

| Règle | Valeur |
|-------|--------|
| Intervalle entre tentatives | 5 min (via schedule GitHub Actions) |
| Max tentatives | 200 (~16 heures) |
| Boucle interne | NON — 1 tentative par run |
| Backoff | Automatique (schedule fixe) |
| Notification | ntfy à la création |
| Arrêt auto | Oui, quand VM créée |

## Secrets GitHub

| Secret | Description |
|--------|-------------|
| `OCI_TENANCY` | OCID du tenancy |
| `OCI_USER` | OCID de l'utilisateur API |
| `OCI_FINGERPRINT` | Fingerprint de la clé API |
| `OCI_PRIVATE_KEY` | Clé privée PEM complète |
| `NTFY_TOPIC` | Topic ntfy.sh pour notifications |

## VM créée

- **Shape:** VM.Standard.A1.Flex (2 OCPU, 12GB RAM)
- **OS:** Ubuntu 24.04 aarch64
- **Storage:** 50GB boot + 200GB block volume
- **Ports:** SSH (22), RDP (3389), HTTP (80), HTTPS (443)
- **User:** ilyes / imadil123
# CI
