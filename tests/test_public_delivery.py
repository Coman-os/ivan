"""Offline acceptance against the actual shipped packages. No account required.

Run from the public repository: python3 -B -m unittest discover -s tests -v
For a staged tree, set IVAN_DELIVERY_ROOT to its absolute path.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(os.environ.get("IVAN_DELIVERY_ROOT", Path(__file__).resolve().parents[1]))


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class DeliveredPackages(unittest.TestCase):
    def test_actual_manifest_schema_and_update_addresses(self):
        for platform in ("cowork", "chatgpt"):
            with self.subTest(platform=platform):
                pkg = ROOT / platform
                manifest = json.loads((pkg / "BUILD-MANIFEST.json").read_text())
                lookup = module(pkg / "hooks/_support_lookup.py", "support_" + platform)
                self.assertEqual(lookup.harness_rules_sha(manifest), manifest["figure"]["rules_sha"])
                plugin = "coman-ivan-" + ("cowork" if platform == "cowork" else "codex")
                version = manifest["harness"]["version"]
                config_path = platform + ("/.claude-plugin/plugin.json" if platform == "cowork" else "/.codex-plugin/plugin.json")
                remote = {**manifest, "figure": {**manifest["figure"], "rules_sha": "changed-rules"}}
                files = {".claude-plugin/marketplace.json": {"plugins": [{"name": plugin, "version": version}]},
                         config_path: {"version": version}, platform + "/BUILD-MANIFEST.json": remote}
                def get(path, *args):
                    if path not in files:
                        raise urllib.error.URLError("missing " + path)
                    return files[path]
                with patch.object(lookup, "_get_repo_json", side_effect=get), patch.object(lookup, "load_manifest", return_value=manifest):
                    text = lookup.summary_update(installed=version)
                    self.assertIn("отпечаток свода правил другой", text)
                    self.assertNotIn("У вас последняя", text)
                    del files[platform + "/BUILD-MANIFEST.json"]
                    self.assertIn("не проверен", lookup.summary_update(installed=version))

    def test_publication_does_not_send_unreviewed_or_sensitive_text(self):
        for platform in ("cowork", "chatgpt"):
            lookup = module(ROOT / platform / "hooks/_support_lookup.py", "privacy_" + platform)
            with patch.object(lookup, "_gh_graphql", side_effect=AssertionError("unexpected network")):
                self.assertFalse(lookup.answer(1, "Safe draft")["ok"])
                for body in ("ghp_" + "a" * 36, "REDACTED ghp_" + "a" * 36,
                             "y0__" + "a" * 32, "OAuth " + "a" * 32, "private@example.com"):
                    result = lookup.answer(1, body, approved_sha=lookup.hashlib.sha256(body.encode()).hexdigest())
                    self.assertFalse(result["ok"])
                    self.assertNotIn(body, json.dumps(result))

    def test_cowork_registered_parts_are_lossless_and_bounded(self):
        pkg = ROOT / "cowork"
        hook = pkg / "hooks/session_start_load_rules.py"
        config = json.loads((pkg / "hooks/hooks.json").read_text())
        commands = [h["command"] for group in config["hooks"]["SessionStart"]
                    for h in group["hooks"] if "session_start_load_rules.py" in h.get("command", "")]
        self.assertGreater(len(commands), 1)
        self.assertEqual(len(commands), len(set(commands)))
        with tempfile.TemporaryDirectory() as cwd:
            env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(pkg), "PLUGIN_ROOT": str(pkg),
                   "CLAUDE_PROJECT_DIR": cwd, "PYTHONDONTWRITEBYTECODE": "1"}
            def run(args):
                proc = subprocess.run([sys.executable, "-B", str(hook), *args], env=env, cwd=cwd,
                                      capture_output=True, text=True, timeout=20, check=True)
                return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"] if proc.stdout.strip() else None
            full = run([])
            parts = []
            for index, command in enumerate(commands):
                self.assertIn(f"--part {index} --parts {len(commands)}", command)
                part = run(["--part", str(index), "--parts", str(len(commands))])
                if part:
                    self.assertLessEqual(len(part.encode("utf-16-le")) // 2, 9000)
                    parts.append(part.split("\n", 1)[1])
            self.assertEqual("".join(parts), full)
            Path(cwd, ".es-connection.json").write_text("{}")
            self.assertIn("ES", run(["--part", "0", "--parts", str(len(commands))]))
            self.assertIsNone(run(["--part", "1", "--parts", str(len(commands))]))

    def test_issue_20_auth_header_is_host_scoped(self):
        for platform in ("cowork", "chatgpt"):
            lookup = module(ROOT / platform / "hooks/_support_lookup.py", "headers_" + platform)
            requests = []
            def open_url(req, timeout=0):
                requests.append(req)
                return contextlib.closing(io.BytesIO(b"{}"))
            with patch.dict(os.environ, {"GITHUB_TOKEN": "fixture-only"}), patch.object(lookup.urllib.request, "urlopen", side_effect=open_url):
                lookup._get_json("https://api.github.com/test")
                lookup._get_json("https://raw.githubusercontent.com/test")
            self.assertEqual(requests[0].get_header("Authorization"), "Bearer fixture-only")
            self.assertIsNone(requests[1].get_header("Authorization"))

    def test_issue_18_judge_recursion_is_blocked(self):
        for platform in ("cowork", "chatgpt"):
            hooks = ROOT / platform / "hooks"
            with patch.object(sys, "path", [str(hooks), *sys.path]):
                handoff = module(hooks / "check_closing_handoff.py", "handoff_" + platform)
            with patch.object(handoff.shutil, "which", return_value="fixture"), patch.object(handoff.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, stdout='{"result":"{\\"verdict\\":\\"report\\"}"}')
                handoff.ask_judge("Safe test")
                self.assertEqual(run.call_args.kwargs["env"]["IVAN_HANDOFF_JUDGE_RUNNING"], "1")
            env = {**os.environ, "IVAN_HANDOFF_JUDGE_RUNNING": "1", "PYTHONDONTWRITEBYTECODE": "1"}
            proc = subprocess.run([sys.executable, "-B", str(hooks / "check_closing_handoff.py")],
                                  env=env, input="not-json", capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")

    def test_issue_30_explained_questions_skip_only_redundant_review(self):
        allowed = [
            "Публикация пойдёт от твоего имени. Публикуем?",
            "Отправка клиенту требует твоего согласия. Отправить документ?",
            "Изменение утверждённого тобой правила требует твоего согласия. Изменить правило?",
        ]
        checked = [
            "Собрать PDF?",
            "Публикация требует твоего согласия. Исправить опечатку?",
            "Отправка от твоего имени требует согласия. Отправить и заодно исправить ошибку?",
            "Публикация будет не от твоего имени. Публикуем?",
            "Отправка требует согласия юриста. Отправить документ?",
            "Отправка утверждённого тобой договора требует твоего согласия. Обновить договор?",
            "Публикация требует твоего согласия. Отправить файл в локальный каталог?",
            "Прочитать файл? Публикация требует твоего согласия. Публикуем?",
            "Вписываю изменение в файл.",
        ]
        import uuid
        for platform in ("cowork", "chatgpt"):
            hook = ROOT / platform / "hooks/check_closing_handoff.py"
            with tempfile.TemporaryDirectory() as cwd:
                env = {**os.environ, "IVAN_HANDOFF_JUDGE": "not-installed", "CLAUDE_PROJECT_DIR": cwd}
                env.pop("IVAN_HANDOFF_JUDGE_RUNNING", None)
                for text, blocked in [(t, False) for t in allowed] + [(t, True) for t in checked]:
                    with self.subTest(platform=platform, text=text):
                        payload = {"session_id": uuid.uuid4().hex, "cwd": cwd, "last_assistant_message": text}
                        proc = subprocess.run([sys.executable, "-B", str(hook)], input=json.dumps(payload),
                                              cwd=cwd, env=env, capture_output=True, text=True, timeout=10)
                        self.assertEqual(proc.returncode, 0, proc.stderr)
                        if blocked:
                            self.assertEqual(json.loads(proc.stdout)["decision"], "block")
                        else:
                            self.assertEqual(proc.stdout.strip(), "")

    def test_gh_receives_literal_json_not_file_expansion(self):
        for platform in ("cowork", "chatgpt"):
            lookup = module(ROOT / platform / "hooks/_support_lookup.py", "transport_" + platform)
            with patch.object(lookup.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, stdout='{"data":{}}')
                lookup._gh_graphql("query", {"body": "@/tmp/private-file", "n": 1})
                self.assertEqual(run.call_args.args[0], ["gh", "api", "graphql", "--input", "-"])
                self.assertEqual(json.loads(run.call_args.kwargs["input"])["variables"]["body"], "@/tmp/private-file")


if __name__ == "__main__":
    unittest.main()
