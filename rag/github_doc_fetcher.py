import httpx
import json
import time
from rag.rag_config import PROVIDERS, GITHUB_API_BASE, COMMITS_FILE, GITHUB_TOKEN


class GitHubFetcher:
    """
    Télécharge les fichiers de documentation Terraform depuis GitHub.
    Utilise l'API GitHub REST pour éviter de cloner des repos entiers.

    Rate limits :
    - Sans token : 60 requêtes/heure
    - Avec token : 5000 requêtes/heure
    """

    def __init__(self, token: str = None):
        headers = {"Accept": "application/vnd.github.v3+json"}
        resolved_token = token or GITHUB_TOKEN
        if resolved_token:
            headers["Authorization"] = f"token {resolved_token}"
        self.client = httpx.Client(headers=headers, timeout=30.0)

    def get_latest_commit_sha(self, provider: str) -> str:
        """Récupère le SHA du dernier commit sur la branche main."""
        config = PROVIDERS[provider]
        url = (
            f"{GITHUB_API_BASE}/repos/{config['repo']}/commits/"
            f"{config['branch']}"
        )
        response = self.client.get(url)
        response.raise_for_status()
        return response.json()["sha"]

    def list_doc_files(self, provider: str) -> list[dict]:
        """
        Liste tous les fichiers dans le dossier docs du provider.
        Utilise l'API Git Trees (recursive=1) pour dépasser la limite de 1000
        fichiers de l'API /contents/.
        """
        config = PROVIDERS[provider]
        repo = config["repo"]
        docs_path = config["docs_path"]
        branch = config["branch"]

        # Récupère le SHA du tree racine via le dernier commit
        commit_url = f"{GITHUB_API_BASE}/repos/{repo}/commits/{branch}"
        commit_resp = self.client.get(commit_url)
        commit_resp.raise_for_status()
        root_tree_sha = commit_resp.json()["commit"]["tree"]["sha"]

        # Navigue jusqu'au sous-répertoire docs (ex: website/docs/r)
        current_sha = root_tree_sha
        for part in docs_path.split("/"):
            tree_url = f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{current_sha}"
            tree_resp = self.client.get(tree_url)
            tree_resp.raise_for_status()
            subtree = next(
                (
                    item
                    for item in tree_resp.json().get("tree", [])
                    if item["path"] == part and item["type"] == "tree"
                ),
                None,
            )
            if subtree is None:
                return []
            current_sha = subtree["sha"]
            time.sleep(0.05)

        # Liste tous les fichiers du dossier docs (pas de limite 1000)
        tree_url = f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{current_sha}"
        tree_resp = self.client.get(tree_url)
        tree_resp.raise_for_status()
        tree_data = tree_resp.json()

        if tree_data.get("truncated"):
            print(f"  WARNING: tree truncated for {provider}, some files may be missing")

        all_files = []
        for item in tree_data.get("tree", []):
            if item["type"] != "blob":
                continue
            name = item["path"]
            if not (name.endswith(".html.markdown") or name.endswith(".md")):
                continue
            download_url = (
                f"https://raw.githubusercontent.com/{repo}/{branch}/{docs_path}/{name}"
            )
            all_files.append({
                "name": name,
                "sha": item["sha"],
                "download_url": download_url,
                "path": f"{docs_path}/{name}",
            })

        return all_files

    def download_file_content(self, download_url: str) -> str:
        """Télécharge le contenu brut d'un fichier Markdown."""
        response = self.client.get(download_url)
        response.raise_for_status()
        return response.text

    def load_last_commits(self) -> dict:
        """Charge les derniers SHA de commits connus."""
        if COMMITS_FILE.exists():
            return json.loads(COMMITS_FILE.read_text())
        return {}

    def save_last_commits(self, commits: dict):
        """Sauvegarde les SHA de commits après une mise à jour."""
        COMMITS_FILE.write_text(json.dumps(commits, indent=2))

    def needs_update(self, provider: str) -> tuple[bool, str]:
        """
        Vérifie si le provider a de nouveaux commits depuis
        la dernière construction de la base RAG.

        Returns:
            (True, new_sha) si mise à jour nécessaire
            (False, current_sha) si déjà à jour
        """
        last_commits = self.load_last_commits()
        current_sha = self.get_latest_commit_sha(provider)
        last_known = last_commits.get(provider, "")

        if current_sha != last_known:
            return True, current_sha
        return False, current_sha
