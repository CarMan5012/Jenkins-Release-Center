import os
import base64
import re
import json
import html
import shutil
import zipfile
import logging
import requests
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from requests.auth import HTTPBasicAuth
from urllib.parse import urljoin, quote, urlparse

from app.core.database import SyncSessionLocal
from app.models.jenkins import JenkinsServer, JenkinsBackup
from app.core.security import decrypt_secret
from app.core.config import settings

# ==========================================
# 1. Jenkins Config XML Parser (Embedded)
# ==========================================
class JenkinsConfigParser:
    def __init__(self, xml_content):
        self.xml_content = xml_content
        try:
            self.root = ET.fromstring(xml_content)
        except Exception as e:
            logging.error(f"Failed to parse XML content: {e}")
            self.root = None

    def parse(self):
        if self.root is None:
            return None
        
        project_type = self.root.tag
        build_steps = self._parse_build_steps()
        
        # Extract Maven build configurations if present
        maven_config = None
        if "maven" in project_type.lower():
            root_pom = self.root.find('rootPOM')
            goals = self.root.find('goals')
            maven_config = {
                "root_pom": root_pom.text if root_pom is not None and root_pom.text else "pom.xml",
                "goals": goals.text if goals is not None and goals.text else ""
            }
        else:
            # Check if there is any Maven step in build steps
            maven_steps = [s for s in build_steps if s.get("type") == "maven"]
            if maven_steps:
                first_maven = maven_steps[0]
                maven_config = {
                    "root_pom": first_maven.get("pom") or "pom.xml",
                    "goals": first_maven.get("targets") or ""
                }
        
        info = {
            "project_type": project_type,
            "general": self._parse_general(),
            "scm": self._parse_scm(),
            "triggers": self._parse_triggers(),
            "environment": self._parse_environment(),
            "parameters": self._parse_parameters(),
            "credentials": self._parse_credentials(),
            "build_steps": build_steps,
            "post_build_actions": self._parse_post_build(),
            "pipeline": self._parse_pipeline(),
            "maven_config": maven_config,
            "selected_tools": self._parse_selected_tools()
        }
        return info

    def _parse_general(self):
        general = {}
        desc_node = self.root.find('description')
        general['description'] = desc_node.text if desc_node is not None else ""
        
        disabled_node = self.root.find('disabled')
        if disabled_node is not None:
            general['disabled'] = disabled_node.text.lower() == 'true'
        else:
            general['disabled'] = False
            
        concurrent_node = self.root.find('concurrentBuild')
        if concurrent_node is not None:
            general['concurrent_build'] = concurrent_node.text.lower() == 'true'
        
        log_rotator = self.root.find('logRotator')
        if log_rotator is not None:
            general['log_rotator'] = {
                "days_to_keep": getattr(log_rotator.find('daysToKeep'), 'text', ''),
                "num_to_keep": getattr(log_rotator.find('numToKeep'), 'text', ''),
                "artifact_days_to_keep": getattr(log_rotator.find('artifactDaysToKeep'), 'text', ''),
                "artifact_num_to_keep": getattr(log_rotator.find('artifactNumToKeep'), 'text', '')
            }
        return general

    def _parse_scm(self):
        scm = {"type": "none", "repos": []}
        scm_node = self.root.find('scm')
        if scm_node is None:
            return scm
        
        scm_class = scm_node.get('class', '')
        if 'GitSCM' in scm_class:
            scm['type'] = 'git'
            user_configs = scm_node.findall('.//hudson.plugins.git.UserRemoteConfig')
            for config in user_configs:
                url_node = config.find('url')
                credentials_node = config.find('credentialsId')
                scm['repos'].append({
                    "url": url_node.text if url_node is not None else "",
                    "credentials_id": credentials_node.text if credentials_node is not None else ""
                })
            branches = []
            branch_specs = scm_node.findall('.//hudson.plugins.git.BranchSpec')
            for spec in branch_specs:
                name_node = spec.find('name')
                if name_node is not None and name_node.text:
                    branches.append(name_node.text)
            scm['branches'] = branches
        elif 'SubversionSCM' in scm_class:
            scm['type'] = 'svn'
            locations = scm_node.findall('.//hudson.scm.SubversionSCM_-ModuleLocation')
            for loc in locations:
                remote_node = loc.find('remote')
                scm['repos'].append({
                    "url": remote_node.text if remote_node is not None else ""
                })
        return scm

    def _parse_triggers(self):
        triggers = []
        triggers_node = self.root.find('triggers')
        if triggers_node is None:
            return triggers
            
        for trigger in triggers_node:
            tag = trigger.tag
            trigger_info = {"type": tag}
            spec_node = trigger.find('spec')
            if spec_node is not None:
                trigger_info["spec"] = spec_node.text
            
            if "GitLabPushTrigger" in tag:
                trigger_info["type"] = "GitLabPushTrigger"
                trigger_info["desc"] = "GitLab push hook"
            elif "TimerTrigger" in tag:
                trigger_info["type"] = "TimerTrigger"
                trigger_info["desc"] = f"Build periodically: {spec_node.text if spec_node is not None else ''}"
            elif "SCMTrigger" in tag:
                trigger_info["type"] = "SCMTrigger"
                trigger_info["desc"] = f"Poll SCM: {spec_node.text if spec_node is not None else ''}"
                
            triggers.append(trigger_info)
        return triggers

    def _parse_parameters(self):
        parameters = []
        definitions = self.root.find('.//hudson.model.ParametersDefinitionProperty/parameterDefinitions')
        if definitions is None:
            return parameters

        for definition in definitions:
            name = definition.findtext('name', '')
            if not name:
                continue
            parameters.append({
                "name": name,
                "type": definition.tag.split('.')[-1],
                "default_value": definition.findtext('defaultValue', ''),
                "description": definition.findtext('description', '')
            })
        return parameters

    def _parse_environment(self):
        env = {"variables": [], "tools": {}}
        wrappers_node = self.root.find('buildWrappers')
        if wrappers_node is None:
            return env
            
        for wrapper in wrappers_node:
            tag = wrapper.tag
            if "EnvInject" in tag:
                properties = wrapper.find('.//propertiesContent')
                for line in (properties.text if properties is not None and properties.text else '').splitlines():
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    name, value = line.split('=', 1)
                    env["variables"].append({
                        "name": name.strip(),
                        "value": value.strip(),
                        "source": "envinject"
                    })
            elif "jenkins.plugins.nodejs.NodeJSBuildWrapper" in tag:
                node_version = wrapper.find('nodeJSInstallationName')
                env["tools"]['nodejs'] = node_version.text if node_version is not None else "default"
            elif "hudson.plugins.ansicolor.AnsiColorBuildWrapper" in tag:
                env["tools"]['ansi_color'] = True
            elif "hudson.plugins.build__timeout.BuildTimeoutWrapper" in tag:
                strategy = wrapper.find('.//strategy')
                timeout_minutes = wrapper.find('.//timeoutMinutes')
                env["tools"]['timeout'] = {
                    "minutes": timeout_minutes.text if timeout_minutes is not None else "elastic",
                    "strategy": strategy.get('class', '').split('.')[-1] if strategy is not None else ""
                }
            elif "hudson.plugins.ws__cleanup.PreBuildCleanup" in tag:
                env["tools"]['clean_workspace_before_build'] = True
        return env

    def _parse_selected_tools(self):
        candidates = {
            "jdk": [self.root.findtext("jdk", "")],
            "maven": [node.findtext("mavenName", "") for node in self.root.findall(".//hudson.tasks.Maven")],
            "nodejs": [node.findtext("nodeJSInstallationName", "") for node in self.root.findall(".//jenkins.plugins.nodejs.NodeJSBuildWrapper")],
            "gradle": [node.findtext("gradleName", "") for node in self.root.findall(".//hudson.plugins.gradle.Gradle")],
        }
        return {
            tool_type: next((value.strip() for value in values if value and value.strip()), "")
            for tool_type, values in candidates.items()
            if any(value and value.strip() for value in values)
        }
    def _parse_credentials(self):
        credentials = []
        wrappers_node = self.root.find('buildWrappers')
        if wrappers_node is None:
            return credentials

        for wrapper in wrappers_node:
            if "SecretBuildWrapper" not in wrapper.tag:
                continue
            bindings_node = wrapper.find('bindings')
            if bindings_node is None:
                continue
            for binding in bindings_node:
                credential_id = binding.findtext('credentialsId', '')
                if not credential_id:
                    continue
                variable_fields = {
                    "variable": "value",
                    "usernameVariable": "username",
                    "passwordVariable": "password",
                    "keyFileVariable": "private_key_file",
                    "passphraseVariable": "passphrase",
                }
                variables = {
                    target: binding.findtext(source, '')
                    for source, target in variable_fields.items()
                    if binding.findtext(source, '')
                }
                credentials.append({
                    "id": credential_id,
                    "type": binding.tag.split('.')[-1],
                    "bindings": variables
                })
        return credentials

    def _parse_builders_node(self, node_name):
        steps = []
        node = self.root.find(node_name)
        if node is None:
            return steps
            
        for builder in node:
            tag = builder.tag
            step_info = {"type": tag, "phase": node_name}
            
            if "hudson.tasks.Shell" in tag:
                command_node = builder.find('command')
                step_info["type"] = "shell"
                step_info["script"] = command_node.text if command_node is not None else ""
            elif "hudson.tasks.BatchFile" in tag:
                command_node = builder.find('command')
                step_info["type"] = "batch"
                step_info["script"] = command_node.text if command_node is not None else ""
            elif "hudson.tasks.Maven" in tag:
                targets = builder.find('targets')
                pom = builder.find('pom')
                step_info["type"] = "maven"
                step_info["targets"] = targets.text if targets is not None else ""
                step_info["pom"] = pom.text if pom is not None else "pom.xml"
            elif "hudson.plugins.gradle.Gradle" in tag:
                tasks = builder.find('tasks')
                step_info["type"] = "gradle"
                step_info["tasks"] = tasks.text if tasks is not None else ""
                
            steps.append(step_info)
        return steps

    def _parse_build_steps(self):
        steps = []
        
        # 1. 如果是 maven2-moduleset，解析其根部的 Maven 构建属性
        if self.root.tag == "maven2-moduleset":
            root_pom = self.root.find('rootPOM')
            goals = self.root.find('goals')
            if root_pom is not None or goals is not None:
                steps.append({
                    "type": "maven",
                    "targets": goals.text if goals is not None else "",
                    "pom": root_pom.text if root_pom is not None else "pom.xml",
                    "phase": "maven-moduleset"
                })
        
        # 2. 解析 prebuilders, builders, postbuilders 阶段
        steps.extend(self._parse_builders_node('prebuilders'))
        steps.extend(self._parse_builders_node('builders'))
        steps.extend(self._parse_builders_node('postbuilders'))
        
        return steps

    def _parse_post_build(self):
        actions = []
        publishers_node = self.root.find('publishers')
        if publishers_node is None:
            return actions
            
        for publisher in publishers_node:
            tag = publisher.tag
            action_info = {"type": tag}
            
            if "hudson.tasks.ArtifactArchiver" in tag:
                artifacts = publisher.find('artifacts')
                action_info["type"] = "archive_artifacts"
                action_info["files"] = artifacts.text if artifacts is not None else ""
            elif "hudson.tasks.Mailer" in tag:
                recipients = publisher.find('recipients')
                action_info["type"] = "email"
                action_info["recipients"] = recipients.text if recipients is not None else ""
            elif "hudson.tasks.BuildTrigger" in tag:
                child_projects = publisher.find('childProjects')
                threshold = publisher.find('.//name')
                action_info["type"] = "trigger_downstream"
                action_info["projects"] = child_projects.text if child_projects is not None else ""
                action_info["condition"] = threshold.text if threshold is not None else "SUCCESS"
                
            actions.append(action_info)
        return actions

    def _parse_pipeline(self):
        pipeline = {}
        definition = self.root.find('definition')
        if definition is None:
            return pipeline
            
        def_class = definition.get('class', '')
        if "CpsFlowDefinition" in def_class:
            pipeline["type"] = "inline"
            script_node = definition.find('script')
            pipeline["script"] = script_node.text if script_node is not None else ""
        elif "CpsScmFlowDefinition" in def_class:
            pipeline["type"] = "scm"
            script_path_node = definition.find('scriptPath')
            pipeline["script_path"] = script_path_node.text if script_path_node is not None else "Jenkinsfile"
            
            scm_node = definition.find('scm')
            if scm_node is not None:
                scm_class = scm_node.get('class', '')
                if 'GitSCM' in scm_class:
                    pipeline["scm_type"] = "git"
                    url_node = scm_node.find('.//hudson.plugins.git.UserRemoteConfig/url')
                    branch_node = scm_node.find('.//hudson.plugins.git.BranchSpec/name')
                    pipeline["url"] = url_node.text if url_node is not None else ""
                    pipeline["branch"] = branch_node.text if branch_node is not None else "master"
        return pipeline

CREDENTIAL_EXPORT_SCRIPT = """
import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import com.cloudbees.plugins.credentials.CredentialsProvider
import com.cloudbees.plugins.credentials.common.StandardCredentials
import jenkins.model.Jenkins

def requested = new JsonSlurper().parseText(
    new String('__CREDENTIAL_IDS__'.decodeBase64(), 'UTF-8')
) as Set

def plainText(value) {
    value == null ? '' : (value.metaClass.hasProperty(value, 'plainText') ? value.plainText : value.toString())
}

def rows = CredentialsProvider.lookupCredentials(
    StandardCredentials.class,
    Jenkins.get(),
    null,
    null
).findAll { requested.contains(it.id) }.collect { credential ->
    def type = 'unknown'
    def values = [:]
    if (credential.metaClass.hasProperty(credential, 'privateKey')) {
        type = 'ssh_private_key'
        values.username = credential.username ?: ''
        values.private_key = credential.privateKey ?: ''
        if (credential.metaClass.hasProperty(credential, 'passphrase')) {
            values.passphrase = plainText(credential.passphrase)
        }
    } else if (credential.metaClass.hasProperty(credential, 'password')) {
        type = 'username_password'
        values.username = credential.username ?: ''
        values.password = plainText(credential.password)
    } else if (credential.metaClass.hasProperty(credential, 'secret')) {
        type = 'secret_text'
        values.value = plainText(credential.secret)
    }
    [id: credential.id, type: type, values: values]
}
println JsonOutput.toJson(rows)
"""


def jenkins_request(session, method, url, base_url, **kwargs):
    def origin(value):
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Invalid Jenkins URL origin")
        default_port = 443 if parsed.scheme == "https" else 80
        return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or default_port

    if origin(url) != origin(base_url):
        raise ValueError("Jenkins URL origin mismatch")
    kwargs["allow_redirects"] = False
    return getattr(session, method.lower())(url, **kwargs)
def safe_backup_job_path(root, job_name):
    root_path = Path(root).resolve()
    if not isinstance(job_name, str) or not job_name:
        raise ValueError("Invalid Jenkins job path")

    normalized_name = job_name.replace("\\", "/")
    parts = normalized_name.split("/")
    if normalized_name.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ValueError("Invalid Jenkins job path")

    candidate = root_path.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root_path):
        raise ValueError("Jenkins job path escapes backup root")
    return candidate


def run_jenkins_script(session, base_url, script):
    headers = {}
    try:
        crumb_response = jenkins_request(session, 'GET', urljoin(base_url, 'crumbIssuer/api/json'), base_url, timeout=15)
        if crumb_response.status_code == 200:
            crumb_data = crumb_response.json()
            if crumb_data.get('crumbRequestField') and crumb_data.get('crumb'):
                headers[crumb_data['crumbRequestField']] = crumb_data['crumb']
    except (requests.RequestException, ValueError, TypeError):
        pass

    response = jenkins_request(
        session,
        'POST',
        urljoin(base_url, 'scriptText'),
        base_url,
        data={'script': script},
        headers=headers,
        timeout=15
    )
    if response.status_code != 200:
        raise requests.RequestException(f'Jenkins script unavailable: HTTP {response.status_code}')
    return response.text


TOOL_INSTALLATIONS_SCRIPT = """
import groovy.json.JsonOutput
import hudson.tools.ToolDescriptor

def toolType(descriptor) {
    def key = ((descriptor.id ?: '') + ' ' + descriptor.class.name).toLowerCase()
    if (key.contains('jdk')) return 'jdk'
    if (key.contains('maven')) return 'maven'
    if (key.contains('nodejs')) return 'nodejs'
    if (key.contains('gradle')) return 'gradle'
    if (key.contains('git')) return 'git'
    return ''
}

def rows = ToolDescriptor.all().collectMany { descriptor ->
    def type = toolType(descriptor)
    if (!type) return []
    (descriptor.installations ?: []).collect { installation ->
        [type: type, name: installation.name ?: '', home: installation.home ?: '']
    }
}
println JsonOutput.toJson(rows)
"""


def fetch_tool_installations(session, base_url):
    try:
        rows = json.loads(run_jenkins_script(session, base_url, TOOL_INSTALLATIONS_SCRIPT))
        supported = {'jdk', 'maven', 'nodejs', 'gradle', 'git'}
        return [
            {'type': row['type'].strip(), 'name': row['name'].strip(), 'home': row['home'].strip()}
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get('type'), str) and row['type'].strip() in supported
            and isinstance(row.get('name'), str) and row['name'].strip()
            and isinstance(row.get('home'), str) and row['home'].strip()
        ]
    except (requests.RequestException, ValueError, TypeError, KeyError) as error:
        logging.warning('Jenkins tool export unavailable (%s)', type(error).__name__)
        return []


def fetch_credential_values(session, base_url, credential_ids):
    if not credential_ids:
        return {}

    encoded_ids = base64.b64encode(
        json.dumps(sorted(credential_ids), ensure_ascii=False).encode('utf-8')
    ).decode('ascii')
    script = CREDENTIAL_EXPORT_SCRIPT.replace('__CREDENTIAL_IDS__', encoded_ids)

    try:
        rows = json.loads(run_jenkins_script(session, base_url, script))
        return {
            row['id']: {
                'type': row.get('type', 'unknown'),
                'values': row.get('values') or {}
            }
            for row in rows
            if row.get('id') in credential_ids
        }
    except (requests.RequestException, ValueError, TypeError, KeyError) as error:
        logging.warning('Jenkins credential export unavailable (%s)', type(error).__name__)
        return {}
# ==========================================
# 2. Recursive SCM & Jenkins Job Fetching
# ==========================================
def get_all_jobs_recursive(
    session,
    base_url,
    folder_url=None,
    parent_path="",
    raise_errors=False
):
    """Recursively fetch all Jobs under any Jenkins folder structure."""
    if folder_url is None:
        api_url = urljoin(base_url, "api/json?tree=jobs[name,url,class]")
    else:
        api_url = urljoin(
            folder_url.rstrip('/') + '/',
            "api/json?tree=jobs[name,url,class]"
        )

    jobs_list = []
    try:
        response = jenkins_request(session, 'GET', api_url, base_url, timeout=15)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Jenkins jobs response must be an object")

        jobs = data.get("jobs", [])
        if not isinstance(jobs, list):
            raise ValueError("Jenkins jobs must be a list")

        for job in jobs:
            if not isinstance(job, dict):
                raise ValueError("Jenkins job must be an object")
            job_name = job.get("name", "")
            job_url = job.get("url", "")
            job_class = job.get("_class", job.get("class", ""))
            if not all(isinstance(value, str) for value in (
                job_name,
                job_url,
                job_class
            )):
                raise ValueError("Jenkins job fields must be strings")
            full_job_name = f"{parent_path}/{job_name}" if parent_path else job_name

            if "folder" in job_class.lower() or "multibranch" in job_class.lower():
                jobs_list.extend(get_all_jobs_recursive(
                    session,
                    base_url,
                    job_url,
                    full_job_name,
                    raise_errors=raise_errors
                ))
            else:
                jobs_list.append({
                    "name": full_job_name,
                    "url": job_url,
                    "class": job_class
                })
    except (requests.RequestException, ValueError) as error:
        if raise_errors:
            raise
        logging.error("Error recursively fetching jobs: %s", error)

    return jobs_list


def get_view_snapshots(session, base_url, backed_job_names):
    backed_job_names = list(backed_job_names)
    fallback = [{"name": "全部任务", "job_names": backed_job_names}]

    try:
        response = jenkins_request(
            session,
            'GET',
            urljoin(base_url, "api/json?tree=views[name,url]"),
            base_url,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Jenkins views response must be an object")
        views = data.get("views")
        if not isinstance(views, list):
            raise ValueError("Jenkins views must be a list")

        backed_job_names_set = set(backed_job_names)
        snapshots = []
        for view in views:
            if not isinstance(view, dict):
                continue
            name = view.get("name")
            view_url = view.get("url")
            if name is None or view_url is None or name == "" or view_url == "":
                continue
            if not isinstance(name, str) or not isinstance(view_url, str):
                raise ValueError("Jenkins view fields must be strings")
            view_jobs = get_all_jobs_recursive(
                session,
                base_url,
                view_url,
                raise_errors=True
            )
            snapshots.append({
                "name": name,
                "job_names": [
                    job["name"] for job in view_jobs
                    if job["name"] in backed_job_names_set
                ]
            })

        return snapshots or fallback
    except (requests.RequestException, ValueError) as error:
        logging.warning(
            "Jenkins view snapshot unavailable (%s); using fallback",
            type(error).__name__
        )
        return fallback

# ==========================================
# 3. Core Zip Backup & Archive Worker
# ==========================================
def execute_jenkins_backup(server_id: int, backup_id: int):
    """
    Background worker that runs inside a thread pool to perform Jenkins backup.
    Downloads config.xml, parses metadata, extracts scripts, packages as zip,
    saves summary Markdown to DB, and cleans up the temporary files.
    """
    db = SyncSessionLocal()
    backup_record = db.query(JenkinsBackup).filter(JenkinsBackup.id == backup_id).first()
    if not backup_record:
        db.close()
        return
        
    server = db.query(JenkinsServer).filter(JenkinsServer.id == server_id).first()
    if not server:
        backup_record.status = "FAILED"
        db.commit()
        db.close()
        return

    # Clean URL to prevent trailing "/login" copied from browser address bar
    url_clean = server.url.rstrip('/')
    if url_clean.endswith('/login'):
        url_clean = url_clean[:-6]
    base_url = url_clean if url_clean.endswith('/') else url_clean + '/'

    try:
        decrypted_token = decrypt_secret(server.api_token)
    except Exception:
        decrypted_token = server.api_token

    try:
        decrypted_username = decrypt_secret(server.username)
    except Exception:
        decrypted_username = server.username

    # Setup Session
    session = requests.Session()
    if decrypted_username and decrypted_token:
        session.auth = HTTPBasicAuth(decrypted_username, decrypted_token)

    # Persistent storage receives encrypted files only; plaintext stays in tmpfs/temp.
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    backups_root = os.path.join(base_dir, "data", "backups", f"server_{server_id}")
    temp_base = os.getenv("BACKUP_TMP_DIR", tempfile.gettempdir())
    temp_container = None
    enc_filepath = None
    try:
        os.makedirs(backups_root, exist_ok=True)
        os.makedirs(temp_base, exist_ok=True)
        temp_container = tempfile.mkdtemp(prefix=f"server_{server_id}_backup_{backup_id}_", dir=temp_base)
        temp_root = os.path.join(temp_container, "payload")
        os.makedirs(temp_root)
        enc_filepath = os.path.join(backups_root, f"backup_{backup_id}.zip.enc")

        # 1. Fetch recursively all jobs (raise errors if API request fails, e.g., 401 Unauthorized)
        all_jobs = get_all_jobs_recursive(session, base_url, raise_errors=True)
        all_parsed_info = {}
        backed_up_count = 0

        # 2. Download and Parse each job
        for job in all_jobs:
            job_name = job["name"]
            try:
                job_dir = safe_backup_job_path(temp_root, job_name)
            except ValueError as error:
                logging.warning("Skipping Jenkins job with unsafe path %r: %s", job_name, error)
                continue
            job_url = job["url"]

            # Pull config.xml
            config_url = urljoin(job_url.rstrip('/') + '/', "config.xml")
            try:
                config_res = jenkins_request(session, 'GET', config_url, base_url, timeout=15)
                if config_res.status_code != 200:
                    continue
                config_xml = config_res.text
            except Exception:
                continue

            # Parse config xml
            parser = JenkinsConfigParser(config_xml)
            parsed_info = parser.parse()
            if not parsed_info:
                continue

            all_parsed_info[job_name] = parsed_info
            
            # Save into temporary files directory structure
            os.makedirs(job_dir, exist_ok=True)

            # Write config.xml
            with open(os.path.join(job_dir, "config.xml"), "w", encoding="utf-8") as f:
                f.write(config_xml)

            # Write info.json
            with open(os.path.join(job_dir, "info.json"), "w", encoding="utf-8") as f:
                json.dump(parsed_info, f, indent=4, ensure_ascii=False)

            # Extract shell scripting steps
            build_steps = parsed_info.get("build_steps") or []
            shell_steps = [step for step in build_steps if step.get("type") == "shell" and step.get("script")]
            if len(shell_steps) == 1:
                with open(os.path.join(job_dir, "build.sh"), "w", encoding="utf-8", newline="\n") as f:
                    f.write(shell_steps[0]["script"])
            elif len(shell_steps) > 1:
                for idx, step in enumerate(shell_steps, 1):
                    with open(os.path.join(job_dir, f"build_step_{idx}.sh"), "w", encoding="utf-8", newline="\n") as f:
                        f.write(step["script"])

            # Extract inline pipelines
            pipeline = parsed_info.get("pipeline")
            if pipeline and pipeline.get("type") == "inline" and pipeline.get("script"):
                with open(os.path.join(job_dir, "Jenkinsfile"), "w", encoding="utf-8", newline="\n") as f:
                    f.write(pipeline["script"])

            backed_up_count += 1

        tool_installations = fetch_tool_installations(session, base_url)
        credential_ids = {
            credential["id"]
            for info in all_parsed_info.values()
            for credential in info.get("credentials", [])
        }
        credential_ids.update(
            repo.get("credentials_id")
            for info in all_parsed_info.values()
            for repo in (info.get("scm") or {}).get("repos", [])
            if repo.get("credentials_id")
        )
        credential_values = fetch_credential_values(session, base_url, credential_ids)
        job_details = []

        for job_name, info in all_parsed_info.items():
            scm = info.get("scm") or {}
            pipeline = info.get("pipeline") or {}
            build_steps = info.get("build_steps") or []
            shell_steps = [
                step for step in build_steps
                if step.get("type") == "shell" and step.get("script")
            ]
            scripts = []

            if pipeline.get("type") == "inline" and pipeline.get("script"):
                scripts.append({
                    "filename": "Jenkinsfile",
                    "type": "pipeline",
                    "phase": "pipeline",
                    "content": pipeline["script"]
                })

            shell_number = 0
            for index, step in enumerate(build_steps, 1):
                step_type = step.get("type", "unknown")
                content_value = ""
                filename = f"build_step_{index}.txt"

                if step_type == "shell" and step.get("script"):
                    shell_number += 1
                    filename = (
                        "build.sh" if len(shell_steps) == 1
                        else f"build_step_{shell_number}.sh"
                    )
                    content_value = step["script"]
                elif step_type == "batch" and step.get("script"):
                    filename = f"build_step_{index}.bat"
                    content_value = step["script"]
                elif step_type == "maven" and step.get("targets"):
                    filename = f"maven_step_{index}.sh"
                    content_value = f"mvn {step['targets']}"
                elif step_type == "gradle" and step.get("tasks"):
                    filename = f"gradle_step_{index}.sh"
                    content_value = f"gradle {step['tasks']}"

                if content_value:
                    scripts.append({
                        "filename": filename,
                        "type": step_type,
                        "phase": step.get("phase", "builders"),
                        "content": content_value
                    })

            credentials = []
            for reference in info.get("credentials", []):
                resolved = credential_values.get(reference["id"])
                credential = {
                    "id": reference["id"],
                    "type": resolved["type"] if resolved else reference["type"],
                    "bindings": reference.get("bindings") or {},
                    "values": resolved["values"] if resolved else {}
                }
                if not resolved:
                    credential["value_unavailable"] = True
                credentials.append(credential)

            git_urls = [
                repo.get("url", "")
                for repo in scm.get("repos", [])
                if repo.get("url")
            ]
            git_repositories = []
            for repo in scm.get("repos", []):
                if not repo.get("url"):
                    continue
                credential_id = repo.get("credentials_id")
                resolved = credential_values.get(credential_id) if credential_id else None
                credential = None
                if credential_id:
                    credential = {
                        "id": credential_id,
                        "type": resolved["type"] if resolved else "unknown",
                        "values": resolved["values"] if resolved else {},
                    }
                    if not resolved:
                        credential["value_unavailable"] = True
                git_repositories.append({"url": repo["url"], "credential": credential})
            branches = list(scm.get("branches", []))
            if pipeline.get("type") == "scm":
                if pipeline.get("url"):
                    git_urls.append(pipeline["url"])
                if pipeline.get("branch"):
                    branches.append(pipeline["branch"])

            job_details.append({
                "name": job_name,
                "project_type": info.get("project_type", "").split(".")[-1],
                "status": "disabled" if info.get("general", {}).get("disabled") else "enabled",
                "git_urls": git_urls,
                "git_repositories": git_repositories,
                "selected_tools": info.get("selected_tools") or {},
                "branches": branches,
                "triggers": info.get("triggers") or [],
                "parameters": info.get("parameters") or [],
                "environment": info.get("environment") or {"variables": [], "tools": {}},
                "credentials": credentials,
                "scripts": scripts,
                "maven_config": info.get("maven_config")
            })

        view_snapshots = get_view_snapshots(
            session,
            base_url,
            all_parsed_info.keys()
        )
        with open(os.path.join(temp_root, "details.json"), "w", encoding="utf-8") as file:
            json.dump(
                {"version": 3, "views": view_snapshots, "jobs": job_details, "tool_installations": tool_installations},
                file,
                indent=2,
                ensure_ascii=False
            )
        # 3. Generate summary Markdown text
        summary_md = ""
        if all_parsed_info:
            headers = ["Job 名称", "项目类型", "状态", "Git 仓库 / SCM", "分支", "构建触发器", "构建脚本/打包命令摘要"]
            md_lines = [
                "# Jenkins 备份配置汇总报告",
                f"\n* **备份时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"* **备份任务数**: {backed_up_count} 个\n",
                "| " + " | ".join(headers) + " |",
                "| " + " | ".join(["---"] * len(headers)) + " |"
            ]

            for job_name, info in all_parsed_info.items():
                general = info.get("general", {})
                scm = info.get("scm", {})
                pipeline = info.get("pipeline", {})
                triggers = info.get("triggers", [])
                build_steps = info.get("build_steps", [])

                p_type = info.get("project_type", "").split(".")[-1]
                status = "禁用" if general.get("disabled") else "启用"

                git_urls = []
                branches = []

                if scm.get("type") == "git":
                    git_urls = [r.get("url", "") for r in scm.get("repos", []) if r.get("url")]
                    branches = scm.get("branches", [])

                if pipeline:
                    if pipeline.get("type") == "scm":
                        if pipeline.get("url"):
                            git_urls.append(pipeline.get("url"))
                        if pipeline.get("branch"):
                            branches.append(pipeline.get("branch"))

                git_urls_str = "<br>".join(git_urls) if git_urls else "无"
                branches_str = "<br>".join(branches) if branches else "无"

                trigger_list = []
                for t in triggers:
                    desc = t.get("desc", t.get("type", "").split(".")[-1])
                    trigger_list.append(desc)
                trigger_str = "<br>".join(trigger_list) if trigger_list else "手动触发"

                summary_parts = []
                
                # 徽章样式定义
                badge_style = "display: inline-block; padding: 2px 5px; border-radius: 3px; font-size: 10px; font-weight: bold; margin-right: 4px; line-height: 1.2;"
                phase_badges = {
                    "prebuilders": f'<span style="{badge_style} background-color: #ffebee; color: #c62828;">前置</span>',
                    "builders": f'<span style="{badge_style} background-color: #e3f2fd; color: #1565c0;">构建</span>',
                    "postbuilders": f'<span style="{badge_style} background-color: #fff3e0; color: #ef6c00;">后置</span>',
                    "maven-moduleset": f'<span style="{badge_style} background-color: #e8f5e9; color: #2e7d32;">Maven配置</span>'
                }
                type_badges = {
                    "shell": f'<span style="{badge_style} background-color: #eceff1; color: #37474f; font-family: monospace;">Shell</span>',
                    "maven": f'<span style="{badge_style} background-color: #efebe9; color: #4e342e; font-family: monospace;">Maven</span>',
                    "gradle": f'<span style="{badge_style} background-color: #f3e5f5; color: #6a1b9a; font-family: monospace;">Gradle</span>',
                    "batch": f'<span style="{badge_style} background-color: #f1f8e9; color: #558b2f; font-family: monospace;">Batch</span>'
                }

                if pipeline:
                    p_script_type = pipeline.get("type")
                    if p_script_type == "inline":
                        summary_parts.append(
                            f'<div style="margin-bottom: 6px; line-height: 1.4;">'
                            f'<span style="{badge_style} background-color: #e0f7fa; color: #006064;">Pipeline</span>'
                            f'<span style="font-size: 11px; color: #555; margin-left: 4px;">内嵌脚本 (📁 `Jenkinsfile`)</span></div>'
                        )
                    elif p_script_type == "scm":
                        summary_parts.append(
                            f'<div style="margin-bottom: 6px; line-height: 1.4;">'
                            f'<span style="{badge_style} background-color: #e0f7fa; color: #006064;">Pipeline</span>'
                            f'<span style="font-size: 11px; color: #555; margin-left: 4px;">SCM 脚本 (路径: `{pipeline.get("script_path")}`)</span></div>'
                        )
                        
                # 统计当前任务的所有有效 Shell 步骤，以便与导出的文件名匹配
                valid_shell_steps = [s for s in build_steps if s.get("type") == "shell" and s.get("script")]
                shell_counter = 0
                
                for i, step in enumerate(build_steps):
                    stype = step.get("type")
                    phase = step.get("phase", "builders")
                    
                    phase_badge = phase_badges.get(phase, "")
                    
                    if stype == "shell":
                        shell_counter += 1
                        # 确定导出的文件名
                        filename = "build.sh" if len(valid_shell_steps) == 1 else f"build_step_{shell_counter}.sh"
                        
                        script_content = step.get("script", "").strip()
                        first_line = script_content.split("\n")[0] if script_content else ""
                        # 如果首行是 #!/bin/bash 等，尝试获取第二行作为更有效的信息
                        if (first_line.startswith("#!") or first_line.startswith("#")) and len(script_content.split("\n")) > 1:
                            # 找前几行中非注释的第一行作为预览
                            script_lines = [line.strip() for line in script_content.split("\n") if line.strip()]
                            for line in script_lines:
                                if not line.startswith("#"):
                                    first_line = line
                                    break
                        
                        if len(first_line) > 50:
                            first_line = first_line[:50] + "..."
                        
                        first_line = html.escape(first_line).replace("`", "&#96;")
                        preview_str = f' <code style="padding: 1px 3px; background: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 3px; font-size: 11px; font-family: Consolas, monospace; color: #c7254e; word-break: break-all;">{first_line}</code>' if first_line else ""
                        type_badge = type_badges.get("shell", "")
                        
                        step_html = f'<div style="margin-bottom: 6px; line-height: 1.4;">' \
                                    f'{phase_badge}{type_badge}' \
                                    f'<span style="font-size: 11px; color: #666; margin-right: 4px;">(📁 `{filename}`)</span>' \
                                    f'{preview_str}</div>'
                        summary_parts.append(step_html)
                        
                    elif stype == "batch":
                        type_badge = type_badges.get("batch", "")
                        step_html = f'<div style="margin-bottom: 6px; line-height: 1.4;">' \
                                    f'{phase_badge}{type_badge}' \
                                    f'<span style="font-size: 11px; color: #666;">Windows Batch 步骤 {i+1}</span></div>'
                        summary_parts.append(step_html)
                    elif stype == "maven":
                        type_badge = type_badges.get("maven", "")
                        pom_info = f' <span style="font-size: 11px; color: #888;">(POM: `{step.get("pom")}`)</span>' if step.get("pom") and step.get("pom") != "pom.xml" else ""
                        targets_str = f' <code style="padding: 1px 3px; background: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 3px; font-size: 11px; font-family: Consolas, monospace; color: #1565c0; word-break: break-all;">mvn {step.get("targets")}</code>' if step.get("targets") else ""
                        
                        step_html = f'<div style="margin-bottom: 6px; line-height: 1.4;">' \
                                    f'{phase_badge}{type_badge}' \
                                    f'{pom_info}{targets_str}</div>'
                        summary_parts.append(step_html)
                    elif stype == "gradle":
                        type_badge = type_badges.get("gradle", "")
                        tasks_str = f' <code style="padding: 1px 3px; background: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 3px; font-size: 11px; font-family: Consolas, monospace; color: #6a1b9a; word-break: break-all;">gradle {step.get("tasks")}</code>' if step.get("tasks") else ""
                        
                        step_html = f'<div style="margin-bottom: 6px; line-height: 1.4;">' \
                                    f'{phase_badge}{type_badge}' \
                                    f'{tasks_str}</div>'
                        summary_parts.append(step_html)
                        
                summary_str = "".join(summary_parts) if summary_parts else '<span style="color: #999; font-size: 11px;">未检测到构建步骤</span>'

                row = [
                    f"`{job_name}`",
                    p_type,
                    status,
                    git_urls_str,
                    branches_str,
                    trigger_str,
                    summary_str
                ]
                md_lines.append("| " + " | ".join(row) + " |")

            summary_md = "\n".join(md_lines)

            # Write summary.md to temp dir
            with open(os.path.join(temp_root, "summary.md"), "w", encoding="utf-8") as f:
                f.write(summary_md)

        # 4. Package temp directory to target zip file
        zip_filename = f"backup_{backup_id}.zip"
        zip_filepath = os.path.join(temp_container, zip_filename)

        try:
            with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(temp_root):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # Zip hierarchy starts under temp_root
                        arcname = os.path.relpath(file_path, temp_root)
                        zipf.write(file_path, arcname)


            # Read the plain zip data
            with open(zip_filepath, "rb") as f:
                plain_data = f.read()

            # Encrypt zip data using AES-256-GCM
            import hashlib
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            key_material = settings.BACKUP_ENCRYPTION_KEY
            if not key_material:
                raise ValueError("备份密钥未配置")

            aes_key = hashlib.sha256(key_material.strip().encode()).digest()
            aesgcm = AESGCM(aes_key)

            # Generate 12-byte random nonce
            nonce = os.urandom(12)
            ciphertext = aesgcm.encrypt(nonce, plain_data, None)

            # Form format: JRCB1 + nonce + ciphertext
            encrypted_data = b"JRCB1" + nonce + ciphertext

            with open(enc_filepath, "wb") as f:
                f.write(encrypted_data)

            # 6. Update database record with success status
            backup_record.status = "SUCCESS"
            backup_record.job_count = backed_up_count
            backup_record.summary_md = summary_md
            backup_record.zip_path = enc_filepath
            db.commit()

        finally:
            # Always delete the plain zip file
            if os.path.exists(zip_filepath):
                try:
                    os.remove(zip_filepath)
                except Exception:
                    pass

    except Exception as err:
        logging.error(f"Error executing Jenkins backup: {str(err)}")
        backup_record.status = "FAILED"
        backup_record.zip_path = None
        db.commit()
        if enc_filepath and os.path.exists(enc_filepath):
            try:
                os.remove(enc_filepath)
            except OSError:
                pass
    finally:
        if temp_container:
            shutil.rmtree(temp_container, ignore_errors=True)
        db.close()
