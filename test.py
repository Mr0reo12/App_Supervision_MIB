import requests

# --- Configuration de l'API ---
LOGIN_URL = "https://57.203.253.112:443/api/auth/login"
ASSETS_URL = "https://57.203.253.112:443/api/v1/assets/search"
STATUS_URL = "https://57.203.253.112:443/api/v1/assets/{asset_id}/status"

# --- Identifiants ---
USER_ID = "CAXCL164"
PASSWORD = "G/WnZ1n%LN#VYa"

# --- Fonction pour obtenir un token ---
def get_token():
    data = {"userId": USER_ID, "password": PASSWORD}
    response = requests.post(LOGIN_URL, json=data, verify=False)
    response.raise_for_status()
    return response.json()["accessToken"]

# --- Fonction pour rechercher une machine par son nom ---
def find_asset_by_name(asset_name, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "pagination": {"page": 1, "perPage": 100},
        "filtering": [{"property": "assetName", "rule": "eq", "value": asset_name}]
    }
    response = requests.post(ASSETS_URL, headers=headers, json=payload, verify=False)
    response.raise_for_status()
    data = response.json().get("data", [])
    return data[0] if data else None

# --- Fonction pour obtenir le statut d'une machine ---
def get_status(asset_id, organization, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    url = STATUS_URL.format(asset_id=asset_id) + f"/status/{organization}"
    response = requests.get(url, headers=headers, verify=False)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()

# --- Fonction principale ---
def check_vm_status(asset_name):
    print(f"[INFO] Vérification du statut pour la VM '{asset_name}'...")
    token = get_token()
    
    asset = find_asset_by_name(asset_name, token)
    if not asset:
        print(f"[ERREUR] Machine '{asset_name}' introuvable.")
        return

    asset_id = asset["assetId"]
    organization = asset["organization"]

    status = get_status(asset_id, organization, token)
    if not status:
        print(f"[AVERTISSEMENT] Aucun statut trouvé pour '{asset_name}' dans '{organization}'.")
        return

    print(f"[INFO] Statuts des services pour '{asset_name}':")
    for service, service_status in status.get("monitored_services", {}).items():
        print(f"  ➔ Service: {service} ➔ Statut: {service_status}")

# --- Exécution directe (machine statique) ---
if __name__ == "__main__":
    vm_name_to_test = "CHUMR1DB501"  # VM à tester statiquement
    check_vm_status(vm_name_to_test)
