import re
import time
from datetime import datetime

import requests
from typing import Dict, Any, List, Tuple, Optional
from loguru import logger
from urllib.parse import urljoin, quote, urlparse
from app.core.security import decrypt_secret


def normalize_jenkins_status(building: bool, result: Optional[str]) -> str:
    if building:
        return "BUILDING"
    return {
        "SUCCESS": "SUCCESS",
        "UNSTABLE": "UNSTABLE",
        "ABORTED": "CANCELLED",
        "FAILURE": "FAILED",
        "NOT_BUILT": "FAILED",
    }.get(result, "UNKNOWN")


def jenkins_datetime(timestamp_ms: Any) -> Optional[datetime]:
    if not isinstance(timestamp_ms, (int, float)) or timestamp_ms <= 0:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000)


class SafeSession(requests.Session):
    def resolve_redirects(self, resp, req, **kwargs):
        if kwargs.get("yield_requests"):
            yield from super().resolve_redirects(resp, req, **kwargs)
            return

        from urllib.parse import urlparse
        orig_parsed = urlparse(resp.url)
        orig_origin = f"{orig_parsed.scheme}://{orig_parsed.netloc}"

        for redirect_resp in super().resolve_redirects(resp, req, **kwargs):
            if hasattr(redirect_resp, "url"):
                target_url = redirect_resp.url
            else:
                target_url = redirect_resp.req.url

            target_parsed = urlparse(target_url)
            target_origin = f"{target_parsed.scheme}://{target_parsed.netloc}"
            if target_origin != orig_origin:
                raise requests.exceptions.InvalidSchema("禁止跨 Origin 重定向")
            yield redirect_resp

class JenkinsClient:
    def __init__(self, url: str, username: str, encrypted_token: str):
        # Clean URL to prevent trailing "/login" copied from browser address bar
        url_clean = url.rstrip('/')
        if url_clean.endswith('/login'):
            url_clean = url_clean[:-6]
        self.base_url = url_clean if url_clean.endswith('/') else url_clean + '/'
        try:
            self.username = decrypt_secret(username)
        except Exception:
            self.username = username
            
        try:
            self.token = decrypt_secret(encrypted_token)
        except Exception:
            # Fallback for unit testing where key encryption is not set or token is raw
            self.token = encrypted_token
        self.auth = (self.username, self.token)
        self.session = SafeSession()
        self.session.auth = self.auth
        # Standard retries for network resilience
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=3
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def extract_queue_id(self, queue_url: str) -> int:
        queue_parsed = urlparse(queue_url)
        base_parsed = urlparse(self.base_url)
        if (queue_parsed.scheme, queue_parsed.netloc) != (
            base_parsed.scheme,
            base_parsed.netloc,
        ):
            raise ValueError("Queue URL origin does not match Jenkins base URL")
        base_path = base_parsed.path.rstrip("/")
        match = re.fullmatch(rf"{re.escape(base_path)}/queue/item/(\d+)/?", queue_parsed.path)
        if not match:
            raise ValueError("Invalid Jenkins queue URL")
        return int(match.group(1))

    def get_queue_item(self, queue_id: int) -> Optional[Dict[str, Any]]:
        url = urljoin(self.base_url, f"queue/item/{queue_id}/api/json")
        response = self.session.get(url, timeout=10)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def get_crumb_headers(self) -> Dict[str, str]:
        """
        Fetch Jenkins CSRF crumb if enabled.
        """
        try:
            url = urljoin(self.base_url, "crumbIssuer/api/json")
            response = self.session.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                crumb = data.get("crumb")
                field = data.get("crumbRequestField", "Jenkins-Crumb")
                if crumb:
                    return {field: crumb}
        except Exception as e:
            logger.debug(f"CSRF crumb is not enabled or failed to fetch: {str(e)}")
        return {}

    def test_connection(self) -> Tuple[bool, str]:
        """
        Verify credentials and connection with Jenkins.
        """
        try:
            url = urljoin(self.base_url, "api/json")
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return True, "连接 Jenkins 成功"
            return False, f"HTTP 错误 {response.status_code}：{response.text[:200]}"
        except Exception as e:
            return False, str(e)

    def get_views(self) -> List[Dict[str, Any]]:
        """
        Fetch all Jenkins views to map them as release environments.
        """
        url = urljoin(self.base_url, "api/json?tree=views[name,url]")
        response = self.session.get(url, timeout=15)
        response.raise_for_status()
        return response.json().get("views", [])

    def get_jobs_in_view(self, view_name: str) -> List[Dict[str, Any]]:
        """
        Fetch all jobs nested in a specific view.
        """
        encoded_view = quote(view_name)
        # Fetching nested job information, including last build details
        url = urljoin(self.base_url, f"view/{encoded_view}/api/json?tree=jobs[name,url,color,description,lastBuild[number,result,timestamp]]")
        response = self.session.get(url, timeout=15)
        response.raise_for_status()
        return response.json().get("jobs", [])

    def trigger_build(
        self,
        job_name: str,
        parameters: Optional[Dict[str, Any]] = None,
        branch: Optional[str] = None
    ) -> str:
        """
        Trigger a build. Handles both standard and parameterized builds.
        Returns the queue URL of the build.
        """
        job_path = "/".join([f"job/{quote(part)}" for part in job_name.split("/")])

        merged_params = {}
        if parameters:
            merged_params.update(parameters)

        if branch:
            # 1. Try to find the exact Git Parameter name configured in Jenkins
            branch_param_name = "branch" # default fallback
            try:
                url = urljoin(self.base_url, f"{job_path}/api/json?tree=property[parameterDefinitions[*]],actions[parameterDefinitions[*]]")
                response = self.session.get(url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    definitions = []
                    for prop in data.get("property", []):
                        if prop and "parameterDefinitions" in prop:
                            definitions.extend(prop["parameterDefinitions"])
                    for act in data.get("actions", []):
                        if act and "parameterDefinitions" in act:
                            definitions.extend(act["parameterDefinitions"])

                    for p in definitions:
                        p_class = p.get("_class") or p.get("type", "")
                        p_name = p.get("name")
                        if p_name and ("gitparameter" in p_class.lower() or "git_parameter" in p_class.lower()):
                            branch_param_name = p_name
                            break
            except Exception as e:
                logger.debug(f"Failed to fetch parameter name for trigger_build: {str(e)}")

            merged_params[branch_param_name] = branch

        headers = self.get_crumb_headers()
        if merged_params:
            url = urljoin(self.base_url, f"{job_path}/buildWithParameters")
            response = self.session.post(url, data=merged_params, headers=headers, timeout=10)
        else:
            url = urljoin(self.base_url, f"{job_path}/build")
            response = self.session.post(url, headers=headers, timeout=10)

        if response.status_code in [200, 201]:
            location = response.headers.get("Location")
            if not location:
                raise Exception("Build triggered but Location header was missing in Jenkins response.")
            return location
        raise Exception(f"Failed to trigger build: HTTP {response.status_code} - {response.text}")

    def get_build_number_from_queue(
        self,
        queue_url: str,
        timeout: int = 300,
        on_poll: Optional[Any] = None
    ) -> int:
        """
        Polls the queue URL until the job gets scheduled, returning the actual build number.
        This provides high concurrency safety.
        """
        # Validate queue_url origin against base_url origin
        from urllib.parse import urlparse
        queue_parsed = urlparse(queue_url)
        base_parsed = urlparse(self.base_url)
        if queue_parsed.netloc and queue_parsed.scheme:
            queue_origin = f"{queue_parsed.scheme}://{queue_parsed.netloc}"
            base_origin = f"{base_parsed.scheme}://{base_parsed.netloc}"
            if queue_origin != base_origin:
                raise Exception("队列 URL 与配置的 Jenkins Origin 不一致，已拒绝请求")

        # Ensure the queue URL points to our JSON API
        api_url = queue_url if queue_url.endswith('/') else queue_url + '/'

        # Scheme alignment: if base_url is HTTPS but queue_url returned is HTTP (e.g. proxy configuration issue),
        # force HTTPS to avoid network block or redirect issues inside container.
        if self.base_url.startswith("https://") and api_url.startswith("http://"):
            api_url = "https://" + api_url[7:]
        elif self.base_url.startswith("http://") and api_url.startswith("https://"):
            api_url = "http://" + api_url[8:]

        api_url = urljoin(api_url, "api/json")

        last_response_time = time.time()
        while time.time() - last_response_time < timeout:
            try:
                response = self.session.get(api_url, timeout=5)
            except requests.RequestException as error:
                logger.warning(f"Error querying Jenkins queue item: {error}")
                time.sleep(3)
                continue

            if response.status_code == 200:
                data = response.json()
                executable = data.get("executable")
                if executable and executable.get("number") is not None:
                    return executable["number"]
                if data.get("cancelled", False):
                    raise RuntimeError("The build task in Jenkins queue was cancelled.")
                last_response_time = time.time()
                why = data.get("why", "")
                if why:
                    logger.info(f"Build pending in queue: {why}")
                if callable(on_poll):
                    on_poll(why)
            time.sleep(3)
        raise RuntimeError(f"Timeout ({timeout}s) without a valid queue response. Queue URL: {queue_url}")

    def cancel_queue_item(self, queue_id: int) -> None:
        url = urljoin(self.base_url, "queue/cancelItem")
        response = self.session.post(
            url,
            params={"id": queue_id},
            headers=self.get_crumb_headers(),
            timeout=10,
            allow_redirects=False,
        )
        if not (
            200 <= response.status_code < 300
            or response.status_code == 302
        ):
            raise RuntimeError(
                "Failed to cancel queue item: "
                f"HTTP {response.status_code} - {response.text}"
            )

    def stop_build(self, job_name: str, build_number: int) -> None:
        job_path = "/".join([f"job/{quote(part)}" for part in job_name.split("/")])
        url = urljoin(self.base_url, f"{job_path}/{build_number}/stop")
        response = self.session.post(
            url,
            headers=self.get_crumb_headers(),
            timeout=10,
            allow_redirects=False,
        )
        if not (200 <= response.status_code < 300 or response.status_code == 302):
            raise Exception(f"Failed to stop build: HTTP {response.status_code} - {response.text}")
    def get_build_status(self, job_name: str, build_number: int) -> Dict[str, Any]:
        """
        Query build status and details.
        """
        job_path = "/".join([f"job/{quote(part)}" for part in job_name.split("/")])
        url = urljoin(self.base_url, f"{job_path}/{build_number}/api/json")
        response = self.session.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            is_building = data.get("building", False)
            raw_duration = data.get("duration", 0) or 0
            # 当构建还在进行中 (is_building=True) 时，Jenkins API 可能会返回 estimatedDuration，不能用作当前完成耗时
            duration_sec = 0 if is_building else (raw_duration // 1000)
            return {
                "building": is_building,
                "result": data.get("result"), # SUCCESS, FAILURE, ABORTED, etc.
                "timestamp": data.get("timestamp"), # Epoch ms
                "duration": duration_sec, # seconds
                "url": data.get("url"),
                "queue_id": data.get("queueId")
            }
        raise Exception(f"Failed to fetch build status: HTTP {response.status_code}")

    def _parse_value_items(self, data: Any) -> List[str]:
        """
        Helper method to parse fillValueItems response from Jenkins.
        Handles both List (ListBoxModel) and Dict structures.
        """
        results = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("values") or data.get("items") or data.get("choices") or []
        else:
            items = []

        for item in items:
            if isinstance(item, dict):
                val = item.get("value") or item.get("name")
                if val:
                    results.append(str(val).strip())
            elif isinstance(item, str) and item.strip():
                results.append(item.strip())
        return results

    def get_branches_and_tags(self, job_name: str) -> List[str]:
        """
        Dynamically query git branches/tags for a specific job.
        Tries to dynamically detect ChoiceParameter, GitParameter, or StringParameter
        definitions from Job config, then calls descriptor endpoints if needed.
        Fallbacks gracefully.
        """
        job_path = "/".join([f"job/{quote(part)}" for part in job_name.split("/")])
        discovered_branches: List[str] = []
        git_params: List[Tuple[str, str]] = []

        # 1. Try to fetch Job parameter definitions
        try:
            url = urljoin(self.base_url, f"{job_path}/api/json")
            response = self.session.get(url, params={"tree": "property[parameterDefinitions[*]],actions[parameterDefinitions[*]]"}, timeout=10)
            if response.status_code != 200:
                # Retry without tree constraint if strict tree failed
                response = self.session.get(url, timeout=10)

            if response.status_code == 200:
                data = response.json()
                definitions = []
                for prop in (data.get("property") or []):
                    if isinstance(prop, dict) and "parameterDefinitions" in prop:
                        definitions.extend(prop["parameterDefinitions"] or [])
                for act in (data.get("actions") or []):
                    if isinstance(act, dict) and "parameterDefinitions" in act:
                        definitions.extend(act["parameterDefinitions"] or [])

                for p in definitions:
                    if not isinstance(p, dict):
                        continue
                    p_class = p.get("_class") or p.get("type", "")
                    p_name = p.get("name", "")
                    p_name_lower = p_name.lower()
                    p_class_lower = p_class.lower()

                    is_branch_related = any(k in p_name_lower for k in [
                        "branch", "tag", "git", "version", "release"
                    ]) or "gitparameter" in p_class_lower

                    # A. Check ChoiceParameterDefinition or choices in dict
                    choices = p.get("choices")
                    if choices:
                        if isinstance(choices, list):
                            for c in choices:
                                if isinstance(c, str) and c.strip():
                                    discovered_branches.append(c.strip())
                                elif isinstance(c, dict) and (c.get("value") or c.get("name")):
                                    discovered_branches.append(str(c.get("value") or c.get("name")).strip())
                        elif isinstance(choices, str):
                            for line in choices.splitlines():
                                line_clean = line.strip()
                                if line_clean:
                                    discovered_branches.append(line_clean)

                    # B. Check String/Text Parameter default value
                    default_val = None
                    if "defaultparametervalue" in p:
                        default_val = p.get("defaultparametervalue", {}).get("value")
                    elif "defaultValue" in p:
                        default_val = p.get("defaultValue")

                    if default_val and is_branch_related:
                        val_str = str(default_val).strip()
                        if val_str and val_str not in discovered_branches:
                            discovered_branches.append(val_str)

                    # C. Check GitParameterDefinition for dynamic fillValueItems polling
                    if is_branch_related or "gitparameter" in p_class_lower:
                        if p_name:
                            git_params.append((p_name, p_class))

        except Exception as e:
            logger.debug(f"Failed to fetch parameter definitions for job {job_name}: {str(e)}")

        # 2. Query GitParameter definitions via fillValueItems descriptor
        if git_params:
            for p_name, p_class in git_params:
                try:
                    endpoint = f"{job_path}/descriptorByName/{p_class}/fillValueItems"
                    url = urljoin(self.base_url, endpoint)
                    response = self.session.get(url, params={"param": p_name, "job": job_name}, timeout=10)
                    if response.status_code == 200:
                        vals = self._parse_value_items(response.json())
                        for v in vals:
                            if v not in discovered_branches:
                                discovered_branches.append(v)
                except Exception as e:
                    logger.debug(f"Git Parameter poll failed for '{p_name}' using class '{p_class}': {str(e)}")

        # 3. Fallback heuristic descriptor search if still empty
        if not discovered_branches:
            potential_params = ["branch", "BRANCH", "tag", "TAG", "git_parameter", "GitParameter"]
            potential_classes = [
                "net.uaznia.lukanus.hudson.plugins.gitparameter.GitParameterDefinition",
                "net.uaznia.lukanus.jenkins.plugins.gitparameter.GitParameterDefinition"
            ]
            for param in potential_params:
                for p_class in potential_classes:
                    try:
                        endpoint = f"{job_path}/descriptorByName/{p_class}/fillValueItems"
                        url = urljoin(self.base_url, endpoint)
                        response = self.session.get(url, params={"param": param, "job": job_name}, timeout=10)
                        if response.status_code == 200:
                            vals = self._parse_value_items(response.json())
                            for v in vals:
                                if v not in discovered_branches:
                                    discovered_branches.append(v)
                            if discovered_branches:
                                break
                    except Exception:
                        continue
                if discovered_branches:
                    break

        # Remove duplicates while preserving order
        unique_branches = []
        for b in discovered_branches:
            if b and b not in unique_branches:
                unique_branches.append(b)

        # Final fallback default branches if none discovered
        if not unique_branches:
            unique_branches = ["master", "main", "develop", "release"]

        return unique_branches

    def get_progressive_log(self, job_name: str, build_number: int, start: int = 0) -> Tuple[str, int, bool]:
        """
        Fetches progressive console log text from Jenkins.
        Returns: Tuple[log_text, next_start_byte_offset, has_more_data]
        """
        job_path = "/".join([f"job/{quote(part)}" for part in job_name.split("/")])
        url = urljoin(self.base_url, f"{job_path}/{build_number}/logText/progressiveText")
        try:
            headers = self.get_crumb_headers()
            response = self.session.post(url, data={"start": start}, headers=headers, timeout=10)
            if response.status_code == 200:
                log_text = response.text
                next_start = int(response.headers.get("X-Text-Size", start))
                has_more = response.headers.get("X-More-Data") == "true"
                return log_text, next_start, has_more
            elif response.status_code == 404:
                # Build may have not initialized yet
                return "Waiting for build console output to generate...", start, True
            else:
                return f"HTTP Error {response.status_code} while reading logs.", start, False
        except Exception as e:
            return f"Error retrieving build logs: {str(e)}", start, False

    def get_build_numbers(self, job_name: str) -> set[int] | None:
        """
        Fetch every build number for safe deletion reconciliation.
        None means Jenkins could not provide an inventory; an empty set is valid.
        """
        job_path = "/".join([f"job/{quote(part)}" for part in job_name.split("/")])
        url = urljoin(self.base_url, f"{job_path}/api/json?tree=builds[number]")
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return {
                    build["number"]
                    for build in response.json().get("builds", [])
                    if isinstance(build.get("number"), int)
                }
            logger.warning(f"Failed to fetch build numbers for job {job_name}: HTTP {response.status_code}")
        except Exception as e:
            logger.warning(f"Failed to fetch build numbers for job {job_name}: {str(e)}")
        return None

    def get_recent_builds(self, job_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Fetch recent builds for a job including number, result, timestamp, duration and causes.
        Queries both allBuilds and builds to guarantee capturing in-progress (building) items across all Jenkins versions.
        """
        try:
            job_path = "/".join([f"job/{quote(part)}" for part in job_name.split("/")])
            tree_param = "allBuilds[number,queueId,result,timestamp,duration,building,actions[causes[userName,shortDescription],parameters[name,value]]],builds[number,queueId,result,timestamp,duration,building,actions[causes[userName,shortDescription],parameters[name,value]]]"
            url = urljoin(self.base_url, f"{job_path}/api/json?tree={tree_param}")
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                raw_builds = data.get("allBuilds") or data.get("builds") or []
                sorted_builds = sorted(
                    [b for b in raw_builds if isinstance(b, dict) and b.get("number")],
                    key=lambda b: b.get("number") or 0,
                    reverse=True
                )
                return sorted_builds[:limit]
        except Exception as e:
            logger.warning(f"Failed to fetch recent builds for job {job_name}: {str(e)}")
        return []

    def get_job_parameters(self, job_name: str) -> List[str]:
        job_path = "/".join([f"job/{quote(part)}" for part in job_name.split("/")])
        url = urljoin(self.base_url, f"{job_path}/api/json?tree=property[parameterDefinitions[*]],actions[parameterDefinitions[*]]")
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                definitions = []
                for prop in data.get("property", []):
                    if prop and "parameterDefinitions" in prop:
                        definitions.extend(prop["parameterDefinitions"])
                for act in data.get("actions", []):
                    if act and "parameterDefinitions" in act:
                        definitions.extend(act["parameterDefinitions"])
                return [p.get("name") for p in definitions if p.get("name")]
            elif response.status_code == 404:
                return []
            else:
                raise Exception(f"HTTP {response.status_code}")
        except Exception as e:
            logger.debug(f"Failed to fetch parameter definitions for job {job_name}: {str(e)}")
            raise Exception(f"无法获取 Jenkins 任务的参数定义: {str(e)}")
