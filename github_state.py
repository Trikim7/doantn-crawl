"""Lưu lịch sử thông báo trên nhánh bot-state bằng token tạm của Actions."""
import argparse
import base64
import json
import os
import re
from pathlib import Path

import requests

BRANCH = "bot-state"
REMOTE_FILE = "iuh_event_state.json"


def validate_state(state):
    if (not isinstance(state, dict) or not isinstance(state.get("sent"), list)
            or any(not isinstance(value, str) for value in state["sent"])):
        raise RuntimeError("Lịch sử không hợp lệ: sent phải là danh sách mã chuỗi.")
    if "subscribers_encrypted" in state and not isinstance(state["subscribers_encrypted"], str):
        raise RuntimeError("Danh sách người nhận phải được mã hóa.")
    return state


class GitHubState:
    def __init__(self, session, repository, source_sha):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise RuntimeError("GITHUB_REPOSITORY không hợp lệ.")
        self.session = session
        self.root = f"https://api.github.com/repos/{repository}"
        self.source_sha = source_sha

    def request(self, method, path, allow_missing=False, **kwargs):
        try:
            response = self.session.request(method, self.root + path, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError("Không kết nối được GitHub để lưu/đọc lịch sử.") from exc
        if response.status_code == 404 and allow_missing:
            return None
        if not response.ok:
            raise RuntimeError(f"GitHub history API trả HTTP {response.status_code}.")
        return response.json()

    def remote_state(self):
        data = self.request("GET", f"/contents/{REMOTE_FILE}", allow_missing=True,
                            params={"ref": BRANCH})
        if data is None:
            return None, None
        state = validate_state(json.loads(base64.b64decode(data["content"]).decode("utf-8")))
        return state, data["sha"]

    def restore(self, path):
        # Repo private không truy cập được cũng trả 404: không coi là lịch sử rỗng.
        self.request("GET", "")
        state, _ = self.remote_state()
        if state is None:
            state = {"sent": []}
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as stream:
            stream.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)
        print(f"Đã khôi phục lịch sử {len(state['sent'])} hoạt động.")

    def save(self, path):
        if not path.is_file():
            print("Không có lịch sử để lưu.")
            return
        state = validate_state(json.loads(path.read_text(encoding="utf-8")))
        remote, sha = self.remote_state()
        if remote == state:
            print("Lịch sử không đổi; không tạo commit.")
            return
        ref = self.request("GET", f"/git/ref/heads/{BRANCH}", allow_missing=True)
        if ref is None:
            if not re.fullmatch(r"[0-9a-fA-F]{40,64}", self.source_sha):
                raise RuntimeError("Thiếu GITHUB_SHA hợp lệ để tạo nhánh lịch sử.")
            self.request("POST", "/git/refs", json={"ref": f"refs/heads/{BRANCH}", "sha": self.source_sha})
        content = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        payload = {"message": "Update IUH notification history", "branch": BRANCH,
                   "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
        if sha is not None:
            payload["sha"] = sha
        self.request("PUT", f"/contents/{REMOTE_FILE}", json=payload)
        print(f"Đã lưu lịch sử {len(state['sent'])} hoạt động trên nhánh {BRANCH}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("restore", "save"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("Thiếu GITHUB_TOKEN.")
    with requests.Session() as session:
        session.headers.update({"Authorization": f"Bearer {token}",
                                "Accept": "application/vnd.github+json",
                                "X-GitHub-Api-Version": "2022-11-28"})
        store = GitHubState(session, os.environ.get("GITHUB_REPOSITORY", ""),
                            os.environ.get("GITHUB_SHA", ""))
        try:
            getattr(store, args.action)(args.path)
        except (RuntimeError, ValueError, KeyError, OSError) as exc:
            parser.exit(1, f"Lỗi lịch sử: {exc}\n")


if __name__ == "__main__":
    main()
