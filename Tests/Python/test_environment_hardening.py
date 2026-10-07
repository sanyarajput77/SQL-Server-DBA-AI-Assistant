"""Offline validation of environment-specific configuration contracts."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from assistant.environment_config import load_automation_configuration
from monitoring.workflow import MonitoringWorkflowConfig
from rag import load_documents


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "Config"


def valid_ops_config(environment="Test", **overrides):
    values = {
        "Environment": environment,
        "EnvironmentId": environment,
        "ApprovedInstances": ["sql-target.example.org"],
        "DefaultInstance": "sql-target.example.org",
        "TrustServerCertificate": False,
        "BackupDirectory": "D:/DBA/Backups",
        "ReportDirectory": "D:/DBA/Reports",
        "LogDirectory": "D:/DBA/Logs",
        "Thresholds": {
            "BackupWarningHours": 24,
            "DiskWarningPercentFree": 15,
            "LongRunningQuerySeconds": 60,
            "FailedJobsHours": 24,
        },
        "Timeouts": {"QueryTimeoutSeconds": 30, "ConnectionTimeoutSeconds": 15},
        "ApprovedDatabases": ["AppDb"],
        "ProtectedDatabases": ["master", "model", "msdb", "tempdb"],
        "BackupPolicy": {
            "Enabled": False,
            "AllowedTypes": [],
            "ApprovalRequired": True,
        },
    }
    values.update(overrides)
    return values


class AutomationEnvironmentConfigurationTests(unittest.TestCase):
    def load_document(self, document, environment=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "environment.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return load_automation_configuration(
                path, project_root=directory, expected_environment=environment
            )

    def test_development_test_uat_and_production_contracts_validate(self):
        development = json.loads((CONFIG / "config.development.json").read_text(encoding="utf-8"))
        test = json.loads((CONFIG / "config.test.json.example").read_text(encoding="utf-8"))
        uat = json.loads((CONFIG / "config.uat.json.example").read_text(encoding="utf-8"))
        for document in (development, test, uat):
            self.assertEqual(load_automation_configuration(
                CONFIG / {"Development": "config.development.json", "Test": "config.test.json.example", "UAT": "config.uat.json.example"}[document["Environment"]],
                project_root=ROOT,
            )["Environment"], document["Environment"])

        production = valid_ops_config(
            "Production",
            EnvironmentId="prod-east-1",
            ApprovedInstances=["sql-prod-17.corp.internal"],
            DefaultInstance="sql-prod-17.corp.internal",
            BackupDirectory="\\\\backup01.corp.internal\\sql",
            ReportDirectory="E:\\DBA\\Reports",
            LogDirectory="E:\\DBA\\Logs",
            ApprovedDatabases=["Operations"],
        )
        self.assertEqual(self.load_document(production)["Environment"], "Production")

    def test_environment_selection_is_required_and_must_be_supported(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "invalid or unavailable"):
                load_automation_configuration(project_root=ROOT)
        for environment in (None, "Staging"):
            document = valid_ops_config()
            if environment is None:
                document.pop("Environment")
            else:
                document["Environment"] = environment
            with self.assertRaises(ValueError):
                self.load_document(document)

    def test_explicit_environment_must_match_file(self):
        with self.assertRaises(ValueError):
            self.load_document(valid_ops_config("Test"), environment="Production")

    def test_missing_required_values_and_invalid_sql_instance_are_rejected(self):
        missing = valid_ops_config()
        missing.pop("ApprovedDatabases")
        with self.assertRaises(ValueError):
            self.load_document(missing)
        invalid_instance = valid_ops_config(ApprovedInstances=["server;arbitrary"], DefaultInstance="server;arbitrary")
        with self.assertRaises(ValueError):
            self.load_document(invalid_instance)

    def test_production_example_placeholders_and_relative_artifact_paths_fail_closed(self):
        example = CONFIG / "config.production.json.example"
        with self.assertRaises(ValueError):
            load_automation_configuration(example, project_root=ROOT)
        placeholder = valid_ops_config(
            "Production", EnvironmentId="production",
            ApprovedInstances=["MSSQL-VM1"], DefaultInstance="MSSQL-VM1",
            ApprovedDatabases=["Operations"],
        )
        with self.assertRaises(ValueError):
            self.load_document(placeholder)
        relative = valid_ops_config("Production", EnvironmentId="prod", BackupDirectory="../Backups")
        with self.assertRaises(ValueError):
            self.load_document(relative)

    def test_invalid_path_backup_policy_and_secret_like_property_are_rejected(self):
        for changes in (
            {"ReportDirectory": "D:/bad|path"},
            {"BackupPolicy": {"Enabled": True, "AllowedTypes": [], "ApprovalRequired": True}},
            {"BackupPolicy": {"Enabled": True, "AllowedTypes": ["CopyOnly"], "ApprovalRequired": True}},
            {"BackupPolicy": {"Enabled": True, "AllowedTypes": ["Full"], "ApprovalRequired": False}},
            {"SqlPassword": "must-not-be-stored"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load_document(valid_ops_config(**changes))

    def test_backup_policy_is_environment_specific_and_always_requires_approval(self):
        development = json.loads((CONFIG / "config.development.json").read_text(encoding="utf-8"))
        self.assertTrue(development["BackupPolicy"]["Enabled"])
        self.assertEqual(set(development["BackupPolicy"]["AllowedTypes"]), {"Full", "Differential", "Log"})
        self.assertTrue(development["BackupPolicy"]["ApprovalRequired"])
        for name in ("config.test.json.example", "config.uat.json.example", "config.production.json.example"):
            policy = json.loads((CONFIG / name).read_text(encoding="utf-8"))["BackupPolicy"]
            self.assertFalse(policy["Enabled"])
            self.assertEqual(policy["AllowedTypes"], [])
            self.assertTrue(policy["ApprovalRequired"])


class MonitoringEnvironmentConfigurationTests(unittest.TestCase):
    def test_missing_environment_and_policy_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = CONFIG / "monitoring.evaluation.example.json"
            (root / "policy.json").write_text(policy.read_text(encoding="utf-8"), encoding="utf-8")
            document = json.loads((CONFIG / "monitoring.workflow.example.json").read_text(encoding="utf-8"))
            document["EvaluationPolicyPath"] = "policy.json"
            workflow_path = root / "workflow.json"
            document.pop("Environment")
            workflow_path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                MonitoringWorkflowConfig.load(workflow_path)

            document["Environment"] = "Development"
            document["EnvironmentId"] = "Development-Lab"
            document["EvaluationPolicyPath"] = "missing-policy.json"
            workflow_path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                MonitoringWorkflowConfig.load(workflow_path)

            document["EvaluationPolicyPath"] = "policy.json"
            (root / "policy.json").write_text("{}", encoding="utf-8")
            workflow_path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                MonitoringWorkflowConfig.load(workflow_path)

    def test_enabled_checks_formats_paths_and_production_artifact_location_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "policy.json").write_text(
                (CONFIG / "monitoring.evaluation.example.json").read_text(encoding="utf-8"), encoding="utf-8"
            )
            document = json.loads((CONFIG / "monitoring.workflow.example.json").read_text(encoding="utf-8"))
            document["EvaluationPolicyPath"] = "policy.json"
            document["SqlInstance"] = "sql-dev.example.org"
            document["AllowedSqlInstances"] = ["sql-dev.example.org"]
            path = root / "workflow.json"
            for changes in (
                {"EnabledChecks": ["run_arbitrary_sql"]},
                {"ArtifactFormats": ["csv"]},
                {"ReportOutputDirectory": "D:/invalid|path"},
                {"Environment": "Production", "ReportOutputDirectory": "../Reports"},
                {"Environment": "Production", "SqlInstance": "MSSQL-VM1"},
            ):
                candidate = dict(document)
                candidate.update(changes)
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    MonitoringWorkflowConfig.load(path)

    def test_development_workflow_example_loads_and_has_environment_metadata(self):
        config, _ = MonitoringWorkflowConfig.load(CONFIG / "monitoring.workflow.example.json")
        self.assertEqual(config.environment, "Development")
        self.assertEqual(config.environment_id, "Development-Lab")
        self.assertEqual(config.sql_instance, "MSSQL-VM1")


class KnowledgeDirectoryEnvironmentTests(unittest.TestCase):
    def test_production_requires_explicit_knowledge_directory(self):
        with patch.dict(os.environ, {"DBA_AUTOMATION_ENV": "Production"}, clear=True):
            with self.assertRaisesRegex(ValueError, "explicit DBA_KNOWLEDGE_DIRECTORY"):
                load_documents()

    def test_environment_configured_relative_knowledge_directory_is_project_relative(self):
        with patch.dict(os.environ, {
            "DBA_AUTOMATION_ENV": "Production",
            "DBA_KNOWLEDGE_DIRECTORY": "docs/knowledge",
        }, clear=True):
            documents = load_documents()
        self.assertTrue(documents)
        self.assertTrue(all(document.source_path == document.filename for document in documents))


if __name__ == "__main__":
    unittest.main()
