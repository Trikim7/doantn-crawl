import base64
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from github_state import GitHubState, validate_state


def response(data, status=200):
    return Mock(status_code=status, ok=status < 400, json=Mock(return_value=data))


def remote(state):
    return {"content": base64.b64encode(json.dumps(state).encode()).decode(), "sha": "old-file-sha"}


class GitHubStateTests(unittest.TestCase):
    def test_restore_existing_history(self):
        session = Mock()
        session.request.side_effect = [response({}), response(remote({"sent": ["123"]}))]
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / "nested/state.json"
            GitHubState(session, "owner/repo", "a" * 40).restore(path)
            self.assertEqual(json.loads(path.read_text()), {"sent": ["123"]})

    def test_first_run_restores_empty_state(self):
        session = Mock()
        session.request.side_effect = [response({}), response({}, 404)]
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / "state.json"
            GitHubState(session, "owner/repo", "a" * 40).restore(path)
            self.assertEqual(json.loads(path.read_text()), {"sent": []})

    def test_inaccessible_repository_does_not_create_empty_history(self):
        session = Mock()
        session.request.return_value = response({}, 404)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            with self.assertRaises(RuntimeError):
                GitHubState(session, "owner/repo", "a" * 40).restore(path)
            self.assertFalse(path.exists())

    def test_save_creates_branch_and_writes_history(self):
        session = Mock()
        session.request.side_effect = [response({}, 404), response({}, 404), response({}, 201), response({}, 201)]
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / "state.json"
            path.write_text('{"sent": ["123"]}')
            GitHubState(session, "owner/repo", "a" * 40).save(path)
        calls = session.request.call_args_list
        self.assertEqual(calls[2].args[0], "POST")
        self.assertEqual(calls[2].kwargs["json"]["ref"], "refs/heads/bot-state")
        self.assertEqual(calls[3].args[0], "PUT")
        payload = calls[3].kwargs["json"]
        self.assertEqual(json.loads(base64.b64decode(payload["content"])), {"sent": ["123"]})
        self.assertNotIn("sha", payload)

    def test_unchanged_history_does_not_write(self):
        session = Mock()
        session.request.return_value = response(remote({"sent": ["123"]}))
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / "state.json"
            path.write_text('{"sent": ["123"]}')
            GitHubState(session, "owner/repo", "a" * 40).save(path)
        self.assertEqual(session.request.call_count, 1)

    def test_update_includes_previous_file_sha(self):
        session = Mock()
        session.request.side_effect = [response(remote({"sent": ["123"]})), response({}), response({})]
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / "state.json"
            path.write_text('{"sent": ["123", "456"]}')
            GitHubState(session, "owner/repo", "a" * 40).save(path)
        self.assertEqual(session.request.call_args.kwargs["json"]["sha"], "old-file-sha")

    def test_invalid_history_is_rejected(self):
        for data in ({}, [], {"sent": "123"}, {"sent": [123]}):
            with self.assertRaises(RuntimeError):
                validate_state(data)


if __name__ == "__main__":
    unittest.main()
