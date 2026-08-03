import os
import json
import zipfile
from pathlib import Path
import pytest
import requests
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.jenkins import JenkinsServer, JenkinsBackup
from app.services.jenkins_backup_service import (
    JenkinsConfigParser,
    execute_jenkins_backup,
    fetch_credential_values,
)
from app.services import jenkins_backup_service
from app.api.jenkins import decrypt_backup_to_memory


def test_parser_extracts_parameters_environment_credentials_and_scripts():
    xml = """
    <project>
      <properties>
        <hudson.model.ParametersDefinitionProperty>
          <parameterDefinitions>
            <hudson.model.StringParameterDefinition>
              <name>BRANCH</name>
              <description>Git branch</description>
              <defaultValue>main</defaultValue>
            </hudson.model.StringParameterDefinition>
          </parameterDefinitions>
        </hudson.model.ParametersDefinitionProperty>
      </properties>
      <builders>
        <hudson.tasks.Shell><command>echo one</command></hudson.tasks.Shell>
        <hudson.tasks.Shell><command>echo two</command></hudson.tasks.Shell>
      </builders>
      <buildWrappers>
        <EnvInjectBuildWrapper>
          <info><propertiesContent># app config
APP_ENV=test
REGISTRY_URL=https://registry.example/a=b
          </propertiesContent></info>
        </EnvInjectBuildWrapper>
        <org.jenkinsci.plugins.credentialsbinding.impl.SecretBuildWrapper>
          <bindings>
            <org.jenkinsci.plugins.credentialsbinding.impl.StringBinding>
              <credentialsId>Harbor</credentialsId>
              <variable>HARBOR_PASSWORD</variable>
            </org.jenkinsci.plugins.credentialsbinding.impl.StringBinding>
          </bindings>
        </org.jenkinsci.plugins.credentialsbinding.impl.SecretBuildWrapper>
        <jenkins.plugins.nodejs.NodeJSBuildWrapper>
          <nodeJSInstallationName>node-v14.17.5</nodeJSInstallationName>
        </jenkins.plugins.nodejs.NodeJSBuildWrapper>
      </buildWrappers>
    </project>
    """

    parsed = JenkinsConfigParser(xml).parse()

    assert parsed["parameters"] == [{
        "name": "BRANCH",
        "type": "StringParameterDefinition",
        "default_value": "main",
        "description": "Git branch",
    }]
    assert parsed["environment"]["variables"] == [
        {"name": "APP_ENV", "value": "test", "source": "envinject"},
        {"name": "REGISTRY_URL", "value": "https://registry.example/a=b", "source": "envinject"},
    ]
    assert parsed["environment"]["tools"]["nodejs"] == "node-v14.17.5"
    assert parsed["credentials"] == [{
        "id": "Harbor",
        "type": "StringBinding",
        "bindings": {"value": "HARBOR_PASSWORD"},
    }]
    assert [step["script"] for step in parsed["build_steps"]] == ["echo one", "echo two"]

def test_fetch_credential_values_returns_only_referenced_credentials():
    session = MagicMock()
    crumb_response = MagicMock(status_code=200)
    crumb_response.json.return_value = {
        "crumbRequestField": "Jenkins-Crumb",
        "crumb": "test-crumb",
    }
    session.get.return_value = crumb_response
    response = MagicMock(status_code=200)
    response.text = """[
      {"id":"Harbor","type":"username_password","values":{"username":"robot","password":"secret"}},
      {"id":"DeployKey","type":"ssh_private_key","values":{"username":"git","private_key":"PRIVATE"}},
      {"id":"Unused","type":"secret_text","values":{"value":"ignore"}}
    ]"""
    session.post.return_value = response

    result = fetch_credential_values(
        session,
        "http://localhost:8080/",
        {"Harbor", "DeployKey"},
    )

    assert result == {
        "Harbor": {
            "type": "username_password",
            "values": {"username": "robot", "password": "secret"},
        },
        "DeployKey": {
            "type": "ssh_private_key",
            "values": {"username": "git", "private_key": "PRIVATE"},
        },
    }
    session.post.assert_called_once()
    assert session.post.call_args.kwargs["headers"] == {
        "Jenkins-Crumb": "test-crumb",
    }


def test_fetch_credential_values_returns_empty_when_jenkins_forbids_script_access():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=403)

    assert fetch_credential_values(
        session,
        "http://localhost:8080/",
        {"Harbor"},
    ) == {}


def test_fetch_tool_installations_returns_supported_non_empty_tools():
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=404)
    session.post.return_value = MagicMock(status_code=200, text=json.dumps([
        {"type": "jdk", "name": "jdk17", "home": "/opt/jdk-17"},
        {"type": "maven", "name": "maven39", "home": "/opt/maven-3.9"},
        {"type": "unknown", "name": "ignored", "home": "/tmp/x"},
        {"type": "nodejs", "name": "", "home": "/opt/node"},
    ]))
    assert jenkins_backup_service.fetch_tool_installations(session, "http://localhost:8080/") == [
        {"type": "jdk", "name": "jdk17", "home": "/opt/jdk-17"},
        {"type": "maven", "name": "maven39", "home": "/opt/maven-3.9"},
    ]


def test_parser_extracts_explicit_selected_tools():
    parsed = JenkinsConfigParser("""
    <project><jdk>jdk17</jdk><builders>
      <hudson.tasks.Maven><mavenName>maven39</mavenName><targets>package</targets></hudson.tasks.Maven>
      <hudson.plugins.gradle.Gradle><gradleName>gradle8</gradleName><tasks>build</tasks></hudson.plugins.gradle.Gradle>
    </builders><buildWrappers><jenkins.plugins.nodejs.NodeJSBuildWrapper><nodeJSInstallationName>node20</nodeJSInstallationName></jenkins.plugins.nodejs.NodeJSBuildWrapper></buildWrappers></project>
    """).parse()
    assert parsed["selected_tools"] == {"jdk": "jdk17", "maven": "maven39", "nodejs": "node20", "gradle": "gradle8"}

def test_get_view_snapshots_maps_backed_up_jobs_to_multiple_views():
    session = MagicMock()
    views_response = MagicMock()
    views_response.json.return_value = {
        "views": [
            {"name": "Frontend", "url": "http://jenkins/view/frontend/"},
            {"name": "Release", "url": "http://jenkins/view/release/"},
        ]
    }
    frontend_response = MagicMock()
    frontend_response.json.return_value = {
        "jobs": [
            {"name": "shared", "url": "http://jenkins/job/shared", "_class": "job"},
            {"name": "not-backed", "url": "http://jenkins/job/not-backed", "_class": "job"},
        ]
    }
    release_response = MagicMock()
    release_response.json.return_value = {
        "jobs": [
            {"name": "shared", "url": "http://jenkins/job/shared", "_class": "job"},
            {"name": "release", "url": "http://jenkins/job/release", "_class": "job"},
        ]
    }
    responses = {
        "http://jenkins/api/json?tree=views[name,url]": views_response,
        "http://jenkins/view/frontend/api/json?tree=jobs[name,url,class]": frontend_response,
        "http://jenkins/view/release/api/json?tree=jobs[name,url,class]": release_response,
    }

    def get_response(url, **kwargs):
        return responses[url]

    session.get.side_effect = get_response

    assert jenkins_backup_service.get_view_snapshots(
        session,
        "http://jenkins/",
        ["shared", "release"],
    ) == [
        {"name": "Frontend", "job_names": ["shared"]},
        {"name": "Release", "job_names": ["shared", "release"]},
    ]


def test_get_view_snapshots_falls_back_when_specific_view_request_fails():
    session = MagicMock()
    views_response = MagicMock()
    views_response.json.return_value = {
        "views": [
            {"name": "Broken", "url": "http://jenkins/view/broken/"},
        ]
    }

    def get_response(url, **kwargs):
        if url == "http://jenkins/api/json?tree=views[name,url]":
            return views_response
        if url == "http://jenkins/view/broken/api/json?tree=jobs[name,url,class]":
            raise requests.RequestException("unavailable")
        raise AssertionError(f"unexpected URL: {url}")

    session.get.side_effect = get_response

    assert jenkins_backup_service.get_view_snapshots(
        session,
        "http://jenkins/",
        ["job-a"],
    ) == [{"name": "\u5168\u90e8\u4efb\u52a1", "job_names": ["job-a"]}]


def test_get_view_snapshots_falls_back_for_malformed_view_url_type():
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = {
        "views": [{"name": "Broken", "url": 123}]
    }
    session.get.return_value = response

    assert jenkins_backup_service.get_view_snapshots(
        session,
        "http://jenkins/",
        ["job-a"],
    ) == [{"name": "\u5168\u90e8\u4efb\u52a1", "job_names": ["job-a"]}]


def test_get_view_snapshots_falls_back_when_view_request_fails():
    session = MagicMock()
    session.get.side_effect = requests.RequestException("unavailable")

    assert jenkins_backup_service.get_view_snapshots(
        session,
        "http://jenkins/",
        ["job-b", "job-a"],
    ) == [{"name": "全部任务", "job_names": ["job-b", "job-a"]}]

@patch('app.services.jenkins_backup_service.SyncSessionLocal')
def test_execute_jenkins_backup_cleans_up_when_payload_initialization_fails(mock_session_local, tmp_path, monkeypatch):
    tmpfs_dir = tmp_path / "tmpfs"
    tmpfs_dir.mkdir()
    monkeypatch.setenv("BACKUP_TMP_DIR", str(tmpfs_dir))

    db = MagicMock()
    mock_session_local.return_value = db
    backup_record = MagicMock(status="BACKUPING", zip_path=None)
    server = MagicMock(
        url="http://localhost:8080",
        username="admin",
        api_token="token123",
    )
    db.query.return_value.filter.return_value.first.side_effect = [backup_record, server]

    real_makedirs = os.makedirs

    def fail_for_payload(path, *args, **kwargs):
        if Path(path).name == "payload":
            raise OSError("payload initialization failed")
        return real_makedirs(path, *args, **kwargs)

    monkeypatch.setattr(jenkins_backup_service.os, "makedirs", fail_for_payload)

    execute_jenkins_backup(server_id=1, backup_id=1)

    assert backup_record.status == "FAILED"
    assert backup_record.zip_path is None
    db.commit.assert_called_once()
    db.close.assert_called_once()
    assert list(tmpfs_dir.iterdir()) == []


@patch('app.services.jenkins_backup_service.requests.Session')
@patch('app.services.jenkins_backup_service.SyncSessionLocal')
def test_execute_jenkins_backup_success(mock_session_local, mock_session_class, tmp_path, monkeypatch):
    """
    Test Jenkins configuration backup service: recursively scans, downloads config.xml,
    parses into info.json, generates markdown summary table, zips output and cleans up.
    """
    tmpfs_dir = tmp_path / "tmpfs"
    tmpfs_dir.mkdir()
    monkeypatch.setenv("BACKUP_TMP_DIR", str(tmpfs_dir))
    real_zipfile = zipfile.ZipFile
    write_paths = []

    def recording_zipfile(file, mode="r", *args, **kwargs):
        if mode == "w":
            write_paths.append(Path(file).resolve())
        return real_zipfile(file, mode, *args, **kwargs)

    monkeypatch.setattr(jenkins_backup_service.zipfile, "ZipFile", recording_zipfile)
    real_safe_backup_job_path = jenkins_backup_service.safe_backup_job_path
    validated_job_names = []

    def recording_safe_backup_job_path(root, job_name):
        validated_job_names.append(job_name)
        return real_safe_backup_job_path(root, job_name)

    monkeypatch.setattr(jenkins_backup_service, "safe_backup_job_path", recording_safe_backup_job_path)

    # 1. Setup in-memory SQLite DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    
    db = TestingSessionLocal()
    mock_session_local.return_value = db
    
    # Preseed server and backup entry
    server = JenkinsServer(
        id=1,
        name="backup-server",
        url="http://localhost:8080",
        username="admin",
        api_token="token123",
        is_active=1
    )
    backup_record = JenkinsBackup(
        id=1,
        server_id=1,
        status="BACKUPING"
    )
    db.add(server)
    db.add(backup_record)
    db.commit()
    
    # 2. Mock requests Session behaviors
    mock_session = MagicMock()
    mock_session_class.return_value = mock_session
    
    # Mock recursive fetch get request: returning 1 job
    mock_all_jobs_response = MagicMock()
    mock_all_jobs_response.status_code = 200
    mock_all_jobs_response.json.return_value = {
        "jobs": [
            {
                "name": "h5-shop-build",
                "url": "http://localhost:8080/job/h5-shop-build",
                "_class": "hudson.model.FreeStyleProject"
            },
            {
                "name": "../../../app/data/leak",
                "url": "http://localhost:8080/job/unsafe",
                "_class": "hudson.model.FreeStyleProject"
            }
        ]
    }
    mock_views_response = MagicMock()
    mock_views_response.status_code = 200
    mock_views_response.json.return_value = {
        "views": [
            {"name": "Builds", "url": "http://localhost:8080/view/builds/"}
        ]
    }
    
    # Mock config.xml get request: returning free style config XML
    mock_config_response = MagicMock()
    mock_config_response.status_code = 200
    mock_config_response.text = """<?xml version='1.1' encoding='UTF-8'?>
    <project>
      <description>H5 Online Shop build profile</description>
      <jdk>jdk17</jdk>
      <scm class="hudson.plugins.git.GitSCM">
        <userRemoteConfigs><hudson.plugins.git.UserRemoteConfig>
          <url>https://gitlab.example/team/shop.git</url><credentialsId>GitLab</credentialsId>
        </hudson.plugins.git.UserRemoteConfig></userRemoteConfigs>
      </scm>
      <properties>
        <hudson.model.ParametersDefinitionProperty>
          <parameterDefinitions>
            <hudson.model.StringParameterDefinition>
              <name>BRANCH</name>
              <defaultValue>main</defaultValue>
            </hudson.model.StringParameterDefinition>
          </parameterDefinitions>
        </hudson.model.ParametersDefinitionProperty>
      </properties>
      <builders>
        <hudson.tasks.Shell><command>echo prepare</command></hudson.tasks.Shell>
        <hudson.tasks.Shell><command>npm run build</command></hudson.tasks.Shell>
      </builders>
      <buildWrappers>
        <EnvInjectBuildWrapper>
          <info><propertiesContent>APP_ENV=test</propertiesContent></info>
        </EnvInjectBuildWrapper>
        <org.jenkinsci.plugins.credentialsbinding.impl.SecretBuildWrapper>
          <bindings>
            <org.jenkinsci.plugins.credentialsbinding.impl.UsernamePasswordMultiBinding>
              <credentialsId>Harbor</credentialsId>
              <usernameVariable>HARBOR_USER</usernameVariable>
              <passwordVariable>HARBOR_PASSWORD</passwordVariable>
            </org.jenkinsci.plugins.credentialsbinding.impl.UsernamePasswordMultiBinding>
          </bindings>
        </org.jenkinsci.plugins.credentialsbinding.impl.SecretBuildWrapper>
      </buildWrappers>
    </project>
    """
    
    # Define session get responses side effect
    def session_get_side_effect(url, **kwargs):
        if "tree=views" in url:
            return mock_views_response
        if "api/json" in url:
            return mock_all_jobs_response
        elif "config.xml" in url:
            return mock_config_response
        return MagicMock(status_code=404)
        
    mock_session.get.side_effect = session_get_side_effect
    def session_post_side_effect(url, **kwargs):
        if "ToolDescriptor" in kwargs["data"]["script"]:
            return MagicMock(status_code=200, text=json.dumps([
                {"type": "jdk", "name": "jdk17", "home": "/opt/jdk-17"}
            ]))
        return MagicMock(status_code=200, text=json.dumps([
            {"id":"Harbor","type":"username_password","values":{"username":"robot","password":"secret"}},
            {"id":"GitLab","type":"username_password","values":{"username":"git-user","password":"git-token"}}
        ]))
    mock_session.post.side_effect = session_post_side_effect
    
    # 3. Trigger backup worker
    execute_jenkins_backup(server_id=1, backup_id=1)
    
    # 4. Verify DB and Zip artifacts
    updated_backup = db.query(JenkinsBackup).filter(JenkinsBackup.id == 1).first()
    assert updated_backup.status == "SUCCESS"
    assert updated_backup.job_count == 1
    assert "../../../app/data/leak" in validated_job_names
    assert "h5-shop-build" in updated_backup.summary_md
    assert "project" in updated_backup.summary_md
    
    # Assert zip file is actually created
    assert updated_backup.zip_path is not None
    assert os.path.exists(updated_backup.zip_path)
    assert updated_backup.zip_path.endswith(".zip.enc")
    assert write_paths
    assert write_paths[0].is_relative_to(tmpfs_dir.resolve())
    import io
    decrypted_data = decrypt_backup_to_memory(updated_backup.zip_path)
    with zipfile.ZipFile(io.BytesIO(decrypted_data)) as archive:
        assert "backup_1.zip" not in archive.namelist()
        details = json.loads(archive.read("details.json"))

    job = details["jobs"][0]
    assert details["version"] == 3
    assert details["views"] == [
        {"name": "Builds", "job_names": ["h5-shop-build"]}
    ]
    assert job["environment"]["variables"][0] == {
        "name": "APP_ENV",
        "value": "test",
        "source": "envinject",
    }
    assert job["parameters"][0]["name"] == "BRANCH"
    assert job["scripts"][1]["filename"] == "build_step_2.sh"
    assert job["scripts"][1]["content"] == "npm run build"
    assert details["tool_installations"] == [
        {"type": "jdk", "name": "jdk17", "home": "/opt/jdk-17"}
    ]
    assert job["selected_tools"] == {"jdk": "jdk17"}
    assert job["git_repositories"] == [{
        "url": "https://gitlab.example/team/shop.git",
        "credential": {
            "id": "GitLab",
            "type": "username_password",
            "values": {"username": "git-user", "password": "git-token"},
        },
    }]
    assert job["credentials"][0]["values"]["password"] == "secret"
    assert "secret" not in updated_backup.summary_md
    assert "git-token" not in updated_backup.summary_md
    
    # Clean up created file
    if os.path.exists(updated_backup.zip_path):
        os.remove(updated_backup.zip_path)
        # remove parent dirs if empty
        try:
            os.rmdir(os.path.dirname(updated_backup.zip_path))
            os.rmdir(os.path.dirname(os.path.dirname(updated_backup.zip_path)))
            os.rmdir(os.path.dirname(os.path.dirname(os.path.dirname(updated_backup.zip_path))))
        except Exception:
            pass
            
    db.close()

def test_parser_extracts_maven_config():
    # Test maven2-moduleset project parser
    maven_xml = """
    <maven2-moduleset>
      <rootPOM>sub-folder/pom.xml</rootPOM>
      <goals>clean package -DskipTests</goals>
    </maven2-moduleset>
    """
    parsed = JenkinsConfigParser(maven_xml).parse()
    assert parsed["maven_config"] == {
        "root_pom": "sub-folder/pom.xml",
        "goals": "clean package -DskipTests"
    }

    # Test freestyle project with Maven build step parser
    freestyle_maven_xml = """
    <project>
      <builders>
        <hudson.tasks.Maven>
          <targets>clean install</targets>
          <pom>api/pom.xml</pom>
        </hudson.tasks.Maven>
      </builders>
    </project>
    """
    parsed_fs = JenkinsConfigParser(freestyle_maven_xml).parse()
    assert parsed_fs["maven_config"] == {
        "root_pom": "api/pom.xml",
        "goals": "clean install"
    }


@patch('app.services.jenkins_backup_service.requests.Session')
def test_execute_jenkins_backup_fails_when_job_config_fails(mock_session_class, tmp_path):
    from datetime import datetime
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    server = JenkinsServer(id=1, name="j", url="http://j.example", username="u", api_token="t")
    backup = JenkinsBackup(id=10, server_id=1, backup_time=datetime.now(), status="PENDING", job_count=0)
    db.add_all([server, backup])
    db.commit()

    session = MagicMock()
    mock_session_class.return_value = session

    def mock_get(url, **kwargs):
        if "job1/config.xml" in url:
            return MagicMock(status_code=200, text="<project/>")
        if "job2/config.xml" in url:
            return MagicMock(status_code=500, text="server error")
        if "api/json" in url:
            return MagicMock(status_code=200, json=lambda: {"jobs": [
                {"name": "job1", "url": "http://j.example/job/job1", "_class": "hudson.model.FreeStyleProject"},
                {"name": "job2", "url": "http://j.example/job/job2", "_class": "hudson.model.FreeStyleProject"},
            ]})
        return MagicMock(status_code=200, text="<crumb/>")

    session.get.side_effect = mock_get
    session.post.return_value = MagicMock(status_code=200, text="[]")

    with patch("app.services.jenkins_backup_service.SyncSessionLocal", return_value=db):
        execute_jenkins_backup(server_id=1, backup_id=10)

    db.expire_all()
    record = db.get(JenkinsBackup, 10)
    assert record.status == "FAILED"
    assert record.job_count == 1
    assert not record.zip_path
    db.close()
